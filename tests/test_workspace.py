from __future__ import annotations

import copy
import ctypes
import dataclasses
import json
import os
import pickle
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Any, cast

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


def windows_short_path(path: Path) -> Path:
    if os.name != "nt":
        pytest.skip("Windows alternate-path regression")
    buffer = ctypes.create_unicode_buffer(32_768)
    result = ctypes.windll.kernel32.GetShortPathNameW(os.fspath(path), buffer, len(buffer))
    if result == 0 or result >= len(buffer) or Path(buffer.value) == path:
        pytest.skip("8.3 short names are unavailable on this volume")
    return Path(buffer.value)


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


def test_create_case_rejects_concurrent_source_replacement_and_removes_partial_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.feb"
    displaced = tmp_path / "displaced-input.feb"
    source.write_bytes(b"authoritative input")
    workspace = make_workspace(tmp_path)
    original_open = Path.open
    replaced = False

    def replace_before_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[Any]:
        nonlocal replaced
        if path == source and mode == "rb" and not replaced:
            source.replace(displaced)
            replaced = True
            source.write_bytes(b"replacement input")
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", replace_before_open)

    with pytest.raises(WorkspaceBoundaryError, match="changed during case creation"):
        workspace.create_case("case-a", [source])

    assert replaced
    assert not (workspace.cae_root / "case-a").exists()
    assert displaced.read_bytes() == b"authoritative input"
    assert source.read_bytes() == b"replacement input"


def test_create_case_copies_one_stable_source_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.feb"
    source.write_bytes(b"authoritative input")
    workspace = make_workspace(tmp_path)
    original_open = Path.open
    source_opens = 0

    def track_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[Any]:
        nonlocal source_opens
        if path == source and mode == "rb":
            source_opens += 1
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", track_open)

    case = workspace.create_case("case-a", [source])

    assert source_opens == 1
    assert case.original_inputs[0].read_bytes() == b"authoritative input"
    assert source.read_bytes() == b"authoritative input"


def test_create_case_revalidates_all_sources_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    earlier_source = tmp_path / "earlier.feb"
    later_source = tmp_path / "later.feb"
    earlier_initial = b"earlier authoritative input"
    earlier_changed = b"earlier changed by test"
    later_initial = b"later authoritative input"
    earlier_source.write_bytes(earlier_initial)
    later_source.write_bytes(later_initial)
    workspace = make_workspace(tmp_path)
    original_copyfileobj = shutil.copyfileobj
    changed = False

    def change_earlier_while_copying_later(source_stream: IO[Any], target: IO[Any]) -> None:
        nonlocal changed
        if Path(source_stream.name) == later_source and not changed:
            target.write(source_stream.read(1))
            earlier_source.write_bytes(earlier_changed)
            changed = True
        original_copyfileobj(source_stream, target)

    monkeypatch.setattr(shutil, "copyfileobj", change_earlier_while_copying_later)

    with pytest.raises(WorkspaceBoundaryError, match="changed during case creation"):
        workspace.create_case("case-a", [earlier_source, later_source])

    assert changed
    assert not (workspace.cae_root / "case-a").exists()
    assert earlier_source.read_bytes() == earlier_changed
    assert later_source.read_bytes() == later_initial


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


def test_manager_rejects_windows_short_path_overlap_with_tool_tree(tmp_path: Path) -> None:
    tool_root = tmp_path / "tool-root-with-a-long-name"
    cae_root = tool_root / "nested-location-with-a-long-name" / "02_CAE"
    cae_root.mkdir(parents=True)
    alias = windows_short_path(cae_root)

    with pytest.raises((ValueError, WorkspaceBoundaryError)):
        ValidatedCaseWorkspace(tool_root=tool_root, cae_root=alias)
    assert tuple(cae_root.iterdir()) == ()


def test_manager_rejects_nonexistent_short_path_child_without_creating_it(
    tmp_path: Path,
) -> None:
    tool_root = tmp_path / "tool-root-with-a-long-name"
    tool_root.mkdir()
    alias = windows_short_path(tool_root)
    cae_root = alias / "02_CAE"

    with pytest.raises((ValueError, WorkspaceBoundaryError)):
        ValidatedCaseWorkspace(tool_root=tool_root, cae_root=cae_root)

    assert not (tool_root / "02_CAE").exists()


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


