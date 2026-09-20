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
import builtins
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
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import (
    BuiltinFunctionType,
    ClassMethodDescriptorType,
    CodeType,
    FunctionType,
    GetSetDescriptorType,
    MappingProxyType,
    MethodDescriptorType,
    MethodWrapperType,
    ModuleType,
    WrapperDescriptorType,
)
from typing import Any, BinaryIO, Protocol, TypeGuard, cast

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
_MAX_FUNCTION_DEPENDENCY_NODES = 512
_NATIVE_SYMBOL_PREFIX = "gmsh"
_NATIVE_CALL_ATTRIBUTES = ("argtypes", "restype", "errcheck")
_SAFE_ATTRIBUTE_NAMESPACE_TYPES = (dict, list, tuple, set, frozenset, str, bytes)
_REPARSE_POINT_ATTRIBUTE = getattr(ctypes.wintypes, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_DEPENDENCY_TRUSTED_OBJECTS: dict[int, object] = {}
_DEPENDENCY_OBJECT_DESCRIPTORS: dict[int, tuple[type, dict[str, object]]] = {}
_DEPENDENCY_EXPECTED_BINDINGS: dict[tuple[str, str], object] = {}


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
    if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE:
        raise _error(f"{label} file is a reparse point")
    return resolved


def _read_stream_bounded(stream: BinaryIO, limit: int, label: str) -> tuple[bytes, int, str, int]:
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


def _read_file_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
    except (OSError, TypeError, ValueError) as exc:
        raise _error(f"{label} file cannot be read") from exc
    if len(content) > limit:
        raise _error(f"{label} file exceeds the finite verification limit")
    return content


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
        raise TypeError(f"runtime identity {label} path is malformed")
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
    sizeof = _CTYPES_BINDINGS.get("sizeof", _MISSING)
    c_void_p = _CTYPES_BINDINGS.get("c_void_p", _MISSING)
    if sizeof is _MISSING or c_void_p is _MISSING:
        raise _error("ctypes module-handle bindings are unavailable")
    bits = cast(Callable[..., Any], sizeof)(c_void_p) * 8
    if handle >= 1 << bits:
        raise _error("native module handle is outside the process address width")
    return ctypes.wintypes.HMODULE(handle)


def _mapped_module_path(handle: object) -> Path:
    """Resolve a Windows module handle using a bounded Unicode API call."""

    _validate_ctypes_dependencies()
    if os.name != "nt":
        if handle is None:
            return _resolve_regular_file(sys.executable, "process image")
        raise _error("native module-handle lookup requires Windows")
    module_handle = _coerce_module_handle(handle)
    try:
        windll = _CTYPES_BINDINGS.get("WinDLL", _MISSING)
        create_unicode_buffer = _CTYPES_BINDINGS.get("create_unicode_buffer", _MISSING)
        if windll is _MISSING or create_unicode_buffer is _MISSING:
            raise _error("ctypes Windows bindings are unavailable")
        api = cast(Callable[..., Any], windll)("kernel32", use_last_error=True)
        function = api.GetModuleFileNameW
        function.argtypes = [ctypes.wintypes.HMODULE, ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD]
        function.restype = ctypes.wintypes.DWORD
        buffer = cast(Callable[..., Any], create_unicode_buffer)(_MAX_WINDOWS_PATH)
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
            located = Path(str(distribution.locate_file(entry))).resolve(strict=True)
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


def _canonical_stdlib_dir() -> Path:
    stdlib_value = vars(sys).get("_stdlib_dir", _MISSING)
    if not isinstance(stdlib_value, str) or not stdlib_value:
        raise _error("Python stdlib runtime anchor is unavailable")
    try:
        path = Path(stdlib_value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _error("Python stdlib runtime anchor is unavailable") from exc
    if not path.is_dir():
        raise _error("Python stdlib runtime anchor is not a directory")
    return path


def _canonical_stdlib_source_path(module_name: str, label: str) -> Path:
    if not isinstance(module_name, str) or not module_name or "\x00" in module_name:
        raise _error(f"{label} module name is invalid")
    parts = module_name.split(".")
    if any(not part or part in {".", ".."} for part in parts):
        raise _error(f"{label} module name is invalid")
    root = _canonical_stdlib_dir()
    candidates = (
        root.joinpath(*parts).with_suffix(".py"),
        root.joinpath(*parts, "__init__.py"),
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    raise _error(f"{label} canonical source is unavailable")


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


def _source_snapshot(module_record: dict[str, object], admission: ExitStack) -> _SourceSnapshot:
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


def _verify_process_binding(expected: dict[str, object], observed: dict[str, object]) -> None:
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


def _code_constant_fingerprint(value: object) -> object:
    if isinstance(value, CodeType):
        return ("code", _code_digest(value))
    if type(value) is tuple:
        return ("tuple", tuple(_code_constant_fingerprint(item) for item in value))
    if type(value) is frozenset:
        return (
            "frozenset",
            tuple(_code_constant_fingerprint(item) for item in value),
        )
    if type(value) in (type(None), bool, int, float, complex, str, bytes):
        return (type(value).__name__, value)
    return ("opaque", id(value))


def _code_digest(code: CodeType) -> str:
    payload = (
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        code.co_nlocals,
        code.co_stacksize,
        code.co_flags,
        code.co_code,
        tuple(_code_constant_fingerprint(value) for value in code.co_consts),
        code.co_names,
        code.co_varnames,
        code.co_filename,
        code.co_name,
        code.co_qualname,
        code.co_firstlineno,
        code.co_linetable,
        code.co_exceptiontable,
        code.co_freevars,
        code.co_cellvars,
    )
    try:
        content = marshal.dumps(cast(Any, payload))
    except (TypeError, ValueError) as exc:
        raise _error("Gmsh module code cannot be attested") from exc
    return hashlib.sha256(content).hexdigest()


_MISSING = object()


def _is_class_object(value: object) -> TypeGuard[type]:
    try:
        mro = type.__getattribute__(value, "__mro__")
    except (AttributeError, TypeError):
        return False
    return type(mro) is tuple


def _raw_class_namespace(value: object) -> MappingProxyType[str, Any] | None:
    if not _is_class_object(value):
        return None
    try:
        namespace = type.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        return None
    return namespace if type(namespace) is MappingProxyType else None


def _raw_module_class(module: object, qualname: str) -> object:
    if type(module) is not ModuleType or not qualname:
        return _MISSING
    current: object = vars(module).get(qualname.partition(".")[0], _MISSING)
    for name in qualname.split(".")[1:]:
        namespace = _raw_class_namespace(current)
        if namespace is None:
            return _MISSING
        current = namespace.get(name, _MISSING)
    return current


def _authenticated_dependency_metaclass(value: object, label: str) -> bool:
    if value is type:
        return True
    if not _is_class_object(value):
        return False
    try:
        module_name = type.__getattribute__(value, "__module__")
        qualname = type.__getattribute__(value, "__qualname__")
    except (AttributeError, TypeError):
        return False
    if not isinstance(module_name, str) or not isinstance(qualname, str):
        return False
    stdlib_names = vars(sys).get("stdlib_module_names", frozenset())
    if (
        not isinstance(stdlib_names, (set, frozenset))
        or module_name.partition(".")[0] not in stdlib_names
    ):
        return False
    module = sys.modules.get(module_name, _MISSING)
    if type(module) is not ModuleType or _raw_module_class(module, qualname) is not value:
        return False
    source = vars(module).get("__file__", _MISSING)
    try:
        canonical = _canonical_stdlib_source_path(module_name, label)
    except _RuntimeBindingError:
        return False
    return isinstance(source, str) and _same_path(source, str(canonical))


_CFUNC_PTR_TYPE = getattr(ctypes, "_CFuncPtr", None)
_CFUNC_PTR_METACLASS = type(_CFUNC_PTR_TYPE) if _is_class_object(_CFUNC_PTR_TYPE) else None

_CFUNC_PTR_CALL_OWNER: type | None
_CFUNC_PTR_CALL: object
_CFUNC_PTR_GETATTRIBUTE_OWNER: type | None
_CFUNC_PTR_GETATTRIBUTE: object
_CFUNC_PTR_SETATTR_OWNER: type | None
_CFUNC_PTR_SETATTR: object
_CFUNC_PTR_METADATA: dict[str, tuple[type, object]]


class _DescriptorWithGet(Protocol):
    def __get__(self, instance: object, owner: type | None = None) -> object: ...


def _raw_type_descriptor(value_type: type, name: str) -> tuple[type, object]:
    try:
        method_resolution_order = type.__getattribute__(value_type, "__mro__")
    except (AttributeError, TypeError):
        return value_type, _MISSING
    for owner in method_resolution_order:
        namespace = _raw_class_namespace(owner)
        if namespace is None:
            return owner, _MISSING
        descriptor = namespace.get(name, _MISSING)
        if descriptor is not _MISSING:
            return owner, descriptor
    return value_type, _MISSING


if _is_class_object(_CFUNC_PTR_TYPE):
    _CFUNC_PTR_CALL_OWNER, _CFUNC_PTR_CALL = _raw_type_descriptor(_CFUNC_PTR_TYPE, "__call__")
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


def _establish_dependency_object_baseline() -> None:
    if _DEPENDENCY_TRUSTED_OBJECTS:
        return
    value = os.environ
    value_type = type(value)
    if type(type(value_type)) is not type:
        raise _error("standard dependency object uses an unsupported type")
    descriptors = {
        name: _raw_type_descriptor(value_type, name)[1]
        for name in ("__getattribute__", "__getattr__", "__setattr__", "get")
    }
    _DEPENDENCY_TRUSTED_OBJECTS[id(value)] = value
    _DEPENDENCY_OBJECT_DESCRIPTORS[id(value)] = (value_type, descriptors)
    _DEPENDENCY_EXPECTED_BINDINGS[("os", "environ")] = value


def _validate_dependency_object(value: object, label: str) -> None:
    expected = _DEPENDENCY_TRUSTED_OBJECTS.get(id(value))
    state = _DEPENDENCY_OBJECT_DESCRIPTORS.get(id(value))
    if expected is None or expected is not value or state is None:
        raise _error(f"Gmsh executable dependency {label} has an unsupported namespace")
    value_type, descriptors = state
    if type(value) is not value_type:
        raise _error(f"Gmsh executable dependency {label} type changed")
    for name, descriptor in descriptors.items():
        current = _raw_type_descriptor(value_type, name)[1]
        if current is not descriptor:
            raise _error(f"Gmsh executable dependency {label} dispatch changed")


def _value_signature(value: object, *, depth: int = 0, budget: int = _MAX_VALUE_ITEMS) -> object:
    """Describe only bounded built-in values; never walk arbitrary user objects."""

    value_type = type(value)
    if value is None:
        return (value_type.__name__, value)
    if type(value) is bool:
        return (value_type.__name__, value)
    if type(value) is int:
        return (value_type.__name__, value)
    if type(value) is complex:
        return (value_type.__name__, value)
    if type(value) is float:
        return (value_type.__name__, repr(value))
    if type(value) is str:
        if len(value) <= 256:
            return (value_type.__name__, value)
        return (value_type.__name__, len(value), hashlib.sha256(value.encode()).hexdigest())
    if type(value) is bytes:
        if len(value) <= 256:
            return (value_type.__name__, value)
        return (value_type.__name__, len(value), hashlib.sha256(value).hexdigest())
    if depth >= _MAX_VALUE_DEPTH or budget <= 0:
        return ("object", value_type, id(value))
    if type(value) is tuple:
        items = tuple(
            _value_signature(item, depth=depth + 1, budget=budget - index - 1)
            for index, item in enumerate(value[:budget])
        )
        return ("tuple", len(value), items)
    if type(value) is list:
        items = tuple(
            _value_signature(item, depth=depth + 1, budget=budget - index - 1)
            for index, item in enumerate(value[:budget])
        )
        return ("list", len(value), items)
    if type(value) is dict:
        dict_items: list[object] = []
        for index, (key, item) in enumerate(value.items()):
            if index >= budget:
                break
            dict_items.append(
                (
                    _value_signature(key, depth=depth + 1, budget=budget - index - 1),
                    _value_signature(item, depth=depth + 1, budget=budget - index - 1),
                )
            )
        items = tuple(dict_items)
        return ("dict", len(value), items)
    if type(value) is frozenset:
        set_items: list[object] = []
        for index, item in enumerate(value):
            if index >= budget:
                break
            set_items.append(_value_signature(item, depth=depth + 1, budget=budget - index - 1))
        items = tuple(sorted(set_items, key=repr))
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
    builtins_value: object
    builtins_signature: object
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
    code_digest: str | None = None
    source_path: Path | None = None
    source_identity: dict[str, object] | None = None


@dataclass(slots=True)
class _DependencyContext:
    functions: dict[int, _FunctionState]


def _function_state(
    function: FunctionType,
    *,
    capture_globals: bool = False,
    code: CodeType | None = None,
    code_digest: str | None = None,
    source_path: Path | None = None,
    source_identity: dict[str, object] | None = None,
    dependency_depth: int = 0,
    dependency_seen: frozenset[int] = frozenset(),
    dependency_context: _DependencyContext | None = None,
) -> _FunctionState:
    selected_code = function.__code__ if code is None else code
    function_dict = function.__dict__
    state = _FunctionState(
        function=function,
        code=selected_code,
        globals_dict=function.__globals__,
        builtins_value=cast(Any, function).__builtins__,
        builtins_signature=_value_signature(cast(Any, function).__builtins__),
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
        code_digest=code_digest,
        source_path=source_path,
        source_identity=source_identity,
    )
    if capture_globals:
        context = dependency_context or _DependencyContext({id(function): state})
        context.functions.setdefault(id(function), state)
        state.global_states = _capture_function_globals(
            function,
            code=selected_code,
            dependency_depth=dependency_depth,
            dependency_seen=dependency_seen,
            dependency_context=context,
        )
    return state


def _validate_function_state(
    state: _FunctionState,
    label: str,
    validation_seen: set[int] | None = None,
) -> None:
    seen = validation_seen if validation_seen is not None else set()
    if id(state) in seen:
        return
    seen.add(id(state))
    function = state.function
    if state.code_digest is None:
        if function.__code__ is not state.code:
            raise _error(f"live Gmsh executable function {label} code changed")
    elif _code_digest(function.__code__) != state.code_digest:
        raise _error(f"live Gmsh executable function {label} code changed")
    if function.__globals__ is not state.globals_dict:
        raise _error(f"live Gmsh executable function {label} globals changed")
    if (
        cast(Any, function).__builtins__ is not state.builtins_value
        or _value_signature(cast(Any, function).__builtins__) != state.builtins_signature
    ):
        raise _error(f"live Gmsh executable function {label} builtins changed")
    if (
        function.__defaults__ is not state.defaults
        or _value_signature(function.__defaults__) != state.defaults_signature
    ):
        raise _error(f"live Gmsh executable function {label} defaults changed")
    if (
        function.__kwdefaults__ is not state.kwdefaults
        or _value_signature(function.__kwdefaults__) != state.kwdefaults_signature
    ):
        raise _error(f"live Gmsh executable function {label} keyword defaults changed")
    if (
        function.__annotations__ is not state.annotations
        or _value_signature(function.__annotations__) != state.annotations_signature
    ):
        raise _error(f"live Gmsh executable function {label} annotations changed")
    if (
        function.__dict__ is not state.function_dict
        or _value_signature(function.__dict__) != state.function_dict_signature
    ):
        raise _error(f"live Gmsh executable function {label} attributes changed")
    if (
        function.__closure__ is not state.closure
        or _closure_signature(function.__closure__) != state.closure_signature
    ):
        raise _error(f"live Gmsh executable function {label} closure changed")
    if (
        function.__name__ != state.name
        or function.__qualname__ != state.qualname
        or function.__module__ != state.module_name
    ):
        raise _error(f"live Gmsh executable function {label} metadata changed")
    if state.source_path is not None or state.source_identity is not None:
        if state.source_path is None or state.source_identity is None:
            raise _error(f"live Gmsh executable function {label} source state malformed")
        current_source_path = _dependency_source_path(function.__code__, label, state.module_name)
        if not _same_path(str(current_source_path), str(state.source_path)):
            raise _error(f"live Gmsh executable function {label} source changed")
        current_identity = _file_identity(state.source_path, f"Gmsh executable dependency {label}")
        _compare_file_records(
            state.source_identity, current_identity, f"Gmsh executable dependency {label}"
        )
    _validate_function_globals(state, label, seen)


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


def _code_attribute_chains(code: CodeType) -> frozenset[tuple[str, tuple[str, ...]]]:
    chains: set[tuple[str, tuple[str, ...]]] = set()
    pending = [code]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        instructions = tuple(dis.get_instructions(current))
        for index, instruction in enumerate(instructions):
            if instruction.opname not in {"LOAD_GLOBAL", "LOAD_NAME"} or not isinstance(
                instruction.argval, str
            ):
                continue
            attributes: list[str] = []
            for following in instructions[index + 1 :]:
                if following.opname not in {"LOAD_ATTR", "LOAD_METHOD"} or not isinstance(
                    following.argval, str
                ):
                    break
                attributes.append(following.argval)
            if attributes:
                chains.add((instruction.argval, tuple(attributes)))
        pending.extend(constant for constant in current.co_consts if isinstance(constant, CodeType))
    return frozenset(chains)


@dataclass(slots=True)
class _SourceImportBinding:
    module_name: str
    load_name: str
    path: tuple[str, ...]


@dataclass(slots=True)
class _DependencyTypeMemberState:
    descriptor: object
    signature: object
    functions: tuple[_FunctionState, ...]


@dataclass(slots=True)
class _DependencyTypeState:
    value: type
    module_name: str
    qualname: str
    source_path: Path
    source_identity: dict[str, object]
    bases: tuple[type, ...]
    base_signatures: tuple[object, ...]
    members: dict[str, _DependencyTypeMemberState]


@dataclass(slots=True)
class _ExecutableAttributeState:
    root_name: str
    path: tuple[str, ...]
    values: tuple[object, ...]
    function_state: _FunctionState | None = None
    type_state: _DependencyTypeState | None = None
    absent_imports: tuple[str, ...] = ()


@dataclass(slots=True)
class _ModuleDependencyState:
    module: ModuleType
    path: tuple[str, ...]
    values: tuple[object, ...]
    function_state: _FunctionState | None = None
    type_state: _DependencyTypeState | None = None


def _attribute_chain(node: ast.AST) -> tuple[str, tuple[str, ...]] | None:
    attributes: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    attributes.reverse()
    return current.id, tuple(attributes)


def _source_import_bindings(
    tree: ast.AST, used_names: frozenset[str]
) -> dict[str, _SourceImportBinding]:
    result: dict[str, _SourceImportBinding] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.asname or alias.name.partition(".")[0]
                if root_name not in used_names:
                    continue
                module_name = alias.name if alias.asname else alias.name.partition(".")[0]
                result[root_name] = _SourceImportBinding(module_name, alias.name, ())
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not isinstance(node.module, str) or not node.module:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                root_name = alias.asname or alias.name
                if root_name not in used_names:
                    continue
                path = tuple(part for part in alias.name.split(".") if part)
                result[root_name] = _SourceImportBinding(
                    node.module,
                    node.module,
                    path,
                )
    return result


class _SourceImportAbsent(Exception):
    """Only nonexecuting resolution may select an import-error handler."""


def _source_import_absent(name: str) -> bool:
    if name in sys.modules:
        _validate_dependency_module(sys.modules[name], name)
        return False
    if name in sys.builtin_module_names or name.partition(".")[0] in sys.stdlib_module_names:
        _preflight_dependency_import(name, name)
        return False
    search = list(sys.path)
    parts = name.split(".")
    for index in range(len(parts)):
        qualified = ".".join(parts[: index + 1])
        if qualified in sys.modules:
            _validate_dependency_module(sys.modules[qualified], qualified)
        try:
            spec = importlib.machinery.PathFinder.find_spec(qualified, search)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise _error(f"Gmsh dependency {name} resolution failed") from exc
        if spec is None:
            if qualified in sys.modules:
                raise _error(f"Gmsh dependency {qualified} has an injected module")
            return True
        if index < len(parts) - 1:
            locations = spec.submodule_search_locations
            if locations is None:
                raise _error(f"Gmsh dependency {qualified} is not a package")
            search = list(locations)
    return False


def _source_import_flow(
    tree: ast.Module,
) -> tuple[dict[str, _SourceImportBinding], dict[str, object], tuple[str, ...]]:
    """Select only bounded module-level import regions; never execute vendor code."""
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def region_nodes(node: ast.AST) -> list[ast.AST]:
        pending = [node]
        result: list[ast.AST] = []
        while pending:
            current = pending.pop()
            if isinstance(current, definitions):
                continue
            result.append(current)
            if len(result) > _MAX_LIVE_MEMBERS:
                raise _error("Gmsh import flow exceeds the finite verification limit")
            pending.extend(ast.iter_child_nodes(current))
        return result

    nodes = region_nodes(tree)
    import_names: set[str] = set()
    flags: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_names.update(
                alias.asname
                or (alias.name.partition(".")[0] if isinstance(node, ast.Import) else alias.name)
                for alias in node.names
                if alias.name != "*"
            )
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and type(node.value.value) is bool
        ):
            flags.update(target.id for target in node.targets if isinstance(target, ast.Name))
    tracked = import_names | flags
    bindings: dict[str, _SourceImportBinding] = {}
    expected: dict[str, object] = dict.fromkeys(tracked, _MISSING)
    absent_imports: set[str] = set()

    def relevant(node: ast.AST) -> bool:
        return any(
            isinstance(child, (ast.Import, ast.ImportFrom))
            or isinstance(child, ast.Name)
            and isinstance(child.ctx, (ast.Store, ast.Del))
            and child.id in tracked
            for child in region_nodes(node)
        )

    def visit(statements: list[ast.stmt], *, strict: bool = False) -> None:
        for node in statements:
            if isinstance(node, ast.Pass):
                continue
            if not relevant(node):
                if strict:
                    raise _error("Gmsh import flow contains an unsupported statement")
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and (node.level or not node.module):
                    raise _error("Gmsh import flow contains a relative import")
                for alias in node.names:
                    module_name = (
                        alias.name if isinstance(node, ast.Import) else cast(str, node.module)
                    )
                    if _source_import_absent(module_name):
                        absent_imports.add(module_name)
                        raise _SourceImportAbsent(module_name)
                    if alias.name == "*":
                        _safe_import_dependency_module(module_name, module_name)
                        continue
                    root = alias.asname or (
                        alias.name.partition(".")[0] if isinstance(node, ast.Import) else alias.name
                    )
                    binding = _SourceImportBinding(
                        module_name
                        if isinstance(node, ast.ImportFrom) or alias.asname
                        else alias.name.partition(".")[0],
                        module_name,
                        () if isinstance(node, ast.Import) else tuple(alias.name.split(".")),
                    )
                    value, _owner = _load_source_import_binding(binding, root)
                    bindings[root] = binding
                    expected[root] = value
            elif (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and type(node.value.value) is bool
                and all(isinstance(target, ast.Name) for target in node.targets)
            ):
                for target in node.targets:
                    name = cast(ast.Name, target).id
                    bindings.pop(name, None)
                    expected[name] = node.value.value
            elif isinstance(node, ast.If):
                condition = node.test
                value = (
                    expected.get(condition.id, _MISSING)
                    if isinstance(condition, ast.Name)
                    else condition.value
                    if isinstance(condition, ast.Constant)
                    else _MISSING
                )
                if type(value) is not bool:
                    raise _error("Gmsh import flow has an unsupported condition")
                visit(node.body if value else node.orelse, strict=True)
            elif isinstance(node, ast.Try):
                if node.orelse or node.finalbody or len(node.handlers) != 1:
                    raise _error("Gmsh import flow has unsupported exception control")
                handler = node.handlers[0]
                if handler.name or not (
                    handler.type is None
                    or isinstance(handler.type, ast.Name)
                    and handler.type.id in {"ImportError", "ModuleNotFoundError"}
                ):
                    raise _error("Gmsh import flow has an unsupported exception handler")
                try:
                    visit(node.body, strict=True)
                except _SourceImportAbsent:
                    visit(handler.body, strict=True)
            else:
                raise _error("Gmsh import flow contains an unsupported binding statement")

    try:
        visit(tree.body)
    except _SourceImportAbsent as exc:
        raise _error(f"Gmsh required dependency import {exc} is absent") from exc
    if absent_imports and not expected:
        raise _error("Gmsh optional wildcard import flow is unsupported")
    return bindings, expected, tuple(sorted(absent_imports))


def _raw_dependency_attribute(value: object, name: str) -> object:
    if type(value) is ModuleType:
        try:
            return vars(value).get(name, _MISSING)
        except TypeError:
            return _MISSING
    if _is_class_object(value) or _CTYPES_TRUSTED_TYPES.get(id(value)) is value:
        return _raw_type_descriptor(cast(type, value), name)[1]
    if type(value) in _SAFE_ATTRIBUTE_NAMESPACE_TYPES:
        return _raw_type_descriptor(type(value), name)[1]
    if _DEPENDENCY_TRUSTED_OBJECTS.get(id(value)) is value:
        _validate_dependency_object(value, f"object before {name}")
        return _raw_type_descriptor(type(value), name)[1]
    raise _error(f"Gmsh executable dependency has an unsupported namespace before {name}")


def _preflight_dependency_import(name: str, label: str) -> Path | None:
    builtin_names = vars(sys).get("builtin_module_names", ())
    if isinstance(builtin_names, tuple) and name in builtin_names:
        return None
    stdlib_names = vars(sys).get("stdlib_module_names", frozenset())
    root_name = name.partition(".")[0]
    if isinstance(stdlib_names, (set, frozenset)) and root_name in stdlib_names:
        try:
            return _canonical_stdlib_source_path(name, label)
        except _RuntimeBindingError:
            pass
    try:
        spec = importlib.machinery.PathFinder.find_spec(name, list(sys.path))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise _error(f"Gmsh executable dependency {label} cannot be resolved") from exc
    if spec is None:
        raise _error(f"Gmsh executable dependency {label} cannot be resolved")
    origin = getattr(spec, "origin", None)
    if not isinstance(origin, str) or not origin or origin in {"built-in", "frozen"}:
        raise _error(f"Gmsh executable dependency {label} has no authenticated source path")
    resolved = _resolve_regular_file(origin, f"Gmsh executable dependency {label}")
    if isinstance(stdlib_names, (set, frozenset)) and root_name in stdlib_names:
        native_roots = (
            _canonical_stdlib_dir().parent / "DLLs",
            _canonical_stdlib_dir() / "lib-dynload",
        )
        if not any(resolved.parent.resolve() == root.resolve() for root in native_roots):
            raise _error(f"Gmsh executable dependency {label} has an untrusted native path")
    return resolved


def _validate_dependency_module(module: object, label: str) -> ModuleType:
    if type(module) is not ModuleType:
        raise _error(f"Gmsh executable dependency {label} has an unsupported module")
    namespace = vars(module)
    name = namespace.get("__name__", _MISSING)
    if not isinstance(name, str) or sys.modules.get(name) is not module:
        raise _error(f"Gmsh executable dependency {label} has an unexpected module identity")
    expected_source = _preflight_dependency_import(name, label)
    if expected_source is not None:
        source = namespace.get("__file__", _MISSING)
        if not isinstance(source, str) or not _same_path(source, str(expected_source)):
            raise _error(f"Gmsh executable dependency {label} has an unexpected source path")
    return module


def _execute_dependency_spec(name: str, expected_source: Path | None, label: str) -> ModuleType:
    if expected_source is None:
        try:
            spec = importlib.machinery.BuiltinImporter.find_spec(name)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise _error(f"Gmsh executable dependency {label} cannot be resolved") from exc
        if spec is None:
            raise _error(f"Gmsh executable dependency {label} has no authenticated loader")
        if vars(spec).get("loader", _MISSING) is not importlib.machinery.BuiltinImporter:
            raise _error(f"Gmsh executable dependency {label} has no authenticated loader")
    elif expected_source.name == "__init__.py" or expected_source.suffix == ".py":
        locations = [str(expected_source.parent)] if expected_source.name == "__init__.py" else None
        loader = importlib.machinery.SourceFileLoader(name, str(expected_source))
        spec = importlib.util.spec_from_file_location(
            name,
            str(expected_source),
            loader=loader,
            submodule_search_locations=locations,
        )
        if spec is None:
            raise _error(f"Gmsh executable dependency {label} has no authenticated loader")
        if type(vars(spec).get("loader", _MISSING)) is not importlib.machinery.SourceFileLoader:
            raise _error(f"Gmsh executable dependency {label} has no authenticated loader")
    else:
        try:
            spec = importlib.machinery.PathFinder.find_spec(name, list(sys.path))
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise _error(f"Gmsh executable dependency {label} cannot be resolved") from exc
        if spec is None:
            raise _error(f"Gmsh executable dependency {label} has no authenticated loader")
        if not _same_path(str(vars(spec).get("origin", "")), str(expected_source)):
            raise _error(f"Gmsh executable dependency {label} source path changed")
        if type(vars(spec).get("loader", _MISSING)) is not importlib.machinery.ExtensionFileLoader:
            raise _error(f"Gmsh executable dependency {label} has no authenticated loader")
    loader = vars(spec).get("loader", _MISSING)
    if loader is _MISSING or loader is None:
        raise _error(f"Gmsh executable dependency {label} has no authenticated loader")
    module: ModuleType | None = None
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loader.exec_module(module)
    except BaseException as exc:
        if module is not None and sys.modules.get(name) is module:
            sys.modules.pop(name, None)
        if isinstance(exc, _RuntimeBindingError):
            raise
        raise _error(f"Gmsh executable dependency {label} import failed") from exc
    return _validate_dependency_module(module, label)


def _safe_import_dependency_module(name: str, label: str) -> ModuleType:
    parent_name = name.rpartition(".")[0]
    if parent_name:
        _safe_import_dependency_module(parent_name, label)
    cached = sys.modules.get(name, _MISSING)
    if cached is not _MISSING:
        module = _validate_dependency_module(cached, label)
        expected_source = _preflight_dependency_import(name, label)
        current_source = vars(module).get("__file__", _MISSING)
        if expected_source is not None and (
            not isinstance(current_source, str)
            or not _same_path(current_source, str(expected_source))
        ):
            raise _error(f"Gmsh executable dependency {label} has an unexpected source path")
    else:
        expected_source = _preflight_dependency_import(name, label)
        module = _execute_dependency_spec(name, expected_source, label)
    namespace = vars(module)
    if namespace.get("__name__", _MISSING) != name or sys.modules.get(name) is not module:
        raise _error(f"Gmsh executable dependency {label} has an unexpected module identity")
    if expected_source is not None:
        current_source = namespace.get("__file__", _MISSING)
        if not isinstance(current_source, str) or not _same_path(
            current_source, str(expected_source)
        ):
            raise _error(f"Gmsh executable dependency {label} source path changed")
    return module


def _load_source_import_binding(
    binding: _SourceImportBinding, label: str
) -> tuple[object, ModuleType]:
    module = _safe_import_dependency_module(binding.module_name, label)
    if binding.load_name != binding.module_name:
        _safe_import_dependency_module(binding.load_name, label)
    current: object = module
    for index, name in enumerate(binding.path):
        candidate = _raw_dependency_attribute(current, name)
        if candidate is _MISSING:
            if type(current) is not ModuleType:
                raise _error(f"Gmsh executable dependency {label} has an unsupported namespace")
            raise _error(f"Gmsh executable dependency {label} is unavailable")
        current = candidate
    return current, module


def _supported_dependency_builtin(value: object, parent: object, name: str) -> bool:
    descriptor_types = {
        BuiltinFunctionType,
        ClassMethodDescriptorType,
        MethodDescriptorType,
        MethodWrapperType,
        WrapperDescriptorType,
    }
    if type(parent) in _SAFE_ATTRIBUTE_NAMESPACE_TYPES:
        namespace = _raw_class_namespace(type(parent))
        return (
            type(value) in descriptor_types
            and namespace is not None
            and namespace.get(name, _MISSING) is value
        )
    if _DEPENDENCY_TRUSTED_OBJECTS.get(id(parent)) is parent:
        namespace = _raw_class_namespace(type(parent))
        return (
            type(value) in descriptor_types
            and namespace is not None
            and namespace.get(name, _MISSING) is value
        )
    if type(value) is not BuiltinFunctionType or type(parent) is not ModuleType:
        return False
    expected = _ctypes_expected_binding(parent, name)
    if expected is not _MISSING:
        return expected is value
    parent_name = vars(parent).get("__name__", _MISSING)
    if not isinstance(parent_name, str) or sys.modules.get(parent_name) is not parent:
        return False
    namespace_value = vars(parent).get(name, _MISSING)
    value_name = getattr(value, "__name__", _MISSING)
    if namespace_value is not value:
        return False
    if value_name == name:
        return True
    # A stdlib re-export can have a different public name (ntpath.isfile).
    # Authenticate the alias from canonical source, not from mutable metadata.
    if parent_name not in sys.stdlib_module_names:
        return False
    source_path = _canonical_stdlib_source_path(parent_name, parent_name)
    _validate_dependency_module(parent, parent_name)
    tree = ast.parse(source_path.read_bytes())
    binding = _source_import_bindings(tree, frozenset({name})).get(name)
    if binding is None or binding.path != (value_name,):
        return False
    expected_value, owner = _load_source_import_binding(binding, f"{parent_name}.{name}")
    return (
        expected_value is value
        and value.__module__ == binding.module_name
        and cast(BuiltinFunctionType, value).__self__ is owner
    )


def _supported_dependency_type(value: object, parent: object, name: str) -> bool:
    try:
        expected = _ctypes_expected_binding(parent, name)
        if expected is not _MISSING:
            if expected is not value:
                return False
            _validate_ctypes_type(value, f"dependency.{name}")
            return True
        if _CTYPES_TRUSTED_TYPES.get(id(value)) is value:
            _validate_ctypes_type(value, f"dependency.{name}")
            return True
        return (
            _authenticate_dependency_type(
                value,
                parent,
                f"{vars(parent).get('__name__', 'dependency')}.{name}",
                dependency_context=_DependencyContext({}),
            )
            is not None
        )
    except (OSError, ValueError, TypeError):
        return False


def _dependency_type_base_signature(value: object, label: str) -> object:
    if type(value) is not type and type(type(value)) is not type:
        raise _error(f"Gmsh executable dependency {label} has an unsupported base class")
    try:
        module_name = type.__getattribute__(value, "__module__")
        qualname = type.__getattribute__(value, "__qualname__")
        bases = type.__getattribute__(value, "__bases__")
    except (AttributeError, TypeError) as exc:
        raise _error(f"Gmsh executable dependency {label} base class cannot be inspected") from exc
    if (
        not isinstance(module_name, str)
        or not isinstance(qualname, str)
        or type(bases) is not tuple
    ):
        raise _error(f"Gmsh executable dependency {label} base class metadata is malformed")
    return (
        module_name,
        qualname,
        tuple(
            (
                type.__getattribute__(base, "__module__"),
                type.__getattribute__(base, "__qualname__"),
            )
            for base in bases
            if type(base) is type
        ),
    )


def _dependency_class_node(tree: ast.AST, qualname: str) -> ast.ClassDef | None:
    parts = tuple(part for part in qualname.split(".") if part)
    if not parts or type(tree) is not ast.Module:
        return None
    body: list[ast.stmt] = list(tree.body)
    current: ast.ClassDef | None = None
    for part in parts:
        candidates = [
            statement
            for statement in body
            if isinstance(statement, ast.ClassDef) and statement.name == part
        ]
        if len(candidates) != 1:
            return None
        current = candidates[0]
        body = current.body
    return current


def _dependency_class_member_nodes(node: ast.ClassDef) -> dict[str, ast.AST]:
    result: dict[str, ast.AST] = {}
    for statement in node.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result[statement.name] = statement
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    result[target.id] = statement
    return result


def _authenticate_dependency_type(
    value: object,
    parent: object,
    label: str,
    *,
    dependency_context: _DependencyContext | None = None,
) -> _DependencyTypeState | None:
    expected = _ctypes_expected_binding(parent, label.rpartition(".")[2])
    if expected is not _MISSING:
        if expected is not value:
            raise _error(f"Gmsh executable dependency {label} type was replaced")
        _validate_ctypes_type(value, label)
        return None
    if _CTYPES_TRUSTED_TYPES.get(id(value)) is value:
        _validate_ctypes_type(value, label)
        return None
    member_name = label.rpartition(".")[2]
    expected_qualname: str
    parent_namespace: Mapping[str, Any] | None
    if type(parent) is ModuleType:
        parent_namespace = vars(parent)
        parent_name = parent_namespace.get("__name__", _MISSING)
        if not isinstance(parent_name, str) or sys.modules.get(parent_name) is not parent:
            raise _error(f"Gmsh executable dependency {label} has an unexpected module owner")
        if parent_namespace.get(member_name, _MISSING) is not value:
            raise _error(f"Gmsh executable dependency {label} was replaced")
        expected_qualname = member_name
    elif _is_class_object(parent) and type(type(parent)) is type:
        parent_namespace = _raw_class_namespace(parent)
        if parent_namespace is None:
            raise _error(f"Gmsh executable dependency {label} has no class owner")
        owner, descriptor = _raw_type_descriptor(parent, member_name)
        if descriptor is not value:
            raise _error(f"Gmsh executable dependency {label} was replaced")
        parent_name = parent_namespace.get("__module__", _MISSING)
        parent_qualname = parent_namespace.get("__qualname__", _MISSING)
        if not isinstance(parent_name, str) or not isinstance(parent_qualname, str):
            raise _error(f"Gmsh executable dependency {label} has an unexpected class owner")
        owner_qualname = type.__getattribute__(owner, "__qualname__")
        if not isinstance(owner_qualname, str):
            raise _error(f"Gmsh executable dependency {label} has an unexpected class owner")
        expected_qualname = f"{owner_qualname}.{member_name}"
    else:
        raise _error(f"Gmsh executable dependency {label} has no module owner")
    if not _is_class_object(value) or not _authenticated_dependency_metaclass(type(value), label):
        raise _error(f"Gmsh executable dependency {label} has an unsupported metaclass")
    try:
        value_module = type.__getattribute__(value, "__module__")
        value_qualname = type.__getattribute__(value, "__qualname__")
        bases = type.__getattribute__(value, "__bases__")
        class_namespace = type.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError) as exc:
        raise _error(f"Gmsh executable dependency {label} class cannot be inspected") from exc
    if (
        not isinstance(value_module, str)
        or not isinstance(value_qualname, str)
        or type(bases) is not tuple
        or type(class_namespace) is not MappingProxyType
    ):
        raise _error(f"Gmsh executable dependency {label} has an unexpected class owner")
    if value_module == parent_name:
        if value_qualname != expected_qualname:
            raise _error(f"Gmsh executable dependency {label} has an unexpected class owner")
    else:
        owner_module = sys.modules.get(value_module, _MISSING)
        if (
            type(owner_module) is not ModuleType
            or _raw_module_class(owner_module, value_qualname) is not value
        ):
            raise _error(f"Gmsh executable dependency {label} has an unexpected class owner")
    stdlib_names = vars(sys).get("stdlib_module_names", frozenset())
    source_module_name = value_module
    if (
        isinstance(stdlib_names, (set, frozenset))
        and source_module_name.partition(".")[0] in stdlib_names
    ):
        source_path = _canonical_stdlib_source_path(source_module_name, label)
    else:
        source_module = sys.modules.get(source_module_name, _MISSING)
        source_value = (
            vars(source_module).get("__file__", _MISSING)
            if type(source_module) is ModuleType
            else _MISSING
        )
        if not isinstance(source_value, str):
            raise _error(f"Gmsh executable dependency {label} has no source file")
        source_path = _resolve_regular_file(source_value, label)
    source_identity = _file_identity(source_path, label)
    source = _read_file_bounded(source_path, _MAX_SOURCE_BYTES, label)
    compiled = _compile_source_bytes(source, source_path)
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise _error(f"Gmsh executable dependency {label} source cannot be inspected") from exc
    class_node = _dependency_class_node(tree, value_qualname)
    expected_members = _dependency_class_member_nodes(class_node) if class_node is not None else {}
    members: dict[str, _DependencyTypeMemberState] = {}
    for member_name, member_node in expected_members.items():
        namespace_name = member_name
        class_name = value_qualname.rpartition(".")[2]
        if member_name.startswith("__") and not member_name.endswith("__"):
            namespace_name = f"_{class_name}{member_name}"
        descriptor = class_namespace.get(namespace_name, _MISSING)
        if descriptor is _MISSING:
            raise _error(f"Gmsh executable dependency {label}.{namespace_name} is unavailable")
        expected_code: CodeType | None = None
        if isinstance(member_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            expected_code = _code_object(
                compiled,
                f"{value_qualname}.{member_name}",
                member_node.lineno,
            )
            if expected_code is None:
                expected_code = _code_object_by_qualname(
                    compiled, f"{value_qualname}.{member_name}"
                )
            if expected_code is None:
                raise _error(
                    f"Gmsh executable dependency {label}.{member_name} source implementation is unavailable"
                )
        functions = _descriptor_functions(descriptor)
        if expected_code is not None:
            if not any(
                type(function) is FunctionType
                and function.__module__ == source_module_name
                and function.__qualname__ == f"{value_qualname}.{member_name}"
                and _code_digest(function.__code__) == _code_digest(expected_code)
                for function in functions
            ):
                raise _error(
                    f"Gmsh executable dependency {label}.{member_name} implementation changed"
                )
        elif isinstance(member_node, (ast.Assign, ast.AnnAssign)):
            value_node = member_node.value
            try:
                literal = ast.literal_eval(cast(ast.AST, value_node))
            except (ValueError, TypeError, MemoryError):
                literal = _MISSING
            if (
                literal is not _MISSING
                and type(descriptor)
                in (type(None), bool, int, float, complex, str, bytes, tuple, list, dict, frozenset)
                and _value_signature(descriptor) != _value_signature(literal)
            ):
                raise _error(f"Gmsh executable dependency {label}.{member_name} value changed")
        members[namespace_name] = _DependencyTypeMemberState(
            descriptor,
            _value_signature(descriptor),
            tuple(
                _function_state(
                    function,
                    code_digest=_code_digest(function.__code__),
                    source_path=source_path,
                    source_identity=source_identity,
                )
                for function in functions
            ),
        )
    base_signatures = tuple(_dependency_type_base_signature(base, label) for base in bases)
    for base_node, base_signature in zip(
        class_node.bases if class_node is not None else (), base_signatures
    ):
        expected_base_name: str | None = None
        if isinstance(base_node, ast.Name):
            expected_base_name = base_node.id
        elif isinstance(base_node, ast.Call) and base_node.args:
            function = base_node.func
            function_name = (
                function.id
                if isinstance(function, ast.Name)
                else (function.attr if isinstance(function, ast.Attribute) else None)
            )
            if function_name == "namedtuple" and isinstance(base_node.args[0], ast.Constant):
                expected_base_name = (
                    base_node.args[0].value if isinstance(base_node.args[0].value, str) else None
                )
        if (
            expected_base_name is not None
            and cast(tuple[str, str], base_signature)[1].rpartition(".")[2] != expected_base_name
        ):
            raise _error(f"Gmsh executable dependency {label} base implementation changed")
    state = _DependencyTypeState(
        value,
        source_module_name,
        value_qualname,
        source_path,
        source_identity,
        bases,
        base_signatures,
        members,
    )
    return state


def _supported_dependency_ctypes_callable(value: object, parent: object, name: str) -> bool:
    expected = _ctypes_expected_binding(parent, name)
    if expected is _MISSING or expected is not value or not _is_ctypes_callable(value):
        return False
    _ctypes_dispatch_descriptor(value, f"ctypes.{name}")
    if name == "_cast":
        _ctypes_internal_cast_signature(value, f"ctypes.{name}")
    else:
        argtypes = _ctypes_metadata_value(value, f"ctypes.{name}", "argtypes")
        restype = _ctypes_metadata_value(value, f"ctypes.{name}", "restype")
        _validate_ctypes_conversion_value(argtypes, f"ctypes.{name}.argtypes")
        _validate_ctypes_conversion_value(restype, f"ctypes.{name}.restype")
    return True


def _dependency_callable_state(
    value: object,
    parent: object,
    label: str,
    *,
    dependency_depth: int,
    dependency_seen: frozenset[int],
    dependency_context: _DependencyContext | None = None,
) -> _FunctionState | _DependencyTypeState | None:
    name = label.rpartition(".")[2]
    if type(value) is FunctionType:
        return _authenticate_dependency_function(
            value,
            parent,
            label,
            dependency_depth=dependency_depth,
            dependency_seen=dependency_seen,
            dependency_context=dependency_context,
        )
    if _supported_dependency_ctypes_callable(value, parent, name) or _supported_dependency_builtin(
        value, parent, name
    ):
        return None
    if _is_class_object(value):
        return _authenticate_dependency_type(
            value,
            parent,
            label,
            dependency_context=dependency_context,
        )
    if callable(value):
        raise _error(f"Gmsh executable dependency {label} has an unsupported callable")
    return None


def _split_dependency_state(
    state: _FunctionState | _DependencyTypeState | None,
) -> tuple[_FunctionState | None, _DependencyTypeState | None]:
    if type(state) is _FunctionState:
        return state, None
    if type(state) is _DependencyTypeState:
        return None, state
    return None, None


def _capture_module_dependency_states(
    function: FunctionType,
    root_name: str,
    module: ModuleType,
    *,
    dependency_depth: int,
    dependency_seen: frozenset[int],
    dependency_context: _DependencyContext | None = None,
) -> tuple[_ModuleDependencyState, ...]:
    _validate_dependency_module(module, root_name)
    result: list[_ModuleDependencyState] = []
    for chain_root, path in sorted(_code_attribute_chains(function.__code__)):
        if chain_root != root_name:
            continue
        values: list[object] = [module]
        current: object = module
        actual_path: list[str] = []
        for name in path:
            current = _raw_dependency_attribute(current, name)
            if current is _MISSING:
                actual_path.append(name)
                values.append(_MISSING)
                break
            if type(values[-1]) is ModuleType:
                module_name = vars(values[-1]).get("__name__", _MISSING)
                expected = (
                    _DEPENDENCY_EXPECTED_BINDINGS.get((module_name, name), _MISSING)
                    if isinstance(module_name, str)
                    else _MISSING
                )
                if expected is not _MISSING and current is not expected:
                    raise _error(
                        f"Gmsh executable dependency {root_name}.{'.'.join(actual_path + [name])} was replaced"
                    )
            actual_path.append(name)
            values.append(current)
        function_state = None
        type_state = None
        if current is not _MISSING and callable(current):
            parent = values[-2]
            dependency_state = _dependency_callable_state(
                current,
                parent,
                f"{root_name}.{'.'.join(actual_path)}",
                dependency_depth=dependency_depth,
                dependency_seen=dependency_seen,
                dependency_context=dependency_context,
            )
            function_state, type_state = _split_dependency_state(dependency_state)
        result.append(
            _ModuleDependencyState(
                module, tuple(actual_path), tuple(values), function_state, type_state
            )
        )
    return tuple(result)


def _authenticate_dependency_function(
    function: FunctionType,
    parent: object,
    label: str,
    *,
    dependency_depth: int,
    dependency_seen: frozenset[int],
    dependency_context: _DependencyContext | None = None,
) -> _FunctionState:
    module_name: object
    source_name: object
    expected_qualname: str
    parent_namespace: Mapping[str, Any] | None
    if type(parent) is ModuleType:
        parent_namespace = vars(parent)
        module_name = parent_namespace.get("__name__", _MISSING)
        source_name = parent_namespace.get("__file__", _MISSING)
        if parent_namespace.get(label.rpartition(".")[2], _MISSING) is not function:
            raise _error(f"Gmsh executable dependency {label} was replaced")
        expected_qualname = label.rpartition(".")[2]
    elif _is_class_object(parent) and type(type(parent)) is type:
        class_namespace = _raw_class_namespace(parent)
        if class_namespace is None:
            raise _error(f"Gmsh executable dependency {label} class namespace is unavailable")
        module_name = class_namespace.get("__module__", _MISSING)
        module = sys.modules.get(module_name) if isinstance(module_name, str) else None
        source_name = (
            vars(module).get("__file__", _MISSING) if type(module) is ModuleType else _MISSING
        )
        owner, descriptor = _raw_type_descriptor(parent, label.rpartition(".")[2])
        if descriptor is not function:
            raise _error(f"Gmsh executable dependency {label} was replaced")
        owner_qualname = type.__getattribute__(owner, "__qualname__")
        if not isinstance(owner_qualname, str):
            raise _error(f"Gmsh executable dependency {label} has an unexpected class owner")
        expected_qualname = f"{owner_qualname}.{label.rpartition('.')[2]}"
    elif _DEPENDENCY_TRUSTED_OBJECTS.get(id(parent)) is parent:
        parent_type = type(parent)
        owner, descriptor = _raw_type_descriptor(parent_type, label.rpartition(".")[2])
        if descriptor is not function:
            raise _error(f"Gmsh executable dependency {label} was replaced")
        class_namespace = _raw_class_namespace(owner)
        if class_namespace is None:
            raise _error(f"Gmsh executable dependency {label} class namespace is unavailable")
        module_name = class_namespace.get("__module__", _MISSING)
        module = sys.modules.get(module_name) if isinstance(module_name, str) else None
        source_name = (
            vars(module).get("__file__", _MISSING) if type(module) is ModuleType else _MISSING
        )
        owner_qualname = type.__getattribute__(owner, "__qualname__")
        if not isinstance(owner_qualname, str):
            raise _error(f"Gmsh executable dependency {label} has an unexpected class owner")
        expected_qualname = f"{owner_qualname}.{label.rpartition('.')[2]}"
    else:
        raise _error(f"Gmsh executable dependency {label} has no module owner")
    if (
        not isinstance(module_name, str)
        or function.__module__ != module_name
        or function.__qualname__ != expected_qualname
    ):
        raise _error(f"Gmsh executable dependency {label} has an unexpected source owner")
    if (
        isinstance(source_name, str)
        and not function.__code__.co_filename.startswith("<frozen ")
        and not _same_path(function.__code__.co_filename, source_name)
    ):
        raise _error(f"Gmsh executable dependency {label} has an unexpected source owner")
    state = _function_dependency_state(
        function,
        dependency_depth=dependency_depth,
        dependency_seen=dependency_seen,
        dependency_context=dependency_context,
    )
    if state is None:
        raise _error(f"Gmsh executable dependency {label} is not independently authenticated")
    return state


def _capture_source_dependencies(
    source: bytes, code: CodeType
) -> tuple[_ExecutableAttributeState, ...]:
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise _error("Gmsh executable dependencies cannot be inspected") from exc
    used_names = _code_global_names(code)
    bindings, expected, absent_imports = _source_import_flow(tree)
    chains: set[tuple[str, tuple[str, ...]]] = set()
    for node in ast.walk(tree):
        chain = _attribute_chain(node)
        if chain is not None and chain[0] in bindings and chain[1]:
            chains.add(chain)
    for root_name in bindings:
        if root_name in used_names:
            chains.add((root_name, ()))
    if len(chains) > _MAX_LIVE_MEMBERS:
        raise _error("Gmsh executable dependencies exceed the finite verification limit")

    result: list[_ExecutableAttributeState] = [
        _ExecutableAttributeState(name, (), (value,)) for name, value in sorted(expected.items())
    ]
    if result:
        result[0].absent_imports = absent_imports
    dependency_context = _DependencyContext({})
    for root_name, path in sorted(chains):
        root, module = _load_source_import_binding(bindings[root_name], root_name)
        values: list[object] = [root]
        current = root
        for name in path:
            current = _raw_dependency_attribute(current, name)
            if current is _MISSING:
                raise _error(
                    f"Gmsh executable dependency {root_name}.{'.'.join(path)} is unavailable"
                )
            values.append(current)
        if not callable(current):
            continue
        parent = values[-2] if len(values) > 1 else module
        binding_path = bindings[root_name].path
        dependency_label = (
            (binding_path[-1] if binding_path else root_name)
            if not path
            else f"{root_name}.{'.'.join(path)}"
        )
        dependency_state = _dependency_callable_state(
            current,
            parent,
            dependency_label,
            dependency_depth=0,
            dependency_seen=frozenset(),
            dependency_context=dependency_context,
        )
        function_state, type_state = _split_dependency_state(dependency_state)
        result.append(
            _ExecutableAttributeState(root_name, path, tuple(values), function_state, type_state)
        )
    return tuple(result)


def _validate_source_dependencies(
    module: ModuleType, states: tuple[_ExecutableAttributeState, ...]
) -> None:
    namespace = vars(module)
    for state in states:
        for name in state.absent_imports:
            if not _source_import_absent(name):
                raise _error(f"Gmsh dependency import {name} is no longer absent")
        current = namespace.get(state.root_name, _MISSING)
        if current is not state.values[0]:
            raise _error(f"live Gmsh executable dependency {state.root_name} was replaced")
        for index, name in enumerate(state.path, start=1):
            current = _raw_dependency_attribute(current, name)
            if current is _MISSING or current is not state.values[index]:
                raise _error(
                    f"live Gmsh executable dependency {state.root_name}.{'.'.join(state.path)} was replaced"
                )
        if state.function_state is not None:
            _validate_function_state(
                state.function_state,
                f"{state.root_name}.{'.'.join(state.path)}",
            )
        if state.type_state is not None:
            _validate_dependency_type_state(
                state.type_state,
                current,
                f"{state.root_name}.{'.'.join(state.path)}",
            )


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
    if type(member) is staticmethod or type(member) is classmethod:
        function = member.__func__
        return (function,) if type(function) is FunctionType else ()
    if type(member) is property:
        return tuple(
            function
            for function in (member.fget, member.fset, member.fdel)
            if type(function) is FunctionType
        )
    member_type = type(member)
    if (
        type.__getattribute__(member_type, "__module__") == "functools"
        and type.__getattribute__(member_type, "__qualname__") == "cached_property"
    ):
        descriptor_namespace = vars(member)
        function = descriptor_namespace.get("func", _MISSING)
        return (function,) if type(function) is FunctionType else ()
    return (member,) if type(member) is FunctionType else ()


@dataclass(slots=True)
class _DispatchDescriptorState:
    owner: type
    descriptor: object
    functions: tuple[_FunctionState, ...]


def _dispatch_descriptor_state(value_type: type, name: str) -> _DispatchDescriptorState:
    owner, descriptor = _raw_type_descriptor(value_type, name)
    trusted = _CTYPES_TRUSTED_DESCRIPTORS.get((owner, name))
    if trusted is not None:
        trusted_descriptor, function_state = trusted
        if descriptor is not trusted_descriptor:
            raise _error(f"ctypes {name} descriptor changed before admission")
        _validate_function_state(function_state, f"ctypes {name}")
        return _DispatchDescriptorState(owner, descriptor, (function_state,))
    permitted = _CTYPES_PERMITTED_DESCRIPTORS.get((owner, name))
    if permitted is not None and descriptor is not permitted:
        raise _error(f"ctypes {name} descriptor changed before admission")
    if descriptor is _MISSING:
        return _DispatchDescriptorState(owner, descriptor, ())
    if permitted is None:
        raise _error(f"ctypes {name} descriptor is not statically authenticated")
    return _DispatchDescriptorState(owner, descriptor, ())


def _generic_dispatch_descriptor_state(value_type: type, name: str) -> _DispatchDescriptorState:
    owner, descriptor = _raw_type_descriptor(value_type, name)
    return _DispatchDescriptorState(
        owner,
        descriptor,
        tuple(
            _function_state(function, capture_globals=True)
            for function in _descriptor_functions(descriptor)
        ),
    )


def _factory_descriptor_state(value_type: type, name: str) -> _DispatchDescriptorState:
    owner, descriptor = _raw_type_descriptor(value_type, name)
    trusted = _CTYPES_TRUSTED_DESCRIPTORS.get((owner, name))
    if trusted is not None:
        trusted_descriptor, function_state = trusted
        if descriptor is not trusted_descriptor:
            raise _error(f"ctypes {name} descriptor changed before admission")
        _validate_function_state(function_state, f"ctypes {name}")
        return _DispatchDescriptorState(owner, descriptor, (function_state,))
    permitted = _CTYPES_PERMITTED_DESCRIPTORS.get((owner, name))
    if permitted is not None:
        if descriptor is not permitted:
            raise _error(f"ctypes {name} descriptor changed before admission")
        return _DispatchDescriptorState(owner, descriptor, ())
    if descriptor is _MISSING:
        return _DispatchDescriptorState(owner, descriptor, ())
    if name not in {"__new__", "__init__"}:
        raise _error(f"ctypes {name} descriptor is not statically authenticated")
    if owner is not value_type or type(descriptor) is not FunctionType:
        raise _error(f"ctypes {name} descriptor is not statically authenticated")
    captured_function_state = _function_dependency_state(
        descriptor,
        dependency_depth=0,
        dependency_seen=frozenset(),
    )
    if captured_function_state is None:
        raise _error(f"ctypes {name} descriptor is not statically authenticated")
    return _DispatchDescriptorState(owner, descriptor, (captured_function_state,))


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
    if not _is_class_object(value):
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
        _factory_descriptor_state(value, "__new__"),
        _factory_descriptor_state(value, "__init__"),
        instance_descriptors,
    )


@dataclass(slots=True)
class _AttributeDispatchState:
    value_type: type
    descriptors: dict[str, _DispatchDescriptorState]


def _attribute_dispatch_state(value: object, names: tuple[str, ...]) -> _AttributeDispatchState:
    value_type = type(value)
    if type(value_type) is not type:
        raise _error("Gmsh attribute dispatch uses an unsupported metaclass")
    return _AttributeDispatchState(
        value_type,
        {name: _generic_dispatch_descriptor_state(value_type, name) for name in names},
    )


@dataclass(slots=True)
class _InstanceBindingState:
    value: object
    signature: object
    function_state: _FunctionState | None = None


_INSTANCE_DISPATCH_NAMES = (
    "__getattribute__",
    "__getattr__",
    "__getitem__",
    "__setattr__",
    "_FuncPtr",
    "_name",
    "_handle",
)


def _capture_instance_bindings(
    namespace: dict[str, object], label: str
) -> dict[str, _InstanceBindingState]:
    result: dict[str, _InstanceBindingState] = {}
    for name in _INSTANCE_DISPATCH_NAMES:
        value = namespace.get(name, _MISSING)
        if name.startswith("__") and value is not _MISSING:
            raise _error(f"{label} instance dispatch {name} is overridden")
        function_state = (
            _function_dependency_state(
                value,
                dependency_depth=0,
                dependency_seen=frozenset(),
            )
            if type(value) is FunctionType
            else None
        )
        result[name] = _InstanceBindingState(
            value,
            _value_signature(value),
            function_state,
        )
    return result


def _validate_instance_bindings(
    states: dict[str, _InstanceBindingState],
    namespace: dict[str, object],
    label: str,
) -> None:
    for name, state in states.items():
        current = namespace.get(name, _MISSING)
        if state.value is _MISSING:
            if current is not _MISSING:
                raise _error(f"live Gmsh {label} instance binding {name} was added")
        elif current is not state.value:
            raise _error(f"live Gmsh {label} instance binding {name} was replaced")
        if state.value is not _MISSING and _value_signature(current) != state.signature:
            raise _error(f"live Gmsh {label} instance binding {name} changed")
        if state.function_state is not None:
            _validate_function_state(state.function_state, f"{label}.{name}")


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
        namespace = _raw_class_namespace(class_object)
        if namespace is None:
            raise _error(f"Gmsh live class {'.'.join(path)} cannot be inspected")
    except TypeError as exc:
        raise _error("Gmsh live class namespace cannot be inspected") from exc
    if namespace.get("__module__") != module_name:
        return
    class_state = _ClassState(class_object, {})
    classes[path] = class_state
    for name, descriptor in namespace.items():
        member_functions = tuple(
            _function_state(function, capture_globals=True)
            for function in _descriptor_functions(descriptor)
        )
        nested = False
        if _is_class_object(descriptor):
            nested = type.__getattribute__(descriptor, "__module__") == module_name
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
    function_state: _FunctionState | None = None
    module_states: tuple[_ModuleDependencyState, ...] = ()
    type_state: _DependencyTypeState | None = None


def _builtin_binding_from_globals(globals_dict: dict[str, object], name: str) -> object:
    builtins_value = globals_dict.get("__builtins__", _MISSING)
    if isinstance(builtins_value, dict):
        return builtins_value.get(name, _MISSING)
    if type(builtins_value) is ModuleType:
        return vars(builtins_value).get(name, _MISSING)
    return _MISSING


def _builtin_binding_from_function(function: FunctionType, name: str) -> object:
    builtins_value = cast(Any, function).__builtins__
    if isinstance(builtins_value, dict):
        return builtins_value.get(name, _MISSING)
    if type(builtins_value) is ModuleType:
        return vars(builtins_value).get(name, _MISSING)
    return _MISSING


def _builtin_binding(module: ModuleType, name: str) -> object:
    return _builtin_binding_from_globals(vars(module), name)


def _code_object(root: CodeType, qualname: str, firstlineno: int) -> CodeType | None:
    pending = [root]
    candidates: list[CodeType] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.co_qualname == qualname and current.co_firstlineno == firstlineno:
            candidates.append(current)
        pending.extend(constant for constant in current.co_consts if isinstance(constant, CodeType))
    if len(candidates) != 1:
        return None
    return candidates[0]


def _code_object_by_qualname(root: CodeType, qualname: str) -> CodeType | None:
    pending = [root]
    candidates: list[CodeType] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.co_qualname == qualname:
            candidates.append(current)
        pending.extend(constant for constant in current.co_consts if isinstance(constant, CodeType))
    return candidates[0] if len(candidates) == 1 else None


def _dependency_source_path(code: CodeType, label: str, module_name: str | None = None) -> Path:
    filename = code.co_filename
    if isinstance(filename, str) and filename.startswith("<frozen ") and filename.endswith(">"):
        frozen_name = filename[len("<frozen ") : -1]
        frozen_module = sys.modules.get(frozen_name, _MISSING)
        frozen_source = (
            vars(frozen_module).get("__file__", _MISSING)
            if type(frozen_module) is ModuleType
            else _MISSING
        )
        if isinstance(frozen_source, str):
            return _resolve_regular_file(frozen_source, f"Gmsh executable dependency {label}")
    if isinstance(module_name, str):
        stdlib_names = vars(sys).get("stdlib_module_names", frozenset())
        root_name = module_name.partition(".")[0]
        if isinstance(stdlib_names, (set, frozenset)) and root_name in stdlib_names:
            canonical = _canonical_stdlib_source_path(module_name, label)
            filename = code.co_filename
            if (
                isinstance(filename, str)
                and not filename.startswith("<")
                and not _same_path(filename, str(canonical))
            ):
                raise _error(f"Gmsh executable dependency {label} has an unexpected source file")
            return canonical
    if not isinstance(filename, str) or not filename:
        raise _error(f"Gmsh executable dependency {label} has no source file")
    if filename.startswith("<frozen ") and filename.endswith(">"):
        frozen_name = filename[len("<frozen ") : -1]
        module = sys.modules.get(frozen_name)
        source_name = (
            vars(module).get("__file__", _MISSING) if type(module) is ModuleType else _MISSING
        )
        if not isinstance(source_name, str):
            raise _error(f"Gmsh executable dependency {label} has no source file")
        return _resolve_regular_file(source_name, f"Gmsh executable dependency {label}")
    if filename.startswith("<"):
        raise _error(f"Gmsh executable dependency {label} has no source file")
    return _resolve_regular_file(filename, f"Gmsh executable dependency {label}")


def _source_code_state(
    code: CodeType, label: str, module_name: str | None = None
) -> tuple[Path, dict[str, object]]:
    path = _dependency_source_path(code, label, module_name)
    before = _file_identity(path, f"Gmsh executable dependency {label}")
    content = _read_file_bounded(path, _MAX_SOURCE_BYTES, f"Gmsh executable dependency {label}")
    after = _file_identity(path, f"Gmsh executable dependency {label}")
    _compare_file_records(before, after, f"Gmsh executable dependency {label}")
    compiled = _compile_source_bytes(content, path)
    expected = _code_object(compiled, code.co_qualname, code.co_firstlineno)
    if expected is None:
        raise _error(f"Gmsh executable dependency {label} source function is missing")
    expected_digest = _code_digest(expected)
    if isinstance(code.co_filename, str) and code.co_filename.startswith("<frozen "):
        expected_digest = _code_digest(expected.replace(co_filename=code.co_filename))
    if expected_digest != _code_digest(code):
        raise _error(f"Gmsh executable dependency {label} code differs from source")
    return path, before


def _function_dependency_state(
    function: FunctionType,
    *,
    dependency_depth: int,
    dependency_seen: frozenset[int],
    dependency_context: _DependencyContext | None = None,
) -> _FunctionState | None:
    context = dependency_context or _DependencyContext({})
    existing = context.functions.get(id(function))
    if existing is not None:
        return existing
    if len(context.functions) >= _MAX_FUNCTION_DEPENDENCY_NODES:
        raise _error("Gmsh executable dependency graph exceeds the finite limit")
    trusted = _CTYPES_TRUSTED_FUNCTIONS.get(id(function))
    if trusted is not None:
        context.functions[id(function)] = trusted
        _validate_function_state(trusted, f"imported {function.__qualname__}")
        return trusted
    source_path, source_identity = _source_code_state(
        function.__code__, function.__qualname__, function.__module__
    )
    state = _function_state(
        function,
        capture_globals=False,
        source_path=source_path,
        source_identity=source_identity,
    )
    context.functions[id(function)] = state
    state.global_states = _capture_function_globals(
        function,
        dependency_depth=dependency_depth + 1,
        dependency_seen=dependency_seen | {id(function)},
        dependency_context=context,
    )
    _validate_function_state(state, f"imported {function.__qualname__}")
    return state


def _capture_function_globals(
    function: FunctionType,
    *,
    code: CodeType | None = None,
    dependency_depth: int = 0,
    dependency_seen: frozenset[int] = frozenset(),
    dependency_context: _DependencyContext | None = None,
) -> dict[str, _GlobalState]:
    context = dependency_context or _DependencyContext({})
    globals_dict = function.__globals__
    builtins_value = cast(Any, function).__builtins__
    global_builtins = globals_dict.get("__builtins__", _MISSING)
    if global_builtins is not _MISSING and global_builtins is not builtins_value:
        raise _error("Gmsh executable function builtins mapping differs from its globals")
    names = set(_code_global_names(function.__code__ if code is None else code))
    attribute_roots = {
        root for root, _path in _code_attribute_chains(function.__code__ if code is None else code)
    }
    names.add("__builtins__")
    result: dict[str, _GlobalState] = {}
    for name in names:
        if name in globals_dict:
            value = globals_dict[name]
            if (
                name in attribute_roots
                and name != "lib"
                and type(value) is not ModuleType
                and type(value) not in _SAFE_ATTRIBUTE_NAMESPACE_TYPES
                and not _is_class_object(value)
            ):
                raise _error(f"Gmsh executable dependency {name} has an unsupported namespace")
            if type(value) is ModuleType:
                _validate_dependency_module(value, f"{function.__qualname__}.{name}")
            owner_name = globals_dict.get("__name__", _MISSING)
            owner = sys.modules.get(owner_name) if isinstance(owner_name, str) else _MISSING
            dependency_state = (
                _dependency_callable_state(
                    value,
                    owner,
                    f"{owner_name}.{name}" if isinstance(owner_name, str) else name,
                    dependency_depth=dependency_depth,
                    dependency_seen=dependency_seen,
                    dependency_context=context,
                )
                if name != "lib" and callable(value)
                else None
            )
            function_state, type_state = _split_dependency_state(dependency_state)
            module_states = (
                _capture_module_dependency_states(
                    function,
                    name,
                    value,
                    dependency_depth=dependency_depth,
                    dependency_seen=dependency_seen,
                    dependency_context=context,
                )
                if type(value) is ModuleType
                else ()
            )
            result[name] = _GlobalState(
                value,
                _stable_global_signature(value),
                _MISSING,
                None,
                function_state,
                module_states,
                type_state,
            )
        else:
            builtin = _builtin_binding_from_function(function, name)
            if (
                builtin is not _MISSING
                and name in vars(builtins)
                and builtin is not vars(builtins)[name]
            ):
                raise _error(f"Gmsh executable dependency builtin {name} is replaced")
            function_state = None
            result[name] = _GlobalState(
                _MISSING,
                None,
                builtin,
                _stable_global_signature(builtin),
                function_state,
                (),
                None,
            )
        if len(result) > _MAX_LIVE_GLOBALS:
            raise _error("Gmsh dispatch globals exceed the finite verification limit")
    return result


def _validate_module_dependency_states(
    states: tuple[_ModuleDependencyState, ...], label: str, validation_seen: set[int] | None = None
) -> None:
    for state in states:
        current: object = state.module
        if type(current) is not ModuleType:
            raise _error(f"live Gmsh executable dependency {label} module changed")
        for index, name in enumerate(state.path, start=1):
            current = _raw_dependency_attribute(current, name)
            expected = state.values[index]
            if expected is _MISSING:
                if current is not _MISSING:
                    raise _error(
                        f"live Gmsh executable dependency {label}.{'.'.join(state.path)} was added"
                    )
                break
            if current is _MISSING or current is not expected:
                raise _error(
                    f"live Gmsh executable dependency {label}.{'.'.join(state.path)} was replaced"
                )
        if state.function_state is not None:
            _validate_function_state(
                state.function_state,
                f"{label}.{'.'.join(state.path)}",
                validation_seen,
            )
        if state.type_state is not None:
            _validate_dependency_type_state(
                state.type_state,
                current,
                f"{label}.{'.'.join(state.path)}",
                validation_seen,
            )


def _validate_dependency_type_state(
    state: _DependencyTypeState,
    current: object,
    label: str,
    validation_seen: set[int] | None = None,
) -> None:
    if current is not state.value or type(current) is not type(state.value):
        raise _error(f"live Gmsh executable dependency {label} class was replaced")
    try:
        module_name = type.__getattribute__(current, "__module__")
        qualname = type.__getattribute__(current, "__qualname__")
        bases = type.__getattribute__(current, "__bases__")
        namespace = type.__getattribute__(current, "__dict__")
    except (AttributeError, TypeError) as exc:
        raise _error(f"live Gmsh executable dependency {label} class cannot be inspected") from exc
    if (
        module_name != state.module_name
        or qualname != state.qualname
        or type(bases) is not tuple
        or type(namespace) is not MappingProxyType
        or tuple(_dependency_type_base_signature(base, label) for base in bases)
        != state.base_signatures
    ):
        raise _error(f"live Gmsh executable dependency {label} class metadata changed")
    module = sys.modules.get(state.module_name)
    source_name = vars(module).get("__file__", _MISSING) if type(module) is ModuleType else _MISSING
    if not isinstance(source_name, str) or not _same_path(source_name, str(state.source_path)):
        raise _error(f"live Gmsh executable dependency {label} source path changed")
    current_identity = _file_identity(state.source_path, label)
    _compare_file_records(state.source_identity, current_identity, label)
    for name, member_state in state.members.items():
        member = namespace.get(name, _MISSING)
        if member is not member_state.descriptor:
            raise _error(f"live Gmsh executable dependency {label}.{name} changed")
        if _value_signature(member) != member_state.signature:
            raise _error(f"live Gmsh executable dependency {label}.{name} changed")
        for function_state in member_state.functions:
            _validate_function_state(function_state, f"{label}.{name}", validation_seen)


def _is_authenticated_platform_cache(state: _FunctionState, name: str, value: object) -> bool:
    if (
        state.function.__module__ != "platform"
        or state.function.__qualname__ != "uname"
        or name != "_uname_cache"
    ):
        return False
    platform_module = sys.modules.get("platform", _MISSING)
    if type(platform_module) is not ModuleType or state.globals_dict is not vars(platform_module):
        return False
    result_state = state.global_states.get("uname_result") if state.global_states else None
    result_type_state = result_state.type_state if result_state is not None else None
    return result_type_state is not None and type(value) is result_type_state.value


def _validate_function_globals(
    state: _FunctionState, label: str, validation_seen: set[int] | None = None
) -> None:
    if state.global_states is None:
        return
    globals_dict = state.globals_dict
    for name, global_state in state.global_states.items():
        current = globals_dict.get(name, _MISSING)
        if (
            current is not None
            and state.function.__module__ == "platform"
            and state.function.__qualname__ == "uname"
            and name == "_uname_cache"
            and not _is_authenticated_platform_cache(state, name, current)
        ):
            raise _error(f"live Gmsh dispatch global {label}.{name} was replaced")
        if global_state.value is _MISSING:
            if current is not _MISSING:
                raise _error(f"live Gmsh dispatch global {label}.{name} was added")
            builtin = _builtin_binding_from_function(state.function, name)
            if builtin is not global_state.builtin_value:
                raise _error(f"live Gmsh dispatch builtin {label}.{name} was replaced")
            if (
                global_state.builtin_signature is not None
                and _stable_global_signature(builtin) != global_state.builtin_signature
            ):
                raise _error(f"live Gmsh dispatch builtin {label}.{name} changed")
        else:
            if current is not global_state.value:
                if not _is_authenticated_platform_cache(state, name, current):
                    raise _error(f"live Gmsh dispatch global {label}.{name} was replaced")
            else:
                if (
                    global_state.stable_signature is not None
                    and _stable_global_signature(current) != global_state.stable_signature
                ):
                    raise _error(f"live Gmsh dispatch global {label}.{name} changed")
        _validate_module_dependency_states(
            global_state.module_states, f"{label}.{name}", validation_seen
        )
        if global_state.type_state is not None:
            if current is not global_state.type_state.value:
                raise _error(f"live Gmsh executable dependency {label}.{name} was replaced")
            _validate_dependency_type_state(
                global_state.type_state,
                current,
                f"{label}.{name}",
                validation_seen,
            )
        if global_state.function_state is not None:
            dependency = global_state.function_state
            resolved = (
                current
                if global_state.value is not _MISSING
                else _builtin_binding_from_function(state.function, name)
            )
            if resolved is not dependency.function:
                raise _error(f"live Gmsh executable dependency {label}.{name} was replaced")
            _validate_function_state(dependency, f"{label}.{name}", validation_seen)


_CTYPES_TRUSTED_FUNCTIONS: dict[int, _FunctionState] = {}
_CTYPES_TRUSTED_FUNCTIONS_BY_NAME: dict[str, _FunctionState] = {}
_CTYPES_TRUSTED_DESCRIPTORS: dict[tuple[type, str], tuple[object, _FunctionState]] = {}
_CTYPES_TRUSTED_TYPES: dict[int, type] = {}
_CTYPES_PERMITTED_DESCRIPTORS: dict[tuple[type, str], object] = {}
_CTYPES_DISPATCH_EXPECTATIONS: dict[tuple[type, str], tuple[type, object]] = {}
_CTYPES_BINDINGS: dict[str, object] = {}
_CTYPES_INTERNAL_DISPATCH: tuple[type, object, type, object, type, object] | None = None
_CTYPES_INTERNAL_SIGNATURE: tuple[object, ...] | None = None
_CTYPES_NAMESPACE: dict[str, object] | None = None
_CTYPES_SOURCE_PATH: Path | None = None
_CTYPES_SOURCE_IDENTITY: dict[str, object] | None = None
_CTYPES_INTERNAL_ARGTYPES: tuple[type, ...] = ()
_CTYPES_INTERNAL_RESTYPE: type | None = None
_CTYPES_WINTYPES_MODULE = ctypes.wintypes
_CTYPES_EXPECTED_BINDINGS: dict[str, object] = {}
_CTYPES_EXPECTED_TYPE_STATES: dict[int, _CTypeState] = {}
_CTYPES_DYNAMIC_TYPE_STATES: dict[int, _CTypeState] = {}
_CTYPES_EXPECTED_DISPATCH: dict[tuple[type, str], tuple[type, object]] = {}
_CTYPES_TYPE_NAMES = (
    "_CFuncPtr",
    "CDLL",
    "WinDLL",
    "Array",
    "Structure",
    "Union",
    "c_bool",
    "c_byte",
    "c_char",
    "c_char_p",
    "c_double",
    "c_float",
    "c_int",
    "c_int16",
    "c_int32",
    "c_int64",
    "c_long",
    "c_longdouble",
    "c_longlong",
    "c_size_t",
    "c_ssize_t",
    "c_short",
    "c_ubyte",
    "c_uint",
    "c_uint16",
    "c_uint32",
    "c_uint64",
    "c_ulong",
    "c_ulonglong",
    "c_ushort",
    "c_void_p",
    "c_wchar",
    "c_wchar_p",
    "py_object",
)
_CTYPES_WINTYPE_NAMES = ("BOOL", "DWORD", "HMODULE", "LPCSTR", "LPWSTR")
_CTYPES_EXPECTED_DISPATCH_NAMES = (
    "__call__",
    "__getattribute__",
    "__setattr__",
    "__new__",
    "__init__",
    "__getattr__",
    "__getitem__",
    "_name",
    *(_NATIVE_CALL_ATTRIBUTES),
)


@dataclass(slots=True)
class _CTypeState:
    value: type
    metaclass: type
    value_descriptors: dict[str, tuple[type, object]]
    metaclass_descriptors: dict[str, tuple[type, object]]


def _ctypes_expected_binding(parent: object, name: str) -> object:
    if parent is ctypes:
        return _CTYPES_EXPECTED_BINDINGS.get(f"ctypes.{name}", _MISSING)
    if parent is _CTYPES_WINTYPES_MODULE:
        return _CTYPES_EXPECTED_BINDINGS.get(f"ctypes.wintypes.{name}", _MISSING)
    return _MISSING


def _capture_ctypes_type_state(value: type, label: str) -> _CTypeState:
    metaclass = type(value)
    if type(metaclass) is not type:
        raise _error(f"ctypes {label} type uses an unsupported metaclass")
    value_descriptors = {
        name: _raw_type_descriptor(value, name)
        for name in ("from_param", "_type_", "_length_", "__new__", "__init__")
    }
    metaclass_descriptors = {
        name: _raw_type_descriptor(metaclass, name)
        for name in ("__call__", "__getattribute__", "__setattr__", "__new__", "__init__")
    }
    return _CTypeState(value, metaclass, value_descriptors, metaclass_descriptors)


def _ctypes_native_module() -> ModuleType:
    native = sys.modules.get("_ctypes", _MISSING)
    if type(native) is not ModuleType:
        raise _error("ctypes native module is unavailable")
    namespace = vars(native)
    if namespace.get("__name__", _MISSING) != "_ctypes":
        raise _error("ctypes native module identity is invalid")
    source = namespace.get("__file__", _MISSING)
    if not isinstance(source, str):
        raise _error("ctypes native module source is unavailable")
    try:
        source_path = Path(source).resolve(strict=True)
        native_roots = (
            _canonical_stdlib_dir().parent / "DLLs",
            _canonical_stdlib_dir() / "lib-dynload",
        )
        if not any(source_path.parent.resolve() == root.resolve() for root in native_roots):
            raise _error("ctypes native module is outside the Python runtime")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if isinstance(exc, _RuntimeBindingError):
            raise
        raise _error("ctypes native module source is unavailable") from exc
    return native


def _validate_ctypes_native_anchors() -> ModuleType:
    native = _ctypes_native_module()
    namespace = vars(ctypes)
    native_namespace = vars(native)
    native_bindings = {
        "Array": "Array",
        "Structure": "Structure",
        "Union": "Union",
        "_Pointer": "_Pointer",
        "_CFuncPtr": "CFuncPtr",
        "POINTER": "POINTER",
    }
    for current_name, native_name in native_bindings.items():
        expected = native_namespace.get(native_name, _MISSING)
        if expected is _MISSING or namespace.get(current_name, _MISSING) is not expected:
            raise _error(f"ctypes native binding {current_name} is not authentic")
    simple_data = native_namespace.get("_SimpleCData", _MISSING)
    c_void_p = namespace.get("c_void_p", _MISSING)
    c_void_p_mro = (
        type.__getattribute__(c_void_p, "__mro__") if type(c_void_p) is type(simple_data) else ()
    )
    if (
        type(type(simple_data)) is not type
        or type(c_void_p) is not type(simple_data)
        or simple_data not in c_void_p_mro
        or (_raw_class_namespace(c_void_p) or dict[str, Any]()).get("_type_", _MISSING) != "P"
        or namespace.get("c_voidp", _MISSING) is not c_void_p
    ):
        raise _error("ctypes c_void_p native conversion anchor is not authentic")
    converter_owner, converter = _raw_type_descriptor(c_void_p, "from_param")
    if converter_owner is not c_void_p or type(converter) is not ClassMethodDescriptorType:
        raise _error("ctypes c_void_p converter implementation is not authentic")
    c_int = namespace.get("c_int", _MISSING)
    c_long = namespace.get("c_long", _MISSING)
    if os.name == "nt":
        if c_int is not c_long:
            raise _error("ctypes c_int native conversion anchor is not authentic")
    elif (
        not _is_class_object(c_int)
        or (_raw_class_namespace(c_int) or dict[str, Any]()).get("_type_", _MISSING) != "i"
    ):
        raise _error("ctypes c_int native conversion anchor is not authentic")
    wintypes = sys.modules.get("ctypes.wintypes", _MISSING)
    if type(wintypes) is not ModuleType:
        raise _error("ctypes wintypes module is unavailable")
    if namespace.get("wintypes", _MISSING) is not wintypes:
        raise _error("ctypes wintypes module binding changed")
    wintypes_source = vars(wintypes).get("__file__", _MISSING)
    expected_wintypes_source = _canonical_stdlib_source_path("ctypes.wintypes", "ctypes wintypes")
    if not isinstance(wintypes_source, str) or not _same_path(
        wintypes_source, str(expected_wintypes_source)
    ):
        raise _error("ctypes wintypes source path is not authentic")
    ctypes_source = namespace.get("__file__", _MISSING)
    expected_ctypes_source = _canonical_stdlib_source_path("ctypes", "ctypes source")
    if not isinstance(ctypes_source, str) or not _same_path(
        ctypes_source, str(expected_ctypes_source)
    ):
        raise _error("ctypes source path is not authentic")
    if vars(wintypes).get("HMODULE", _MISSING) is not c_void_p:
        raise _error("ctypes wintypes HMODULE binding is not authentic")
    py_object = namespace.get("py_object", _MISSING)
    py_object_mro = (
        type.__getattribute__(py_object, "__mro__") if type(py_object) is type(simple_data) else ()
    )
    if (
        type(py_object) is not type(simple_data)
        or simple_data not in py_object_mro
        or (_raw_class_namespace(py_object) or dict[str, Any]()).get("_type_", _MISSING) != "O"
    ):
        raise _error("ctypes py_object native conversion anchor is not authentic")
    return native


def _establish_ctypes_baseline() -> None:
    global _CTYPES_INTERNAL_ARGTYPES
    global _CTYPES_INTERNAL_RESTYPE
    global _CTYPES_WINTYPES_MODULE
    _validate_ctypes_native_anchors()
    _CTYPES_WINTYPES_MODULE = sys.modules["ctypes.wintypes"]
    _CTYPES_INTERNAL_ARGTYPES = (
        vars(ctypes)["c_void_p"],
        vars(ctypes)["py_object"],
        vars(ctypes)["py_object"],
    )
    _CTYPES_INTERNAL_RESTYPE = vars(ctypes)["py_object"]
    if _CTYPES_EXPECTED_BINDINGS:
        return
    namespace = vars(ctypes)
    if type(_CTYPES_WINTYPES_MODULE) is not ModuleType:
        raise _error("ctypes wintypes module is unavailable")
    for name in (
        "cast",
        "create_unicode_buffer",
        "sizeof",
        "POINTER",
        "CDLL",
        "WinDLL",
        "_CFuncPtr",
        "_cast",
        "_check_HRESULT",
        "_dlopen",
        *_CTYPES_TYPE_NAMES,
    ):
        value = namespace.get(name, _MISSING)
        if value is not _MISSING:
            _CTYPES_EXPECTED_BINDINGS[f"ctypes.{name}"] = value
    for name in _CTYPES_WINTYPE_NAMES:
        value = vars(_CTYPES_WINTYPES_MODULE).get(name, _MISSING)
        if value is not _MISSING:
            _CTYPES_EXPECTED_BINDINGS[f"ctypes.wintypes.{name}"] = value
    for name in ("cast", "create_unicode_buffer", "sizeof", "POINTER", "CDLL", "_cast"):
        if _CTYPES_EXPECTED_BINDINGS.get(f"ctypes.{name}", _MISSING) is _MISSING:
            raise _error(f"ctypes {name} binding is unavailable")
    if (
        _CTYPES_EXPECTED_BINDINGS.get("ctypes.c_void_p", _MISSING) is _MISSING
        or _CTYPES_EXPECTED_BINDINGS.get("ctypes.py_object", _MISSING) is _MISSING
    ):
        raise _error("ctypes cast conversion bindings are unavailable")
    for key, value in _CTYPES_EXPECTED_BINDINGS.items():
        if key.startswith("ctypes.wintypes.") or key.rpartition(".")[2] not in _CTYPES_TYPE_NAMES:
            continue
        if type(value) is not type and type(type(value)) is not type:
            raise _error(f"ctypes {key} type is unavailable")
        _CTYPES_EXPECTED_TYPE_STATES[id(value)] = _capture_ctypes_type_state(cast(type, value), key)
    cdll = _CTYPES_EXPECTED_BINDINGS["ctypes.CDLL"]
    known_types = [object, type, cast(type, cdll)]
    cfunc_ptr = _CTYPES_EXPECTED_BINDINGS.get("ctypes._CFuncPtr", _MISSING)
    if cfunc_ptr is not _MISSING:
        known_types.append(cast(type, cfunc_ptr))
    win_dll = _CTYPES_EXPECTED_BINDINGS.get("ctypes.WinDLL", _MISSING)
    if win_dll is not _MISSING:
        known_types.append(cast(type, win_dll))
    for known_type in known_types:
        if type(type(known_type)) is not type:
            raise _error("ctypes dispatch baseline uses an unsupported type")
        for name in _CTYPES_EXPECTED_DISPATCH_NAMES:
            _CTYPES_EXPECTED_DISPATCH[(known_type, name)] = _raw_type_descriptor(known_type, name)


def _validate_ctypes_type(value: object, label: str) -> None:
    state = _CTYPES_EXPECTED_TYPE_STATES.get(id(value))
    if state is None:
        state = _CTYPES_DYNAMIC_TYPE_STATES.get(id(value))
    if state is None or state.value is not value:
        raise _error(f"ctypes {label} conversion type is not authenticated")
    if type(value) is not state.metaclass:
        raise _error(f"ctypes {label} conversion type metaclass changed")
    for name, expected in state.value_descriptors.items():
        current = _raw_type_descriptor(value, name)
        if current[0] is not expected[0] or current[1] is not expected[1]:
            raise _error(f"ctypes {label} conversion descriptor changed")
    for name, expected in state.metaclass_descriptors.items():
        current = _raw_type_descriptor(state.metaclass, name)
        if current[0] is not expected[0] or current[1] is not expected[1]:
            raise _error(f"ctypes {label} conversion metaclass changed")


def _validate_ctypes_conversion_value(value: object, label: str) -> None:
    if value is None:
        return
    if type(value) is tuple or type(value) is list:
        for index, item in enumerate(value):
            _validate_ctypes_conversion_value(item, f"{label}[{index}]")
        return
    _validate_ctypes_type(value, label)


def _validate_ctypes_baseline() -> None:
    if vars(ctypes).get("wintypes", _MISSING) is not _CTYPES_WINTYPES_MODULE:
        raise _error("ctypes wintypes module binding changed")
    for key, expected in _CTYPES_EXPECTED_BINDINGS.items():
        if key.startswith("ctypes.wintypes."):
            current = vars(_CTYPES_WINTYPES_MODULE).get(key.rpartition(".")[2], _MISSING)
        else:
            current = vars(ctypes).get(key.partition(".")[2], _MISSING)
        if current is not expected:
            raise _error(f"ctypes binding {key} changed before admission")
    for state in _CTYPES_EXPECTED_TYPE_STATES.values():
        _validate_ctypes_type(state.value, "baseline")
    for (value_type, name), expected in _CTYPES_EXPECTED_DISPATCH.items():
        current = _raw_type_descriptor(value_type, name)
        if current[0] is not expected[0] or current[1] is not expected[1]:
            raise _error(f"ctypes {name} dispatch baseline changed")


def _ctypes_bootstrap_dispatch_expectations(
    cdll: type,
    win_dll: object,
    descriptor_specs: dict[str, tuple[object, FunctionType, CodeType]],
) -> dict[tuple[type, str], tuple[type, object]]:
    object_namespace = vars(object)
    object_getattribute = object_namespace.get("__getattribute__", _MISSING)
    object_setattr = object_namespace.get("__setattr__", _MISSING)
    if object_getattribute is _MISSING or object_setattr is _MISSING:
        raise _error("ctypes object dispatch baseline is unavailable")
    expectations: dict[tuple[type, str], tuple[type, object]] = {
        (cdll, "__getattribute__"): (object, object_getattribute),
        (cdll, "__setattr__"): (object, object_setattr),
    }
    for name in ("__getattr__", "__getitem__", "__init__"):
        descriptor = descriptor_specs[name][0]
        owner, current = _raw_type_descriptor(cdll, name)
        if owner is not cdll or current is not descriptor:
            raise _error(f"ctypes CDLL {name} dispatch baseline changed")
        expectations[(cdll, name)] = (cdll, descriptor)
    for dispatch_key, expected in expectations.copy().items():
        owner, descriptor = _raw_type_descriptor(dispatch_key[0], dispatch_key[1])
        if owner is not expected[0] or descriptor is not expected[1]:
            raise _error(
                f"ctypes {dispatch_key[0].__name__} {dispatch_key[1]} dispatch baseline changed"
            )

    if win_dll is not _MISSING:
        if not _is_class_object(win_dll):
            raise _error("ctypes WinDLL type is unavailable")
        win_namespace = vars(win_dll)
        for name in (
            "__getattribute__",
            "__setattr__",
            "__getattr__",
            "__getitem__",
            "__init__",
        ):
            if win_namespace.get(name, _MISSING) is not _MISSING:
                raise _error(f"ctypes WinDLL {name} dispatch shadow is unsupported")
            expected = expectations[(cdll, name)]
            owner, descriptor = _raw_type_descriptor(win_dll, name)
            if owner is not expected[0] or descriptor is not expected[1]:
                raise _error(f"ctypes WinDLL {name} dispatch baseline changed")
            expectations[(win_dll, name)] = expected
    return expectations


def _ctypes_internal_cast_signature(
    value: object, name: str
) -> tuple[tuple[type, object, type, object, type, object], tuple[object, ...]]:
    dispatch = _ctypes_dispatch_descriptor(value, name)
    argtypes = _ctypes_metadata_value(value, name, "argtypes")
    if type(argtypes) is not tuple or len(argtypes) != len(_CTYPES_INTERNAL_ARGTYPES):
        raise _error(f"{name} has unsupported argument conversion metadata")
    if any(actual is not expected for actual, expected in zip(argtypes, _CTYPES_INTERNAL_ARGTYPES)):
        raise _error(f"{name} has unsupported argument conversion metadata")
    _validate_ctypes_conversion_value(argtypes, f"{name}.argtypes")
    restype = _ctypes_metadata_value(value, name, "restype")
    if restype is not _CTYPES_INTERNAL_RESTYPE:
        raise _error(f"{name} has unsupported result conversion metadata")
    _validate_ctypes_conversion_value(restype, f"{name}.restype")
    errcheck = _ctypes_metadata_value(value, name, "errcheck")
    if errcheck is not None:
        raise _error(f"{name} errcheck is not absent")
    return dispatch, (
        _value_signature(_CTYPES_INTERNAL_ARGTYPES),
        _value_signature(_CTYPES_INTERNAL_RESTYPE),
        _value_signature(None),
    )


def _build_ctypes_trust() -> None:
    global _CTYPES_NAMESPACE
    global _CTYPES_SOURCE_IDENTITY
    global _CTYPES_SOURCE_PATH
    global _CTYPES_INTERNAL_DISPATCH
    global _CTYPES_INTERNAL_SIGNATURE

    _establish_dependency_object_baseline()
    _establish_ctypes_baseline()
    _validate_ctypes_baseline()
    namespace = vars(ctypes)
    source_path = _canonical_stdlib_source_path("ctypes", "ctypes source")
    source_identity = _file_identity(source_path, "ctypes source")
    source = _read_file_bounded(source_path, _MAX_SOURCE_BYTES, "ctypes source")
    compiled = _compile_source_bytes(source, source_path)
    cdll = namespace.get("CDLL", _MISSING)
    if cdll is not _CTYPES_EXPECTED_BINDINGS.get("ctypes.CDLL", _MISSING):
        raise _error("ctypes CDLL type is unavailable")
    win_dll = namespace.get("WinDLL", _MISSING)
    if win_dll is not _MISSING and win_dll is not _CTYPES_EXPECTED_BINDINGS.get(
        "ctypes.WinDLL", _MISSING
    ):
        raise _error("ctypes WinDLL type is unavailable")

    definitions: dict[str, tuple[FunctionType, CodeType]] = {}
    for public_name, qualname in (
        ("cast", "cast"),
        ("create_unicode_buffer", "create_unicode_buffer"),
    ):
        function = namespace.get(public_name, _MISSING)
        expected = _code_object(
            compiled,
            qualname,
            function.__code__.co_firstlineno if type(function) is FunctionType else -1,
        )
        if type(function) is not FunctionType or expected is None:
            raise _error(f"ctypes {public_name} implementation is unavailable")
        definitions[public_name] = (function, expected)
    descriptor_specs: dict[str, tuple[object, FunctionType, CodeType]] = {}
    for method_name in ("__getattr__", "__getitem__", "__init__"):
        owner, descriptor = _raw_type_descriptor(cast(type, cdll), method_name)
        if owner is not cdll or type(descriptor) is not FunctionType:
            raise _error(f"ctypes CDLL {method_name} implementation is unavailable")
        function = descriptor
        expected = _code_object(compiled, function.__qualname__, function.__code__.co_firstlineno)
        if expected is None:
            raise _error(f"ctypes CDLL {method_name} source implementation is unavailable")
        descriptor_specs[method_name] = (descriptor, function, expected)
    dispatch_expectations = _ctypes_bootstrap_dispatch_expectations(cdll, win_dll, descriptor_specs)
    internal_cast = namespace.get("_cast", _MISSING)
    if not _is_ctypes_callable(internal_cast):
        raise _error("ctypes internal cast implementation is unavailable")
    internal_dispatch, internal_signature = _ctypes_internal_cast_signature(
        internal_cast, "ctypes._cast"
    )

    _CTYPES_NAMESPACE = namespace
    _CTYPES_SOURCE_PATH = source_path
    _CTYPES_SOURCE_IDENTITY = source_identity
    _CTYPES_TRUSTED_FUNCTIONS.clear()
    _CTYPES_TRUSTED_FUNCTIONS_BY_NAME.clear()
    _CTYPES_TRUSTED_DESCRIPTORS.clear()
    _CTYPES_TRUSTED_TYPES.clear()
    _CTYPES_PERMITTED_DESCRIPTORS.clear()
    _CTYPES_DISPATCH_EXPECTATIONS.clear()
    _CTYPES_BINDINGS.clear()
    _CTYPES_DISPATCH_EXPECTATIONS.update(dispatch_expectations)
    _CTYPES_INTERNAL_DISPATCH = internal_dispatch
    _CTYPES_INTERNAL_SIGNATURE = internal_signature
    for type_state in (
        *_CTYPES_EXPECTED_TYPE_STATES.values(),
        *_CTYPES_DYNAMIC_TYPE_STATES.values(),
    ):
        _CTYPES_TRUSTED_TYPES[id(type_state.value)] = type_state.value
    for name, (function, expected) in definitions.items():
        state = _function_state(
            function,
            capture_globals=True,
            code=expected,
            code_digest=_code_digest(expected),
            source_path=source_path,
            source_identity=source_identity,
        )
        if state.globals_dict is not namespace:
            raise _error(f"ctypes {name} globals are not its module namespace")
        _CTYPES_TRUSTED_FUNCTIONS[id(function)] = state
        _CTYPES_TRUSTED_FUNCTIONS_BY_NAME[name] = state
    for name, (descriptor, function, expected) in descriptor_specs.items():
        state = _function_state(
            function,
            capture_globals=True,
            code=expected,
            code_digest=_code_digest(expected),
            source_path=source_path,
            source_identity=source_identity,
        )
        if state.globals_dict is not namespace:
            raise _error(f"ctypes CDLL {name} globals are not its module namespace")
        _CTYPES_TRUSTED_FUNCTIONS[id(function)] = state
        _CTYPES_TRUSTED_FUNCTIONS_BY_NAME[f"CDLL.{name}"] = state
        _CTYPES_TRUSTED_DESCRIPTORS[(cdll, name)] = (descriptor, state)

    for name in (
        "cast",
        "create_unicode_buffer",
        "sizeof",
        "POINTER",
        "CDLL",
        "WinDLL",
        "_cast",
        "c_void_p",
        "c_int",
    ):
        value = _CTYPES_EXPECTED_BINDINGS.get(f"ctypes.{name}", _MISSING)
        if value is not _MISSING:
            _CTYPES_BINDINGS[name] = value
    for (_known_type, name), (owner, descriptor) in _CTYPES_EXPECTED_DISPATCH.items():
        if descriptor is _MISSING:
            continue
        existing = _CTYPES_PERMITTED_DESCRIPTORS.get((owner, name))
        if existing is not None and existing is not descriptor:
            raise _error(f"ctypes {name} has conflicting descriptors")
        _CTYPES_PERMITTED_DESCRIPTORS[(owner, name)] = descriptor


def _validate_ctypes_source() -> None:
    if _CTYPES_NAMESPACE is None or _CTYPES_SOURCE_PATH is None or _CTYPES_SOURCE_IDENTITY is None:
        raise _error("ctypes trust state is unavailable")
    current_source = vars(ctypes).get("__file__", _MISSING)
    if not isinstance(current_source, str) or not _same_path(
        current_source, str(_CTYPES_SOURCE_PATH)
    ):
        raise _error("ctypes source path changed")
    current_identity = _file_identity(_CTYPES_SOURCE_PATH, "ctypes source")
    _compare_file_records(_CTYPES_SOURCE_IDENTITY, current_identity, "ctypes source")


def _validate_ctypes_dispatch_expectations() -> None:
    for (value_type, name), (
        expected_owner,
        expected_descriptor,
    ) in _CTYPES_DISPATCH_EXPECTATIONS.items():
        owner, descriptor = _raw_type_descriptor(value_type, name)
        if owner is not expected_owner or descriptor is not expected_descriptor:
            raise _error(f"ctypes {value_type.__name__} {name} dispatch changed")
        trusted = _CTYPES_TRUSTED_DESCRIPTORS.get((owner, name))
        if trusted is not None:
            trusted_descriptor, function_state = trusted
            if trusted_descriptor is not descriptor:
                raise _error(f"ctypes {value_type.__name__} {name} descriptor changed")
            _validate_function_state(function_state, f"ctypes {value_type.__name__}.{name}")
        elif _CTYPES_PERMITTED_DESCRIPTORS.get((expected_owner, name)) is not descriptor:
            raise _error(f"ctypes {value_type.__name__} {name} descriptor is not permitted")


def _validate_ctypes_function(name: str) -> None:
    _validate_ctypes_source()
    state = _CTYPES_TRUSTED_FUNCTIONS_BY_NAME.get(name)
    if state is None or _CTYPES_NAMESPACE is None:
        raise _error(f"ctypes {name} trust state is unavailable")
    function: object
    if name.startswith("CDLL."):
        method_name = name.partition(".")[2]
        cdll = _CTYPES_NAMESPACE.get("CDLL", _MISSING)
        if not _is_class_object(cdll):
            raise _error("ctypes CDLL type changed")
        owner, descriptor = _raw_type_descriptor(cdll, method_name)
        trusted = _CTYPES_TRUSTED_DESCRIPTORS.get((cdll, method_name))
        functions = _descriptor_functions(descriptor)
        if (
            trusted is None
            or owner is not cdll
            or descriptor is not trusted[0]
            or len(functions) != 1
            or functions[0] is not state.function
        ):
            raise _error(f"ctypes CDLL {method_name} descriptor changed")
        function = functions[0]
    else:
        function = _CTYPES_NAMESPACE.get(name, _MISSING)
        if function is not state.function:
            raise _error(f"ctypes {name} implementation changed")
    if type(function) is not FunctionType or function.__globals__ is not _CTYPES_NAMESPACE:
        raise _error(f"ctypes {name} globals changed")
    _validate_function_state(state, name)


def _validate_ctypes_dependencies() -> None:
    _validate_ctypes_source()
    _validate_ctypes_dispatch_expectations()
    for name in (
        "CDLL.__getattr__",
        "CDLL.__getitem__",
        "CDLL.__init__",
        "cast",
        "create_unicode_buffer",
    ):
        _validate_ctypes_function(name)
    if _CTYPES_NAMESPACE is None:
        raise _error("ctypes trust state is unavailable")
    for name, expected in _CTYPES_BINDINGS.items():
        if _CTYPES_NAMESPACE.get(name, _MISSING) is not expected:
            raise _error(f"ctypes binding {name} changed")
    internal_cast = _CTYPES_NAMESPACE.get("_cast", _MISSING)
    if not _is_ctypes_callable(internal_cast):
        raise _error("ctypes internal cast dispatch or metadata changed")
    current_dispatch, current_signature = _ctypes_internal_cast_signature(
        internal_cast, "ctypes._cast"
    )
    if (
        _CTYPES_INTERNAL_DISPATCH is None
        or current_dispatch != _CTYPES_INTERNAL_DISPATCH
        or _CTYPES_INTERNAL_SIGNATURE is None
        or current_signature != _CTYPES_INTERNAL_SIGNATURE
    ):
        raise _error("ctypes internal cast dispatch or metadata changed")


def _is_ctypes_callable(value: object) -> bool:
    if not _is_class_object(_CFUNC_PTR_TYPE):
        return False
    value_type = type(value)
    if not _is_class_object(value_type):
        return False
    try:
        return _CFUNC_PTR_TYPE in type.__getattribute__(value_type, "__mro__")
    except (AttributeError, TypeError):
        return False


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
    attribute_owner, attribute_descriptor = _raw_type_descriptor(value_type, "__getattribute__")
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
        expected_owner, expected_descriptor = _CFUNC_PTR_METADATA.get(attribute, (None, _MISSING))
        metadata_owner, metadata_descriptor = _raw_type_descriptor(value_type, attribute)
        if metadata_owner is not expected_owner or metadata_descriptor is not expected_descriptor:
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
    _validate_ctypes_dependencies()
    cast_state = _CTYPES_TRUSTED_FUNCTIONS_BY_NAME.get("cast")
    cast_function = None if cast_state is None else cast_state.function
    c_void_p = _CTYPES_BINDINGS.get("c_void_p", _MISSING)
    if type(cast_function) is not FunctionType or c_void_p is _MISSING:
        raise _error(f"cached native callable {name} ctypes cast binding is unavailable")
    try:
        pointer = cast_function(value, c_void_p).value
    except (AttributeError, OSError, TypeError, ValueError, OverflowError) as exc:
        raise _error(f"cached native callable {name} has no address") from exc
    if type(pointer) is not int or pointer <= 0:
        raise _error(f"cached native callable {name} has an invalid address")
    return pointer


def _ctypes_metadata_value(value: object, name: str, attribute: str) -> object:
    value_type = type(value)
    expected_owner, expected_descriptor = _CFUNC_PTR_METADATA.get(attribute, (None, _MISSING))
    owner, descriptor = _raw_type_descriptor(value_type, attribute)
    if owner is not expected_owner or descriptor is not expected_descriptor:
        raise _error(f"cached native callable {name} metadata access was overridden")
    if type(descriptor) is not GetSetDescriptorType:
        raise _error(f"cached native callable {name} metadata uses an unsupported descriptor")
    try:
        metadata_descriptor = cast(_DescriptorWithGet, descriptor)
        return metadata_descriptor.__get__(value, value_type)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error(f"cached native callable {name} has malformed call metadata") from exc


def _ctypes_call_signature(value: object, name: str) -> tuple[object, ...]:
    try:
        _ctypes_dispatch_descriptor(value, name)
        argtypes = _ctypes_metadata_value(value, name, "argtypes")
        if isinstance(argtypes, (list, tuple)) and not argtypes:
            argtypes = None
        _validate_ctypes_conversion_value(argtypes, f"{name}.argtypes")
        restype = _ctypes_metadata_value(value, name, "restype")
        _validate_ctypes_conversion_value(restype, f"{name}.restype")
        errcheck = _ctypes_metadata_value(value, name, "errcheck")
        return (
            _value_signature(argtypes),
            _value_signature(restype),
            _value_signature(errcheck),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error(f"cached native callable {name} has malformed call metadata") from exc


def _static_ctypes_value(node: ast.AST) -> object:
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (type(None), bool, int, float, str, bytes)
    ):
        return node.value
    if isinstance(node, ast.Name):
        candidate = _CTYPES_EXPECTED_BINDINGS.get(f"ctypes.{node.id}", _MISSING)
        current = vars(ctypes).get(node.id, _MISSING)
        if candidate is _MISSING or current is not candidate:
            return _MISSING
        _validate_ctypes_type(candidate, f"ctypes.{node.id}")
        return candidate
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id != "ctypes":
            return _MISSING
        candidate = _CTYPES_EXPECTED_BINDINGS.get(f"ctypes.{node.attr}", _MISSING)
        current = vars(ctypes).get(node.attr, _MISSING)
        if candidate is _MISSING or current is not candidate:
            return _MISSING
        _validate_ctypes_type(candidate, f"ctypes.{node.attr}")
        return candidate
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
        elif (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "ctypes"
        ):
            function_name = function.attr
        if function_name == "POINTER":
            pointed_type = _static_ctypes_value(node.args[0])
            if _is_class_object(pointed_type):
                try:
                    pointer_factory = _CTYPES_EXPECTED_BINDINGS.get("ctypes.POINTER", _MISSING)
                    if vars(ctypes).get("POINTER", _MISSING) is not pointer_factory:
                        return _MISSING
                    pointer = pointer_factory(pointed_type)
                    if not _is_class_object(pointer):
                        return _MISSING
                    if id(pointer) not in _CTYPES_DYNAMIC_TYPE_STATES:
                        _CTYPES_DYNAMIC_TYPE_STATES[id(pointer)] = _capture_ctypes_type_state(
                            pointer, "POINTER result"
                        )
                    _validate_ctypes_type(pointer, "POINTER result")
                    return pointer
                except (AttributeError, TypeError, ValueError):
                    return _MISSING
    return _MISSING


def _native_signature_policy(
    source: bytes, native_symbols: frozenset[str]
) -> dict[str, frozenset[tuple[object, ...]]]:
    default: tuple[object, ...] = (
        _value_signature(None),
        _value_signature(ctypes.c_int),
        _value_signature(None),
    )
    permitted: dict[str, set[tuple[object, ...]]] = {name: {default} for name in native_symbols}
    if not native_symbols:
        return {}
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError, TypeError, ValueError) as exc:
        raise _error("Gmsh source native metadata cannot be inspected") from exc

    assignments: list[tuple[int, int, str, str, ast.expr]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr]
        expression: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = node.targets
            expression = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            expression = node.value
        else:
            continue
        if expression is None:
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
                (target.lineno, target.col_offset, owner.attr, target.attr, expression)
            )
    if len(assignments) > _MAX_LIVE_MEMBERS:
        raise _error("Gmsh native metadata exceeds the finite verification limit")
    assignments.sort(key=lambda item: (item[0], item[1]))
    current: dict[str, list[object]] = {name: list(default) for name in native_symbols}
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


