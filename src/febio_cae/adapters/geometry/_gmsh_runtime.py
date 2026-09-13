"""Verified runtime boundary for the installed Gmsh Python distribution.

The public geometry producers capture a binding in the parent process and pass
that record to their isolated child. This module deliberately does not import
Gmsh while capturing the binding. The child imports the source module only
after checking the interpreter, import resolution, source/cache and process
runtime identities; the Gmsh native handle is then resolved to the file that
is actually mapped by Windows.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
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
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, ModuleType
from typing import Any

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


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
    except OSError as exc:
        raise _error(f"{label} file cannot be read") from exc
    if len(content) > limit:
        raise _error(f"{label} file exceeds the finite verification limit")
    return content


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


def _cache_signature(path: Path) -> tuple[bool, int, int] | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error("Gmsh module cache cannot be inspected") from exc
    if not stat.S_ISREG(info.st_mode):
        raise _error("Gmsh module cache is not regular")
    if getattr(info, "st_file_attributes", 0) & getattr(
        ctypes.wintypes, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        raise _error("Gmsh module cache is a reparse point")
    return True, info.st_size, getattr(info, "st_mtime_ns", 0)


def _compile_source(module_path: Path) -> CodeType:
    source = _read_bounded(module_path, _MAX_SOURCE_BYTES, "Gmsh Python module")
    try:
        return compile(source, str(module_path), "exec", dont_inherit=True, optimize=-1)
    except (SyntaxError, TypeError, ValueError) as exc:
        raise _error("Gmsh Python module source cannot be compiled") from exc


def _validate_cache(module_path: Path, spec: Any) -> tuple[Path, tuple[bool, int, int] | None]:
    cache = _cache_path(module_path, spec)
    signature = _cache_signature(cache)
    if signature is None:
        return cache, None
    if signature[1] > _MAX_CACHE_BYTES:
        raise _error("Gmsh module cache exceeds the finite verification limit")
    content = _read_bounded(cache, _MAX_CACHE_BYTES, "Gmsh module cache")
    if len(content) < 16 or content[:4] != importlib.util.MAGIC_NUMBER:
        raise _error("Gmsh module cache has an invalid header")
    flags = int.from_bytes(content[4:8], "little")
    if flags & ~0x03:
        raise _error("Gmsh module cache has unsupported flags")
    source = _read_bounded(module_path, _MAX_SOURCE_BYTES, "Gmsh Python module")
    if flags & 1:
        try:
            source_hash = importlib.util.source_hash(source)
        except (AttributeError, TypeError, ValueError) as exc:
            raise _error("Gmsh module source hash cannot be calculated") from exc
        if content[8:16] != source_hash:
            raise _error("Gmsh module cache is stale")
    else:
        try:
            info = module_path.stat()
        except OSError as exc:
            raise _error("Gmsh Python module cannot be inspected") from exc
        if content[8:12] != (int(info.st_mtime) & 0xFFFFFFFF).to_bytes(4, "little"):
            raise _error("Gmsh module cache is stale")
        if content[12:16] != (info.st_size & 0xFFFFFFFF).to_bytes(4, "little"):
            raise _error("Gmsh module cache is stale")
    try:
        cached_code = marshal.loads(content[16:])
    except (EOFError, ValueError, TypeError) as exc:
        raise _error("Gmsh module cache code is malformed") from exc
    if not isinstance(cached_code, CodeType):
        raise _error("Gmsh module cache does not contain module code")
    source_code = _compile_source(module_path)
    try:
        same_code = (
            marshal.dumps(cached_code) == content[16:]
            and marshal.dumps(source_code) == marshal.dumps(cached_code)
        )
    except (ValueError, TypeError) as exc:
        raise _error("Gmsh module cache code cannot be compared") from exc
    if not same_code:
        raise _error("Gmsh module cache code differs from the verified source")
    return cache, signature


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Use normal import machinery while never executing a cached code object."""

    def get_code(self, fullname: str) -> CodeType:
        del fullname
        return _compile_source(Path(self.get_filename("gmsh")))


def _find_gmsh_spec(module_path: Path) -> Any:
    try:
        spec = importlib.util.find_spec("gmsh")
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
        library = getattr(module, "lib")
        handle = getattr(library, "_handle")
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error("Gmsh native handle is unavailable") from exc
    if type(handle) is not int or handle <= 0:
        raise _error("Gmsh native handle is not a positive integer")
    _coerce_module_handle(handle)
    return library, handle


def _stat_signature(record: dict[str, object], label: str) -> tuple[str, int, int]:
    path = record.get("path")
    if not isinstance(path, str):
        raise _RuntimeBindingError(f"{label} identity path is malformed")
    resolved = _resolve_regular_file(path, label)
    try:
        info = resolved.stat()
    except OSError as exc:
        raise _error(f"{label} identity file cannot be inspected") from exc
    return str(resolved), info.st_size, getattr(info, "st_mtime_ns", 0)