def test_attempt_factory_reuses_only_recorded_exact_creation_identity(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    EvidenceStore(case_a, IntentContract(engineering_question="Attempt identity")).record_attempt(
        "attempt-1"
    )
    attempt_root = case_a.temporary_root / "attempts" / "attempt-1"
    reopened_case_a = workspace.open_case("case-a")

    issued = AttemptWorkspace._from_manager(reopened_case_a, "attempt-1", attempt_root)

    assert issued.root == attempt_root
    displaced = case_a.temporary_root / "attempts" / "attempt-1-owned"
    foreign = case_b.temporary_root / "attempts" / "attempt-1"
    foreign.mkdir()
    foreign.joinpath("foreign.txt").write_text("foreign", encoding="utf-8")
    attempt_root.rename(displaced)
    foreign.rename(attempt_root)

    with pytest.raises(WorkspaceBoundaryError):
        AttemptWorkspace._from_manager(reopened_case_a, "attempt-1", attempt_root)
    assert attempt_root.joinpath("foreign.txt").read_text(encoding="utf-8") == "foreign"


def test_attempt_creation_identity_is_single_use_and_cross_case_scoped(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    intent = IntentContract(engineering_question="Attempt identity lifetime")
    EvidenceStore(case_a, intent).record_attempt("attempt-1")
    EvidenceStore(case_b, intent).record_attempt("attempt-1")
    root_a = case_a.temporary_root / "attempts" / "attempt-1"
    root_b = case_b.temporary_root / "attempts" / "attempt-1"

    issued_a = AttemptWorkspace._from_manager(workspace.open_case("case-a"), "attempt-1", root_a)
    issued_b = AttemptWorkspace._from_manager(workspace.open_case("case-b"), "attempt-1", root_b)

    assert issued_a.root == root_a
    assert issued_b.root == root_b
    with pytest.raises(WorkspaceBoundaryError, match="creation identity is required"):
        AttemptWorkspace._from_manager(case_a, "attempt-1", root_a)
    with pytest.raises(WorkspaceBoundaryError, match="creation identity is required"):
        AttemptWorkspace._from_manager(case_b, "attempt-1", root_b)


def test_unclaimed_attempt_creation_identities_have_a_bounded_lifetime(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    limit = 128

    for index in range(limit + 1):
        relative = Path("90_Temporary") / "attempts" / f"bounded-{index:03d}"
        with case._exact_transaction() as exact:
            exact.make_directory(relative)

    case_stamp = workspace_module._registered_case_stamp(case)
    registrations = {
        key
        for key in workspace_module._ATTEMPT_ROOT_STAMPS
        if key[0] == case.case_root and key[1] == case_stamp
    }

    assert len(registrations) <= limit
    with pytest.raises(WorkspaceBoundaryError, match="creation identity is required"):
        AttemptWorkspace._from_manager(
            case,
            "bounded-000",
            case.temporary_root / "attempts" / "bounded-000",
        )
    newest = AttemptWorkspace._from_manager(
        case,
        f"bounded-{limit:03d}",
        case.temporary_root / "attempts" / f"bounded-{limit:03d}",
    )
    assert newest.attempt_id == f"bounded-{limit:03d}"


def test_posix_pending_attempt_cleanup_fails_closed_without_namespace_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction = object.__new__(workspace_module._ExactCaseTransaction)
    root = tmp_path / "case-a"
    root_stamp = (101, 202)
    parts = ("90_Temporary", "attempts", "attempt-1")
    current = cast(Any, SimpleNamespace(expected=(303, 404), validate=lambda: None))
    parent = SimpleNamespace(handle=505)
    transaction.root = root
    transaction._root_stamp = root_stamp
    transaction._directories = {parts: current}
    claim_key = (root, root_stamp, parts[-1])
    mutation_calls: list[str] = []
    claim_owner = SimpleNamespace(close=lambda: mutation_calls.append("release"))
    claim = (root.joinpath(*parts), current.expected, claim_owner)
    monkeypatch.setitem(workspace_module._ATTEMPT_ROOT_STAMPS, claim_key, claim)

    def directory(_self: Any, requested: tuple[str, ...]) -> Any:
        return current if requested == parts else parent

    def rmdir(name: str, *, dir_fd: int) -> None:
        assert name == parts[-1]
        assert dir_fd == parent.handle
        mutation_calls.append("rmdir")

    monkeypatch.setattr(workspace_module._ExactCaseTransaction, "_directory", directory)
    monkeypatch.setattr(workspace_module, "os", SimpleNamespace(name="posix", rmdir=rmdir))

    with pytest.raises(
        WorkspaceBoundaryError,
        match="exact empty directory deletion is unavailable",
    ):
        transaction.remove_empty_directory(Path(*parts))

    assert mutation_calls == []
    assert workspace_module._ATTEMPT_ROOT_STAMPS[claim_key] is claim


def test_attempt_write_creates_nested_parents_under_exact_authority(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    attempt = case.allocate_attempt("attempt-1")

    written = attempt.write_text("nested/child/model.feb", "owned")

    assert written == attempt.root / "nested" / "child" / "model.feb"
    assert written.read_text(encoding="utf-8") == "owned"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows rename substitution")
def test_attempt_nested_write_rejects_substituted_created_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    attempt_a = case_a.allocate_attempt("attempt-1")
    attempt_b = case_b.allocate_attempt("attempt-1")
    foreign = attempt_b.root / "nested"
    foreign.mkdir()
    foreign.joinpath("foreign.txt").write_text("foreign", encoding="utf-8")
    displaced = attempt_a.root / "nested-owned"
    original_make_directory = workspace_module._ExactCaseTransaction.make_directory
    substituted = False
    blocked = False

    def substitute_created_parent(
        self: Any,
        relative_path: str | Path,
    ) -> tuple[int, int]:
        nonlocal blocked, substituted
        stamp = original_make_directory(self, relative_path)
        target = self.root / Path(relative_path)
        if not substituted and target == attempt_a.root / "nested":
            try:
                target.rename(displaced)
            except OSError:
                blocked = True
            else:
                foreign.rename(target)
                substituted = True
        return stamp

    monkeypatch.setattr(
        workspace_module._ExactCaseTransaction,
        "make_directory",
        substitute_created_parent,
    )

    try:
        written = attempt_a.write_text("nested/model.feb", "owned")
    except WorkspaceBoundaryError:
        written = None

    assert blocked or (substituted and written is None)
    if blocked:
        assert written is not None
        assert written.read_text(encoding="utf-8") == "owned"
        assert foreign.joinpath("foreign.txt").read_text(encoding="utf-8") == "foreign"
        assert not displaced.exists()
    else:
        assert (attempt_a.root / "nested" / "foreign.txt").read_text(encoding="utf-8") == (
            "foreign"
        )
        assert not (attempt_a.root / "nested" / "model.feb").exists()
        assert tuple(displaced.iterdir()) == ()


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


def test_write_does_not_reopen_case_path_at_atomic_replace(
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
    written = case_a.write_text(Path("90_Temporary") / "replace-race.txt", "owned")

    assert not swapped
    assert written.read_text(encoding="utf-8") == "owned"
    assert not (case_b.case_root / "90_Temporary" / "replace-race.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows rename substitution")
def test_replace_rejects_target_substitution_at_exact_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    relative = Path("90_Temporary") / "commit-race.txt"
    target = case.case_root / relative
    displaced = target.with_name("commit-race-owned.txt")
    foreign = target.with_name("commit-race-foreign.txt")
    case.write_text(relative, "authoritative-old")
    foreign.write_text("foreign", encoding="utf-8")
    original_replace = workspace_module._replace_exact_entry
    substituted = False
    blocked = False

    def substitute_after_validation(*args: Any, **kwargs: Any) -> None:
        nonlocal blocked, substituted
        try:
            target.rename(displaced)
        except OSError:
            blocked = True
            raise
        foreign.rename(target)
        substituted = True
        original_replace(*args, **kwargs)

    monkeypatch.setattr(workspace_module, "_replace_exact_entry", substitute_after_validation)

    with pytest.raises(WorkspaceBoundaryError):
        case.write_text(relative, "authoritative-new")

    assert blocked or substituted
    if blocked:
        assert target.read_text(encoding="utf-8") == "authoritative-old"
        assert foreign.read_text(encoding="utf-8") == "foreign"
    else:
        assert target.read_text(encoding="utf-8") == "foreign"
    retained = {
        child.read_text(encoding="utf-8") for child in target.parent.iterdir() if child.is_file()
    }
    assert "authoritative-old" in retained
    assert "authoritative-new" not in retained


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


@pytest.mark.skipif(os.name != "nt", reason="requires Windows regular-file cleanup")
def test_regular_file_cleanup_substitution_never_deletes_foreign_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "nested.txt"
    displaced = tmp_path / "displaced-nested.txt"
    parent.mkdir()
    child.write_text("authoritative", encoding="utf-8")
    parent_stamp = workspace_module._identity_stamp(parent, "parent")
    original_unlink = Path.unlink
    pathname_unlink_calls: list[Path] = []
    foreign_replacement_deleted = False

    def substitute_before_unlink(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal foreign_replacement_deleted
        if path == child:
            path.rename(displaced)
            path.write_text("foreign replacement", encoding="utf-8")
        pathname_unlink_calls.append(path)
        original_unlink(path, missing_ok=missing_ok)
        if path == child:
            foreign_replacement_deleted = not path.exists()

    monkeypatch.setattr(Path, "unlink", substitute_before_unlink)
    workspace_module._remove_created_tree_contents(parent, parent_stamp, "parent")

    assert (
        pathname_unlink_calls,
        foreign_replacement_deleted,
        displaced.exists(),
        child.exists(),
    ) == ([], False, False, False)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows regular-file cleanup")
def test_regular_file_cleanup_never_calls_pathname_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "nested.txt"
    parent.mkdir()
    child.write_text("authoritative", encoding="utf-8")
    parent_stamp = workspace_module._identity_stamp(parent, "parent")
    pathname_unlink_calls: list[Path] = []

    def record_unlink(path: Path, *args: object, **kwargs: object) -> None:
        del args, kwargs
        pathname_unlink_calls.append(path)

    monkeypatch.setattr(Path, "unlink", record_unlink)
    workspace_module._remove_created_tree_contents(parent, parent_stamp, "parent")

    assert pathname_unlink_calls == []
    assert not child.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows regular-file cleanup")
def test_regular_file_cleanup_handle_blocks_rename_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "nested.txt"
    displaced = tmp_path / "displaced-nested.txt"
    parent.mkdir()
    child.write_text("authoritative", encoding="utf-8")
    parent_stamp = workspace_module._identity_stamp(parent, "parent")
    original_open = workspace_module._open_cleanup_file
    original_delete = workspace_module._delete_open_file
    original_close = workspace_module._close_handle
    cleanup_handles: dict[int, Path] = {}
    closed_handles: list[int] = []
    blocked = False

    def track_open(path: Path, label: str) -> int:
        handle = original_open(path, label)
        cleanup_handles[handle] = path
        return handle

    def replace_before_delete(handle: int, label: str) -> None:
        nonlocal blocked
        if cleanup_handles.get(handle) == child:
            with pytest.raises(OSError):
                child.rename(displaced)
            blocked = True
        original_delete(handle, label)

    def track_close(handle: int) -> None:
        if handle in cleanup_handles:
            closed_handles.append(handle)
        original_close(handle)

    monkeypatch.setattr(workspace_module, "_open_cleanup_file", track_open)
    monkeypatch.setattr(workspace_module, "_delete_open_file", replace_before_delete)
    monkeypatch.setattr(workspace_module, "_close_handle", track_close)

    workspace_module._remove_created_tree_contents(parent, parent_stamp, "parent")

    assert blocked
    assert len(cleanup_handles) == 1
    assert closed_handles == list(cleanup_handles)
    assert not displaced.exists()
    assert not child.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows regular-file cleanup")
def test_open_cleanup_file_uses_delete_only_access_without_delete_sharing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = tmp_path / "nested.txt"
    child.write_text("authoritative", encoding="utf-8")
    calls: list[tuple[Path, int, int, int, int, str]] = []

    def record_create(
        path: Path,
        access: int,
        share: int,
        disposition: int,
        flags: int,
        label: str,
    ) -> int:
        calls.append((path, access, share, disposition, flags, label))
        return 123

    monkeypatch.setattr(workspace_module, "_windows_create", record_create)
    assert workspace_module._open_cleanup_file(child, "file") == 123

    assert calls == [(child, 0x00010000 | 0x00000080, 0x0001 | 0x0002, 3, 0x00200000, "file")]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows regular-file cleanup")
def test_regular_file_cleanup_closes_handle_when_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "nested.txt"
    parent.mkdir()
    child.write_text("authoritative", encoding="utf-8")
    parent_stamp = workspace_module._identity_stamp(parent, "parent")
    original_open = workspace_module._open_cleanup_file
    original_close = workspace_module._close_handle
    opened_handles: list[int] = []
    closed_handles: list[int] = []

    def track_open(path: Path, label: str) -> int:
        handle = original_open(path, label)
        opened_handles.append(handle)
        return handle

    def fail_delete(handle: int, label: str) -> None:
        del handle, label
        raise WorkspaceBoundaryError("injected exact-file deletion failure")

    def track_close(handle: int) -> None:
        if handle in opened_handles:
            closed_handles.append(handle)
        original_close(handle)

    monkeypatch.setattr(workspace_module, "_open_cleanup_file", track_open)
    monkeypatch.setattr(workspace_module, "_delete_open_file", fail_delete)
    monkeypatch.setattr(workspace_module, "_close_handle", track_close)

    with pytest.raises(WorkspaceBoundaryError, match="injected exact-file deletion failure"):
        workspace_module._remove_created_tree_contents(parent, parent_stamp, "parent")

    assert len(opened_handles) == 1
    assert closed_handles == opened_handles
    assert child.exists()


def test_cleanup_fails_closed_without_exact_object_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "nested.txt"
    parent.mkdir()
    child.write_text("authoritative", encoding="utf-8")
    parent_stamp = workspace_module._identity_stamp(parent, "parent")
    pathname_unlink_calls: list[Path] = []
    pathname_rmdir_calls: list[Path] = []

    def record_unlink(path: Path, *args: object, **kwargs: object) -> None:
        del args, kwargs
        pathname_unlink_calls.append(path)

    def record_rmdir(path: Path) -> None:
        pathname_rmdir_calls.append(path)

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(Path, "unlink", record_unlink)
    monkeypatch.setattr(Path, "rmdir", record_rmdir)

    with pytest.raises(WorkspaceBoundaryError, match="exact-object cleanup is unavailable"):
        workspace_module._remove_created_tree_contents(parent, parent_stamp, "parent")

    assert pathname_unlink_calls == []
    assert pathname_rmdir_calls == []
    assert parent.exists()
    assert child.read_text(encoding="utf-8") == "authoritative"


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

    if os.name == "nt":
        with pytest.raises(RuntimeError, match="creation failed"):
            workspace.create_case("case-a", [source])
        assert not (workspace.cae_root / "case-a").exists()
    else:
        with pytest.raises(WorkspaceBoundaryError, match="exact-object cleanup is unavailable"):
            workspace.create_case("case-a", [source])
        assert (workspace.cae_root / "case-a").exists()


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
    if os.name == "nt":
        assert not case_path.exists()
    else:
        assert case_path.exists()


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
        with pytest.raises(WorkspaceBoundaryError, match="exact-object cleanup is unavailable"):
            workspace.create_case("case-a", [source])
        assert foreign_marker is None
        assert case_path.exists()


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


@pytest.mark.parametrize(
    ("after_close", "released"),
    [((2,), True), (OSError("probe failed"), False)],
)
def test_exact_owner_never_closes_foreign_or_indeterminate_reuse(
    after_close: tuple[int, ...] | OSError, released: bool
) -> None:
    identity: list[tuple[int, ...] | OSError] = [(1,)]
    close_calls: list[int] = []

    def identity_of(handle: int) -> tuple[int, ...]:
        if isinstance(identity[0], OSError):
            raise identity[0]
        return identity[0]

    def failed_close(handle: int) -> None:
        close_calls.append(handle)
        identity[0] = after_close
        raise OSError("close status lost")

    owner = workspace_module._ExactOwner(17, (1,), identity_of, failed_close, "synthetic owner")
    with pytest.raises(WorkspaceBoundaryError):
        owner.close()
    if released:
        owner.close()
    else:
        with pytest.raises(WorkspaceBoundaryError, match="remains indeterminate"):
            owner.close()
    assert owner.released is released
    assert close_calls == [17]


@pytest.mark.parametrize("target_existed", [True, False], ids=["existing", "new"])
def test_posix_exact_replace_helper_fails_before_namespace_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_existed: bool,
) -> None:
    mutations: list[str] = []

    def owner(handle: int, label: str) -> Any:
        return workspace_module._ExactOwner(
            handle,
            (handle,),
            lambda value: (value,),
            lambda _value: None,
            label,
        )

    def reject_mutation(operation: str) -> Callable[..., None]:
        def reject(*args: object, **kwargs: object) -> None:
            del args, kwargs
            mutations.append(operation)
            raise AssertionError(f"unexpected POSIX namespace {operation}")

        return reject

    parent = owner(17, "synthetic parent")
    temporary = owner(18, "synthetic temporary")
    existing = owner(19, "synthetic existing") if target_existed else None
    state = workspace_module._ExactReplacementState()
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "replace", reject_mutation("replace"))
    monkeypatch.setattr(os, "link", reject_mutation("link"))
    monkeypatch.setattr(os, "unlink", reject_mutation("unlink"))

    with pytest.raises(
        WorkspaceBoundaryError,
        match="exact namespace replacement is unavailable on this platform",
    ):
        workspace_module._replace_exact_entry(
            parent=parent,
            parent_path=tmp_path,
            temporary_name=".target.txt.owned",
            target_name="target.txt",
            temporary=temporary,
            existing=existing,
            target_existed=target_existed,
            state=state,
        )

    assert mutations == []
    assert not parent.released
    assert not temporary.released
    assert existing is None or not existing.released
    assert state == workspace_module._ExactReplacementState()


def test_posix_discard_retains_exact_owner_without_namespace_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutations: list[str] = []
    parent = workspace_module._ExactOwner(
        17,
        (17,),
        lambda value: (value,),
        lambda _value: None,
        "synthetic parent",
    )
    discarded = workspace_module._ExactOwner(
        18,
        (18,),
        lambda value: (value,),
        lambda _value: None,
        "synthetic temporary",
    )
    transaction = object.__new__(workspace_module._ExactCaseTransaction)
    monkeypatch.setattr(
        workspace_module._ExactCaseTransaction,
        "_directory",
        lambda _self, _parts: parent,
    )
    monkeypatch.setattr(
        workspace_module._ExactCaseTransaction,
        "_verify_file",
        lambda _self, _parts, _owner, _label: discarded.expected,
    )

    def reject_unlink(*args: object, **kwargs: object) -> None:
        del args, kwargs
        mutations.append("unlink")
        raise AssertionError("unexpected POSIX namespace unlink")

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "unlink", reject_unlink)
    primary = RuntimeError("primary failure")

    transaction._discard(("90_Temporary", "owned.tmp"), discarded, primary)

    assert mutations == []
    assert getattr(primary, "__notes__", ()) == [
        "synthetic temporary cleanup failed: exact-object cleanup is unavailable on this platform",
    ]
    assert not parent.released
    assert not discarded.released


def test_posix_replace_bytes_fails_before_temporary_namespace_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    transaction = case._exact_transaction()
    created: list[tuple[tuple[str, ...], str, int]] = []

    def reject_new_file(
        _self: Any,
        parts: tuple[str, ...],
        label: str,
        mode: int = 0o666,
    ) -> Any:
        created.append((parts, label, mode))
        raise AssertionError("unexpected POSIX temporary namespace creation")

    transaction.__enter__()
    try:
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(
            workspace_module._ExactCaseTransaction,
            "_new_file",
            reject_new_file,
        )
        with pytest.raises(
            WorkspaceBoundaryError,
            match="exact namespace replacement is unavailable on this platform",
        ):
            transaction.replace_bytes("90_Temporary/value.txt", b"new")
        assert created == []
    finally:
        monkeypatch.undo()
        transaction.close()


def test_exact_transaction_preserves_body_exception_and_retains_failed_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    transaction = case._exact_transaction()

    def fail_close(_handle: int) -> None:
        raise OSError("synthetic close failure")

    monkeypatch.setattr(workspace_module, "_close_handle", fail_close)
    with pytest.raises(RuntimeError, match="primary body failure") as caught, transaction:
        raise RuntimeError("primary body failure")

    assert any("cleanup" in note for note in getattr(caught.value, "__notes__", ()))
    assert any(not owner.released for owner in transaction._owners)

    monkeypatch.undo()
    with pytest.raises(WorkspaceBoundaryError, match="indeterminate"):
        transaction.close()
    assert any(owner.indeterminate for owner in transaction._owners)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exact-handle replacement")
def test_exact_replacement_never_installs_substituted_source_or_leaks_owned_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    outside = tmp_path / "foreign.txt"
    outside.write_bytes(b"foreign")
    target = case.temporary_root / "value.txt"
    attacked = False
    owned_recovery = case.temporary_root / ".owned-recovery"
    original_replace = os.replace

    with case._exact_transaction() as exact:
        exact.replace_bytes("90_Temporary/value.txt", b"before")

    def substitute_source_before_path_replace(
        source: str | Path,
        destination: str | Path,
    ) -> None:
        nonlocal attacked
        source_path = Path(source)
        destination_path = Path(destination)
        if destination_path == target and source_path.name.startswith(".value.txt."):
            attacked = True
            source_path.rename(owned_recovery)
            os.link(outside, source_path)
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", substitute_source_before_path_replace)

    with case._exact_transaction() as exact:
        exact.replace_bytes("90_Temporary/value.txt", b"after")

    assert not attacked, "the final install must not reopen the temporary by path"
    assert target.read_bytes() == b"after"
    assert outside.read_bytes() == b"foreign"
    assert outside.stat().st_nlink == 1
    assert not owned_recovery.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows hard-link semantics")
def test_atomic_replace_never_mutates_a_substituted_foreign_hard_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    outside = tmp_path / "foreign.txt"
    outside.write_bytes(b"foreign")
    target = case.temporary_root / "value.txt"
    original_chmod = os.chmod
    attacked = False

    def substitute_before_path_chmod(
        path: str | Path,
        mode: int,
    ) -> None:
        nonlocal attacked
        candidate = Path(path)
        if candidate.name.startswith(".value.txt."):
            attacked = True
            candidate.unlink()
            os.link(outside, candidate)
        original_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", substitute_before_path_chmod)

    written = case.write_bytes("90_Temporary/value.txt", b"owned")

    assert not attacked, "mode and install effects must remain tied to the open file"
    assert written == target
    assert target.read_bytes() == b"owned"
    assert outside.read_bytes() == b"foreign"
    assert outside.stat().st_nlink == 1


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle-relative rename")
def test_windows_exact_file_primitives_reuse_ctypes_declarations(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"source")
    parent_handle = workspace_module._open_directory(tmp_path, "exact parent")
    pointer_cache = cast(dict[object, object], vars(ctypes)["_pointer_type_cache"])

    def inspect_once() -> None:
        workspace_module._windows_file_identity(parent_handle, "exact parent")
        descriptor = workspace_module._open_exact_file_descriptor(
            parent_handle,
            tmp_path,
            source.name,
            flags=os.O_RDONLY,
            access=0x80000000,
            share=0x0001 | 0x0002,
            disposition=3,
        )
        os.close(descriptor)

    try:
        inspect_once()
        baseline = len(pointer_cache)
        for _ in range(32):
            inspect_once()
        assert len(pointer_cache) == baseline
    finally:
        workspace_module._close_handle(parent_handle)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows handle-relative rename")
def test_windows_exact_rename_is_rooted_in_held_parent_not_foreign_path(tmp_path: Path) -> None:
    owned_parent = tmp_path / "owned"
    foreign_parent = tmp_path / "foreign"
    owned_parent.mkdir()
    foreign_parent.mkdir()
    foreign_target = foreign_parent / "target.txt"
    foreign_target.write_bytes(b"foreign")
    parent_handle = workspace_module._open_directory(owned_parent, "owned parent")
    descriptor = workspace_module._open_exact_file_descriptor(
        parent_handle,
        owned_parent,
        "temporary.txt",
        flags=os.O_RDWR | os.O_CREAT | os.O_EXCL,
        access=0xC0010000,
        share=0x0001 | 0x0002 | 0x0004,
        disposition=1,
    )
    try:
        os.write(descriptor, b"owned")
        os.fsync(descriptor)

        workspace_module._windows_rename_open_file(
            descriptor,
            parent_handle,
            foreign_parent,
            "target.txt",
            replace=True,
            label="exact test file",
        )

        os.lseek(descriptor, 0, os.SEEK_SET)
        assert os.read(descriptor, 5) == b"owned"
        assert foreign_target.read_bytes() == b"foreign"
    finally:
        os.close(descriptor)
        workspace_module._close_handle(parent_handle)
    assert (owned_parent / "target.txt").read_bytes() == b"owned"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows rename substitution")
def test_make_directory_rejects_substituted_created_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    relative = Path("90_Temporary") / "attempts" / "attempt-1"
    foreign = case_b.case_root / relative
    foreign.mkdir()
    foreign.joinpath("foreign.txt").write_text("foreign", encoding="utf-8")
    displaced = case_a.temporary_root / "attempts" / "attempt-1-owned"
    original_directory = workspace_module._ExactCaseTransaction._directory
    substituted = False
    blocked = False

    def substitute_before_open(
        self: Any,
        parts: tuple[str, ...],
    ) -> Any:
        nonlocal blocked, substituted
        target = self.root.joinpath(*parts)
        if not substituted and target == case_a.case_root / relative and target.exists():
            try:
                target.rename(displaced)
            except OSError:
                blocked = True
            else:
                substituted = True
                foreign.rename(target)
        return original_directory(self, parts)

    monkeypatch.setattr(
        workspace_module._ExactCaseTransaction,
        "_directory",
        substitute_before_open,
    )

    try:
        with case_a._exact_transaction() as exact:
            exact.make_directory(relative)
    except WorkspaceBoundaryError:
        failed = True
    else:
        failed = False

    assert blocked or (substituted and failed)
    if blocked:
        assert tuple((case_a.case_root / relative).iterdir()) == ()
        assert foreign.joinpath("foreign.txt").read_text(encoding="utf-8") == "foreign"
        assert not displaced.exists()
    else:
        assert (case_a.case_root / relative / "foreign.txt").read_text(encoding="utf-8") == (
            "foreign"
        )
        assert tuple(displaced.iterdir()) == ()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows rename substitution")
def test_allocate_attempt_rejects_substituted_root_before_issuance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case_a = workspace.create_case("case-a")
    case_b = workspace.create_case("case-b")
    foreign = case_b.temporary_root / "attempts" / "attempt-1"
    foreign.mkdir()
    foreign.joinpath("foreign.txt").write_text("foreign", encoding="utf-8")
    displaced = case_a.temporary_root / "attempts" / "attempt-1-owned"
    original_factory = AttemptWorkspace._from_manager
    substituted = False
    blocked = False

    def substitute_before_issue(
        cls: type[AttemptWorkspace],
        /,
        case_workspace: CaseWorkspace,
        attempt_id: str,
        root: Path,
        expected_root_stamp: tuple[int, int] | None = None,
    ) -> AttemptWorkspace:
        del cls
        nonlocal blocked, substituted
        if not substituted:
            try:
                root.rename(displaced)
            except OSError:
                blocked = True
            else:
                substituted = True
                foreign.rename(root)
        return original_factory(case_workspace, attempt_id, root, expected_root_stamp)

    monkeypatch.setattr(AttemptWorkspace, "_from_manager", classmethod(substitute_before_issue))

    try:
        attempt = case_a.allocate_attempt("attempt-1")
    except WorkspaceBoundaryError:
        attempt = None

    assert blocked or (substituted and attempt is None)
    if blocked:
        assert attempt is not None
        assert attempt.root == case_a.temporary_root / "attempts" / "attempt-1"
        assert not displaced.exists()
        assert foreign.joinpath("foreign.txt").read_text(encoding="utf-8") == "foreign"
    else:
        assert (case_a.temporary_root / "attempts" / "attempt-1" / "foreign.txt").read_text(
            encoding="utf-8"
        ) == "foreign"
        assert tuple(displaced.iterdir()) == ()


def test_exact_owner_never_retries_ambiguous_close_with_same_identity() -> None:
    close_calls: list[int] = []

    def same_identity(_handle: int) -> tuple[int, ...]:
        return (1,)

    def lost_close_status(handle: int) -> None:
        close_calls.append(handle)
        raise OSError("close status lost after value reuse")

    owner = workspace_module._ExactOwner(
        17,
        (1,),
        same_identity,
        lost_close_status,
        "synthetic owner",
    )

    with pytest.raises(WorkspaceBoundaryError, match="indeterminate"):
        owner.close()
    with pytest.raises(WorkspaceBoundaryError, match="indeterminate"):
        owner.close()

    assert owner.indeterminate
    assert close_calls == [17]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows CloseHandle")
def test_windows_close_handle_reports_lost_close_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Kernel32:
        @staticmethod
        def CloseHandle(_handle: object) -> int:
            return 0

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: Kernel32())
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 6)

    with pytest.raises(OSError, match="CloseHandle"):
        workspace_module._close_handle(17)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows rename sharing semantics")
def test_new_replacement_blocks_substitution_after_final_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    relative = Path("90_Temporary") / "final-seam.txt"
    target = case.case_root / relative
    displaced = target.with_name("final-seam-owned.txt")
    foreign = target.with_name("final-seam-foreign.txt")
    foreign.write_bytes(b"foreign")
    original_validate = workspace_module._ExactCaseTransaction.validate
    original_windows_create = workspace_module._windows_create
    blocked = False
    substituted = False
    absolute_target_reopens: list[Path] = []

    def reject_absolute_target_reopen(
        path: Path,
        access: int,
        share: int,
        disposition: int,
        flags: int,
        label: str,
    ) -> int:
        if path == target:
            absolute_target_reopens.append(path)
        return original_windows_create(path, access, share, disposition, flags, label)

    def substitute_after_final_validation(self: Any) -> None:
        nonlocal blocked, substituted
        original_validate(self)
        try:
            target.rename(displaced)
        except OSError:
            blocked = True
            return
        foreign.rename(target)
        substituted = True

    monkeypatch.setattr(
        workspace_module._ExactCaseTransaction,
        "validate",
        substitute_after_final_validation,
    )
    monkeypatch.setattr(workspace_module, "_windows_create", reject_absolute_target_reopen)

    with case._exact_transaction() as exact:
        exact.replace_bytes(relative, b"authoritative-new")

    assert blocked
    assert not substituted
    assert target.read_bytes() == b"authoritative-new"
    assert foreign.read_bytes() == b"foreign"
    assert not displaced.exists()
    assert absolute_target_reopens == []


@pytest.mark.skipif(os.name != "nt", reason="requires Windows exact-handle replacement")
@pytest.mark.parametrize("failure_seam", ["precommit-restore-retry", "postcommit-old-cleanup"])
def test_replacement_failure_retains_an_authoritative_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_seam: str,
) -> None:
    workspace = make_workspace(tmp_path)
    case = workspace.create_case("case-a")
    relative = Path("90_Temporary") / "rollback-seam.txt"
    target = case.case_root / relative
    case.write_bytes(relative, b"authoritative-old")

    if failure_seam == "precommit-restore-retry":
        original_rename = workspace_module._windows_rename_open_file
        restoration_attempts = 0

        def fail_commit_and_first_restore(
            descriptor: int,
            parent_handle: int,
            parent_path: Path,
            target_name: str,
            *,
            replace: bool,
            label: str,
        ) -> None:
            nonlocal restoration_attempts
            if label == "exact replacement temporary" and target_name == target.name:
                raise WorkspaceBoundaryError("injected new commit failure")
            if label == "exact replacement prior target recovery":
                restoration_attempts += 1
                if restoration_attempts == 1:
                    raise WorkspaceBoundaryError("injected first restoration failure")
            original_rename(
                descriptor,
                parent_handle,
                parent_path,
                target_name,
                replace=replace,
                label=label,
            )

        monkeypatch.setattr(
            workspace_module,
            "_windows_rename_open_file",
            fail_commit_and_first_restore,
        )

        with pytest.raises(WorkspaceBoundaryError, match="cannot replace exact file") as caught:
            case.write_bytes(relative, b"authoritative-new")

        assert restoration_attempts == 2
        assert target.read_bytes() == b"authoritative-old"
        assert isinstance(caught.value.__cause__, WorkspaceBoundaryError)
        assert "injected new commit failure" in str(caught.value.__cause__)
        assert any(
            "first restoration failure" in note
            for note in getattr(caught.value.__cause__, "__notes__", ())
        )
    else:
        original_delete = workspace_module._delete_open_file
        original_fstat = os.fstat
        fail_next_identity = False
        identity_failed = False

        def delete_then_fail_identity(handle: int, label: str) -> None:
            nonlocal fail_next_identity
            original_delete(handle, label)
            if label == "exact replacement prior target":
                fail_next_identity = True

        def fail_post_delete_identity(descriptor: int) -> os.stat_result:
            nonlocal fail_next_identity, identity_failed
            if fail_next_identity:
                fail_next_identity = False
                identity_failed = True
                raise OSError("injected post-delete identity failure")
            return original_fstat(descriptor)

        monkeypatch.setattr(workspace_module, "_delete_open_file", delete_then_fail_identity)
        monkeypatch.setattr(os, "fstat", fail_post_delete_identity)

        with pytest.raises(WorkspaceBoundaryError, match="cannot replace exact file") as caught:
            case.write_bytes(relative, b"authoritative-new")

        assert identity_failed
        assert target.read_bytes() == b"authoritative-new"
        assert isinstance(caught.value.__cause__, OSError)
        assert "post-delete identity failure" in str(caught.value.__cause__)

    retained = {
        child.read_bytes()
        for child in target.parent.iterdir()
        if child.is_file() and "rollback-seam" in child.name
    }
    assert b"authoritative-old" in retained or b"authoritative-new" in retained
