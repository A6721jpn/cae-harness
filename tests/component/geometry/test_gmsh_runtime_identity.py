"""Bounded synthetic tests for the installed Gmsh runtime identity boundary."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import py_compile
import sys
import ctypes
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from febio_cae.adapters.geometry import _gmsh_runtime as runtime


def _identity(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _binding(
    root: Path,
    *,
    module: Path | None = None,
    library: Path | None = None,
    pyvenv_cfg: Path | None = None,
) -> dict[str, object]:
    module = module or root / "Lib" / "site-packages" / "gmsh.py"
    library = library or root / "Lib" / "gmsh-4.15.dll"
    launcher = root / "Scripts" / "python.exe"
    image = root / "Python312" / "python.exe"
    python_library = root / "Python312" / "python312.dll"
    return {
        "schema_version": "gmsh-runtime-identity-v1",
        "python": _identity(launcher),
        "python_image": _identity(image),
        "python_library": _identity(python_library),
        "module": _identity(module),
        "library": _identity(library),
        "pyvenv_cfg": None if pyvenv_cfg is None else _identity(pyvenv_cfg),
    }


def _files(root: Path, source: str) -> tuple[Path, Path, Path, Path, Path]:
    paths = (
        root / "Lib" / "site-packages" / "gmsh.py",
        root / "Lib" / "gmsh-4.15.dll",
        root / "Scripts" / "python.exe",
        root / "Python312" / "python.exe",
        root / "Python312" / "python312.dll",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source.encode() if path.name == "gmsh.py" else path.name.encode())
    return paths


def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    current = runtime.sys.flags
    names = (
        "debug",
        "inspect",
        "interactive",
        "optimize",
        "dont_write_bytecode",
        "no_user_site",
        "no_site",
        "ignore_environment",
        "verbose",
        "bytes_warning",
        "quiet",
        "hash_randomization",
        "isolated",
        "dev_mode",
        "utf8_mode",
        "warn_default_encoding",
        "safe_path",
        "int_max_str_digits",
    )
    values = {name: getattr(current, name) for name in names}
    values["isolated"] = 1
    monkeypatch.setattr(runtime.sys, "flags", SimpleNamespace(**values))


def test_capture_runtime_binding_uses_distribution_metadata_without_importing_gmsh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, library, launcher, image, python_library = _files(
        tmp_path, "__version__ = '4.15.2'\n"
    )
    cfg = tmp_path / "pyvenv.cfg"
    cfg.write_text("version = 3.12.10\n", encoding="utf-8")
    entries = [SimpleNamespace(name="gmsh.py"), SimpleNamespace(name="../gmsh-4.15.dll")]

    class Distribution:
        version = "4.15.2"
        files = entries

        @staticmethod
        def locate_file(entry: Any) -> Path:
            return module if entry.name == "gmsh.py" else library

    monkeypatch.setattr(runtime.importlib.metadata, "distribution", lambda name: Distribution())
    monkeypatch.setattr(runtime.sys, "executable", str(launcher))
    monkeypatch.setattr(runtime.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(runtime.sys, "dllhandle", 12, raising=False)
    monkeypatch.setattr(
        runtime,
        "_mapped_module_path",
        lambda handle: image if handle is None else python_library,
    )
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: pytest.fail(f"capture imported {name}"),
    )

    binding = runtime.capture_runtime_binding()

    assert binding == _binding(tmp_path, module=module, library=library, pyvenv_cfg=cfg)


def test_verify_runtime_identity_requires_exact_files_and_normalized_paths(
    tmp_path: Path,
) -> None:
    paths = _files(tmp_path, "__version__ = '4.15.2'\n")
    expected = _binding(tmp_path, pyvenv_cfg=None)
    observed = {
        **expected,
        "python": {**expected["python"], "path": str(paths[2]).upper()},
    }

    runtime.verify_runtime_identity(expected, observed)
    observed["library"] = {**expected["library"], "size": 999}
    with pytest.raises(ValueError, match="runtime identity"):
        runtime.verify_runtime_identity(expected, observed)


@pytest.mark.parametrize("field", ["python_image", "pyvenv_cfg"])
def test_load_verified_gmsh_rejects_process_or_environment_mismatch_before_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
) -> None:
    _isolated(monkeypatch)
    module, library, launcher, image, python_library = _files(
        tmp_path, "__version__ = '4.15.2'\n"
    )
    cfg = tmp_path / "pyvenv.cfg"
    cfg.write_text("version = 3.12.10\n", encoding="utf-8")
    expected = _binding(tmp_path, module=module, library=library, pyvenv_cfg=cfg)
    process = {
        "python": _identity(launcher),
        "python_image": _identity(image),
        "python_library": _identity(python_library),
        "pyvenv_cfg": _identity(cfg),
    }
    if field == "python_image":
        image_record = process["python_image"]
        assert isinstance(image_record, dict)
        image_size = image_record.get("size")
        assert type(image_size) is int
        process[field] = {**image_record, "size": image_size + 1}
    else:
        process[field] = None
    monkeypatch.setattr(runtime, "_current_process_binding", lambda: process)
    monkeypatch.setattr(
        runtime,
        "_find_gmsh_spec",
        lambda path: pytest.fail(f"import resolution reached after {field} mismatch"),
    )
    sys.modules.pop("gmsh", None)

    with pytest.raises((OSError, ValueError), match=field):
        runtime.load_verified_gmsh(expected)


def test_load_verified_gmsh_rejects_changed_module_and_dll_before_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    module, library, launcher, image, python_library = _files(
        tmp_path,
        "events = []\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
        "def isInitialized():\n"
        "    events.append('isInitialized')\n"
        "    return False\n",
    )
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(runtime, "_current_process_binding", lambda: {
        "python": _identity(launcher),
        "python_image": _identity(image),
        "python_library": _identity(python_library),
        "pyvenv_cfg": None,
    })
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)

    loaded, observed = runtime.load_verified_gmsh(expected)

    assert loaded.__version__ == "4.15.2"
    assert loaded.events == []
    assert observed["library"] == expected["library"]
    loaded_again, _ = runtime.load_verified_gmsh(expected)
    assert loaded_again is loaded

    module.write_text("__version__ = '4.15.2'\n", encoding="utf-8")
    with pytest.raises((OSError, ValueError), match="module"):
        runtime.load_verified_gmsh(expected)


def test_load_verified_gmsh_rejects_dll_mapping_mismatch_before_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    module, library, launcher, image, python_library = _files(
        tmp_path,
        "events = []\n__version__ = '4.15.2'\nclass Lib:\n    _handle = 99\nlib = Lib()\n",
    )
    other = tmp_path / "Lib" / "other.dll"
    other.write_bytes(b"other")
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: other)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)

    with pytest.raises((OSError, ValueError), match="library"):
        runtime.load_verified_gmsh(expected)
    assert sys.modules.get("gmsh") is None


def test_load_verified_gmsh_refuses_stale_cache_and_unverified_preload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    module, library, launcher, image, python_library = _files(
        tmp_path,
        "__version__ = '4.15.2'\nclass Lib:\n    _handle = 99\nlib = Lib()\n",
    )
    py_compile.compile(
        str(module), cfile=importlib.util.cache_from_source(str(module)), doraise=True
    )
    cache = Path(importlib.util.cache_from_source(str(module)))
    cache.write_bytes(cache.read_bytes()[:-1] + b"x")
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(runtime, "_current_process_binding", lambda: {
        "python": _identity(launcher),
        "python_image": _identity(image),
        "python_library": _identity(python_library),
        "pyvenv_cfg": None,
    })
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules["gmsh"] = SimpleNamespace(__file__=str(module), __version__="4.15.2")
    with pytest.raises((OSError, ValueError), match="preloaded"):
        runtime.load_verified_gmsh(expected)
    sys.modules.pop("gmsh", None)
    with pytest.raises((OSError, ValueError), match="cache"):
        runtime.load_verified_gmsh(expected)


def test_load_verified_gmsh_refuses_nonisolated_calls_before_import_or_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, library, launcher, image, python_library = _files(
        tmp_path, "__version__ = '4.15.2'\n"
    )
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_find_gmsh_spec",
        lambda path: pytest.fail(f"nonisolated call resolved {path}"),
    )
    monkeypatch.setattr(
        runtime,
        "_cached_session",
        lambda module, binding: pytest.fail("nonisolated call reused a session"),
    )
    with pytest.raises(OSError, match="isolated"):
        runtime.load_verified_gmsh(expected)


def test_load_verified_gmsh_executes_only_the_authenticated_source_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    marker = tmp_path / "changed-source-side-effect.txt"
    source = (
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    original_exec = runtime._SourceOnlyLoader.exec_module

    def unguarded_pin(admission: Any, path: Path, label: str) -> Any:
        del label
        return admission.enter_context(path.open("rb"))

    # Synthetic race injection: the product guard remains covered by the other tests.
    monkeypatch.setattr(runtime, "_pinned_file", unguarded_pin)

    def mutate_before_exec(loader: Any, loaded: Any) -> None:
        module.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('changed', encoding='utf-8')\n"
            + source,
            encoding="utf-8",
        )
        original_exec(loader, loaded)

    monkeypatch.setattr(runtime._SourceOnlyLoader, "exec_module", mutate_before_exec)
    with pytest.raises((OSError, ValueError), match="module"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()
    assert sys.modules.get("gmsh") is None


@pytest.mark.parametrize("mutation", ["module", "cache", "pyvenv", "module_state"])
def test_verified_session_revalidates_exact_reuse_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    _isolated(monkeypatch)
    source = (
        "events = []\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    cfg = tmp_path / "pyvenv.cfg"
    expected = _binding(tmp_path, module=module, library=library)
    process: dict[str, object] = {
        "python": _identity(launcher),
        "python_image": _identity(image),
        "python_library": _identity(python_library),
        "pyvenv_cfg": None,
    }
    monkeypatch.setattr(runtime, "_current_process_binding", lambda: dict(process))
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)
    assert runtime.load_verified_gmsh(expected)[0] is loaded

    if mutation == "module":
        before = module.stat()
        module.write_bytes(source.replace("events = []", "events = ()").encode())
        os.utime(module, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif mutation == "cache":
        py_compile.compile(
            str(module), cfile=importlib.util.cache_from_source(str(module)), doraise=True
        )
        cache = Path(importlib.util.cache_from_source(str(module)))
        before = cache.stat()
        content = cache.read_bytes()
        cache.write_bytes(content[:-1] + bytes((content[-1] ^ 1,)))
        os.utime(cache, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif mutation == "pyvenv":
        cfg.write_text("version = 3.12.10\n", encoding="utf-8")
        process["pyvenv_cfg"] = _identity(cfg)
    else:
        loaded.__version__ = "tampered"

    with pytest.raises((OSError, ValueError), match="runtime|module|cache|code|pyvenv"):
        runtime.load_verified_gmsh(expected)


@pytest.mark.parametrize(
    "mutation", ["module_function", "function_code", "class_method", "lib_callable"]
)
def test_verified_session_rejects_live_executable_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    _isolated(monkeypatch)
    source = (
        "events = []\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    events.append('isInitialized')\n"
        "    return False\n"
        "def replacement():\n"
        "    events.append('replacement')\n"
        "    return False\n"
        "class model:\n"
        "    @staticmethod\n"
        "    def getEntities():\n"
        "        return []\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
        "lib.gmshIsInitialized = isInitialized\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    if mutation == "module_function":
        loaded.isInitialized = loaded.replacement
    elif mutation == "function_code":
        loaded.isInitialized.__code__ = loaded.replacement.__code__
    elif mutation == "class_method":
        loaded.model.getEntities = loaded.replacement
    else:
        loaded.lib.gmshIsInitialized = loaded.replacement

    with pytest.raises((OSError, ValueError), match="live|executable|function|class|native"):
        runtime.load_verified_gmsh(expected)
    assert loaded.events == []


def test_verified_session_preserves_legitimate_module_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    source = (
        "events = []\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    events.append('isInitialized')\n"
        "    return False\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)
    loaded.events.append("legitimate API state")

    reused, _ = runtime.load_verified_gmsh(expected)

    assert reused is loaded
    assert loaded.events == ["legitimate API state"]


def _synthetic_native_callable() -> Any:
    """Create a harmless CFuncPtr for the synthetic native-symbol tests."""

    native = ctypes.CFUNCTYPE(ctypes.c_int)(lambda: 0)
    native.argtypes = None
    return native


def _install_synthetic_native_export(
    monkeypatch: pytest.MonkeyPatch, loaded: Any, name: str, value: Any
) -> None:
    pointer = ctypes.cast(value, ctypes.c_void_p).value
    assert isinstance(pointer, int) and pointer > 0
    setattr(loaded.lib, name, value)
    monkeypatch.setattr(
        runtime, "_native_export_address", lambda library, symbol: pointer
    )


def test_verified_session_rejects_preverification_ctypes_metadata_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The native-symbol premise is synthetic; metadata is poisoned before reuse."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    native.errcheck = lambda result, function, arguments: result
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)

    with pytest.raises((OSError, ValueError), match="metadata|signature|call"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_accepts_legitimate_lazy_native_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A source-referenced symbol may be materialized between verified operations."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)

    reused, _ = runtime.load_verified_gmsh(expected)
    assert reused is loaded


def test_verified_session_rejects_ctypes_dispatch_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The synthetic CFuncPtr type dispatch is part of the native call surface."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)
    runtime.load_verified_gmsh(expected)

    native_type = type(native)

    def replacement(_self: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    monkeypatch.setattr(native_type, "__call__", replacement)
    with pytest.raises((OSError, ValueError), match="dispatch|callable|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_ctypes_setattr_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resolved CFuncPtr cannot use a replacement instance setter."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)
    runtime.load_verified_gmsh(expected)

    def replacement(self: Any, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)

    monkeypatch.setattr(type(native), "__setattr__", replacement)
    with pytest.raises((OSError, ValueError), match="setattr|dispatch|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_unresolved_ctypes_setattr_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unresolved symbol's future CFuncPtr setter is authenticated in advance."""

    _isolated(monkeypatch)
    source = (
        "import ctypes\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "lib = object.__new__(ctypes.CDLL)\n"
        "lib._handle = 99\n"
        "lib._FuncPtr = ctypes.CFUNCTYPE(ctypes.c_int)\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    def replacement(self: Any, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)

    monkeypatch.setattr(loaded.lib._FuncPtr, "__setattr__", replacement)
    with pytest.raises((OSError, ValueError), match="setattr|dispatch|factory|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_ctypes_symbol_name_descriptor_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Native symbol validation cannot execute a replacement _name descriptor."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)
    runtime.load_verified_gmsh(expected)
    monkeypatch.setattr(type(native), "_name", property(lambda self: None), raising=False)

    with pytest.raises((OSError, ValueError), match="name|descriptor|dispatch|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_ctypes_metadata_access_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The synthetic CFuncPtr metadata access path cannot hide live metadata."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)
    runtime.load_verified_gmsh(expected)
    callback = lambda result, function, arguments: result
    native.errcheck = callback

    native_type = type(native)

    def spoofed_getattribute(self: Any, attribute: str) -> Any:
        if attribute == "errcheck":
            return None
        return object.__getattribute__(self, attribute)

    monkeypatch.setattr(native_type, "__getattribute__", spoofed_getattribute)
    assert object.__getattribute__(native, "errcheck") is callback
    with pytest.raises((OSError, ValueError), match="metadata|attribute|dispatch|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_library_attribute_dispatch_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A synthetic CDLL-like library cannot redirect source attribute lookup."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    class RedirectingLib(type(loaded.lib)):
        def __getattribute__(self, attribute: str) -> Any:
            if attribute == "gmshIsInitialized":
                return lambda: True
            return super().__getattribute__(attribute)

    monkeypatch.setattr(loaded.lib, "__class__", RedirectingLib)
    with pytest.raises((OSError, ValueError), match="library|dispatch|attribute|type"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_ctypes_metaclass_mro_spoof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A synthetic CFuncPtr metaclass cannot hide an overridden dispatch MRO."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    native_type = type(native)

    class LyingMeta(type(native_type)):
        def __getattribute__(cls, attribute: str) -> Any:
            if attribute == "__mro__":
                return native_type.__mro__
            return super().__getattribute__(attribute)

    class RedirectedCFunc(native_type, metaclass=LyingMeta):
        _argtypes_ = native_type._argtypes_
        _restype_ = native_type._restype_
        _flags_ = native_type._flags_

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            return None

    native.__class__ = RedirectedCFunc
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)
    with pytest.raises((OSError, ValueError), match="dispatch|metaclass|type|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_library_symbol_descriptor_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A synthetic external CDLL type cannot shadow an authenticated symbol."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "Lib.__module__ = 'ctypes'\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)
    native = _synthetic_native_callable()
    _install_synthetic_native_export(monkeypatch, loaded, "gmshIsInitialized", native)
    runtime.load_verified_gmsh(expected)

    monkeypatch.setattr(
        type(loaded.lib),
        "gmshIsInitialized",
        property(lambda self: None),
        raising=False,
    )
    with pytest.raises((OSError, ValueError), match="library|symbol|attribute|dispatch"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_library_lazy_dispatch_code_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An external-library lazy resolver cannot have its Python body replaced."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "    def __getattr__(self, name):\n"
        "        raise AttributeError(name)\n"
        "Lib.__module__ = 'ctypes'\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)
    resolver = type(loaded.lib).__getattr__

    def replacement(self: Any, name: str) -> Any:
        del self, name
        return None

    monkeypatch.setattr(resolver, "__code__", replacement.__code__)
    with pytest.raises((OSError, ValueError), match="library|function|dispatch|code"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_module_attribute_dispatch_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return False\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    class RedirectingModule(ModuleType):
        def __getattribute__(self, attribute: str) -> Any:
            if attribute == "isInitialized":
                return lambda: True
            return super().__getattribute__(attribute)

    monkeypatch.setattr(loaded, "__class__", RedirectingModule)
    with pytest.raises((OSError, ValueError), match="module|dispatch|attribute|type"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_lazy_factory_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lazy CDLL symbol factory cannot change before first materialization."""

    _isolated(monkeypatch)
    source = (
        "import ctypes\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "lib = object.__new__(ctypes.CDLL)\n"
        "lib._handle = 99\n"
        "lib._FuncPtr = object\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    loaded.lib._FuncPtr = lambda *args: None
    with pytest.raises((OSError, ValueError), match="library|factory|dispatch|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_lazy_factory_constructor_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A retained lazy factory cannot change its constructor before resolution."""

    _isolated(monkeypatch)
    source = (
        "import ctypes\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "class FuncFactory:\n"
        "    def __init__(self, *args):\n"
        "        del args\n"
        "FuncFactory.__module__ = 'ctypes'\n"
        "lib = object.__new__(ctypes.CDLL)\n"
        "lib._handle = 99\n"
        "lib._FuncPtr = FuncFactory\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    constructor = loaded.lib._FuncPtr.__dict__["__init__"]

    def replacement(self: Any, *args: Any) -> None:
        del self, args

    monkeypatch.setattr(constructor, "__code__", replacement.__code__)
    with pytest.raises((OSError, ValueError), match="factory|constructor|dispatch|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_lazy_factory_descriptor_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The library class cannot replace the descriptor for its lazy factory."""

    _isolated(monkeypatch)
    source = (
        "import ctypes\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "lib = object.__new__(ctypes.CDLL)\n"
        "lib._handle = 99\n"
        "lib._FuncPtr = object\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    monkeypatch.setattr(
        type(loaded.lib), "_FuncPtr", property(lambda self: object), raising=False
    )
    with pytest.raises((OSError, ValueError), match="factory|descriptor|dispatch|library"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_library_setattr_dispatch_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lazy CDLL cache cannot use a replacement library setter."""

    _isolated(monkeypatch)
    source = (
        "import ctypes\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "lib = object.__new__(ctypes.CDLL)\n"
        "lib._handle = 99\n"
        "lib._FuncPtr = object\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    def replacement(self: Any, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)

    monkeypatch.setattr(type(loaded.lib), "__setattr__", replacement)
    with pytest.raises((OSError, ValueError), match="setattr|dispatch|library|native"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_spoofed_library_namespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A library __dict__ property cannot hide the actual instance storage."""

    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return False\n"
        "class Lib:\n"
        "    __slots__ = ('_actual', '_handle')\n"
        "    def __init__(self):\n"
        "        self._handle = 99\n"
        "        self._actual = {'_handle': 99, 'gmshIsInitialized': lambda: True}\n"
        "    @property\n"
        "    def __dict__(self):\n"
        "        return {'_handle': 99}\n"
        "Lib.__module__ = 'ctypes'\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)

    with pytest.raises((OSError, ValueError), match="namespace|dict|library"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_external_dispatch_global_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ctypes lazy resolver cannot receive replacement global bindings."""

    _isolated(monkeypatch)
    source = (
        "import ctypes\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return lib.gmshIsInitialized()\n"
        "lib = object.__new__(ctypes.CDLL)\n"
        "lib._handle = 99\n"
        "lib._FuncPtr = object\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    runtime.load_verified_gmsh(expected)

    resolver = ctypes.CDLL.__getattr__
    monkeypatch.setitem(resolver.__globals__, "setattr", lambda *args: None)
    with pytest.raises((OSError, ValueError), match="global|dispatch|library"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_accepts_authenticated_source_restype_transition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A source-declared restype transition remains allowed for lazy caching."""

    _isolated(monkeypatch)
    source = (
        "from ctypes import c_double\n"
        "__version__ = '4.15.2'\n"
        "def wall_time():\n"
        "    lib.gmshLoggerGetWallTime.restype = c_double\n"
        "    return lib.gmshLoggerGetWallTime()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)

    native = _synthetic_native_callable()
    _install_synthetic_native_export(
        monkeypatch, loaded, "gmshLoggerGetWallTime", native
    )
    runtime.load_verified_gmsh(expected)
    native.restype = ctypes.c_double

    reused, _ = runtime.load_verified_gmsh(expected)
    assert reused is loaded


def test_verified_session_rejects_module_builtin_shadowing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "def uses_bool(value):\n"
        "    return bool(value)\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)
    loaded.bool = lambda value: False

    with pytest.raises((OSError, ValueError), match="global|builtin"):
        runtime.load_verified_gmsh(expected)


def test_cached_session_resolves_current_import_precedence_independently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    source = (
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    module, library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=library)
    earlier = tmp_path / "earlier" / "gmsh.py"
    earlier.parent.mkdir()
    earlier.write_text(source, encoding="utf-8")
    monkeypatch.setattr(
        runtime,
        "_current_process_binding",
        lambda: {
            "python": _identity(launcher),
            "python_image": _identity(image),
            "python_library": _identity(python_library),
            "pyvenv_cfg": None,
        },
    )
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)
    loaded, _ = runtime.load_verified_gmsh(expected)
    assert loaded.__file__ == str(module)

    monkeypatch.syspath_prepend(str(earlier.parent))
    with pytest.raises((OSError, ValueError), match="different module|import"):
        runtime.load_verified_gmsh(expected)
