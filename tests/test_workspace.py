from __future__ import annotations

import copy
import dataclasses
import json
import os
import pickle
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import febio_cae_harness.workspace as workspace_module
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


def make_file_hard_link(link: Path, target: Path) -> None:
    os.link(target, link)
    assert link.is_file()
    assert link.stat().st_ino == target.stat().st_ino


def assert_hard_link_write_is_contained(
    target: Path,
    outside: Path,
    writer: Callable[[], Path],
    expected: bytes,
) -> None:
    original = outside.read_bytes()
    try:
        written = writer()
    except WorkspaceBoundaryError:
        assert outside.read_bytes() == original
        assert target.read_bytes() == original
    else:
        assert written == target
        assert outside.read_bytes() == original
        assert target.read_bytes() == expected
        assert target.stat().st_ino != outside.stat().st_ino


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


def test_case_text_write_cannot_mutate_outside_file_through_hard_link(tmp_path: Path) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    target = case.case_root / "90_Temporary" / "linked.txt"
    make_file_hard_link(target, outside)

    assert_hard_link_write_is_contained(
        target,
        outside,
        lambda: case.write_text(Path("90_Temporary") / "linked.txt", "owned"),
        b"owned",
    )


def test_case_bytes_write_cannot_mutate_outside_file_through_hard_link(tmp_path: Path) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    target = case.case_root / "90_Temporary" / "linked.bin"
    make_file_hard_link(target, outside)

    assert_hard_link_write_is_contained(
        target,
        outside,
        lambda: case.write_bytes(Path("90_Temporary") / "linked.bin", b"owned"),
        b"owned",
    )


def test_attempt_write_cannot_mutate_outside_file_through_hard_link(tmp_path: Path) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
    attempt = case.allocate_attempt("attempt-1")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    target = attempt.root / "linked.txt"
    make_file_hard_link(target, outside)

    assert_hard_link_write_is_contained(
        target,
        outside,
        lambda: attempt.write_text("linked.txt", "owned"),
        b"owned",
    )


@pytest.mark.parametrize("control_name", ["CASE_MANIFEST.json", "intent.json"])
def test_control_write_cannot_mutate_outside_file_through_hard_link(
    tmp_path: Path,
    control_name: str,
) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
    outside = tmp_path / f"outside-{control_name}"
    outside.write_bytes(b"outside")
    target = case.case_root / control_name
    make_file_hard_link(target, outside)

    assert_hard_link_write_is_contained(
        target,
        outside,
        lambda: case._write_control_text(control_name, "owned"),
        b"owned",
    )


def test_event_append_rejects_hard_link_to_outside_file(tmp_path: Path) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
    outside = tmp_path / "outside-events.jsonl"
    outside.write_bytes(b"outside\n")
    target = case.case_root / "90_Temporary" / "events.jsonl"
    make_file_hard_link(target, outside)

    with pytest.raises(WorkspaceBoundaryError):
        case.append_text(Path("90_Temporary") / "events.jsonl", "owned\n")

    assert outside.read_bytes() == b"outside\n"
    assert target.read_bytes() == b"outside\n"


def test_append_bytes_rejects_hard_link_to_outside_file(tmp_path: Path) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
    outside = tmp_path / "outside-bytes.bin"
    outside.write_bytes(b"outside")
    target = case.case_root / "90_Temporary" / "linked.bin"
    make_file_hard_link(target, outside)

    with pytest.raises(WorkspaceBoundaryError):
        case.append_bytes(Path("90_Temporary") / "linked.bin", b"owned")

    assert outside.read_bytes() == b"outside"
    assert target.read_bytes() == b"outside"


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
    assert verification is None
    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(verification)  # type: ignore[arg-type]
    assert not (case.case_root / destination).exists()
    assert store.reopen() is store