def _binding_signatures(binding: dict[str, object]) -> tuple[tuple[str, str, int, int], ...]:
    result: list[tuple[str, str, int, int]] = []
    for field in ("python", "python_image", "python_library", "module", "library"):
        record = binding[field]
        if not isinstance(record, dict):
            raise ValueError(f"runtime identity {field} record is malformed")
        path, size, mtime = _stat_signature(record, field)
        result.append((field, path, size, mtime))
    cfg = binding["pyvenv_cfg"]
    if cfg is not None:
        if not isinstance(cfg, dict):
            raise ValueError("runtime identity pyvenv_cfg record is malformed")
        path, size, mtime = _stat_signature(cfg, "pyvenv.cfg")
        result.append(("pyvenv_cfg", path, size, mtime))
    return tuple(result)


def _signatures_unchanged(signatures: tuple[tuple[str, str, int, int], ...]) -> bool:
    for _field, path, size, mtime in signatures:
        try:
            info = Path(path).stat()
        except OSError:
            return False
        if info.st_size != size or getattr(info, "st_mtime_ns", 0) != mtime:
            return False
    return True


@dataclass(slots=True)
class _VerifiedSession:
    module: ModuleType
    library: Any
    binding: dict[str, object]
    observed: dict[str, object]
    signatures: tuple[tuple[str, str, int, int], ...]
    cache_path: Path
    cache_signature: tuple[bool, int, int] | None


_CACHE_LOCK = threading.RLock()
_VERIFIED_SESSIONS: list[_VerifiedSession] = []


def _cached_session(
    module: ModuleType | None, binding: dict[str, object]
) -> _VerifiedSession | None:
    if module is None:
        return None
    for session in _VERIFIED_SESSIONS:
        if session.module is module and session.binding == binding:
            if not _signatures_unchanged(session.signatures):
                raise _error(
                    "verified Gmsh runtime identity files changed (module/library/process)"
                )
            current_cache = _cache_signature(session.cache_path)
            if current_cache != session.cache_signature:
                raise _error("Gmsh module cache changed after verification")
            try:
                _library, handle = _module_handle(module)
                mapped = _mapped_module_path(handle)
            except (OSError, TypeError, ValueError) as exc:
                if isinstance(exc, _RuntimeBindingError):
                    raise
                raise _error("verified Gmsh native handle cannot be resolved") from exc
            library_record = session.observed.get("library")
            if not isinstance(library_record, dict) or not _same_path(
                str(mapped), str(library_record.get("path", ""))
            ):
                raise _error("verified Gmsh native library mapping changed")
            return session
    return None


def _import_verified_source(module_path: Path, spec: Any) -> ModuleType:
    loader = _SourceOnlyLoader("gmsh", str(module_path))
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
        for field in _PROCESS_FIELDS:
            expected_value = expected[field]
            observed_value = process[field]
            if expected_value is None or observed_value is None:
                if expected_value is not observed_value:
                    raise _error(f"runtime identity mismatch for {field}")
            else:
                _compare_file_records(
                    expected_value,  # type: ignore[arg-type]
                    observed_value,  # type: ignore[arg-type]
                    field,
                )
        module_record = expected["module"]
        if not isinstance(module_record, dict):
            raise ValueError("runtime identity module record is malformed")
        module_path = _resolve_regular_file(module_record["path"], "Gmsh Python module")
        actual_module = _find_gmsh_spec(module_path)
        cache_path, cache_signature = _validate_cache(module_path, actual_module)
        module = _import_verified_source(module_path, actual_module)
        try:
            if not _same_path(str(module_path), str(getattr(module, "__file__", ""))):
                raise _error("executing Gmsh module has a different source path")
            if getattr(module, "__version__", None) != _GMSH_VERSION:
                raise _error("executing Gmsh module has an unexpected version")
            library_object, handle = _module_handle(module)
            mapped_path = _mapped_module_path(handle)
            actual_module_record = _file_identity(module_path, "executing Gmsh Python module")
            actual_library_record = _file_identity(mapped_path, "mapped Gmsh native library")
            _compare_file_records(module_record, actual_module_record, "module")
            expected_library = expected["library"]
            if not isinstance(expected_library, dict):
                raise ValueError("runtime identity library record is malformed")
            _compare_file_records(expected_library, actual_library_record, "library")
            observed: dict[str, object] = {
                "schema_version": _SCHEMA_VERSION,
                **process,
                "module": actual_module_record,
                "library": actual_library_record,
            }
            verify_runtime_identity(expected, observed)
            signatures = _binding_signatures(expected)
        except BaseException:
            if sys.modules.get("gmsh") is module:
                sys.modules.pop("gmsh", None)
            raise
        session = _VerifiedSession(
            module,
            library_object,
            expected,
            observed,
            signatures,
            cache_path,
            cache_signature,
        )
        _VERIFIED_SESSIONS.append(session)
        return module, dict(observed)


__all__ = ["capture_runtime_binding", "load_verified_gmsh", "verify_runtime_identity"]
