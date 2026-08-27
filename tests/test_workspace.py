from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceIntegrityError, EvidenceStore
from febio_cae_harness.workspace import (
    AttemptWorkspace,
    CaseWorkspace,
    ImmutableInputError,
    ValidatedCaseWorkspace,
    WorkspaceBoundaryError,
)


def make_workspace(tmp_path: Path) -> ValidatedCaseWorkspace:
    tool_root = tmp_path / "tool"
    cae_root = tmp_path / "02_CAE"
    tool_root.mkdir()
    return ValidatedCaseWorkspace(tool_root=tool_root, cae_root=cae_root)


def make_directory_junction(link: Path, target: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert link.is_dir()


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

    attempt = case.allocate_attempt("attempt-1")
    temporary_path = attempt.write_text("event.json", "event")

    assert isinstance(attempt, AttemptWorkspace)
    assert attempt.root == case.case_root / "90_Temporary" / "attempts" / "attempt-1"
    assert temporary_path.read_text(encoding="utf-8") == "event"

    with pytest.raises(FileExistsError):
        case.allocate_attempt("attempt-1")


def test_case_handle_allows_normal_writes_only_in_temporary(tmp_path: Path) -> None:
    case = make_workspace(tmp_path).create_case("case-a")

    temporary_path = case.write_text(Path("90_Temporary") / "derived.txt", "derived")

    assert temporary_path == case.case_root / "90_Temporary" / "derived.txt"
    assert temporary_path.read_text(encoding="utf-8") == "derived"
    for directory_name in ("02_Model", "03_Result", "04_Report", "05_Verification"):
        with pytest.raises(WorkspaceBoundaryError):
            case.write_text(Path(directory_name) / "derived.txt", "must be promoted")


def test_case_handles_cannot_be_forged_with_arbitrary_roots(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case_root = tmp_path / "02_CAE" / "case-a"

    with pytest.raises(TypeError):
        CaseWorkspace(workspace, "case-a", case_root)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        AttemptWorkspace("case-a", "attempt-1", case_root)  # type: ignore[call-arg]

    case = workspace.create_case("case-a")
    with pytest.raises(WorkspaceBoundaryError):
        CaseWorkspace._from_manager(workspace, "case-a", workspace.cae_root)
    with pytest.raises(WorkspaceBoundaryError):
        AttemptWorkspace._from_manager(case, "attempt-1", workspace.cae_root)


def test_public_workspace_has_no_promotion_authority(tmp_path: Path) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
    with pytest.raises(TypeError):
        case.promote_verified("raw-source", "02_Model/output", expected_sha256="0" * 64)
    with pytest.raises(TypeError):
        case.promote_artifact("raw-source", "02_Model/output", expected_sha256="0" * 64)


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
    marker = case.write_text(Path("90_Temporary") / "marker.txt", "keep")

    with pytest.raises(FileExistsError):
        workspace.create_case("case-a")

    assert marker.read_text(encoding="utf-8") == "keep"


def test_manager_rejects_reparse_cae_root_alias(tmp_path: Path) -> None:
    real_root = tmp_path / "real-02_CAE"
    real_root.mkdir()
    cae_alias = tmp_path / "02_CAE"
    make_directory_junction(cae_alias, real_root)

    with pytest.raises(WorkspaceBoundaryError):
        ValidatedCaseWorkspace(tool_root=tmp_path / "tool", cae_root=cae_alias)


def test_open_case_rejects_case_alias_instead_of_issuing_cross_case_authority(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    case_a_root = case_a.case_root
    import shutil

    shutil.rmtree(case_a_root)
    make_directory_junction(case_a_root, case_b.case_root)

    with pytest.raises(WorkspaceBoundaryError):
        workspace.open_case("case-a")


def test_attempt_handle_rejects_reparse_alias(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    real_attempt = case.temporary_root / "attempts" / "real-attempt"
    real_attempt.mkdir()
    attempt_alias = case.temporary_root / "attempts" / "attempt-1"
    make_directory_junction(attempt_alias, real_attempt)

    with pytest.raises(WorkspaceBoundaryError):
        AttemptWorkspace._from_manager(case, "attempt-1", attempt_alias)


def test_case_handle_rejects_reparse_write_alias_inside_owned_tree(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    real_directory = case.temporary_root / "real"
    real_directory.mkdir()
    alias_directory = case.temporary_root / "alias"
    make_directory_junction(alias_directory, real_directory)

    with pytest.raises(WorkspaceBoundaryError):
        case.write_text(Path("90_Temporary") / "alias" / "output.txt", "must reject alias")


def test_promotion_requires_persisted_evidence_store_authorization(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    store = EvidenceStore(
        case,
        IntentContract(engineering_question="What is the displacement?"),
    )
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    destination = Path("02_Model") / "derived.feb"

    with pytest.raises(TypeError):
        case.promote_verified(source, destination, expected_sha256="0" * 64)
    with pytest.raises(TypeError):
        case.promote_artifact(source, destination, expected_sha256="0" * 64)

    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified("not-a-receipt")  # type: ignore[arg-type]

    verification = store.record_verification(
        source,
        destination,
        attempt_id="attempt-1",
    )
    assert verification is not None
    promoted = store.promote_verified(verification)
    assert promoted == case.case_root / destination
    assert promoted.read_text(encoding="utf-8") == "derived"
    assert store.reopen() is store


def test_promotion_verification_binds_exact_destination(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    store = EvidenceStore(
        case,
        IntentContract(engineering_question="What is the displacement?"),
    )
    store.record_attempt("attempt-1")
    source = case.write_text(
        Path("90_Temporary") / "attempts" / "attempt-1" / "derived.feb",
        "derived",
    )
    verification = store.record_verification(
        source,
        Path("02_Model") / "derived.feb",
        attempt_id="attempt-1",
    )
    assert verification is not None

    events_path = store.events_path
    lines = events_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[-1])
    tampered["payload"]["destination"] = "03_Result/not-authorized.xplt"
    lines[-1] = json.dumps(tampered, separators=(",", ":"), sort_keys=True)
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(verification)
