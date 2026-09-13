"""Verified runtime boundary for the installed Gmsh Python distribution.

The public geometry producers capture a binding in the parent process and pass
that record to their isolated child. This module deliberately does not import
Gmsh while capturing the binding. The child imports the source module only
after checking the interpreter, import resolution, source/cache and process
runtime identities; the Gmsh native handle is then resolved to the file that
is actually mapped by Windows.
"""

from __future__ import annotations

import ast
import ctypes
import ctypes.wintypes
import dis
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import marshal
import os
import stat
import sys
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, FunctionType, GetSetDescriptorType, ModuleType
from typing import Any, BinaryIO, cast

_SCHEMA_VERSION = "gmsh-runtime-identity-v1"
_GMSH_DISTRIBUTION = "gmsh"
_GMSH_VERSION = "4.15.2"
_MODULE_FILE = "gmsh.py"
_LIBRARY_FILE = "gmsh-4.15.dll"
_FILE_FIELDS = frozenset({"path", "size", "sha256"})
_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "python",
        "python_image",
        "python_library",
        "module",
        "library",
        "pyvenv_cfg",
    }
)
_PROCESS_FIELDS = ("python", "python_image", "python_library", "pyvenv_cfg")
_HASH_CHUNK = 1024 * 1024
_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_MAX_CACHE_BYTES = 32 * 1024 * 1024
_MAX_WINDOWS_PATH = 32768
_MAX_LIVE_CLASSES = 1024
_MAX_LIVE_MEMBERS = 8192
_MAX_LIVE_GLOBALS = 16384
_MAX_VALUE_ITEMS = 128
_MAX_VALUE_DEPTH = 4
_NATIVE_SYMBOL_PREFIX = "gmsh"
_NATIVE_CALL_ATTRIBUTES = ("argtypes", "restype", "errcheck")


class _RuntimeBindingError(OSError):
    """A closed-fail runtime layout or loaded-code error."""


def _error(message: str) -> _RuntimeBindingError:
    return _RuntimeBindingError(message)


def _path_key(value: str | os.PathLike[str]) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(value))))
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime identity path is invalid") from exc


def _resolve_regular_file(value: object, label: str) -> Path:
    if isinstance(value, Path):
        candidate = value
    elif isinstance(value, str):
        candidate = Path(value)
    else:
        raise _error(f"{label} path is not text")
    if not str(candidate) or "\x00" in str(candidate):
        raise _error(f"{label} path is invalid")
    try:
        resolved = candidate.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error(f"{label} file is unavailable") from exc
    if not stat.S_ISREG(info.st_mode):
        raise _error(f"{label} file is not regular")
    if getattr(info, "st_file_attributes", 0) & getattr(
        ctypes.wintypes, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        raise _error(f"{label} file is a reparse point")
    return resolved


def _read_stream_bounded(
    stream: BinaryIO, limit: int, label: str
) -> tuple[bytes, int, str, int]:
    try:
        before = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while chunk := stream.read(_HASH_CHUNK):
            if not isinstance(chunk, bytes):
                raise _error(f"{label} file returned non-binary content")
            size += len(chunk)
            if size > limit:
                raise _error(f"{label} file exceeds the finite verification limit")
            digest.update(chunk)
            chunks.append(chunk)
        after = os.fstat(stream.fileno())
    except _RuntimeBindingError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise _error(f"{label} file cannot be read") from exc
    if (
        size != before.st_size
        or after.st_size != before.st_size
        or getattr(after, "st_mtime_ns", None) != getattr(before, "st_mtime_ns", None)
    ):
        raise _error(f"{label} file changed while it was being verified")
    return b"".join(chunks), size, digest.hexdigest(), getattr(after, "st_mtime_ns", 0)


def _pinned_file(admission: ExitStack, path: Path, label: str) -> BinaryIO:
    """Retain the existing Windows directory/file identity guard through admission."""

    try:
        if os.name == "nt":
            from febio_cae.storage._ownership import pinned_read

            return admission.enter_context(pinned_read(path))
        return admission.enter_context(path.open("rb"))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _error(f"{label} file cannot be pinned for admission") from exc


def _file_identity(value: object, label: str = "runtime") -> dict[str, object]:
    path = _resolve_regular_file(value, label)
    try:
        before = path.stat()
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK):
                digest.update(chunk)
                size += len(chunk)
        after = path.stat()
    except OSError as exc:
        raise _error(f"{label} file cannot be hashed") from exc
    if (
        size != before.st_size
        or after.st_size != before.st_size
        or getattr(after, "st_mtime_ns", None) != getattr(before, "st_mtime_ns", None)
    ):
        raise _error(f"{label} file changed while it was being verified")
    return {"path": str(path), "size": size, "sha256": digest.hexdigest()}


