from __future__ import annotations

from pathlib import Path

import pytest

from febio_cae_harness.workspace import (
    AttemptWorkspace,
    ImmutableInputError,
    ValidatedCaseWorkspace,
    WorkspaceBoundaryError,
)


def make_workspace(tmp_path: Path) -> ValidatedCaseWorkspace:
    tool_root = tmp_path / "tool"
    cae_root = tmp_path / "02_CAE"
    tool_root.mkdir()
    return ValidatedCaseWorkspace(tool_root=tool_root, cae_root=cae_root)


def test_create_case_copies_inputs_and_creates_canonical_layout(tmp_path: Path) -> None:
    source = tmp_path / "source" / "model.feb"
    source.parent.mkdir()
    source.write_bytes(b"authoritative input")
    workspace = make_workspace(tmp_path)

    case = workspace.create_case("case-a", [source])

    assert case.case_id == "case-a"
    assert case.case_root == (tmp_path / "02_CAE" / "case-a").resolve()
    assert case.case_root.is_dir()
    assert case.original_inputs == (case.case_root / "01_Input" / "model.feb",)
    assert case.original_inputs[0].read_bytes() == b"authoritative input"
    assert source.read_bytes() == b"authoritative input"
    for directory_name in (
        "01_Input",
        "02_Model",
        "03_Result",
        "04_Report",
        "05_Verification",
        "90_Temporary/attempts",
    ):
        assert (case.case_root / directory_name).is_dir()


def test_original_input_is_immutable_through_case_handle(tmp_path: Path) -> None:
    source = tmp_path / "input.feb"
    source.write_bytes(b"original")
    case = make_workspace(tmp_path).create_case("case-a", [source])

    with pytest.raises(ImmutableInputError):
        case.write_bytes(Path("01_Input") / "input.feb", b"replacement")

    assert source.read_bytes() == b"original"
    assert case.original_inputs[0].read_bytes() == b"original"


def test_case_handle_rejects_sibling_tool_and_cae_root_targets(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")

    forbidden_targets = (
        Path("..") / "case-b" / "03_Result" / "cross-case.txt",
        workspace.tool_root / "tool-output.txt",
        workspace.cae_root / "root-output.txt",
    )
    for target in forbidden_targets:
        with pytest.raises(WorkspaceBoundaryError):
            case_a.write_text(target, "must not be written")

    assert not (case_b.case_root / "03_Result" / "cross-case.txt").exists()
    assert not (workspace.tool_root / "tool-output.txt").exists()
    assert not (workspace.cae_root / "root-output.txt").exists()


def test_case_handle_allows_owned_writes_and_allocates_bounded_attempts(
    tmp_path: Path,
) -> None:
    case = make_workspace(tmp_path).create_case("case-a")

    model_path = case.write_text(Path("02_Model") / "derived.txt", "derived")
    attempt = case.allocate_attempt("attempt-1")
    attempt_path = attempt.write_text("event.json", "event")

    assert model_path == case.case_root / "02_Model" / "derived.txt"
    assert model_path.read_text(encoding="utf-8") == "derived"
    assert isinstance(attempt, AttemptWorkspace)
    assert attempt.root == case.case_root / "90_Temporary" / "attempts" / "attempt-1"
    assert attempt_path.read_text(encoding="utf-8") == "event"

    with pytest.raises(FileExistsError):
        case.allocate_attempt("attempt-1")


def test_case_ids_and_case_root_targets_are_validated(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)

    for invalid_case_id in ("", ".", "..", "case/a", "case\\b"):
        with pytest.raises(ValueError):
            workspace.create_case(invalid_case_id)

    case = workspace.create_case("case-a")
    with pytest.raises(WorkspaceBoundaryError):
        case.write_text(".", "must not replace case root")


def test_duplicate_case_creation_does_not_modify_existing_case(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    marker = case.write_text(Path("02_Model") / "marker.txt", "keep")

    with pytest.raises(FileExistsError):
        workspace.create_case("case-a")

    assert marker.read_text(encoding="utf-8") == "keep"
