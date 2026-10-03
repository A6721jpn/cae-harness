from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.geometry import static_load
from febio_cae.adapters.geometry.backend import BackendError
from febio_cae.domain import PortError, PortErrorCategory
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.static_load import EdgeTotalForce, StaticLoadRequest


def _request(content: bytes) -> StaticLoadRequest:
    return StaticLoadRequest(
        source_sha256=hashlib.sha256(content).hexdigest(),
        fixed_face_ids=(1,),
        loads=(EdgeTotalForce(12, (0, 0, -10)), EdgeTotalForce(14, (0, 0, -10))),
        youngs_modulus_pa=68_000_000_000,
        poisson_ratio=0.33,
        global_size_m=0.001,
        native_coordinate_unit="MM",
        algorithm_2d=6,
        algorithm_3d=1,
        curvature_points=20,
        cpu_workers=1,
        mesh_wall_seconds=600,
        solver_wall_seconds=600,
        memory_bytes=100_000_000,
        max_nodes=1000,
        max_elements=1000,
    )


def _input(directory: Path, content: bytes) -> None:
    (directory / "input.json").write_bytes(
        canonical_bytes(
            {
                "content_hex": content.hex(),
                "request": _request(content).to_dict(),
                "runtime_binding": {},
            }
        )
    )


def test_malformed_step_preserves_specific_input_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _input(tmp_path, b"not a STEP source")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BackendError, match="malformed STEP HEADER"):
        static_load._main()
    original = OSError("preparation child failed with exit 1; see its stderr.log")
    error = static_load._child_failure(tmp_path, 100_000, original)
    assert error.category == PortErrorCategory.INVALID_INPUT
    assert "malformed STEP HEADER" in str(error)
    assert "stderr.log" in str(error)
    assert "malformed STEP HEADER" in capsys.readouterr().err
    assert not (tmp_path / "output.json").exists()


def test_error_publication_failure_does_not_mask_the_source_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _input(tmp_path, b"not a STEP source")
    monkeypatch.chdir(tmp_path)
    original_write = Path.write_bytes

    def fail_error_write(path: Path, content: bytes) -> int:
        if path.name == "error.json":
            raise OSError("synthetic disk failure")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_error_write)
    with pytest.raises(BackendError, match="malformed STEP HEADER"):
        static_load._main()
    stderr = capsys.readouterr().err
    assert "malformed STEP HEADER" in stderr
    assert "synthetic disk failure" in stderr


def test_runtime_admission_failure_is_not_a_bad_source_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = (
        b"ISO-10303-21;HEADER;"
        b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));ENDSEC;DATA;ENDSEC;END-ISO-10303-21;"
    )
    _input(tmp_path, content)
    monkeypatch.chdir(tmp_path)
    # The real authenticated loader rejects the empty binding, or the non-isolated
    # test interpreter, before any installed native runtime may be consumed.
    with pytest.raises((OSError, ValueError)) as caught:
        static_load._main()
    error = static_load._child_failure(tmp_path, 100_000, OSError("child exited 1"))
    assert error.category == PortErrorCategory.ENVIRONMENT
    assert str(caught.value) in str(error)
    assert not (tmp_path / "output.json").exists()


def test_timeout_takes_precedence_over_a_child_selection_error(tmp_path: Path) -> None:
    (tmp_path / "error.json").write_bytes(
        canonical_bytes(
            {
                "schema_version": "1",
                "category": "invalid_input",
                "message": "selected curve is absent",
            }
        )
    )
    error = static_load._child_failure(tmp_path, 100_000, TimeoutError("owned wall deadline"))
    assert error.category == PortErrorCategory.ENVIRONMENT
    assert str(error) == "owned wall deadline"


@pytest.mark.parametrize(
    "record",
    [
        {"schema_version": "1", "category": "invalid_input"},
        {"schema_version": "1", "category": "invalid_input", "message": None},
        {"schema_version": "1", "category": "invalid_input", "message": "x" * 65536},
    ],
)
def test_untrusted_error_record_cannot_override_the_native_failure(
    tmp_path: Path, record: dict[str, Any]
) -> None:
    (tmp_path / "error.json").write_bytes(canonical_bytes(record))
    error = static_load._child_failure(tmp_path, 100_000, OSError("native crash; see stderr.log"))
    assert error.category == PortErrorCategory.ENVIRONMENT
    assert str(error) == "native crash; see stderr.log"


@pytest.mark.parametrize("response", [None, [], {}, {"mesh": {}}])
def test_missing_child_response_is_integrity_not_a_key_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: Any
) -> None:
    content = b"synthetic source"
    monkeypatch.setattr(
        static_load,
        "resource_snapshot",
        lambda workers: {"available_cpus": workers, "memory_bytes": 100_000_000},
    )
    monkeypatch.setattr(static_load, "capture_runtime_binding", dict)

    def run(argv: tuple[str, ...], directory: Path, **limits: Any) -> dict[str, int]:
        if response is not None:
            (directory / "output.json").write_text(json.dumps(response), encoding="utf-8")
        return {"pid": 1, "creation_time": 1, "exit_code": 0}

    monkeypatch.setattr(static_load, "_run_owned", run)
    with pytest.raises(PortError, match="invalid native static response") as caught:
        static_load.prepare_native(content, _request(content), tmp_path / "child")
    assert caught.value.category == PortErrorCategory.INTEGRITY