def _validate_file_record(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _FILE_FIELDS:
        raise ValueError(f"runtime identity {label} record is malformed")
    path = value.get("path")
    size = value.get("size")
    digest = value.get("sha256")
    if (
        not isinstance(path, str)
        or not path
        or "\x00" in path
        or type(size) is not int
        or size < 0
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(f"runtime identity {label} record is malformed")
    return {"path": path, "size": size, "sha256": digest}


def _normalise_binding(value: object, label: str = "runtime") -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _BINDING_FIELDS:
        raise ValueError(f"{label} identity record is malformed")
    if value.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError(f"{label} identity schema is unsupported")
    result: dict[str, object] = {"schema_version": _SCHEMA_VERSION}
    for field in ("python", "python_image", "python_library", "module", "library"):
        result[field] = _validate_file_record(value.get(field), field)
    cfg = value.get("pyvenv_cfg")
    result["pyvenv_cfg"] = None if cfg is None else _validate_file_record(cfg, "pyvenv_cfg")
    return result


def _compare_file_records(
    expected: dict[str, object], observed: dict[str, object], label: str
) -> None:
    expected_path = expected["path"]
    observed_path = observed["path"]
    if not isinstance(expected_path, str) or not isinstance(observed_path, str):
        raise ValueError(f"runtime identity {label} path is malformed")
    if (
        _path_key(expected_path) != _path_key(observed_path)
        or expected["size"] != observed["size"]
        or expected["sha256"] != observed["sha256"]
    ):
        raise ValueError(f"runtime identity mismatch for {label}")


def verify_runtime_identity(expected: object, observed: object) -> None:
    """Authenticate a child identity record against the parent capture."""

    expected_record = _normalise_binding(expected, "expected runtime")
    observed_record = _normalise_binding(observed, "observed runtime")
    for field in ("python", "python_image", "python_library", "module", "library"):
        _compare_file_records(
            expected_record[field],  # type: ignore[arg-type]
            observed_record[field],  # type: ignore[arg-type]
            field,
        )
    expected_cfg = expected_record["pyvenv_cfg"]
    observed_cfg = observed_record["pyvenv_cfg"]
    if (expected_cfg is None) != (observed_cfg is None):
        raise ValueError("runtime identity mismatch for pyvenv_cfg")
    if expected_cfg is not None and observed_cfg is not None:
        _compare_file_records(expected_cfg, observed_cfg, "pyvenv_cfg")  # type: ignore[arg-type]


def _coerce_module_handle(handle: object) -> ctypes.wintypes.HMODULE | None:
    if handle is None:
        return None
    if type(handle) is not int or handle <= 0:
        raise _error("native module handle is not a positive integer")
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    if handle >= 1 << bits:
        raise _error("native module handle is outside the process address width")
    return ctypes.wintypes.HMODULE(handle)


def _mapped_module_path(handle: object) -> Path:
    """Resolve a Windows module handle using a bounded Unicode API call."""

    if os.name != "nt":
        if handle is None:
            return _resolve_regular_file(sys.executable, "process image")
        raise _error("native module-handle lookup requires Windows")
    module_handle = _coerce_module_handle(handle)
    try:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        function = api.GetModuleFileNameW
        function.argtypes = [ctypes.wintypes.HMODULE, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD]
        function.restype = ctypes.wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(_MAX_WINDOWS_PATH)
        length = int(function(module_handle, buffer, _MAX_WINDOWS_PATH))
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise _error("Windows module-handle lookup is unavailable") from exc
    if length <= 0 or length >= _MAX_WINDOWS_PATH or len(buffer.value) != length:
        raise _error("Windows module-handle lookup was empty or truncated")
    return _resolve_regular_file(buffer.value, "mapped module")


def _pyvenv_identity() -> dict[str, object] | None:
    candidate = Path(sys.prefix) / "pyvenv.cfg"
    try:
        info = candidate.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error("pyvenv.cfg cannot be inspected") from exc
    if not stat.S_ISREG(info.st_mode):
        raise _error("pyvenv.cfg is not a regular file")
    return _file_identity(candidate, "pyvenv.cfg")


def _current_process_binding() -> dict[str, object]:
    """Capture only process/interpreter identities; never import Gmsh."""

    launcher = _file_identity(sys.executable, "Python launcher")
    image = _file_identity(_mapped_module_path(None), "Python process image")
    handle = getattr(sys, "dllhandle", None)
    if type(handle) is not int or handle <= 0:
        raise _error("CPython runtime DLL handle is unavailable")
    library = _file_identity(_mapped_module_path(handle), "CPython runtime DLL")
    return {
        "python": launcher,
        "python_image": image,
        "python_library": library,
        "pyvenv_cfg": _pyvenv_identity(),
    }


def _distribution_paths() -> tuple[Path, Path]:
    try:
        distribution = importlib.metadata.distribution(_GMSH_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise _error("installed Gmsh distribution is unavailable") from exc
    except (OSError, ValueError) as exc:
        raise _error("installed Gmsh distribution metadata is unavailable") from exc
    if distribution.version != _GMSH_VERSION:
        raise _error(f"installed Gmsh distribution is not {_GMSH_VERSION!r}")
    entries = distribution.files
    if entries is None:
        raise _error("installed Gmsh distribution has no file manifest")

    modules: list[Path] = []
    libraries: list[Path] = []
    for entry in entries:
        entry_name = getattr(entry, "name", None)
        if not isinstance(entry_name, str):
            try:
                entry_name = Path(str(entry)).name
            except (TypeError, ValueError):
                continue
        candidate_name = Path(entry_name).name
        if candidate_name.casefold() not in {
            _MODULE_FILE.casefold(),
            _LIBRARY_FILE.casefold(),
        }:
            continue
        try:
            located = Path(distribution.locate_file(entry)).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise _error("installed Gmsh distribution contains an unresolved file") from exc
        name = candidate_name.casefold()
        if name == _MODULE_FILE.casefold():
            modules.append(located)
        elif name == _LIBRARY_FILE.casefold():
            libraries.append(located)
    if len(modules) != 1 or len(libraries) != 1:
        raise _error("installed Gmsh distribution module/DLL layout is missing or ambiguous")
    module, library = modules[0], libraries[0]
    if (
        module.parent.name.casefold() != "site-packages"
        or library.parent.name.casefold() != "lib"
        or library.parent != module.parent.parent
    ):
        raise _error("installed Gmsh distribution module/DLL layout is unsupported")
    try:
        prefix = Path(sys.prefix).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _error("Python environment prefix is unavailable") from exc
    if library.parent.parent != prefix:
        raise _error("Gmsh distribution is outside the active Python environment")
    return module, library


def capture_runtime_binding() -> dict[str, object]:
    """Capture the exact installed Gmsh/CPython binding without importing Gmsh."""

    process = _current_process_binding()
    module, library = _distribution_paths()
    return {
        "schema_version": _SCHEMA_VERSION,
        **process,
        "module": _file_identity(module, "Gmsh Python module"),
        "library": _file_identity(library, "Gmsh native library"),
    }


def _same_path(left: object, right: object) -> bool:
    return isinstance(left, str) and isinstance(right, str) and _path_key(left) == _path_key(right)


def _cache_path(module_path: Path, spec: Any) -> Path:
    try:
        expected = Path(importlib.util.cache_from_source(str(module_path))).resolve(strict=False)
    except (NotImplementedError, OSError, TypeError, ValueError) as exc:
        raise _error("Gmsh module cache path cannot be resolved") from exc
    cached = getattr(spec, "cached", None)
    if cached is not None and not _same_path(str(expected), str(cached)):
        raise _error("Gmsh module cache resolution differs from the verified source")
    return expected


def _compile_source_bytes(source: bytes, module_path: Path) -> CodeType:
    try:
        return compile(source, str(module_path), "exec", dont_inherit=True, optimize=-1)
    except (SyntaxError, TypeError, ValueError) as exc:
        raise _error("Gmsh Python module source cannot be compiled") from exc


@dataclass(slots=True)
class _SourceSnapshot:
    path: Path
    stream: BinaryIO
    content: bytes
    identity: dict[str, object]
    mtime_ns: int
    code: CodeType
    code_digest: str


def _source_snapshot(
    module_record: dict[str, object], admission: ExitStack
) -> _SourceSnapshot:
    module_path = _resolve_regular_file(module_record.get("path"), "Gmsh Python module")
    stream = _pinned_file(admission, module_path, "Gmsh Python module")
    content, size, digest, mtime_ns = _read_stream_bounded(
        stream, _MAX_SOURCE_BYTES, "Gmsh Python module"
    )
    identity = {"path": str(module_path), "size": size, "sha256": digest}
    _compare_file_records(module_record, identity, "module")
    code = _compile_source_bytes(content, module_path)
    return _SourceSnapshot(
        module_path,
        stream,
        content,
        identity,
        mtime_ns,
        code,
        _code_digest(code),
    )


def _verify_source_snapshot_current(snapshot: _SourceSnapshot) -> None:
    try:
        snapshot.stream.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        raise _error("Gmsh Python module admission guard cannot be rewound") from exc
    content, size, digest, _mtime_ns = _read_stream_bounded(
        snapshot.stream, _MAX_SOURCE_BYTES, "Gmsh Python module"
    )
    current = {"path": str(snapshot.path), "size": size, "sha256": digest}
    if content != snapshot.content or current != snapshot.identity:
        raise _error("Gmsh Python module changed during admission")


_CacheIdentity = tuple[str, int, str]


def _validate_cache(
    module_path: Path,
    spec: Any,
    snapshot: _SourceSnapshot,
    admission: ExitStack,
) -> tuple[Path, _CacheIdentity | None]:
    cache = _cache_path(module_path, spec)
    try:
        cache.stat()
    except FileNotFoundError:
        return cache, None
    except OSError as exc:
        raise _error("Gmsh module cache cannot be inspected") from exc
    stream = _pinned_file(admission, cache, "Gmsh module cache")
    content, size, digest, _mtime_ns = _read_stream_bounded(
        stream, _MAX_CACHE_BYTES, "Gmsh module cache"
    )
    cache_identity = (str(cache), size, digest)
    if len(content) < 16 or content[:4] != importlib.util.MAGIC_NUMBER:
        raise _error("Gmsh module cache has an invalid header")
    flags = int.from_bytes(content[4:8], "little")
    if flags & ~0x03:
        raise _error("Gmsh module cache has unsupported flags")
    if flags & 1:
        try:
            source_hash = importlib.util.source_hash(snapshot.content)
        except (AttributeError, TypeError, ValueError) as exc:
            raise _error("Gmsh module source hash cannot be calculated") from exc
        if content[8:16] != source_hash:
            raise _error("Gmsh module cache is stale")
    else:
        source_mtime_seconds = (snapshot.mtime_ns // 1_000_000_000) & 0xFFFFFFFF
        if content[8:12] != source_mtime_seconds.to_bytes(4, "little"):
            raise _error("Gmsh module cache is stale")
        if content[12:16] != (len(snapshot.content) & 0xFFFFFFFF).to_bytes(4, "little"):
            raise _error("Gmsh module cache is stale")
    try:
        cached_code = marshal.loads(content[16:])
    except (EOFError, ValueError, TypeError) as exc:
        raise _error("Gmsh module cache code is malformed") from exc
    if not isinstance(cached_code, CodeType):
        raise _error("Gmsh module cache does not contain module code")
    try:
        cached_marshaled = marshal.dumps(cached_code)
        source_marshaled = marshal.dumps(snapshot.code)
    except (ValueError, TypeError) as exc:
        raise _error("Gmsh module cache code cannot be compared") from exc
    if cached_marshaled != content[16:] or source_marshaled != cached_marshaled:
        raise _error("Gmsh module cache code differs from the verified source")
    return cache, cache_identity


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Execute the authenticated source snapshot, never a loader reread."""

    def __init__(self, fullname: str, path: str, code: CodeType) -> None:
        super().__init__(fullname, path)
        self._verified_code = code
        self._verified_code_digest = _code_digest(code)

    def get_code(self, fullname: str) -> CodeType:
        del fullname
        return self._verified_code


def _find_gmsh_spec(module_path: Path) -> Any:
    try:
        # Do not consult sys.modules: util.find_spec returns the cached
        # module's existing spec once the first verified session is loaded.
        spec = importlib.machinery.PathFinder.find_spec("gmsh", list(sys.path))
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise _error("Gmsh import resolution failed") from exc
    if spec is None or getattr(spec, "origin", None) is None:
        raise _error("Gmsh import resolution has no source origin")
    if not _same_path(str(module_path), str(spec.origin)):
        raise _error("Gmsh import resolved to a different module")
    if getattr(spec, "submodule_search_locations", None) is not None:
        raise _error("Gmsh import resolved to a package")
    return spec


def _module_handle(module: ModuleType) -> tuple[Any, int]:
    try:
        library = vars(module)["lib"]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise _error("Gmsh native handle is unavailable") from exc
    handle = _library_handle(library)
    _coerce_module_handle(handle)
    return library, handle


def _require_isolated_interpreter() -> None:
    if getattr(getattr(sys, "flags", None), "isolated", 0) != 1:
        raise _error("verified Gmsh loading requires an isolated interpreter")


def _verify_process_binding(
    expected: dict[str, object], observed: dict[str, object]
) -> None:
    for field in _PROCESS_FIELDS:
        expected_value = expected[field]
        observed_value = observed[field]
        if expected_value is None or observed_value is None:
            if expected_value is not observed_value:
                raise _error(f"runtime identity mismatch for {field}")
        else:
            _compare_file_records(
                expected_value,  # type: ignore[arg-type]
                observed_value,  # type: ignore[arg-type]
                field,
            )


def _code_digest(code: CodeType) -> str:
    try:
        content = marshal.dumps(code)
    except (TypeError, ValueError) as exc:
        raise _error("Gmsh module code cannot be attested") from exc
    return hashlib.sha256(content).hexdigest()


_MISSING = object()
_CFUNC_PTR_TYPE = getattr(ctypes, "_CFuncPtr", None)
_CFUNC_PTR_METACLASS = (
    type(_CFUNC_PTR_TYPE) if isinstance(_CFUNC_PTR_TYPE, type) else None
)


def _raw_type_descriptor(value_type: type, name: str) -> tuple[type, object]:
    try:
        method_resolution_order = value_type.__mro__
    except (AttributeError, TypeError):
        return value_type, _MISSING
    for owner in method_resolution_order:
        try:
            descriptor = vars(owner).get(name, _MISSING)
        except TypeError:
            return owner, _MISSING
        if descriptor is not _MISSING:
            return owner, descriptor
    return value_type, _MISSING


if isinstance(_CFUNC_PTR_TYPE, type):
    _CFUNC_PTR_CALL_OWNER, _CFUNC_PTR_CALL = _raw_type_descriptor(
        _CFUNC_PTR_TYPE, "__call__"
    )
    _CFUNC_PTR_GETATTRIBUTE_OWNER, _CFUNC_PTR_GETATTRIBUTE = _raw_type_descriptor(
        _CFUNC_PTR_TYPE, "__getattribute__"
    )
    _CFUNC_PTR_SETATTR_OWNER, _CFUNC_PTR_SETATTR = _raw_type_descriptor(
        _CFUNC_PTR_TYPE, "__setattr__"
    )
    _CFUNC_PTR_METADATA = {
        attribute: _raw_type_descriptor(_CFUNC_PTR_TYPE, attribute)
        for attribute in _NATIVE_CALL_ATTRIBUTES
    }
else:
    _CFUNC_PTR_CALL_OWNER = None
    _CFUNC_PTR_CALL = _MISSING
    _CFUNC_PTR_GETATTRIBUTE_OWNER = None
    _CFUNC_PTR_GETATTRIBUTE = _MISSING
    _CFUNC_PTR_SETATTR_OWNER = None
    _CFUNC_PTR_SETATTR = _MISSING
    _CFUNC_PTR_METADATA = {}


def _value_signature(value: object, *, depth: int = 0, budget: int = _MAX_VALUE_ITEMS) -> object:
    """Describe only bounded built-in values; never walk arbitrary user objects."""

    value_type = type(value)
    if value is None or value_type in (bool, int, complex):
        return (value_type.__name__, value)
    if value_type is float:
        return (value_type.__name__, repr(value))
    if value_type is str:
        if len(value) <= 256:
            return (value_type.__name__, value)
        return (value_type.__name__, len(value), hashlib.sha256(value.encode()).hexdigest())
    if value_type is bytes:
        if len(value) <= 256:
            return (value_type.__name__, value)
        return (value_type.__name__, len(value), hashlib.sha256(value).hexdigest())
    if depth >= _MAX_VALUE_DEPTH or budget <= 0:
        return ("object", value_type, id(value))
    if value_type is tuple:
        items = tuple(
            _value_signature(item, depth=depth + 1, budget=budget - index - 1)
            for index, item in enumerate(value[:budget])
        )
        return ("tuple", len(value), items)
    if value_type is list:
        items = tuple(
            _value_signature(item, depth=depth + 1, budget=budget - index - 1)
            for index, item in enumerate(value[:budget])
        )
        return ("list", len(value), items)
    if value_type is dict:
        items_list: list[object] = []
        for index, (key, item) in enumerate(value.items()):
            if index >= budget:
                break
            items_list.append(
                (
                    _value_signature(key, depth=depth + 1, budget=budget - index - 1),
                    _value_signature(item, depth=depth + 1, budget=budget - index - 1),
                )
            )
        items = tuple(items_list)
        return ("dict", len(value), items)
    if value_type is frozenset:
        items_list: list[object] = []
        for index, item in enumerate(value):
            if index >= budget:
                break
            items_list.append(
                _value_signature(item, depth=depth + 1, budget=budget - index - 1)
            )
        items = tuple(sorted(items_list, key=repr))
        return ("frozenset", len(value), items)
    return ("object", value_type, id(value))


def _stable_global_signature(value: object) -> object | None:
    if type(value) in (type(None), bool, int, float, complex, str, bytes):
        return _value_signature(value, budget=1)
    return None


def _closure_signature(closure: tuple[Any, ...] | None) -> tuple[object, ...] | None:
    if closure is None:
        return None
    result: list[object] = []
    for cell in closure:
        try:
            content = cell.cell_contents
        except ValueError:
            result.append(("empty",))
        else:
            result.append(_value_signature(content))
    return tuple(result)


@dataclass(slots=True)
class _FunctionState:
    function: FunctionType
    code: CodeType
    globals_dict: dict[str, object]
    defaults: object
    defaults_signature: object
    kwdefaults: object
    kwdefaults_signature: object
    annotations: object
    annotations_signature: object
    function_dict: dict[str, object]
    function_dict_signature: object
    closure: tuple[Any, ...] | None
    closure_signature: tuple[object, ...] | None
    name: str
    qualname: str
    module_name: str | None
    global_states: dict[str, _GlobalState] | None = None


def _function_state(
    function: FunctionType, *, capture_globals: bool = False
) -> _FunctionState:
    function_dict = function.__dict__
    state = _FunctionState(
        function=function,
        code=function.__code__,
        globals_dict=function.__globals__,
        defaults=function.__defaults__,
        defaults_signature=_value_signature(function.__defaults__),
        kwdefaults=function.__kwdefaults__,
        kwdefaults_signature=_value_signature(function.__kwdefaults__),
        annotations=function.__annotations__,
        annotations_signature=_value_signature(function.__annotations__),
        function_dict=function_dict,
        function_dict_signature=_value_signature(function_dict),
        closure=function.__closure__,
        closure_signature=_closure_signature(function.__closure__),
        name=function.__name__,
        qualname=function.__qualname__,
        module_name=function.__module__,
    )
    if capture_globals:
        state.global_states = _capture_function_globals(function)
    return state


def _validate_function_state(state: _FunctionState, label: str) -> None:
    function = state.function
    if function.__code__ is not state.code:
        raise _error(f"live Gmsh executable function {label} code changed")
    if function.__globals__ is not state.globals_dict:
        raise _error(f"live Gmsh executable function {label} globals changed")
    if function.__defaults__ is not state.defaults or _value_signature(
        function.__defaults__
    ) != state.defaults_signature:
        raise _error(f"live Gmsh executable function {label} defaults changed")
    if function.__kwdefaults__ is not state.kwdefaults or _value_signature(
        function.__kwdefaults__
    ) != state.kwdefaults_signature:
        raise _error(f"live Gmsh executable function {label} keyword defaults changed")
    if function.__annotations__ is not state.annotations or _value_signature(
        function.__annotations__
    ) != state.annotations_signature:
        raise _error(f"live Gmsh executable function {label} annotations changed")
    if function.__dict__ is not state.function_dict or _value_signature(
        function.__dict__
    ) != state.function_dict_signature:
        raise _error(f"live Gmsh executable function {label} attributes changed")
    if function.__closure__ is not state.closure or _closure_signature(
        function.__closure__
    ) != state.closure_signature:
        raise _error(f"live Gmsh executable function {label} closure changed")
    if (
        function.__name__ != state.name
        or function.__qualname__ != state.qualname
        or function.__module__ != state.module_name
    ):
        raise _error(f"live Gmsh executable function {label} metadata changed")
    _validate_function_globals(state, label)


def _code_global_names(code: CodeType) -> frozenset[str]:
    names: set[str] = set()
    pending = [code]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for instruction in dis.get_instructions(current):
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"} and isinstance(
                instruction.argval, str
            ):
                names.add(instruction.argval)
        pending.extend(constant for constant in current.co_consts if isinstance(constant, CodeType))
    return frozenset(names)


def _native_symbol_names(source: bytes) -> frozenset[str]:
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise _error("Gmsh source native symbols cannot be inspected") from exc
    return frozenset(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "lib"
        and node.attr.startswith(_NATIVE_SYMBOL_PREFIX)
        and len(node.attr) > len(_NATIVE_SYMBOL_PREFIX)
    )


def _descriptor_functions(member: object) -> tuple[FunctionType, ...]:
    if isinstance(member, (staticmethod, classmethod)):
        function = member.__func__
        return (function,) if isinstance(function, FunctionType) else ()
    if isinstance(member, property):
        return tuple(
            function
            for function in (member.fget, member.fset, member.fdel)
            if isinstance(function, FunctionType)
        )
    return (member,) if isinstance(member, FunctionType) else ()


@dataclass(slots=True)
class _DispatchDescriptorState:
    owner: type
    descriptor: object
    functions: tuple[_FunctionState, ...]


def _dispatch_descriptor_state(value_type: type, name: str) -> _DispatchDescriptorState:
    owner, descriptor = _raw_type_descriptor(value_type, name)
    return _DispatchDescriptorState(
        owner,
        descriptor,
        tuple(
            _function_state(function, capture_globals=True)
            for function in _descriptor_functions(descriptor)
        ),
    )


@dataclass(slots=True)
class _FactoryState:
    value: type
    metaclass: type
    metaclass_call: _DispatchDescriptorState
    metaclass_getattribute: _DispatchDescriptorState
    new: _DispatchDescriptorState
    init: _DispatchDescriptorState
    instance_descriptors: dict[str, _DispatchDescriptorState]


def _factory_state(value: object) -> _FactoryState | None:
    if value is _MISSING:
        return None
    if not isinstance(value, type):
        raise _error("Gmsh native library factory is not a type")
    metaclass = type(value)
    instance_names = (
        "__call__",
        "__getattribute__",
        "__setattr__",
        *(_NATIVE_CALL_ATTRIBUTES + ("_name",)),
    )
    instance_descriptors = {
        name: _dispatch_descriptor_state(value, name) for name in instance_names
    }
    if instance_descriptors["_name"].descriptor is not _MISSING:
        raise _error("Gmsh native library factory has an unsupported name descriptor")
    return _FactoryState(
        value,
        metaclass,
        _dispatch_descriptor_state(metaclass, "__call__"),
        _dispatch_descriptor_state(metaclass, "__getattribute__"),
        _dispatch_descriptor_state(value, "__new__"),
        _dispatch_descriptor_state(value, "__init__"),
        instance_descriptors,
    )


@dataclass(slots=True)
class _AttributeDispatchState:
    value_type: type
    descriptors: dict[str, _DispatchDescriptorState]


def _attribute_dispatch_state(
    value: object, names: tuple[str, ...]
) -> _AttributeDispatchState:
    value_type = type(value)
    if type(value_type) is not type:
        raise _error("Gmsh attribute dispatch uses an unsupported metaclass")
    return _AttributeDispatchState(
        value_type,
        {name: _dispatch_descriptor_state(value_type, name) for name in names},
    )


@dataclass(slots=True)
class _MemberState:
    descriptor: object
    functions: tuple[_FunctionState, ...]


@dataclass(slots=True)
class _ClassState:
    class_object: type
    members: dict[str, _MemberState]


def _capture_class_state(
    path: tuple[str, ...],
    class_object: type,
    module_name: str,
    classes: dict[tuple[str, ...], _ClassState],
    functions: list[_FunctionState],
) -> None:
    if path in classes:
        return
    if len(classes) >= _MAX_LIVE_CLASSES:
        raise _error("Gmsh live class ownership exceeds the finite verification limit")
    try:
        namespace = vars(class_object)
    except TypeError as exc:
        raise _error("Gmsh live class namespace cannot be inspected") from exc
    if namespace.get("__module__") != module_name:
        return
    class_state = _ClassState(class_object, {})
    classes[path] = class_state
    for name, descriptor in namespace.items():
        member_functions = tuple(_function_state(function) for function in _descriptor_functions(descriptor))
        nested = isinstance(descriptor, type) and getattr(descriptor, "__module__", None) == module_name
        if member_functions or nested or callable(descriptor):
            if len(class_state.members) >= _MAX_LIVE_MEMBERS:
                raise _error("Gmsh live class members exceed the finite verification limit")
            class_state.members[name] = _MemberState(descriptor, member_functions)
            functions.extend(member_functions)
        if nested:
            _capture_class_state(path + (name,), descriptor, module_name, classes, functions)


@dataclass(slots=True)
class _GlobalState:
    value: object
    stable_signature: object | None
    builtin_value: object
    builtin_signature: object | None


def _builtin_binding_from_globals(globals_dict: dict[str, object], name: str) -> object:
    builtins_value = globals_dict.get("__builtins__", _MISSING)
    if isinstance(builtins_value, dict):
        return builtins_value.get(name, _MISSING)
    if isinstance(builtins_value, ModuleType):
        return vars(builtins_value).get(name, _MISSING)
    return _MISSING


def _builtin_binding(module: ModuleType, name: str) -> object:
    return _builtin_binding_from_globals(vars(module), name)


def _capture_function_globals(function: FunctionType) -> dict[str, _GlobalState]:
    globals_dict = function.__globals__
    names = set(_code_global_names(function.__code__))
    names.add("__builtins__")
    result: dict[str, _GlobalState] = {}
    for name in names:
        if name in globals_dict:
            value = globals_dict[name]
            result[name] = _GlobalState(
                value,
                _stable_global_signature(value),
                _MISSING,
                None,
            )
        else:
            builtin = _builtin_binding_from_globals(globals_dict, name)
            result[name] = _GlobalState(
                _MISSING,
                None,
                builtin,
                _stable_global_signature(builtin),
            )
        if len(result) > _MAX_LIVE_GLOBALS:
            raise _error("Gmsh dispatch globals exceed the finite verification limit")
    return result


def _validate_function_globals(state: _FunctionState, label: str) -> None:
    if state.global_states is None:
        return
    globals_dict = state.globals_dict
    for name, global_state in state.global_states.items():
        current = globals_dict.get(name, _MISSING)
        if global_state.value is _MISSING:
            if current is not _MISSING:
                raise _error(f"live Gmsh dispatch global {label}.{name} was added")
            builtin = _builtin_binding_from_globals(globals_dict, name)
            if builtin is not global_state.builtin_value:
                raise _error(f"live Gmsh dispatch builtin {label}.{name} was replaced")
            if global_state.builtin_signature is not None and _stable_global_signature(
                builtin
            ) != global_state.builtin_signature:
                raise _error(f"live Gmsh dispatch builtin {label}.{name} changed")
        else:
            if current is not global_state.value:
                raise _error(f"live Gmsh dispatch global {label}.{name} was replaced")
            if global_state.stable_signature is not None and _stable_global_signature(
                current
            ) != global_state.stable_signature:
                raise _error(f"live Gmsh dispatch global {label}.{name} changed")


def _is_ctypes_callable(value: object) -> bool:
    return isinstance(_CFUNC_PTR_TYPE, type) and isinstance(value, _CFUNC_PTR_TYPE)


def _ctypes_dispatch_descriptor(
    value: object, name: str
) -> tuple[type, object, type, object, type, object]:
    if not _is_ctypes_callable(value):
        raise _error(f"cached native callable {name} is not a ctypes function")
    value_type = type(value)
    if type(value_type) is not _CFUNC_PTR_METACLASS:
        raise _error(f"cached native callable {name} uses an unsupported metaclass")
    _name_owner, name_descriptor = _raw_type_descriptor(value_type, "_name")
    if name_descriptor is not _MISSING:
        raise _error(f"cached native callable {name} has an unsupported name descriptor")
    call_owner, call_descriptor = _raw_type_descriptor(value_type, "__call__")
    attribute_owner, attribute_descriptor = _raw_type_descriptor(
        value_type, "__getattribute__"
    )
    setattr_owner, setattr_descriptor = _raw_type_descriptor(value_type, "__setattr__")
    if (
        call_owner is not _CFUNC_PTR_CALL_OWNER
        or call_descriptor is not _CFUNC_PTR_CALL
        or attribute_owner is not _CFUNC_PTR_GETATTRIBUTE_OWNER
        or attribute_descriptor is not _CFUNC_PTR_GETATTRIBUTE
        or setattr_owner is not _CFUNC_PTR_SETATTR_OWNER
        or setattr_descriptor is not _CFUNC_PTR_SETATTR
    ):
        raise _error(f"cached native callable {name} dispatch was overridden")
    for attribute in _NATIVE_CALL_ATTRIBUTES:
        expected_owner, expected_descriptor = _CFUNC_PTR_METADATA.get(
            attribute, (None, _MISSING)
        )
        metadata_owner, metadata_descriptor = _raw_type_descriptor(
            value_type, attribute
        )
        if (
            metadata_owner is not expected_owner
            or metadata_descriptor is not expected_descriptor
        ):
            raise _error(f"cached native callable {name} metadata access was overridden")
    return (
        call_owner,
        call_descriptor,
        attribute_owner,
        attribute_descriptor,
        setattr_owner,
        setattr_descriptor,
    )


def _ctypes_pointer(value: object, name: str) -> int:
    try:
        pointer = ctypes.cast(value, ctypes.c_void_p).value
    except (AttributeError, OSError, TypeError, ValueError, OverflowError) as exc:
        raise _error(f"cached native callable {name} has no address") from exc
    if type(pointer) is not int or pointer <= 0:
        raise _error(f"cached native callable {name} has an invalid address")
    return pointer


def _ctypes_metadata_value(value: object, name: str, attribute: str) -> object:
    value_type = type(value)
    expected_owner, expected_descriptor = _CFUNC_PTR_METADATA.get(
        attribute, (None, _MISSING)
    )
    owner, descriptor = _raw_type_descriptor(value_type, attribute)
    if owner is not expected_owner or descriptor is not expected_descriptor:
        raise _error(f"cached native callable {name} metadata access was overridden")
    try:
        return descriptor.__get__(value, value_type)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error(f"cached native callable {name} has malformed call metadata") from exc


def _ctypes_call_signature(value: object, name: str) -> tuple[object, ...]:
    try:
        _ctypes_dispatch_descriptor(value, name)
        argtypes = _ctypes_metadata_value(value, name, "argtypes")
        if isinstance(argtypes, (list, tuple)) and not argtypes:
            argtypes = None
        return (
            _value_signature(argtypes),
            _value_signature(_ctypes_metadata_value(value, name, "restype")),
            _value_signature(_ctypes_metadata_value(value, name, "errcheck")),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error(f"cached native callable {name} has malformed call metadata") from exc


def _static_ctypes_value(node: ast.AST) -> object:
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (type(None), bool, int, float, str, bytes)
    ):
        return node.value
    if isinstance(node, ast.Name):
        candidate = getattr(ctypes, node.id, _MISSING)
        return candidate if isinstance(candidate, type) else _MISSING
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id != "ctypes":
            return _MISSING
        candidate = getattr(ctypes, node.attr, _MISSING)
        return candidate if isinstance(candidate, type) else _MISSING
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_static_ctypes_value(item) for item in node.elts]
        if any(value is _MISSING for value in values):
            return _MISSING
        return values if isinstance(node, ast.List) else tuple(values)
    if isinstance(node, ast.Call) and not node.keywords and len(node.args) == 1:
        function = node.func
        function_name: str | None = None
        if isinstance(function, ast.Name):
            function_name = function.id
        elif isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            if function.value.id == "ctypes":
                function_name = function.attr
        if function_name == "POINTER":
            pointed_type = _static_ctypes_value(node.args[0])
            if isinstance(pointed_type, type):
                try:
                    return ctypes.POINTER(pointed_type)
                except (AttributeError, TypeError, ValueError):
                    return _MISSING
    return _MISSING


def _native_signature_policy(
    source: bytes, native_symbols: frozenset[str]
) -> dict[str, frozenset[tuple[object, ...]]]:
    default = (
        _value_signature(None),
        _value_signature(ctypes.c_int),
        _value_signature(None),
    )
    permitted = {name: {default} for name in native_symbols}
    if not native_symbols:
        return {}
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise _error("Gmsh source native metadata cannot be inspected") from exc

    assignments: list[tuple[int, int, str, str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
            if value is None:
                continue
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Attribute) or target.attr not in _NATIVE_CALL_ATTRIBUTES:
                continue
            owner = target.value
            if not isinstance(owner, ast.Attribute) or owner.attr not in native_symbols:
                continue
            if not isinstance(owner.value, ast.Name) or owner.value.id != "lib":
                continue
            assignments.append(
                (target.lineno, target.col_offset, owner.attr, target.attr, value)
            )
    if len(assignments) > _MAX_LIVE_MEMBERS:
        raise _error("Gmsh native metadata exceeds the finite verification limit")
    assignments.sort(key=lambda item: (item[0], item[1]))
    current = {name: list(default) for name in native_symbols}
    for _line, _column, name, attribute, expression in assignments:
        value = _static_ctypes_value(expression)
        if value is _MISSING:
            raise _error(f"Gmsh native metadata for {name} is not independently supported")
        index = _NATIVE_CALL_ATTRIBUTES.index(attribute)
        signature = _value_signature(value)
        candidate = list(default)
        candidate[index] = signature
        permitted[name].add(tuple(candidate))
        current[name][index] = signature
        permitted[name].add(tuple(current[name]))
    return {name: frozenset(signatures) for name, signatures in permitted.items()}


def _validate_ctypes_call_signature(
    value: object,
    name: str,
    signature_policy: dict[str, frozenset[tuple[object, ...]]],
) -> None:
    signature = _ctypes_call_signature(value, name)
    permitted = signature_policy.get(name)
    if permitted is None or signature not in permitted:
        raise _error(f"cached native callable {name} has unauthorized call metadata")


def _library_namespace(
    library: object, expected: tuple[type, object] | None = None
) -> dict[str, object]:
    owner, descriptor = _raw_type_descriptor(type(library), "__dict__")
    if expected is not None and (
        owner is not expected[0] or descriptor is not expected[1]
    ):
        raise _error("Gmsh native library namespace descriptor changed")
    if type(descriptor) is not GetSetDescriptorType:
        raise _error("Gmsh native library namespace uses an unsupported descriptor")
    try:
        namespace = cast(Any, descriptor).__get__(library, type(library))
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error("Gmsh native library namespace cannot be inspected") from exc
    if not isinstance(namespace, dict):
        raise _error("Gmsh native library namespace is malformed")
    return namespace


def _library_handle_descriptor(
    library: object, expected: tuple[type, object] | None = None
) -> tuple[type, object]:
    owner, descriptor = _raw_type_descriptor(type(library), "_handle")
    if expected is not None and (owner is not expected[0] or descriptor is not expected[1]):
        raise _error("Gmsh native library handle descriptor changed")
    if type(descriptor) is not int:
        raise _error("Gmsh native library handle uses an unsupported descriptor")
    return owner, descriptor


def _library_handle(
    library: object, expected: tuple[type, object] | None = None
) -> int:
    _owner, descriptor = _library_handle_descriptor(library, expected)
    handle = _library_namespace(library).get("_handle", _MISSING)
    if handle is _MISSING:
        handle = descriptor
    if type(handle) is not int or handle <= 0:
        raise _error("Gmsh native library handle is not a positive integer")
    return handle


def _native_export_address(library: object, name: str) -> int | None:
    if os.name != "nt":
        return None
    handle = _library_handle(library)
    try:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        function = api.GetProcAddress
        function.argtypes = [ctypes.wintypes.HMODULE, ctypes.wintypes.LPCSTR]
        function.restype = ctypes.c_void_p
        address = function(ctypes.wintypes.HMODULE(handle), name.encode("ascii"))
        address = int(address or 0)
    except (AttributeError, OSError, TypeError, ValueError, UnicodeError) as exc:
        raise _error(f"cached native callable {name} export cannot be inspected") from exc
    if address <= 0:
        raise _error(f"cached native callable {name} is not exported by the loaded DLL")
    return address


def _validate_ctypes_callable(
    library: object, name: str, value: object, native_symbols: frozenset[str]
) -> int:
    if name not in native_symbols:
        raise _error(f"cached native callable {name} is not in the authenticated source")
    if not _is_ctypes_callable(value):
        raise _error(f"cached native callable {name} is not a ctypes function")
    pointer = _ctypes_pointer(value, name)
    exported = _native_export_address(library, name)
    if exported is not None and pointer != exported:
        raise _error(f"cached native callable {name} is bound to a different export")
    return pointer


@dataclass(slots=True)
class _NativeCallableState:
    value: object
    value_type: type
    function: _FunctionState | None
    pointer: int | None
    dispatch_owner: type | None
    dispatch_descriptor: object | None
    getattribute_owner: type | None
    getattribute_descriptor: object | None
    setattr_owner: type | None = None
    setattr_descriptor: object | None = None


def _native_callable_state(
    library: object,
    name: str,
    value: object,
    native_symbols: frozenset[str],
    signature_policy: dict[str, frozenset[tuple[object, ...]]],
    *,
    allow_new: bool,
) -> _NativeCallableState:
    if allow_new:
        (
            dispatch_owner,
            dispatch_descriptor,
            getattribute_owner,
            getattribute_descriptor,
            setattr_owner,
            setattr_descriptor,
        ) = _ctypes_dispatch_descriptor(value, name)
        pointer = _validate_ctypes_callable(library, name, value, native_symbols)
        _validate_ctypes_call_signature(value, name, signature_policy)
        return _NativeCallableState(
            value,
            type(value),
            None,
            pointer,
            dispatch_owner,
            dispatch_descriptor,
            getattribute_owner,
            getattribute_descriptor,
            setattr_owner,
            setattr_descriptor,
        )
    if isinstance(value, FunctionType):
        return _NativeCallableState(
            value, type(value), _function_state(value), None, None, None, None, None
        )
    if _is_ctypes_callable(value):
        (
            dispatch_owner,
            dispatch_descriptor,
            getattribute_owner,
            getattribute_descriptor,
            setattr_owner,
            setattr_descriptor,
        ) = _ctypes_dispatch_descriptor(value, name)
        pointer = _validate_ctypes_callable(library, name, value, native_symbols)
        _validate_ctypes_call_signature(value, name, signature_policy)
        return _NativeCallableState(
            value,
            type(value),
            None,
            pointer,
            dispatch_owner,
            dispatch_descriptor,
            getattribute_owner,
            getattribute_descriptor,
            setattr_owner,
            setattr_descriptor,
        )
    if not callable(value):
        raise _error(f"cached native callable {name} is not callable")
    return _NativeCallableState(value, type(value), None, None, None, None, None, None)


def _capture_native_callables(
    library: object,
    native_symbols: frozenset[str],
    signature_policy: dict[str, frozenset[tuple[object, ...]]],
) -> dict[str, _NativeCallableState]:
    result: dict[str, _NativeCallableState] = {}
    for name, value in _library_namespace(library).items():
        if isinstance(name, str) and name in native_symbols and callable(value):
            result[name] = _native_callable_state(
                library,
                name,
                value,
                native_symbols,
                signature_policy,
                allow_new=False,
            )
    return result


@dataclass(slots=True)
class _LiveState:
    module_functions: dict[str, _FunctionState]
    classes: dict[tuple[str, ...], _ClassState]
    globals: dict[str, _GlobalState]
    module_dispatch: _AttributeDispatchState
    library_dispatch: _AttributeDispatchState
    library_namespace_owner: type
    library_namespace_descriptor: object
    library_namespace: dict[str, object]
    library_handle_owner: type
    library_handle_descriptor: object
    library_funcptr_owner: type
    library_funcptr_descriptor: object
    library_funcptr: _FactoryState | None
    native_symbols: frozenset[str]
    native_signature_policy: dict[str, frozenset[tuple[object, ...]]]
    native_callables: dict[str, _NativeCallableState]
    native_handle: int


def _capture_live_state(
    module: ModuleType,
    library: object,
    source: bytes,
    native_handle: int,
) -> _LiveState:
    native_symbols = _native_symbol_names(source)
    library_namespace_owner, library_namespace_descriptor = _raw_type_descriptor(
        type(library), "__dict__"
    )
    namespace = _library_namespace(
        library,
        expected=(library_namespace_owner, library_namespace_descriptor),
    )
    library_handle_owner, library_handle_descriptor = _library_handle_descriptor(library)
    library_funcptr_owner, library_funcptr_descriptor = _raw_type_descriptor(
        type(library), "_FuncPtr"
    )
    library_funcptr = _factory_state(namespace.get("_FuncPtr", _MISSING))
    module_dispatch = _attribute_dispatch_state(
        module, ("__getattribute__", "__getattr__")
    )
    library_dispatch = _attribute_dispatch_state(
        library,
        (
            "__getattribute__",
            "__getattr__",
            "__getitem__",
            "__setattr__",
            *sorted(native_symbols),
        ),
    )
    module_namespace = vars(module)
    module_name = module.__name__
    module_functions: dict[str, _FunctionState] = {}
    classes: dict[tuple[str, ...], _ClassState] = {}
    functions: list[_FunctionState] = []
    for name, value in tuple(module_namespace.items()):
        if isinstance(value, FunctionType) and value.__module__ == module_name:
            module_functions[name] = _function_state(value)
            functions.append(module_functions[name])
        elif isinstance(value, type):
            _capture_class_state((name,), value, module_name, classes, functions)
    if len(functions) > _MAX_LIVE_MEMBERS:
        raise _error("Gmsh live executable functions exceed the finite verification limit")
    global_states: dict[str, _GlobalState] = {}
    global_names = {"__builtins__", "__name__", "__package__"}
    for function in functions:
        global_names.update(_code_global_names(function.code))
    for name in global_names:
        if name in module_namespace:
            value = module_namespace[name]
            global_states[name] = _GlobalState(
                value,
                _stable_global_signature(value),
                _MISSING,
                None,
            )
        else:
            builtin = _builtin_binding(module, name)
            global_states[name] = _GlobalState(
                _MISSING,
                None,
                builtin,
                _stable_global_signature(builtin),
            )
        if len(global_states) > _MAX_LIVE_GLOBALS:
            raise _error("Gmsh live globals exceed the finite verification limit")
    native_signature_policy = _native_signature_policy(source, native_symbols)
    return _LiveState(
        module_functions=module_functions,
        classes=classes,
        globals=global_states,
        module_dispatch=module_dispatch,
        library_dispatch=library_dispatch,
        library_namespace_owner=library_namespace_owner,
        library_namespace_descriptor=library_namespace_descriptor,
        library_namespace=namespace,
        library_handle_owner=library_handle_owner,
        library_handle_descriptor=library_handle_descriptor,
        library_funcptr_owner=library_funcptr_owner,
        library_funcptr_descriptor=library_funcptr_descriptor,
        library_funcptr=library_funcptr,
        native_symbols=native_symbols,
        native_signature_policy=native_signature_policy,
        native_callables=_capture_native_callables(
            library, native_symbols, native_signature_policy
        ),
        native_handle=native_handle,
    )


def _lookup_class_path(module: ModuleType, path: tuple[str, ...]) -> object:
    current: object = vars(module).get(path[0], _MISSING)
    for name in path[1:]:
        if not isinstance(current, type):
            return _MISSING
        current = vars(current).get(name, _MISSING)
    return current


def _validate_native_callable_state(
    library: object,
    name: str,
    state: _NativeCallableState,
    current: object,
    native_symbols: frozenset[str],
    signature_policy: dict[str, frozenset[tuple[object, ...]]],
) -> None:
    if current is not state.value or type(current) is not state.value_type:
        raise _error(f"live Gmsh native callable {name} was replaced")
    if state.function is not None:
        if not isinstance(current, FunctionType):
            raise _error(f"live Gmsh native callable {name} changed type")
        _validate_function_state(state.function, f"native {name}")
    elif state.pointer is not None:
        (
            dispatch_owner,
            dispatch_descriptor,
            getattribute_owner,
            getattribute_descriptor,
            setattr_owner,
            setattr_descriptor,
        ) = _ctypes_dispatch_descriptor(current, name)
        if (
            dispatch_owner is not state.dispatch_owner
            or dispatch_descriptor is not state.dispatch_descriptor
            or getattribute_owner is not state.getattribute_owner
            or getattribute_descriptor is not state.getattribute_descriptor
            or setattr_owner is not state.setattr_owner
            or setattr_descriptor is not state.setattr_descriptor
        ):
            raise _error(f"live Gmsh native callable {name} dispatch changed")
        pointer = _validate_ctypes_callable(library, name, current, native_symbols)
        if pointer != state.pointer:
            raise _error(f"live Gmsh native callable {name} address changed")
        _validate_ctypes_call_signature(current, name, signature_policy)


def _validate_attribute_dispatch(
    state: _AttributeDispatchState, value: object, label: str
) -> None:
    current = _attribute_dispatch_state(value, tuple(state.descriptors))
    if current.value_type is not state.value_type:
        raise _error(f"live Gmsh {label} attribute dispatch changed")
    for name, descriptor_state in state.descriptors.items():
        current_descriptor = current.descriptors[name]
        if (
            current_descriptor.owner is not descriptor_state.owner
            or current_descriptor.descriptor is not descriptor_state.descriptor
        ):
            raise _error(f"live Gmsh {label} attribute {name} dispatch changed")
        for function_state in descriptor_state.functions:
            _validate_function_state(function_state, f"{label} {name} dispatch")


def _validate_dispatch_descriptor(
    state: _DispatchDescriptorState, value_type: type, name: str, label: str
) -> None:
    current = _dispatch_descriptor_state(value_type, name)
    if current.owner is not state.owner or current.descriptor is not state.descriptor:
        raise _error(f"live Gmsh {label} descriptor changed")
    for function_state in state.functions:
        _validate_function_state(function_state, f"{label} {name}")


def _validate_factory_state(state: _FactoryState, value: object) -> None:
    if value is not state.value:
        raise _error("live Gmsh native library factory changed")
    if type(value) is not state.metaclass:
        raise _error("live Gmsh native library factory metaclass changed")
    _validate_dispatch_descriptor(
        state.metaclass_call,
        state.metaclass,
        "__call__",
        "native library factory call",
    )
    _validate_dispatch_descriptor(
        state.metaclass_getattribute,
        state.metaclass,
        "__getattribute__",
        "native library factory attribute",
    )
    _validate_dispatch_descriptor(
        state.new, state.value, "__new__", "native library factory constructor"
    )
    _validate_dispatch_descriptor(
        state.init, state.value, "__init__", "native library factory constructor"
    )
    for name, descriptor_state in state.instance_descriptors.items():
        _validate_dispatch_descriptor(
            descriptor_state,
            state.value,
            name,
            "native library factory instance",
        )


def _validate_live_state(state: _LiveState, module: ModuleType, library: object) -> None:
    _validate_attribute_dispatch(state.module_dispatch, module, "module")
    _validate_attribute_dispatch(state.library_dispatch, library, "native library")
    current_library_namespace = _library_namespace(
        library,
        expected=(state.library_namespace_owner, state.library_namespace_descriptor),
    )
    if current_library_namespace is not state.library_namespace:
        raise _error("live Gmsh native library namespace changed")
    current_handle_owner, current_handle_descriptor = _library_handle_descriptor(library)
    if (
        current_handle_owner is not state.library_handle_owner
        or current_handle_descriptor is not state.library_handle_descriptor
    ):
        raise _error("live Gmsh native library handle descriptor changed")
    current_funcptr_owner, current_funcptr_descriptor = _raw_type_descriptor(
        type(library), "_FuncPtr"
    )
    if (
        current_funcptr_owner is not state.library_funcptr_owner
        or current_funcptr_descriptor is not state.library_funcptr_descriptor
    ):
        raise _error("live Gmsh native library factory descriptor changed")
    current_funcptr = current_library_namespace.get("_FuncPtr", _MISSING)
    if state.library_funcptr is None:
        if current_funcptr is not _MISSING:
            raise _error("live Gmsh native library factory was added")
    else:
        _validate_factory_state(state.library_funcptr, current_funcptr)
    namespace = vars(module)
    for name, function_state in state.module_functions.items():
        if namespace.get(name, _MISSING) is not function_state.function:
            raise _error(f"live Gmsh executable function {name} was replaced")
        _validate_function_state(function_state, name)
    for path, class_state in state.classes.items():
        current_class = _lookup_class_path(module, path)
        if current_class is not class_state.class_object:
            raise _error(f"live Gmsh class {'.'.join(path)} was replaced")
        try:
            current_members = vars(class_state.class_object)
        except TypeError as exc:
            raise _error(f"live Gmsh class {'.'.join(path)} cannot be inspected") from exc
        for name, member_state in class_state.members.items():
            current = current_members.get(name, _MISSING)
            if current is not member_state.descriptor:
                raise _error(f"live Gmsh class API member {'.'.join(path + (name,))} changed")
            for function_state in member_state.functions:
                _validate_function_state(function_state, ".".join(path + (name,)))
    for name, global_state in state.globals.items():
        current = namespace.get(name, _MISSING)
        if global_state.value is _MISSING:
            if current is not _MISSING:
                raise _error(f"live Gmsh global {name} was added")
            builtin = _builtin_binding(module, name)
            if builtin is not global_state.builtin_value:
                raise _error(f"live Gmsh builtin {name} was replaced")
            if global_state.builtin_signature is not None and _stable_global_signature(
                builtin
            ) != global_state.builtin_signature:
                raise _error(f"live Gmsh builtin {name} changed")
        else:
            if current is not global_state.value:
                raise _error(f"live Gmsh global {name} was replaced")
            if global_state.stable_signature is not None and _stable_global_signature(
                current
            ) != global_state.stable_signature:
                raise _error(f"live Gmsh global {name} changed")

    if _library_handle(
        library,
        expected=(state.library_handle_owner, state.library_handle_descriptor),
    ) != state.native_handle:
        raise _error("live Gmsh native library handle changed")
    for name, callable_state in state.native_callables.items():
        current = current_library_namespace.get(name, _MISSING)
        if current is _MISSING:
            raise _error(f"live Gmsh native callable {name} was removed")
        _validate_native_callable_state(
            library,
            name,
            callable_state,
            current,
            state.native_symbols,
            state.native_signature_policy,
        )
    additions: dict[str, _NativeCallableState] = {}
    for name, current in current_library_namespace.items():
        if not isinstance(name, str) or name not in state.native_symbols or name in state.native_callables:
            continue
        if not callable(current):
            raise _error(f"live Gmsh native member {name} is not callable")
        additions[name] = _native_callable_state(
            library,
            name,
            current,
            state.native_symbols,
            state.native_signature_policy,
            allow_new=True,
        )
    state.native_callables.update(additions)


def _validate_loaded_module(
    module: ModuleType,
    module_path: Path,
    spec: Any,
    expected_code_digest: str,
    expected_code: CodeType | None = None,
) -> None:
    if not _same_path(str(module_path), str(getattr(module, "__file__", ""))):
        raise _error("executing Gmsh module has a different source path")
    if getattr(module, "__version__", None) != _GMSH_VERSION:
        raise _error("executing Gmsh module has an unexpected version")
    loaded_spec = getattr(module, "__spec__", None)
    if (
        loaded_spec is None
        or getattr(loaded_spec, "name", None) != "gmsh"
        or getattr(loaded_spec, "submodule_search_locations", None) is not None
        or not _same_path(
            str(getattr(spec, "origin", "")), str(getattr(loaded_spec, "origin", ""))
        )
    ):
        raise _error("executing Gmsh module import spec changed")
    loader = getattr(module, "__loader__", None)
    if (
        not isinstance(loader, _SourceOnlyLoader)
        or getattr(loaded_spec, "loader", None) is not loader
        or loader._verified_code_digest != expected_code_digest
        or (expected_code is not None and loader._verified_code is not expected_code)
    ):
        raise _error("executing Gmsh module code identity changed")
    if sys.modules.get("gmsh") is not module:
        raise _error("executing Gmsh module import state changed")


@dataclass(slots=True)
class _VerifiedSession:
    module: ModuleType
    library: Any
    binding: dict[str, object]
    observed: dict[str, object]
    live_state: _LiveState
    code_digest: str
    code_object: CodeType
    cache_path: Path
    cache_identity: _CacheIdentity | None


_CACHE_LOCK = threading.RLock()
_VERIFIED_SESSIONS: list[_VerifiedSession] = []


def _cached_session(
    module: ModuleType | None, binding: dict[str, object]
) -> _VerifiedSession | None:
    if module is None:
        return None
    for session in _VERIFIED_SESSIONS:
        if session.module is module and session.binding == binding:
            _validate_live_state(session.live_state, module, session.library)
            verify_runtime_identity(binding, session.observed)
            process = _current_process_binding()
            _verify_process_binding(binding, process)
            module_record = binding["module"]
            library_record = binding["library"]
            if not isinstance(module_record, dict) or not isinstance(library_record, dict):
                raise ValueError("runtime identity module/library records are malformed")
            module_path = _resolve_regular_file(module_record["path"], "Gmsh Python module")
            library_path = _resolve_regular_file(library_record["path"], "Gmsh native library")
            with ExitStack() as admission:
                _pinned_file(admission, library_path, "Gmsh native library")
                spec = _find_gmsh_spec(module_path)
                snapshot = _source_snapshot(module_record, admission)
                cache_path, cache_identity = _validate_cache(
                    module_path, spec, snapshot, admission
                )
                if snapshot.code_digest != session.code_digest:
                    raise _error("verified Gmsh module code identity changed")
                _validate_loaded_module(
                    module, module_path, spec, session.code_digest, session.code_object
                )
                try:
                    library_object = vars(module)["lib"]
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    raise _error("verified Gmsh native library object is unavailable") from exc
                if library_object is not session.library:
                    raise _error("verified Gmsh native library object changed")
                handle = session.live_state.native_handle
                mapped_path = _mapped_module_path(handle)
                if not _same_path(str(mapped_path), str(library_path)):
                    raise _error("verified Gmsh native library mapping changed")
                actual_library_record = _file_identity(mapped_path, "mapped Gmsh native library")
                _compare_file_records(library_record, actual_library_record, "library")
                if cache_identity != session.cache_identity or not _same_path(
                    str(cache_path), str(session.cache_path)
                ):
                    raise _error("Gmsh module cache changed after verification")
                _verify_source_snapshot_current(snapshot)
            return session
    return None


def _import_verified_source(module_path: Path, spec: Any, code: CodeType) -> ModuleType:
    loader = _SourceOnlyLoader("gmsh", str(module_path), code)
    verified_spec = importlib.util.spec_from_file_location(
        "gmsh", str(module_path), loader=loader
    )
    if verified_spec is None or not _same_path(str(module_path), str(verified_spec.origin)):
        raise _error("Gmsh source import spec is not the resolved module")
    if not _same_path(str(getattr(spec, "origin", "")), str(verified_spec.origin)):
        raise _error("Gmsh source import spec changed during verification")
    module: ModuleType | None = None
    try:
        module = importlib.util.module_from_spec(verified_spec)
        sys.modules["gmsh"] = module
        loader.exec_module(module)
    except BaseException as exc:
        if module is not None and sys.modules.get("gmsh") is module:
            sys.modules.pop("gmsh", None)
        if isinstance(exc, _RuntimeBindingError):
            raise
        raise _error("Gmsh source module execution failed") from exc
    if module is None:
        sys.modules.pop("gmsh", None)
        raise _error("Gmsh source module is not a module")
    return module


def load_verified_gmsh(binding: dict[str, object]) -> tuple[Any, dict[str, object]]:
    """Load Gmsh only after verifying the exact isolated runtime binding."""

    _require_isolated_interpreter()
    expected = _normalise_binding(binding, "expected runtime")
    with _CACHE_LOCK:
        preloaded = sys.modules.get("gmsh")
        if isinstance(preloaded, ModuleType):
            cached = _cached_session(preloaded, expected)
            if cached is not None:
                return cached.module, dict(cached.observed)
            raise _error("unverified preloaded Gmsh module is refused")
        if preloaded is not None:
            raise _error("unverified preloaded Gmsh module is refused")

        process = _current_process_binding()
        _verify_process_binding(expected, process)
        module_record = expected["module"]
        library_record = expected["library"]
        if not isinstance(module_record, dict) or not isinstance(library_record, dict):
            raise ValueError("runtime identity module/library records are malformed")
        module_path = _resolve_regular_file(module_record["path"], "Gmsh Python module")
        library_path = _resolve_regular_file(library_record["path"], "Gmsh native library")
        with ExitStack() as admission:
            _pinned_file(admission, library_path, "Gmsh native library")
            actual_module = _find_gmsh_spec(module_path)
            snapshot = _source_snapshot(module_record, admission)
            cache_path, cache_identity = _validate_cache(
                module_path, actual_module, snapshot, admission
            )
            module = _import_verified_source(module_path, actual_module, snapshot.code)
            code_digest = snapshot.code_digest
            try:
                _validate_loaded_module(
                    module, module_path, actual_module, code_digest, snapshot.code
                )
                _verify_source_snapshot_current(snapshot)
                library_object, handle = _module_handle(module)
                live_state = _capture_live_state(
                    module,
                    library_object,
                    snapshot.content,
                    handle,
                )
                mapped_path = _mapped_module_path(handle)
                if not _same_path(str(mapped_path), str(library_path)):
                    raise _error("verified Gmsh native library mapping differs from binding")
                actual_library_record = _file_identity(mapped_path, "mapped Gmsh native library")
                _compare_file_records(library_record, actual_library_record, "library")
                actual_module_record = snapshot.identity
                observed: dict[str, object] = {
                    "schema_version": _SCHEMA_VERSION,
                    **process,
                    "module": actual_module_record,
                    "library": actual_library_record,
                }
                verify_runtime_identity(expected, observed)
            except BaseException:
                if sys.modules.get("gmsh") is module:
                    sys.modules.pop("gmsh", None)
                raise
        session = _VerifiedSession(
            module,
            library_object,
            expected,
            observed,
            live_state,
            code_digest,
            snapshot.code,
            cache_path,
            cache_identity,
        )
        _VERIFIED_SESSIONS.append(session)
        return module, dict(observed)


__all__ = ["capture_runtime_binding", "load_verified_gmsh", "verify_runtime_identity"]