_build_ctypes_trust()


def _library_namespace(
    library: object, expected: tuple[type, object] | None = None
) -> dict[str, object]:
    owner, descriptor = _raw_type_descriptor(type(library), "__dict__")
    if expected is not None and (owner is not expected[0] or descriptor is not expected[1]):
        raise _error("Gmsh native library namespace descriptor changed")
    if type(descriptor) is not GetSetDescriptorType:
        raise _error("Gmsh native library namespace uses an unsupported descriptor")
    try:
        namespace_descriptor = cast(_DescriptorWithGet, descriptor)
        namespace = namespace_descriptor.__get__(library, type(library))
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


def _library_handle(library: object, expected: tuple[type, object] | None = None) -> int:
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
        windll = _CTYPES_BINDINGS.get("WinDLL", _MISSING)
        if windll is _MISSING:
            raise _error("ctypes Windows bindings are unavailable")
        api = cast(Callable[..., Any], windll)("kernel32", use_last_error=True)
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
    if type(value) is FunctionType:
        return _NativeCallableState(
            value,
            type(value),
            _function_state(value, capture_globals=True),
            None,
            None,
            None,
            None,
            None,
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
    library_instance_bindings: dict[str, _InstanceBindingState]
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
    source_dependencies: tuple[_ExecutableAttributeState, ...]


def _capture_live_state(
    module: ModuleType,
    library: object,
    source: bytes,
    native_handle: int,
    source_dependencies: tuple[_ExecutableAttributeState, ...],
) -> _LiveState:
    _validate_ctypes_dependencies()
    _validate_source_dependencies(module, source_dependencies)
    native_symbols = _native_symbol_names(source)
    library_namespace_owner, library_namespace_descriptor = _raw_type_descriptor(
        type(library), "__dict__"
    )
    namespace = _library_namespace(
        library,
        expected=(library_namespace_owner, library_namespace_descriptor),
    )
    library_instance_bindings = _capture_instance_bindings(namespace, "Gmsh native library")
    library_handle_owner, library_handle_descriptor = _library_handle_descriptor(library)
    library_funcptr_owner, library_funcptr_descriptor = _raw_type_descriptor(
        type(library), "_FuncPtr"
    )
    library_funcptr = _factory_state(namespace.get("_FuncPtr", _MISSING))
    module_dispatch = _attribute_dispatch_state(module, ("__getattribute__", "__getattr__"))
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
        if type(value) is FunctionType and value.__module__ == module_name:
            module_functions[name] = _function_state(value, capture_globals=True)
            functions.append(module_functions[name])
        elif _is_class_object(value):
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
            function_state = (
                _function_dependency_state(
                    value,
                    dependency_depth=0,
                    dependency_seen=frozenset(),
                )
                if type(value) is FunctionType
                else None
            )
            global_states[name] = _GlobalState(
                value,
                _stable_global_signature(value),
                _MISSING,
                None,
                function_state,
            )
        else:
            builtin = _builtin_binding(module, name)
            global_states[name] = _GlobalState(
                _MISSING,
                None,
                builtin,
                _stable_global_signature(builtin),
                (
                    _function_dependency_state(
                        builtin,
                        dependency_depth=0,
                        dependency_seen=frozenset(),
                    )
                    if type(builtin) is FunctionType
                    else None
                ),
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
        library_instance_bindings=library_instance_bindings,
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
        source_dependencies=source_dependencies,
    )


def _lookup_class_path(module: ModuleType, path: tuple[str, ...]) -> object:
    current: object = vars(module).get(path[0], _MISSING)
    for name in path[1:]:
        if not _is_class_object(current):
            return _MISSING
        namespace = _raw_class_namespace(current)
        if namespace is None:
            return _MISSING
        current = namespace.get(name, _MISSING)
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
        if type(current) is not FunctionType:
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


def _validate_attribute_dispatch(state: _AttributeDispatchState, value: object, label: str) -> None:
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
    if name in {"__new__", "__init__"} and state.functions:
        current = _generic_dispatch_descriptor_state(value_type, name)
    else:
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
    _validate_source_dependencies(module, state.source_dependencies)
    _validate_attribute_dispatch(state.module_dispatch, module, "module")
    _validate_attribute_dispatch(state.library_dispatch, library, "native library")
    current_library_namespace = _library_namespace(
        library,
        expected=(state.library_namespace_owner, state.library_namespace_descriptor),
    )
    if current_library_namespace is not state.library_namespace:
        raise _error("live Gmsh native library namespace changed")
    _validate_instance_bindings(
        state.library_instance_bindings,
        current_library_namespace,
        "native library",
    )
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
            current_members = _raw_class_namespace(class_state.class_object)
        except TypeError as exc:
            raise _error(f"live Gmsh class {'.'.join(path)} cannot be inspected") from exc
        if current_members is None:
            raise _error(f"live Gmsh class {'.'.join(path)} cannot be inspected")
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
            if (
                global_state.builtin_signature is not None
                and _stable_global_signature(builtin) != global_state.builtin_signature
            ):
                raise _error(f"live Gmsh builtin {name} changed")
        else:
            if current is not global_state.value:
                raise _error(f"live Gmsh global {name} was replaced")
            if (
                global_state.stable_signature is not None
                and _stable_global_signature(current) != global_state.stable_signature
            ):
                raise _error(f"live Gmsh global {name} changed")

    if (
        _library_handle(
            library,
            expected=(state.library_handle_owner, state.library_handle_descriptor),
        )
        != state.native_handle
    ):
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
        if (
            not isinstance(name, str)
            or name not in state.native_symbols
            or name in state.native_callables
        ):
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
    if loaded_spec is None:
        raise _error("executing Gmsh module import spec changed")
    if (
        getattr(loaded_spec, "name", None) != "gmsh"
        or getattr(loaded_spec, "submodule_search_locations", None) is not None
        or not _same_path(str(getattr(spec, "origin", "")), str(getattr(loaded_spec, "origin", "")))
    ):
        raise _error("executing Gmsh module import spec changed")
    loader = getattr(module, "__loader__", None)
    if not isinstance(loader, _SourceOnlyLoader):
        raise _error("executing Gmsh module code identity changed")
    if (
        getattr(loaded_spec, "loader", None) is not loader
        or cast(_SourceOnlyLoader, loader)._verified_code_digest != expected_code_digest
        or (
            expected_code is not None
            and cast(_SourceOnlyLoader, loader)._verified_code is not expected_code
        )
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
                raise TypeError("runtime identity module/library records are malformed")
            module_path = _resolve_regular_file(module_record["path"], "Gmsh Python module")
            library_path = _resolve_regular_file(library_record["path"], "Gmsh native library")
            with ExitStack() as admission:
                _pinned_file(admission, library_path, "Gmsh native library")
                spec = _find_gmsh_spec(module_path)
                snapshot = _source_snapshot(module_record, admission)
                cache_path, cache_identity = _validate_cache(module_path, spec, snapshot, admission)
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
    verified_spec = importlib.util.spec_from_file_location("gmsh", str(module_path), loader=loader)
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
    _validate_ctypes_dependencies()
    expected = _normalise_binding(binding, "expected runtime")
    with _CACHE_LOCK:
        preloaded = sys.modules.get("gmsh")
        if type(preloaded) is ModuleType:
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
            raise TypeError("runtime identity module/library records are malformed")
        module_path = _resolve_regular_file(module_record["path"], "Gmsh Python module")
        library_path = _resolve_regular_file(library_record["path"], "Gmsh native library")
        with ExitStack() as admission:
            _pinned_file(admission, library_path, "Gmsh native library")
            actual_module = _find_gmsh_spec(module_path)
            snapshot = _source_snapshot(module_record, admission)
            cache_path, cache_identity = _validate_cache(
                module_path, actual_module, snapshot, admission
            )
            source_dependencies = _capture_source_dependencies(snapshot.content, snapshot.code)
            module = _import_verified_source(module_path, actual_module, snapshot.code)
            code_digest = snapshot.code_digest
            try:
                _validate_loaded_module(
                    module, module_path, actual_module, code_digest, snapshot.code
                )
                _verify_source_snapshot_current(snapshot)
                _validate_source_dependencies(module, source_dependencies)
                library_object, handle = _module_handle(module)
                live_state = _capture_live_state(
                    module,
                    library_object,
                    snapshot.content,
                    handle,
                    source_dependencies,
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
