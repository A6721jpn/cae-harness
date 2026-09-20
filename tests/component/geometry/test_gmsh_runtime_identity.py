"""Bounded synthetic tests for the installed Gmsh runtime identity boundary."""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import importlib.util
import os
import platform
import py_compile
import signal
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

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
    monkeypatch.setitem(vars(runtime.sys), "flags", SimpleNamespace(**values))


def _restore_runtime_module() -> None:
    importlib.reload(runtime)


def _reload_runtime_rejecting_ctypes_mutation(
    monkeypatch: pytest.MonkeyPatch, target: object, name: str, replacement: object
) -> None:
    context = monkeypatch.context()
    scoped = context.__enter__()
    try:
        scoped.setattr(target, name, replacement)
        with pytest.raises((OSError, ValueError), match="ctypes|conversion|factory|binding|ABI"):
            importlib.reload(runtime)
    finally:
        context.__exit__(None, None, None)
        _restore_runtime_module()


def test_capture_runtime_binding_uses_distribution_metadata_without_importing_gmsh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, library, launcher, image, python_library = _files(tmp_path, "__version__ = '4.15.2'\n")
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
    expected_python = expected["python"]
    expected_library = expected["library"]
    assert isinstance(expected_python, dict)
    assert isinstance(expected_library, dict)
    observed = {
        **expected,
        "python": {**expected_python, "path": str(paths[2]).upper()},
    }

    runtime.verify_runtime_identity(expected, observed)
    observed["library"] = {**expected_library, "size": 999}
    with pytest.raises(ValueError, match="runtime identity"):
        runtime.verify_runtime_identity(expected, observed)