def test_verified_promotion_cannot_mutate_outside_file_through_hard_link(
    tmp_path: Path,
) -> None:
    case = make_workspace(tmp_path).create_case("case-a")
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
    verification = store.record_verification(
        source,
        destination,
        attempt_id="attempt-1",
    )
    assert verification is None

    outside = tmp_path / "outside-promoted.feb"
    outside.write_bytes(b"outside")
    target = case.case_root / destination
    make_file_hard_link(target, outside)

    with pytest.raises(EvidenceIntegrityError):
        store.promote_verified(verification)  # type: ignore[arg-type]

    assert outside.read_bytes() == b"outside"
    assert target.read_bytes() == b"outside"


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
    assert verification is None

    events_path = store.events_path
    lines = events_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[-1])
    tampered["payload"]["destination"] = "03_Result/not-authorized.xplt"
    lines[-1] = json.dumps(tampered, separators=(",", ":"), sort_keys=True)
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        store.reopen()
    assert not (case.case_root / "02_Model" / "derived.feb").exists()


def test_workspace_authorities_reject_forged_cloned_and_rebound_objects(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    attempt = case.allocate_attempt("attempt-1")

    forged_manager = object.__new__(ValidatedCaseWorkspace)
    object.__setattr__(forged_manager, "tool_root", tmp_path / "forged-tool")
    object.__setattr__(forged_manager, "cae_root", tmp_path / "forged-cae")
    with pytest.raises(WorkspaceBoundaryError):
        forged_manager.create_case("case-a")

    forged_case = object.__new__(CaseWorkspace)
    object.__setattr__(forged_case, "_manager", workspace)
    object.__setattr__(forged_case, "case_id", "case-a")
    object.__setattr__(forged_case, "case_root", tmp_path / "forged-case")
    object.__setattr__(forged_case, "original_inputs", ())
    object.__setattr__(forged_case, "source_inputs", ())
    with pytest.raises(WorkspaceBoundaryError):
        forged_case.write_text("90_Temporary/forged.txt", "must reject")

    forged_attempt = object.__new__(AttemptWorkspace)
    object.__setattr__(forged_attempt, "case_id", "case-a")
    object.__setattr__(forged_attempt, "attempt_id", "attempt-1")
    object.__setattr__(forged_attempt, "root", tmp_path / "forged-attempt")
    with pytest.raises(WorkspaceBoundaryError):
        forged_attempt.write_text("forged.txt", "must reject")

    for authority in (workspace, case, attempt):
        with pytest.raises(TypeError):
            copy.copy(authority)
        with pytest.raises(TypeError):
            copy.deepcopy(authority)
        with pytest.raises(TypeError):
            pickle.dumps(authority)

    with pytest.raises(TypeError):
        dataclasses.replace(workspace)  # type: ignore[type-var]
    with pytest.raises(TypeError):
        dataclasses.replace(case)
    with pytest.raises(TypeError):
        dataclasses.replace(attempt)

    object.__setattr__(workspace, "cae_root", tmp_path / "swapped-cae")
    with pytest.raises(WorkspaceBoundaryError):
        workspace.create_case("swapped")

    object.__setattr__(case, "case_root", tmp_path / "swapped-case")
    with pytest.raises(WorkspaceBoundaryError):
        case.write_text("90_Temporary/swapped.txt", "must reject")

    object.__setattr__(attempt, "attempt_id", "swapped-attempt")
    with pytest.raises(WorkspaceBoundaryError):
        attempt.write_text("swapped.txt", "must reject")


def test_issued_case_handle_rejects_renamed_case_substitution(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    displaced = tmp_path / "case-a-displaced"

    case_a.case_root.rename(displaced)
    case_b.case_root.rename(case_a.case_root)

    with pytest.raises(WorkspaceBoundaryError):
        case_a.write_text(Path("90_Temporary") / "substitution.txt", "must reject")
    assert not (case_b.case_root / "90_Temporary" / "substitution.txt").exists()
    assert not (case_a.case_root / "90_Temporary" / "substitution.txt").exists()


def test_issued_attempt_handle_rejects_renamed_attempt_substitution(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    attempt_a = case.allocate_attempt("attempt-a")
    attempt_b = case.allocate_attempt("attempt-b")
    displaced = case.temporary_root / "attempts" / "attempt-a-displaced"

    attempt_a.root.rename(displaced)
    attempt_b.root.rename(attempt_a.root)

    with pytest.raises(WorkspaceBoundaryError):
        attempt_a.write_text("substitution.txt", "must reject")
    assert not (attempt_b.root / "substitution.txt").exists()
    assert not (attempt_a.root / "substitution.txt").exists()


@pytest.mark.parametrize("root_name", ["tool_root", "cae_root"])
def test_manager_rejects_replaced_root_after_authority_issuance(
    tmp_path: Path,
    root_name: str,
) -> None:
    workspace = make_workspace(tmp_path)
    workspace.create_case("case-a")
    root = getattr(workspace, root_name)
    displaced = tmp_path / f"{root_name}-displaced"

    root.rename(displaced)
    root.mkdir()

    with pytest.raises(WorkspaceBoundaryError):
        workspace.create_case("case-b")
    assert not (root / "case-b").exists()


def _substitute_case_roots(case_a: CaseWorkspace, case_b: CaseWorkspace, tmp_path: Path) -> None:
    case_a.case_root.rename(tmp_path / "case-a-open-displaced")
    case_b.case_root.rename(case_a.case_root)


def _substitute_temporary_roots(
    case_a: CaseWorkspace,
    case_b: CaseWorkspace,
    tmp_path: Path,
) -> None:
    case_a.temporary_root.rename(tmp_path / "case-a-temporary-displaced")
    case_b.temporary_root.rename(case_a.temporary_root)


def test_write_rejects_case_substitution_at_temporary_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    original_open_directory = workspace_module._open_directory
    swapped = False

    def swap_before_open(path: Path, label: str) -> int:
        nonlocal swapped
        if not swapped and path == case_a.case_root:
            swapped = True
            _substitute_case_roots(case_a, case_b, tmp_path)
        return original_open_directory(path, label)

    monkeypatch.setattr(workspace_module, "_open_directory", swap_before_open)
    with pytest.raises(WorkspaceBoundaryError):
        case_a.write_text(Path("90_Temporary") / "open-race.txt", "must reject")

    assert swapped
    assert not (case_b.case_root / "90_Temporary" / "open-race.txt").exists()
    assert not (case_a.case_root / "90_Temporary" / "open-race.txt").exists()


def test_write_rejects_case_substitution_at_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    original_replace = os.replace
    swapped = False

    def swap_before_replace(source: str | Path, target: str | Path) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            _substitute_temporary_roots(case_a, case_b, tmp_path)
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", swap_before_replace)
    with pytest.raises(WorkspaceBoundaryError):
        case_a.write_text(Path("90_Temporary") / "replace-race.txt", "must reject")

    assert swapped
    assert not (case_b.case_root / "90_Temporary" / "replace-race.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows directory sharing semantics")
def test_directory_guard_blocks_rename_while_held(tmp_path: Path) -> None:
    guarded = tmp_path / "guarded"
    renamed = tmp_path / "renamed"
    guarded.mkdir()
    stamp = workspace_module._identity_stamp(guarded, "guarded")

    try:
        with workspace_module._directory_guard(guarded, stamp, "guarded"), pytest.raises(OSError):
            guarded.rename(renamed)
    finally:
        if renamed.exists():
            renamed.rename(guarded)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle-bound deletion")
def test_cleanup_directory_handle_blocks_rename_until_exact_delete_and_close(
    tmp_path: Path,
) -> None:
    guarded = tmp_path / "guarded"
    renamed = tmp_path / "renamed"
    guarded.mkdir()
    handle = workspace_module._open_cleanup_directory(guarded, "guarded")

    try:
        with pytest.raises(OSError):
            guarded.rename(renamed)
        workspace_module._delete_open_directory(handle, "guarded")
    finally:
        workspace_module._close_handle(handle)

    assert not guarded.exists()
    assert not renamed.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle-bound deletion")
def test_nested_cleanup_blocks_foreign_replacement_before_exact_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "nested"
    displaced = tmp_path / "displaced-nested"
    child.mkdir(parents=True)
    parent_stamp = workspace_module._identity_stamp(parent, "parent")
    original_open_cleanup_directory = workspace_module._open_cleanup_directory
    original_delete_open_directory = workspace_module._delete_open_directory
    cleanup_handles: dict[int, Path] = {}
    blocked = False

    def track_open(path: Path, label: str) -> int:
        handle = original_open_cleanup_directory(path, label)
        cleanup_handles[handle] = path
        return handle

    def replace_before_delete(handle: int, label: str) -> None:
        nonlocal blocked
        if cleanup_handles.get(handle) == child:
            with pytest.raises(OSError):
                child.rename(displaced)
            blocked = True
        original_delete_open_directory(handle, label)

    monkeypatch.setattr(
        workspace_module,
        "_open_cleanup_directory",
        track_open,
        raising=False,
    )
    monkeypatch.setattr(
        workspace_module,
        "_delete_open_directory",
        replace_before_delete,
    )

    workspace_module._remove_created_tree_contents(parent, parent_stamp, "parent")

    assert blocked
    assert not displaced.exists(), "foreign replacement must not be installed"
    assert not child.exists(), "authoritative child directory must be deleted"


def test_failed_promotion_removes_destination_after_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    source = case.write_bytes(Path("90_Temporary") / "derived.feb", b"original")
    expected = workspace_module._sha256_file(source)

    def change_after_digest(path: Path) -> str:
        path.write_bytes(b"changed")
        return expected

    monkeypatch.setattr(workspace_module, "_sha256_file", change_after_digest)
    destination = Path("02_Model") / "derived.feb"
    with pytest.raises(ValueError, match="changed during copy"):
        case._copy_create_new(source, destination, expected_sha256=expected)
    assert not (case.case_root / destination).exists()


@pytest.mark.parametrize("failure", ["copy", "handle"])
def test_failed_case_creation_removes_partial_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    source = tmp_path / "input.feb"
    source.write_bytes(b"input")
    workspace = make_workspace(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("creation failed")

    if failure == "copy":
        monkeypatch.setattr("febio_cae_harness.workspace.shutil.copyfileobj", fail)
    else:
        monkeypatch.setattr(CaseWorkspace, "_from_manager", fail)

    with pytest.raises(RuntimeError, match="creation failed"):
        workspace.create_case("case-a", [source])
    assert not (workspace.cae_root / "case-a").exists()


def test_failed_case_creation_never_recursively_deletes_replaced_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.feb"
    source.write_bytes(b"input")
    workspace = make_workspace(tmp_path)
    case_path = workspace.cae_root / "case-a"
    original_open_directory = workspace_module._open_directory
    original_close_handle = workspace_module._close_handle
    open_handles: dict[int, Path] = {}
    case_guard_closed = False

    def track_open(path: Path, label: str) -> int:
        handle = original_open_directory(path, label)
        open_handles[handle] = path
        return handle

    def track_close(handle: int) -> None:
        nonlocal case_guard_closed
        if open_handles.pop(handle, None) == case_path:
            case_guard_closed = True
        original_close_handle(handle)

    monkeypatch.setattr(workspace_module, "_open_directory", track_open)
    monkeypatch.setattr(workspace_module, "_close_handle", track_close)

    original_rmtree = shutil.rmtree
    recursive_delete_paths: list[Path] = []
    foreign_marker_deleted = False
    recursive_delete_after_close = False

    def replace_before_recursive_delete(path: str | Path) -> None:
        nonlocal foreign_marker_deleted, recursive_delete_after_close
        if Path(path) == case_path:
            recursive_delete_paths.append(Path(path))
            recursive_delete_after_close = case_guard_closed
            displaced = tmp_path / "displaced-case"
            case_path.rename(displaced)
            case_path.mkdir()
            foreign_marker = case_path / "foreign-marker.txt"
            foreign_marker.write_text("foreign", encoding="utf-8")
            original_rmtree(path)
            foreign_marker_deleted = not foreign_marker.exists()
            return
        original_rmtree(path)

    monkeypatch.setattr(
        shutil,
        "rmtree",
        replace_before_recursive_delete,
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("creation failed")

    monkeypatch.setattr("febio_cae_harness.workspace.shutil.copyfileobj", fail)

    with pytest.raises(RuntimeError, match="creation failed"):
        workspace.create_case("case-a", [source])

    assert not recursive_delete_paths
    assert not recursive_delete_after_close
    assert not foreign_marker_deleted
    assert not case_path.exists()


def test_failed_case_creation_final_removal_fails_closed_on_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.feb"
    source.write_bytes(b"input")
    workspace = make_workspace(tmp_path)
    case_path = workspace.cae_root / "case-a"
    original_rmdir = Path.rmdir
    foreign_marker: Path | None = None

    def replace_before_final_rmdir(path: Path) -> None:
        nonlocal foreign_marker
        if path == case_path:
            path.rename(tmp_path / "displaced-case")
            path.mkdir()
            foreign_marker = path / "foreign-marker.txt"
            foreign_marker.write_text("foreign", encoding="utf-8")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", replace_before_final_rmdir)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("creation failed")

    monkeypatch.setattr("febio_cae_harness.workspace.shutil.copyfileobj", fail)

    if os.name == "nt":
        with pytest.raises(RuntimeError, match="creation failed"):
            workspace.create_case("case-a", [source])
        assert foreign_marker is None
    else:
        with pytest.raises(WorkspaceBoundaryError, match="cannot remove case root"):
            workspace.create_case("case-a", [source])
        assert foreign_marker is not None
        assert foreign_marker.read_text(encoding="utf-8") == "foreign"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle-bound deletion")
def test_failed_case_creation_deletes_authoritative_case_not_foreign_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.feb"
    source.write_bytes(b"input")
    workspace = make_workspace(tmp_path)
    case_path = workspace.cae_root / "case-a"
    original_rmdir = Path.rmdir
    original_identity_stamp = workspace_module._identity_stamp
    displaced = tmp_path / "displaced-case"
    identity_checks = 0
    blocked = False

    def track_identity_stamp(
        path: str | Path,
        label: str,
        expected: tuple[int, int] | None = None,
    ) -> tuple[int, int]:
        nonlocal identity_checks
        stamp = original_identity_stamp(path, label, expected)
        if Path(path) == case_path and label == "case root":
            identity_checks += 1
        return stamp

    def swap_case_path() -> None:
        nonlocal blocked
        assert identity_checks > 0
        with pytest.raises(OSError):
            case_path.rename(displaced)
        blocked = True

    def replace_before_final_rmdir(path: Path) -> None:
        if path == case_path:
            swap_case_path()
        original_rmdir(path)

    original_handle_delete = getattr(
        workspace_module,
        "_delete_open_directory",
        None,
    )

    def replace_before_handle_delete(handle: int, label: str) -> None:
        if label == "case root":
            swap_case_path()
        assert original_handle_delete is not None
        original_handle_delete(handle, label)

    monkeypatch.setattr(workspace_module, "_identity_stamp", track_identity_stamp)
    monkeypatch.setattr(Path, "rmdir", replace_before_final_rmdir)
    monkeypatch.setattr(
        workspace_module,
        "_delete_open_directory",
        replace_before_handle_delete,
        raising=False,
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("creation failed")

    monkeypatch.setattr("febio_cae_harness.workspace.shutil.copyfileobj", fail)

    with pytest.raises(RuntimeError, match="creation failed"):
        workspace.create_case("case-a", [source])

    assert blocked
    assert not displaced.exists(), "foreign replacement must not be installed"
    assert not case_path.exists(), "authoritative case directory must be deleted"
