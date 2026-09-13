"""Bounded synthetic tests for the installed Gmsh runtime identity boundary."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import py_compile
import sys
from pathlib import Path
from types import SimpleNamespace
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