@pytest.mark.parametrize("field", ["python_image", "pyvenv_cfg"])
def test_load_verified_gmsh_rejects_process_or_environment_mismatch_before_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str
) -> None:
    _isolated(monkeypatch)
    module, library, launcher, image, python_library = _files(tmp_path, "__version__ = '4.15.2'\n")
    cfg = tmp_path / "pyvenv.cfg"
    cfg.write_text("version = 3.12.10\n", encoding="utf-8")
    expected = _binding(tmp_path, module=module, library=library, pyvenv_cfg=cfg)
    process: dict[str, object] = {
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
    preloaded = ModuleType("gmsh")
    preloaded.__file__ = str(module)
    cast(Any, preloaded).__version__ = "4.15.2"
    sys.modules["gmsh"] = preloaded
    with pytest.raises((OSError, ValueError), match="preloaded"):
        runtime.load_verified_gmsh(expected)
    sys.modules.pop("gmsh", None)
    with pytest.raises((OSError, ValueError), match="cache"):
        runtime.load_verified_gmsh(expected)


def test_load_verified_gmsh_refuses_nonisolated_calls_before_import_or_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, library, _launcher, _image, _python_library = _files(
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
    source = "__version__ = '4.15.2'\nclass Lib:\n    _handle = 99\nlib = Lib()\n"
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
            f"Path({str(marker)!r}).write_text('changed', encoding='utf-8')\n" + source,
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
    source = "events = []\n__version__ = '4.15.2'\nclass Lib:\n    _handle = 99\nlib = Lib()\n"
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
    cast(Any, native).argtypes = None
    return native


def _install_synthetic_native_export(
    monkeypatch: pytest.MonkeyPatch, loaded: Any, name: str, value: Any
) -> None:
    pointer = ctypes.cast(value, ctypes.c_void_p).value
    assert isinstance(pointer, int) and pointer > 0
    setattr(loaded.lib, name, value)
    monkeypatch.setattr(runtime, "_native_export_address", lambda library, symbol: pointer)


def _synthetic_cdll_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[dict[str, object], Path]:
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
    return expected, library


def _rewrite_synthetic_source(expected: dict[str, object], source: str) -> None:
    module = expected["module"]
    assert isinstance(module, dict)
    module_path = Path(module["path"])
    module_path.write_text(source, encoding="utf-8")
    expected["module"] = {
        "path": str(module_path.resolve()),
        "size": module_path.stat().st_size,
        "sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
    }


def test_verified_session_rejects_instance_level_lazy_dispatch_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    loaded, _ = runtime.load_verified_gmsh(expected)

    loaded.lib.__getitem__ = lambda name: None

    with pytest.raises((OSError, ValueError), match="instance|dispatch|library"):
        runtime.load_verified_gmsh(expected)


def test_verified_load_rejects_pre_admission_ctypes_resolver_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    resolver = ctypes.CDLL.__getattr__

    def replacement(self: Any, name: str) -> Any:
        del self, name
        return None

    monkeypatch.setattr(resolver, "__code__", replacement.__code__)

    with pytest.raises((OSError, ValueError), match="ctypes|resolver|dispatch|code"):
        runtime.load_verified_gmsh(expected)


def test_verified_load_rejects_pre_admission_ctypes_cast_code_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)

    def replacement(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    monkeypatch.setattr(ctypes.cast, "__code__", replacement.__code__)

    with pytest.raises((OSError, ValueError), match="ctypes|cast|code|dispatch"):
        runtime.load_verified_gmsh(expected)


def test_verified_load_rejects_cast_errcheck_before_pointer_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _loaded, _ = runtime.load_verified_gmsh(expected)

    marker = tmp_path / "unsafe-cast-called"

    def errcheck(result: Any, function: Any, arguments: Any) -> Any:
        del function, arguments
        marker.write_text("called", encoding="ascii")
        return result

    try:
        cast(Any, ctypes)._cast.errcheck = errcheck
        with pytest.raises((OSError, ValueError)):
            runtime.load_verified_gmsh(expected)
        assert not marker.exists()
    finally:
        del cast(Any, ctypes)._cast.errcheck


def test_verified_load_rejects_cast_restype_callback_before_pointer_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    loaded, _ = runtime.load_verified_gmsh(expected)
    del loaded
    marker = tmp_path / "unsafe-cast-restype-called"

    def restype(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        marker.write_text("called", encoding="ascii")
        return None

    original = cast(Any, ctypes)._cast.restype
    try:
        cast(Any, ctypes)._cast.restype = restype
        with pytest.raises((OSError, ValueError)):
            runtime.load_verified_gmsh(expected)
        assert not marker.exists()
    finally:
        cast(Any, ctypes)._cast.restype = original


@pytest.mark.parametrize("replacement_kind", ["instance", "class"])
def test_verified_load_rejects_unauthenticated_callable_dependency_before_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, replacement_kind: str
) -> None:
    marker = tmp_path / f"unsafe-callable-{replacement_kind}"
    source = (
        "import platform\n"
        "platform.system()\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )

    if replacement_kind == "instance":

        class CallableInstance:
            def __call__(self) -> str:
                marker.write_text("called", encoding="ascii")
                return "Windows"

        replacement: Any = CallableInstance()
    else:

        class CallableMeta(type):
            def __call__(cls: type, *args: Any, **kwargs: Any) -> str:
                del cls, args, kwargs
                marker.write_text("called", encoding="ascii")
                return "Windows"

        class CallableClass(metaclass=CallableMeta):
            pass

        replacement = CallableClass

    monkeypatch.setattr(platform, "system", replacement)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    module = expected["module"]
    assert isinstance(module, dict)
    module_path = Path(module["path"])
    module_path.write_text(source, encoding="utf-8")
    expected["module"] = {
        "path": str(module_path.resolve()),
        "size": module_path.stat().st_size,
        "sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
    }

    with pytest.raises((OSError, ValueError), match="dependency|callable|import"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


def test_verified_load_rejects_recursive_module_dependency_before_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "unsafe-recursive-signal"
    source = (
        "import signal\n"
        "signal.signal(signal.SIGINT, None)\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )

    def poisoned(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        marker.write_text("called", encoding="ascii")
        return None

    signal_module = signal.signal.__globals__["_signal"]
    monkeypatch.setattr(signal_module, "signal", poisoned)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    module = expected["module"]
    assert isinstance(module, dict)
    module_path = Path(module["path"])
    module_path.write_text(source, encoding="utf-8")
    expected["module"] = {
        "path": str(module_path.resolve()),
        "size": module_path.stat().st_size,
        "sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
    }

    with pytest.raises((OSError, ValueError), match="dependency|source|owner|callable"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


def test_verified_load_rejects_dependency_dict_descriptor_without_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "unsafe-dependency-dict"
    source = (
        "import numpy\n"
        "numpy.ctypeslib.as_array(())\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )

    class DescriptorNamespace:
        @property
        def __dict__(self) -> dict[str, object]:  # type: ignore[override]  # adversarial descriptor
            marker.write_text("called", encoding="ascii")
            return {}

    numpy = ModuleType("numpy")
    cast(Any, numpy).ctypeslib = DescriptorNamespace()
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    monkeypatch.setitem(sys.modules, "numpy.ctypeslib", cast(Any, numpy).ctypeslib)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    module = expected["module"]
    assert isinstance(module, dict)
    module_path = Path(module["path"])
    module_path.write_text(source, encoding="utf-8")
    expected["module"] = {
        "path": str(module_path.resolve()),
        "size": module_path.stat().st_size,
        "sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
    }

    with pytest.raises((OSError, ValueError), match="dependency|available|module"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows stdlib builtin alias")
def test_source_dependencies_accept_windows_builtin_alias() -> None:
    source = b"import os\ndef probe(path):\n    return os.path.isfile(path)\n"
    states = runtime._capture_source_dependencies(source, compile(source, "probe.py", "exec"))
    assert any(state.values[-1] is os.path.isfile for state in states)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows stdlib builtin alias")
def test_windows_builtin_alias_rejects_substitution_and_spoofed_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = sys.modules["nt"]
    original = os.path.isfile
    replacement = vars(owner)["_path_isdir"]
    with monkeypatch.context() as scoped:
        scoped.setattr(os.path, "isfile", replacement)
        assert not runtime._supported_dependency_builtin(replacement, os.path, "isfile")
    with monkeypatch.context() as scoped:
        scoped.setattr(owner, "_path_isfile", replacement)
        assert os.path.isfile is original
        assert not runtime._supported_dependency_builtin(original, os.path, "isfile")
    fake_owner = ModuleType("nt")
    vars(fake_owner)["_path_isfile"] = original
    monkeypatch.setitem(sys.modules, "nt", fake_owner)
    assert not runtime._supported_dependency_builtin(original, os.path, "isfile")


def test_verified_load_rejects_same_module_builtin_symbol_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = (
        "import signal\n"
        "def probe():\n"
        "    return signal.signal(signal.SIGINT, None)\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    signal_module = signal.signal.__globals__["_signal"]
    replacement = signal_module.getsignal
    monkeypatch.setattr(signal_module, "signal", replacement)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    with pytest.raises((OSError, ValueError), match="dependency|builtin|symbol|source"):
        runtime.load_verified_gmsh(expected)


def test_verified_load_rejects_exact_module_dependency_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = (
        "import signal\n"
        "def probe():\n"
        "    return signal.signal(signal.SIGINT, None)\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    signal_globals = signal.signal.__globals__
    fake_signal = ModuleType("_signal")
    cast(Any, fake_signal).signal = signal_globals["_signal"].getsignal
    monkeypatch.setitem(signal_globals, "_signal", fake_signal)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    with pytest.raises((OSError, ValueError), match="dependency|module|source|namespace"):
        runtime.load_verified_gmsh(expected)


def test_verified_load_rejects_callable_instance_in_recursive_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "unsafe-recursive-callable"
    source = (
        "import signal\n"
        "def probe():\n"
        "    return signal.signal(signal.SIGINT, None)\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )

    class CallableInstance:
        def __call__(self, value: Any) -> int:
            marker.write_text("called", encoding="ascii")
            return int(value)

    monkeypatch.setitem(signal.signal.__globals__, "_enum_to_int", CallableInstance())
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    with pytest.raises((OSError, ValueError), match="dependency|callable|function"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


@pytest.mark.parametrize("namespace_kind", ["module-subclass", "arbitrary-object"])
def test_verified_load_rejects_cached_dependency_namespace_without_attribute_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, namespace_kind: str
) -> None:
    marker = tmp_path / f"unsafe-cached-namespace-{namespace_kind}"
    source = (
        "import platform\n"
        "def probe():\n"
        "    return platform.system()\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )

    if namespace_kind == "module-subclass":

        class MaliciousModule(ModuleType):
            def __getattribute__(self, attribute: str) -> Any:
                if attribute == "__spec__":
                    marker.write_text("called", encoding="ascii")
                return super().__getattribute__(attribute)

        replacement: Any = MaliciousModule("platform")
        replacement.system = platform.system
    else:

        class MaliciousNamespace:
            @property  # type: ignore[misc]  # adversarial descriptor
            def __class__(self) -> Any:
                marker.write_text("called", encoding="ascii")
                return ModuleType

        replacement = MaliciousNamespace()
    monkeypatch.setitem(sys.modules, "platform", replacement)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    with pytest.raises((OSError, ValueError), match="dependency|module|namespace|import"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


def test_ctypes_bootstrap_rejects_reassigned_cast_conversion_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    original_argtypes = cast(Any, ctypes)._cast.argtypes
    assert isinstance(original_argtypes, tuple)
    with monkeypatch.context() as context:
        context.setattr(ctypes.c_void_p, "from_param", lambda value: value)
        context.setattr(cast(Any, ctypes)._cast, "argtypes", tuple(original_argtypes))
        with pytest.raises((OSError, ValueError), match="ctypes|conversion|descriptor|cast"):
            runtime._build_ctypes_trust()
    runtime._build_ctypes_trust()


@pytest.mark.parametrize("target", ["c_void_p", "wintypes.HMODULE"])
def test_ctypes_bootstrap_rejects_same_module_type_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str
) -> None:
    del tmp_path
    if target == "c_void_p":
        _reload_runtime_rejecting_ctypes_mutation(monkeypatch, ctypes, "c_void_p", ctypes.c_int)
    else:
        _reload_runtime_rejecting_ctypes_mutation(
            monkeypatch,
            ctypes.wintypes,
            "HMODULE",
            ctypes.wintypes.LPCSTR,
        )


def test_ctypes_bootstrap_rejects_converter_mutation_before_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del tmp_path
    _reload_runtime_rejecting_ctypes_mutation(
        monkeypatch,
        ctypes.c_void_p,
        "from_param",
        lambda value: value,
    )


def test_ctypes_bootstrap_rejects_pointer_factory_mutation_before_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del tmp_path

    def replacement(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return ctypes.c_void_p

    _reload_runtime_rejecting_ctypes_mutation(monkeypatch, ctypes, "POINTER", replacement)


def test_dependency_class_admission_requires_authenticated_implementation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = (
        "import platform\n"
        "def probe():\n"
        "    return platform.uname_result\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )

    class MatchingClass(platform.uname_result):
        pass

    MatchingClass.__module__ = "platform"
    MatchingClass.__qualname__ = "uname_result"
    monkeypatch.setattr(platform, "uname_result", MatchingClass)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    with pytest.raises((OSError, ValueError), match="dependency|class|source|platform"):
        runtime.load_verified_gmsh(expected)


def test_dependency_class_rejects_untrusted_metaclass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = (
        "import platform\n"
        "def probe():\n"
        "    return platform.uname_result\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    original = platform.uname_result

    class MatchingMeta(type):
        pass

    namespace = {
        name: value
        for name, value in vars(original).items()
        if name not in {"__module__", "__dict__", "__weakref__"}
    }
    matching = MatchingMeta("MatchingClass", (original.__bases__[0],), namespace)
    matching.__module__ = "platform"
    matching.__qualname__ = "uname_result"
    monkeypatch.setattr(platform, "uname_result", matching)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    with pytest.raises((OSError, ValueError), match="dependency|class|metaclass|platform"):
        runtime.load_verified_gmsh(expected)


def test_dependency_cache_rejects_direct_malicious_object_without_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "malicious-platform-cache-executed"

    class MaliciousCache:
        def __getattribute__(self, name: str) -> Any:
            marker.write_text(name, encoding="ascii")
            return super().__getattribute__(name)

    source = (
        "import platform\n"
        "def probe():\n"
        "    return platform.system()\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    monkeypatch.setattr(platform, "_uname_cache", MaliciousCache())
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    with pytest.raises((OSError, ValueError), match="dependency|cache|platform|uname"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


def test_verified_load_accepts_ordinary_warm_platform_system_dependency_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = (
        "import platform\n"
        "platform.system()\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    platform.system()
    assert type(vars(platform).get("_uname_cache")) is platform.uname_result
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)

    loaded, _ = runtime.load_verified_gmsh(expected)
    reused, _ = runtime.load_verified_gmsh(expected)

    assert reused is loaded


def test_verified_session_rejects_matching_platform_result_class_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = (
        "import platform\n"
        "platform.system()\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    monkeypatch.setattr(platform, "_uname_cache", None)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)
    runtime.load_verified_gmsh(expected)

    class MatchingClass(platform.uname_result):
        pass

    MatchingClass.__module__ = "platform"
    MatchingClass.__qualname__ = "uname_result"
    monkeypatch.setattr(platform, "uname_result", MatchingClass)
    with pytest.raises((OSError, ValueError), match="dependency|class|platform|replaced"):
        runtime.load_verified_gmsh(expected)


def test_verified_session_rejects_malicious_platform_cache_without_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "malicious-platform-cache-reuse"
    source = (
        "import platform\n"
        "platform.system()\n"
        "__version__ = '4.15.2'\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
    )
    monkeypatch.setattr(platform, "_uname_cache", None)
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    _rewrite_synthetic_source(expected, source)
    runtime.load_verified_gmsh(expected)

    class MaliciousCache:
        def __getattribute__(self, name: str) -> Any:
            marker.write_text(name, encoding="ascii")
            return super().__getattribute__(name)

    monkeypatch.setattr(platform, "_uname_cache", MaliciousCache())
    with pytest.raises((OSError, ValueError), match="cache|platform|replaced"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


def test_ctypes_bootstrap_rejects_preexisting_cast_restype_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    marker = tmp_path / "unsafe-bootstrap-cast-restype"

    def poisoned(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        marker.write_text("called", encoding="ascii")
        return None

    with monkeypatch.context() as context:
        context.setattr(cast(Any, ctypes)._cast, "restype", poisoned)
        with pytest.raises((OSError, ValueError), match="ctypes|cast|metadata|restype"):
            runtime._build_ctypes_trust()
    runtime._build_ctypes_trust()
    assert not marker.exists()


@pytest.mark.parametrize("target", ["CDLL", "WinDLL"])
def test_ctypes_bootstrap_rejects_custom_dispatch_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str
) -> None:
    _isolated(monkeypatch)
    marker = tmp_path / f"unsafe-bootstrap-{target.lower()}-dispatch"

    class CallableDescriptor:
        def __get__(self, instance: Any, owner: type | None = None) -> Any:
            del instance, owner
            marker.write_text("called", encoding="ascii")
            return None

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            marker.write_text("called", encoding="ascii")
            return None

    owner = getattr(ctypes, target)
    with monkeypatch.context() as context:
        context.setattr(owner, "__getattribute__", CallableDescriptor())
        with pytest.raises((OSError, ValueError), match="ctypes|descriptor|dispatch"):
            runtime._build_ctypes_trust()
    runtime._build_ctypes_trust()
    assert not marker.exists()


@pytest.mark.parametrize("target", ["cdll_getattribute", "windll_init"])
def test_verified_load_rejects_pre_admission_ctypes_mro_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str
) -> None:
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)

    def replacement(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    if target == "cdll_getattribute":
        monkeypatch.setattr(ctypes.CDLL, "__getattribute__", replacement)
    else:
        monkeypatch.setattr(ctypes.WinDLL, "__init__", replacement)

    with pytest.raises((OSError, ValueError)):
        runtime.load_verified_gmsh(expected)


@pytest.mark.parametrize("dependency", ["signal", "platform", "numpy"])
def test_verified_load_rejects_tampered_import_attribute_before_gmsh_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dependency: str
) -> None:
    marker = tmp_path / f"unsafe-{dependency}-called"
    if dependency == "signal":
        source = "import signal\nsignal.signal(signal.SIGINT, None)\n"

        def poisoned(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            marker.write_text("called", encoding="ascii")
            return None

        monkeypatch.setattr(signal, "signal", poisoned)
    elif dependency == "platform":
        source = "import platform\nplatform.system()\n"

        def poisoned_platform() -> str:
            marker.write_text("called", encoding="ascii")
            return "Windows"

        monkeypatch.setattr(platform, "system", poisoned_platform)
    else:
        source = "import numpy\nnumpy.ctypeslib.as_array(())\n"
        numpy = ModuleType("numpy")
        ctypeslib = ModuleType("numpy.ctypeslib")

        def poisoned(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            marker.write_text("called", encoding="ascii")
            return ()

        cast(Any, ctypeslib).as_array = poisoned
        cast(Any, numpy).ctypeslib = ctypeslib
        monkeypatch.setitem(sys.modules, "numpy", numpy)
        monkeypatch.setitem(sys.modules, "numpy.ctypeslib", ctypeslib)

    source += "__version__ = '4.15.2'\nclass Lib:\n    _handle = 99\nlib = Lib()\n"
    expected, _library = _synthetic_cdll_setup(monkeypatch, tmp_path)
    module = expected["module"]
    assert isinstance(module, dict)
    module_path = Path(module["path"])
    module_path.write_text(source, encoding="utf-8")
    expected["module"] = {
        "path": str(module_path.resolve()),
        "size": module_path.stat().st_size,
        "sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
    }

    with pytest.raises((OSError, ValueError)):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


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


def test_verified_session_does_not_execute_ctypes_symbol_name_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Native symbol validation does not call a mutable CFuncPtr __getattr__."""

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
    calls: list[str] = []

    def replacement(self: Any, name: str) -> None:
        del self
        calls.append(name)

    monkeypatch.setattr(type(native), "__getattr__", replacement, raising=False)
    runtime.load_verified_gmsh(expected)
    assert calls == []


def test_verified_session_rejects_library_handle_descriptor_tampering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A library _handle descriptor cannot redirect a reused verified session."""

    _isolated(monkeypatch)
    source = (
        "import ctypes\n"
        "__version__ = '4.15.2'\n"
        "def isInitialized():\n"
        "    return False\n"
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

    monkeypatch.setattr(type(loaded.lib), "_handle", property(lambda self: 99), raising=False)
    with pytest.raises((OSError, ValueError), match="handle|descriptor|library|dispatch"):
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

    library_type = type(loaded.lib)

    def redirecting_getattribute(self: object, attribute: str) -> object:
        if attribute == "gmshIsInitialized":
            return lambda: True
        return object.__getattribute__(self, attribute)

    redirecting_type = type(
        "RedirectingLib",
        (library_type,),
        {"__getattribute__": redirecting_getattribute},
    )
    monkeypatch.setattr(loaded.lib, "__class__", redirecting_type)
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
    native_mro = native_type.__mro__

    def lying_meta_getattribute(cls: type, attribute: str) -> object:
        if attribute == "__mro__":
            return native_mro
        return type.__getattribute__(cls, attribute)

    lying_meta = type(
        "LyingMeta",
        (type(native_type),),
        {"__getattribute__": lying_meta_getattribute},
    )

    def redirected_call(self: object, *args: object, **kwargs: object) -> None:
        del self, args, kwargs

    redirected_type = lying_meta(
        "RedirectedCFunc",
        (native_type,),
        {
            "_argtypes_": native_type._argtypes_,
            "_restype_": native_type._restype_,
            "_flags_": native_type._flags_,
            "__call__": redirected_call,
        },
    )
    native.__class__ = redirected_type
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

    monkeypatch.setattr(type(loaded.lib), "_FuncPtr", property(lambda self: object), raising=False)
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
    _install_synthetic_native_export(monkeypatch, loaded, "gmshLoggerGetWallTime", native)
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


def test_verified_load_rejects_function_builtins_mapping_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "unsafe-builtin-called"
    source = (
        "__version__ = '4.15.2'\n"
        "def poisoned(value):\n"
        "    with open(_marker, 'w', encoding='ascii') as stream:\n"
        "        stream.write('called')\n"
        "    return False\n"
        "def uses_bool(value):\n"
        "    return bool(value)\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
        "_marker = " + repr(str(marker)) + "\n"
        "__builtins__ = {'bool': poisoned}\n"
    )
    module, _library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=_library)
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
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: _library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)

    with pytest.raises((OSError, ValueError)):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


def test_verified_load_rejects_forged_function_actual_builtins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    marker = tmp_path / "unsafe-forged-builtin-called"
    source = (
        "__version__ = '4.15.2'\n"
        "def poisoned(value):\n"
        "    __import__('pathlib').Path(_marker).write_text('called', encoding='ascii')\n"
        "    return False\n"
        "def uses_bool(value):\n"
        "    return bool(value)\n"
        "def forge():\n"
        "    namespace = dict(globals())\n"
        "    poisoned_builtins = dict(namespace['__builtins__'])\n"
        "    poisoned_builtins['bool'] = poisoned\n"
        "    forged_globals = dict(namespace)\n"
        "    forged_globals['__builtins__'] = poisoned_builtins\n"
        "    forged = __import__('types').FunctionType(\n"
        "        uses_bool.__code__, forged_globals, uses_bool.__name__, uses_bool.__defaults__\n"
        "    )\n"
        "    forged_globals['__builtins__'] = namespace['__builtins__']\n"
        "    return forged\n"
        "uses_bool = forge()\n"
        "class Lib:\n"
        "    _handle = 99\n"
        "lib = Lib()\n"
        "_marker = " + repr(str(marker)) + "\n"
    )
    module, _library, launcher, image, python_library = _files(tmp_path, source)
    expected = _binding(tmp_path, module=module, library=_library)
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
    monkeypatch.setattr(runtime, "_mapped_module_path", lambda handle: _library)
    monkeypatch.syspath_prepend(str(module.parent))
    sys.modules.pop("gmsh", None)

    with pytest.raises((OSError, ValueError), match="builtins|function|dependency"):
        runtime.load_verified_gmsh(expected)
    assert not marker.exists()


def test_cached_session_resolves_current_import_precedence_independently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolated(monkeypatch)
    source = "__version__ = '4.15.2'\nclass Lib:\n    _handle = 99\nlib = Lib()\n"
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


def test_nested_optional_import_source_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    source = b"""try_optional = True
use_optional = False
if try_optional:
    try:
        import _cae_missing_optional_probe as optional
        try:
            from weakref import finalize as finalizer
        except:
            from _cae_missing_fallback_probe import finalize as finalizer
        use_optional = True
    except:
        pass
def probe():
    return optional, finalizer, use_optional
"""
    code = compile(source, "probe.py", "exec")
    states = runtime._capture_source_dependencies(source, code)
    module = ModuleType("probe")
    vars(module).update(try_optional=True, use_optional=False)
    runtime._validate_source_dependencies(module, states)
    for name, value in (
        ("use_optional", True),
        ("try_optional", False),
        ("optional", object()),
        ("finalizer", object()),
    ):
        with monkeypatch.context() as scoped:
            scoped.setattr(module, name, value, raising=False)
            with pytest.raises((OSError, ValueError), match="dependency|flow"):
                runtime._validate_source_dependencies(module, states)
    with monkeypatch.context() as scoped:
        scoped.setitem(
            sys.modules, "_cae_missing_optional_probe", ModuleType("_cae_missing_optional_probe")
        )
        with pytest.raises((OSError, ValueError), match="dependency|module"):
            runtime._capture_source_dependencies(source, code)
        with pytest.raises((OSError, ValueError), match="dependency|module"):
            runtime._validate_source_dependencies(module, states)
    required = (
        b"import _cae_missing_optional_probe\ndef probe(): return _cae_missing_optional_probe"
    )
    with pytest.raises((OSError, ValueError), match="dependency|import"):
        runtime._capture_source_dependencies(required, compile(required, "probe.py", "exec"))
    present = source.replace(b"_cae_missing_optional_probe", b"math")
    import ast
    from weakref import finalize

    bindings, expected, absent_imports = runtime._source_import_flow(ast.parse(present))
    assert not absent_imports
    assert bindings["finalizer"].module_name == "weakref"
    assert expected["finalizer"] is finalize
    assert expected["use_optional"] is True

    partial = b"""try:
    import math as retained
    import _cae_missing_optional_probe as absent
except ImportError:
    pass
"""
    bindings, expected, absent_imports = runtime._source_import_flow(ast.parse(partial))
    assert bindings["retained"].module_name == "math"
    assert expected["absent"] is runtime._MISSING
    assert absent_imports == ("_cae_missing_optional_probe",)
    with pytest.raises((OSError, ValueError), match="unsupported condition"):
        runtime._source_import_flow(
            ast.parse(source.replace(b"if try_optional:", b"if unknown():"))
        )
