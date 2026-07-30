# FEBio CAE Harness Phase 1B Runner and Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 承認済み既存FEBをimmutableにattemptへ引き渡し、FEBio 4.12をWindows Job Object内で所有実行し、LOGと公式FBSの独立証拠を複合判定して、検証済み成果物だけをcreate-new昇格できるPhase 1B runner/evidence層を実装する。

**Architecture:** Phase 1Aが提供するcase state、承認済み`InputRecord`、case lock、hash、create-new IOを入口契約とする。Phase 1Bはmodel adoption、attempt/resume/run lease、typed command、Windows native lifecycle、LOG evidence、隔離FBS bridge、preflight/completion/result validationを小さいモジュールへ分け、solver実行中もcase lockを長時間保持しない。`SOLVED`はprocess、LOG、fresh XPLT、公式FBSの要求field存在・非空・有限性が全て揃った時だけ成立し、`RESULT_VERIFIED`はさらにmodel/mesh/geometry/kinematicsを満たした時だけ成立する。

**Tech Stack:** Python 3.12、setuptools 83.0.0、jsonschema 4.26.0、psutil 7.2.2、pytest 9.1.1、Windows `CreateProcessW`/Job Objects、FEBio 4.12、CPython 3.13.5 embeddable x64、公式FBS `fbs.cp313-win_amd64.pyd`、PowerShell 5.1

## Global Constraints

- 受入れ契約の正本は`docs/superpowers/plans/2026-07-30-febio-llm-cae-harness-phase1.md`である。Phase 1A Setup Gateが記録したplan-suite commit/blobへ本サブプランをbindし、同じblob setだけをconsumesする。review中に変化するworking-tree hashをhardcodeせず、実行開始時はSetup Gate recordと現在のplan-suite blob identityが一致しなければ進まない。
- 実装場所は`C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\_worktrees\febio-cae-harness-phase1`、branchは`codex/febio-cae-harness-phase1`である。
- Phase 1AのTasks 1–6、全focused tests、`tests/contract/test_installed_resources.py`がgreenで、worktreeがcleanであることを開始条件とする。
- 実モデル、実FEB、XPLT、production LOG、STEP/STP、MSH、INP、VTU、VOLをtool repositoryへ置かない。Git fixtureは小型合成データだけとする。
- `CASE_MANIFEST.json`以外の永久CAE成果物はcreate-newである。collisionは同一hashのidempotent reuse、またはcase lock下のunique nameだけを許し、既存の異なるbytesを上書きしない。
- attempt配下の部分LOG、XPLT、dump、stdout、stderrは失敗時も保存するが、`RESULT_VERIFIED`前に`03_Result`、`04_Report`、`05_Verification`へ昇格しない。
- solver実行中はattempt-run lockを保持する。short case lockはlease作成、PID binding、finalize、state/event更新だけで保持し、native process待機中は保持しない。
- production FEBio argvは`febio4.exe -i input.feb -o solver.log -p solver.xplt`だけとし、shell、任意`extra_args`、test-mode bypassを公開しない。
- 不明warning、INIT-only、missing output、stale XPLT、FBS unreadable、要求したfield associationの欠落・曖昧性、hash driftは`SOLVED`を禁止する。要求外associationにも値があることだけではblockしない。
- model/FBS coordinate identityは全nodeをID昇順に並べ、各IDをuint64 big-endian、xyzをIEEE754 binary32 big-endianで連結した`feb-node-coordinate-f32-v1` SHA-256で照合する。選択nodeのdecimal-to-binary32比較は独立spot-checkとして残す。
- 公式FBSのpartition名はidentityに使わない。Phase 1A FEB inspectionから作った`domain_bindings`と、material名、element count、referenced-node count、per-domain connectivity SHA-256のunique one-to-one一致でFEB domain aliasへbindする。FBS `Element`に`Type()`がないため、element typeは一致済みFEB domainから注入する。
- `stress`のeffective operationはrequestではsymbolic `MAT3DS.EFFECTIVE`とし、隔離workerがlocked公式FBS runtimeから解決する。FEBio 4.12の実測契約は`MAT3DS.EFFECTIVE=6`、`MAT3DS.P2=8`であり、`8`をeffectiveとしてhardcodeしない。
- CPython 3.13/FBSはPython 3.12 processへimportせず、`python.exe -I -B -S fbs_worker.py --request $RequestJson --response $ResponseJson`のJSON bridgeだけで使用する。
- schema/runtime lockを追加するTaskは`tests/contract/test_installed_resources.py`の完全inventoryを同じcommitで更新する。
- Phase 1AのCLI `CommandResult.status`はlowercaseのまま保つ。Phase 1BのCLI evidenceはPhase 1Aの`EvidenceRecord(kind, data)`だけを返し、JSON化後の各itemは追加keyを許さない閉じた`{"kind": "...", "data": {...}}`とする。固定`kind`は`process-evidence`、`log-verification`、`fbs-verification`、`completion-decision`、`result-validation`で、`data`内に`kind`を重複させない。
- successful solveの`CommandResult.artifacts`にはroleが`attempt-solver-log`と`attempt-solver-xplt`のrecordを各1件だけ返し、各recordはabsolute `path`、integer `bytes`、uppercase `sha256`を持つ。これはattempt内のfresh `solver.log`/`solver.xplt`を指し、永久領域へのpromotionを意味しない。
- 各checkbox Stepは2–5分のatomic actionとして扱う。掲載済みblockのcopy、単一test作成、単一command実行、またはdiff確認を一動作とし、複数checkboxをまとめて実行しない。
- 各TaskはRED test作成、exact RED確認、最小実装、focused GREEN、関連regression、`git status --short`、exact `git add`、commitの順で行う。
- 本サブプランの実装では正本master planを編集しない。

---

## Phase 1A Interface Contract Consumed by Every Task

Phase 1BはPhase 1Aから次のexact importとsignatureを消費する。Phase 1A側に別名を追加して吸収せず、実装開始時にこの契約と一致することをtest importで確認する。

- `febio_cae_harness.jsonio.ArtifactRef`はfrozen dataclassで、`path: Path`、`bytes: int`、`sha256: str`を持つ。
- `febio_cae_harness.jsonio.atomic_create_artifact(path: Path, data: bytes) -> ArtifactRef`
- `febio_cae_harness.hashing.sha256_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str`
- `febio_cae_harness.hashing.file_evidence(path: Path) -> dict[str, object]`
- `febio_cae_harness.input_store.InputRecord`はPhase 1Aのexact frozen dataclass、すなわち`record_id`、`record_path`、`canonical_path`、`sha256`、`bytes`、`destination_role`を持つ。Phase 1Bは別名fieldを仮定しない。persisted record全体のhashは`sha256_file(record_path)`で別途計算し、`record_id`と混同しない。source-selection bindingは`InputRecord`ではなく、Phase 1A `ApprovalRecord.source_selection_approval_record_digest`から消費する。
- `CaseState`は少なくとも`INTENT_APPROVED`、`MODEL_BUILT`、`PREFLIGHT_PASSED`、`SOLVED`、`RESULT_VERIFIED`、`SOLVE_FAILED`、`RESULT_INCOMPLETE`、`CANCELLED`を持つ。
- `CaseStore.open(case_dir: Path) -> CaseStore`は既存harness caseの再オープン専用である。
- `CaseStore.locked() -> ContextManager[CaseTransaction]`を`with case.locked() as transaction`で使い、transactionは`replay() -> dict[str, object]`と`append(event_type: str, to_state: CaseState, payload: dict[str, object]) -> CaseEvent`を提供する。
- `febio_cae_harness.response.EvidenceRecord`は`kind: str`と`data: dict[str, object]`を持ち、`to_payload()`が追加keyなしの`{"kind": kind, "data": data}`を返す。
- Phase 1A CLI `CommandResult.status`はlowercase string、内部の`CommandResult.evidence`は`tuple[EvidenceRecord, ...]`、JSON化後はrecordのlist、`CommandResult.artifacts`もrecordのlistである。

Phase 1A authorityからPhase 1Bへ渡すfield mappingは次で固定する。

| Phase 1A authority | Phase 1B consumption |
|---|---|
| `InputRecord.record_id` | `MODEL_ADOPTED.input_record_id` |
| `InputRecord.record_path` | schema/body照合後、`sha256_file(record_path)`を`MODEL_ADOPTED.input_record_file_sha256`へbind |
| `InputRecord.canonical_path` | adoption copy source |
| `InputRecord.sha256` / `InputRecord.bytes` | adoption copyのexpected SHA-256 / bytes |
| `InputRecord.destination_role` | exactly `authoritative-feb`であることをgate |
| `ApprovalRecord.input_record_set_digest` | `ResumeInputs.input_record_set_digest` |
| `ApprovalRecord.source_selection_approval_record_digest` | `ResumeInputs.source_selection_approval_record_digest` |
| `ApprovalRecord.contract_sha256` | `ResumeInputs.contract_sha256` |
| `sha256_file(ApprovalRecord.record_path)` | `ResumeInputs.analysis_intent_approval_record_sha256` |

`record_id`はPhase 1A persisted bodyのcanonical digestであり、newlineを含むrecord file
bytesのSHA-256ではない。両者を上表の通り別fieldで保持する。

## File Responsibility Map

- `model_adoption.py`: streaming create-new copy、source rehash、`02_Model` adoption、attempt `input.feb` staging。
- `attempt_store.py`: attempt layout、canonical resume key、stage events、attempt result、pending/final run lease、cancel request。
- `model_evidence.py`: Phase 1A `inspect_feb`からcanonical model/connectivity/all-node binary32 coordinate signaturesをcreate-new生成。
- `promotion.py`: completion/result hashを再検証してからのcreate-new promotion。
- `febio_command.py`: production-only `FebioRunRequest` validationと固定argv。
- `windows_job.py`: pinned file handles、suspended process、Job Object assignment/query/termination。
- `owned_process.py`: generic internal process lifecycle、telemetry、timeout/memory/cancel monitor。
- `febio_runner.py`: strict FEBio requestからowned lifecycleへの変換とlease cleanup。
- `log_parser.py`: FEBio LOGの構文証拠抽出。
- `failure.py`: engineering failure class、phase、fingerprint。
- `fbs_protocol.py`: runtime lock/profile、request/evidence dataclass、canonical JSON。
- `fbs_client.py`: Python 3.12 parent側のhash-stable worker invocation。
- `fbs_worker.py`: Python 3.13/FBS側のstate/mesh/field/kinematics抽出。
- `preflight.py`: launch直前のtransitive hash/profile/state gate。
- `completion.py`: `SOLVED` composite gate。
- `result_validation.py`: `RESULT_VERIFIED` intent-specific gate。

### Task 1: Immutable model adoption and solver staging

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/model_adoption.py`
- Create: `apps/febio_cae_harness/tests/helpers/phase1b.py`
- Create: `apps/febio_cae_harness/tests/integration/test_model_adoption.py`

**Interfaces:**
- Consumes: `InputRecord`, `ArtifactRef`, `sha256_file`, `with case.locked() as transaction`, `transaction.replay()`, `transaction.append()`, `CaseState.INTENT_APPROVED`, `CaseState.MODEL_BUILT`
- Produces: `copy_create_new(source: Path, destination: Path, expected_bytes: int, expected_sha256: str, allow_unique_name: bool) -> ArtifactRef`, `adopt_existing_feb(case: CaseStore, input_record: InputRecord) -> ArtifactRef`, `stage_solver_input(attempt: AttemptStore, adopted_feb: ArtifactRef) -> ArtifactRef`

- [ ] **Step 1: Create the reusable synthetic case helper and failing adoption tests**

```python
# tests/helpers/phase1b.py
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from febio_cae_harness.case_state import CaseState

@dataclass
class RecordingCase:
    case_dir: Path
    state: CaseState
    events: list[tuple[str, CaseState, dict[str, object]]] = field(default_factory=list)

    @property
    def event_log(self) -> Path:
        return self.case_dir / "05_Verification" / "harness" / "events.jsonl"

    @contextmanager
    def locked(self):
        yield self

    def replay(self):
        return {"harness": {"case_state": self.state.value}}

    def append(self, event_type, to_state, payload):
        self.events.append((event_type, to_state, payload))
        self.state = to_state
        return {"event_type": event_type, "to_state": to_state.value}

def initialize_case(root: Path, state: CaseState) -> RecordingCase:
    for name in ("01_Input", "02_Model", "03_Result", "04_Report", "05_Verification", "90_Temporary"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return RecordingCase(root, state)
```

```python
# tests/integration/test_model_adoption.py
from dataclasses import dataclass
from pathlib import Path

import pytest

import febio_cae_harness.model_adoption as adoption
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.input_store import InputRecord, ingest_create_new
from tests.helpers.phase1b import initialize_case

@dataclass(frozen=True)
class SolverPaths:
    solver: Path

@dataclass(frozen=True)
class FakeAttempt:
    paths: SolverPaths

def input_record(case_dir: Path, path: Path) -> InputRecord:
    return ingest_create_new(
        case_dir,
        path,
        "authoritative-feb",
        acquired_at="2026-07-30T00:00:00Z",
    )

def test_adopt_existing_feb_copies_exact_bytes_and_transitions(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.INTENT_APPROVED)
    source = tmp_path / "synthetic.feb"
    source.write_bytes(b"<febio_spec version=\"4.0\"/>")
    record = input_record(case.case_dir, source)
    result = adoption.adopt_existing_feb(case, record)
    assert result.path.parent == case.case_dir / "02_Model"
    assert result.path.read_bytes() == source.read_bytes()
    assert result.sha256 == sha256_file(source)
    assert case.state is CaseState.MODEL_BUILT
    assert case.events[-1][0] == "MODEL_ADOPTED"
    assert case.events[-1][2]["input_record_id"] == record.record_id
    assert (
        case.events[-1][2]["input_record_file_sha256"]
        == sha256_file(record.record_path)
    )

def test_source_mutation_during_copy_fails_without_model(tmp_path, monkeypatch):
    case = initialize_case(tmp_path / "case", CaseState.INTENT_APPROVED)
    source = tmp_path / "synthetic.feb"
    source.write_bytes(b"stable")
    record = input_record(case.case_dir, source)
    real_copy = adoption._copy_stream

    def mutate_after_copy(source_path, partial_path):
        real_copy(source_path, partial_path)
        source_path.write_bytes(b"changed")

    monkeypatch.setattr(adoption, "_copy_stream", mutate_after_copy)
    with pytest.raises(adoption.SourceDriftError, match="source changed during copy"):
        adoption.adopt_existing_feb(case, record)
    assert list((case.case_dir / "02_Model").iterdir()) == []
    assert case.state is CaseState.INTENT_APPROVED

def test_adoption_rejects_non_authoritative_input_record(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.INTENT_APPROVED)
    source = tmp_path / "synthetic.feb"
    source.write_bytes(b"stable")
    record = ingest_create_new(
        case.case_dir,
        source,
        "external-source-record",
        acquired_at="2026-07-30T00:00:00Z",
    )
    with pytest.raises(adoption.SourceDriftError, match="authoritative-feb"):
        adoption.adopt_existing_feb(case, record)
    assert list((case.case_dir / "02_Model").iterdir()) == []

def test_solver_staging_refuses_different_existing_input(tmp_path):
    solver = tmp_path / "attempt" / "solver"
    solver.mkdir(parents=True)
    adopted = tmp_path / "model.feb"
    adopted.write_bytes(b"approved")
    existing = solver / "input.feb"
    existing.write_bytes(b"foreign")
    artifact = adoption.ArtifactRef(adopted, 8, sha256_file(adopted))
    with pytest.raises(adoption.ArtifactCollisionError, match="input.feb already exists"):
        adoption.stage_solver_input(FakeAttempt(SolverPaths(solver)), artifact)
    assert existing.read_bytes() == b"foreign"
```

- [ ] **Step 2: Run the tests and verify the exact RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_model_adoption.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.model_adoption'`.

- [ ] **Step 3: Implement the streaming create-new primitive**

```python
# model_adoption.py
from __future__ import annotations

import ctypes
import json
import os
import tempfile
import uuid
from pathlib import Path

from febio_cae_harness.hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from febio_cae_harness.jsonio import ArtifactRef

MOVEFILE_WRITE_THROUGH = 0x00000008
ERROR_ALREADY_EXISTS = 183
ERROR_FILE_EXISTS = 80

class ArtifactCollisionError(RuntimeError):
    """Create-new destination contains different bytes."""

class SourceDriftError(RuntimeError):
    """Source bytes no longer match approved evidence."""

def _copy_stream(source_path: Path, partial_path: Path) -> None:
    source_fd = os.open(source_path, os.O_RDONLY | os.O_BINARY)
    destination_fd = os.open(
        partial_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY,
        0o600,
    )
    try:
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fsync(destination_fd)
    finally:
        os.close(destination_fd)
        os.close(source_fd)

def _move_without_replace(partial_path: Path, destination: Path) -> bool:
    move = ctypes.windll.kernel32.MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move.restype = ctypes.c_int
    ctypes.set_last_error(0)
    if move(str(partial_path), str(destination), MOVEFILE_WRITE_THROUGH):
        return True
    error = ctypes.get_last_error()
    if error in (ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS):
        return False
    raise ctypes.WinError(error)

def copy_create_new(
    source: Path,
    destination: Path,
    expected_bytes: int,
    expected_sha256: str,
    allow_unique_name: bool,
) -> ArtifactRef:
    source = source.resolve(strict=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_size != expected_bytes or sha256_file(source) != expected_sha256:
        raise SourceDriftError("source does not match approved evidence")
    candidate = destination
    for _ in range(32):
        if candidate.exists():
            if candidate.stat().st_size == expected_bytes and sha256_file(candidate) == expected_sha256:
                return ArtifactRef(candidate, expected_bytes, expected_sha256)
            if not allow_unique_name:
                raise ArtifactCollisionError(f"{destination.name} already exists with different bytes")
            candidate = destination.with_name(
                f"{destination.stem}-{uuid.uuid4().hex[:8]}{destination.suffix}"
            )
            continue
        partial = Path(tempfile.gettempdir()) / (
            f"febio-cae-harness-{candidate.name}-{uuid.uuid4().hex}.partial"
        )
        try:
            _copy_stream(source, partial)
            if partial.stat().st_size != expected_bytes or sha256_file(partial) != expected_sha256:
                raise SourceDriftError("partial copy does not match approved evidence")
            if source.stat().st_size != expected_bytes or sha256_file(source) != expected_sha256:
                raise SourceDriftError("source changed during copy")
            if _move_without_replace(partial, candidate):
                return ArtifactRef(candidate, expected_bytes, expected_sha256)
        finally:
            partial.unlink(missing_ok=True)
    raise ArtifactCollisionError("could not allocate a create-new destination after 32 attempts")
```

- [ ] **Step 4: Implement adoption and staging state/side-effect order**

```python
# append to model_adoption.py
from typing import Protocol

from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.input_store import InputRecord
from febio_cae_harness.schema import validate_schema

class _AttemptPaths(Protocol):
    solver: Path

class AttemptStore(Protocol):
    paths: _AttemptPaths

def _validated_record_file_sha256(
    case: CaseStore,
    input_record: InputRecord,
) -> str:
    record_path = input_record.record_path.resolve(strict=True)
    expected_parent = (case.case_dir / "01_Input").resolve(strict=True)
    if record_path.parent != expected_parent:
        raise SourceDriftError("InputRecord is outside case 01_Input")
    value = json.loads(record_path.read_text(encoding="utf-8"))
    validate_schema("input-record", value)
    body = dict(value)
    persisted_id = str(body.pop("record_id"))
    if sha256_bytes(canonical_json_bytes(body)) != persisted_id:
        raise SourceDriftError("InputRecord record_id does not bind its body")
    expected = {
        "record_id": input_record.record_id,
        "canonical_path": str(input_record.canonical_path.resolve(strict=True)),
        "sha256": input_record.sha256,
        "bytes": input_record.bytes,
        "destination_role": input_record.destination_role,
    }
    if any(value[key] != expected_value for key, expected_value in expected.items()):
        raise SourceDriftError("InputRecord dataclass does not match persisted record")
    if input_record.destination_role != "authoritative-feb":
        raise SourceDriftError("InputRecord destination_role must be authoritative-feb")
    return sha256_file(record_path)

def adopt_existing_feb(case: CaseStore, input_record: InputRecord) -> ArtifactRef:
    with case.locked() as transaction:
        projection = transaction.replay()
        if projection["harness"]["case_state"] != CaseState.INTENT_APPROVED.value:
            raise RuntimeError("model adoption requires INTENT_APPROVED")
        record_file_sha256 = _validated_record_file_sha256(case, input_record)
        destination = case.case_dir / "02_Model" / input_record.canonical_path.name
        artifact = copy_create_new(
            input_record.canonical_path,
            destination,
            input_record.bytes,
            input_record.sha256,
            allow_unique_name=True,
        )
        transaction.append(
            "MODEL_ADOPTED",
            CaseState.MODEL_BUILT,
            {
                "input_record_id": input_record.record_id,
                "input_record_file_sha256": record_file_sha256,
                "source_sha256": input_record.sha256,
                "destination": str(artifact.path.relative_to(case.case_dir)),
                "destination_sha256": artifact.sha256,
                "destination_bytes": artifact.bytes,
            },
        )
        return artifact

def stage_solver_input(attempt: AttemptStore, adopted_feb: ArtifactRef) -> ArtifactRef:
    return copy_create_new(
        adopted_feb.path,
        attempt.paths.solver / "input.feb",
        adopted_feb.bytes,
        adopted_feb.sha256,
        allow_unique_name=False,
    )
```

- [ ] **Step 5: Run the GREEN command and Phase 1A regressions**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_model_adoption.py -q
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_jsonio.py apps/febio_cae_harness/tests/integration/test_input_store.py apps/febio_cae_harness/tests/integration/test_case_store.py -q
```

Expected: both commands report only `passed`.

- [ ] **Step 6: Inspect, stage, and commit exactly Task 1 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/model_adoption.py apps/febio_cae_harness/tests/helpers/phase1b.py apps/febio_cae_harness/tests/integration/test_model_adoption.py
git diff --cached --check
git commit -m "feat: adopt and stage approved FEB immutably"
```

### Task 2: Attempt layout, canonical model evidence, resume key, and completed-stage reuse

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/attempt_store.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/model_evidence.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/run-config.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/attempt-result.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/model-evidence.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_resume_key.py`
- Create: `apps/febio_cae_harness/tests/unit/test_model_evidence.py`
- Create: `apps/febio_cae_harness/tests/integration/test_attempt_store.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**
- Consumes: `atomic_create_artifact(path: Path, data: bytes) -> ArtifactRef`, Task 1 staged `input.feb`, Phase 1A `inspect_feb(path)` with no exclusions, `CaseStore.case_dir`, `CaseStore.state`, `CaseState.MODEL_BUILT`
- Produces: `AttemptPaths`, `ResumeInputs`, `compute_resume_key(inputs:
  ResumeInputs) -> str`, `AttemptStore.start(case: CaseStore, config:
  dict[str, object]) -> AttemptStore`, `AttemptStore.generated_dir`,
  `next_generated_ordinal()`, `create_generated()`, `record_stage()`,
  `finalize()`, `find_reusable_attempt()`, coordinate/connectivity/domain
  signatures, and `create_model_evidence(attempt, staged_feb,
  excluded_domains=()) -> ArtifactRef`. Model evidence persists the exact
  normalized exclusion set used only for the domain signature while retaining
  full FEB bytes, nodes, elements, connectivity, and domain bindings.

- [ ] **Step 1: Write canonical resume-key tests**

```python
# tests/unit/test_resume_key.py
from dataclasses import replace

from febio_cae_harness.attempt_store import ResumeInputs, compute_resume_key

def seed() -> ResumeInputs:
    return ResumeInputs(
        input_record_set_digest="1" * 64,
        source_selection_approval_record_digest=None,
        contract_sha256="2" * 64,
        analysis_intent_approval_record_sha256="3" * 64,
        normalized_config_sha256="4" * 64,
        harness_version="0.1.0",
        build_provenance_sha256="5" * 64,
        install_manifest_sha256="6" * 64,
        wheel_sha256="F" * 64,
        reviewed_source_commit="7" * 40,
        solver_sha256="8" * 64,
        solver_version="4.12.0",
        fbs_runtime_tree_sha256="9" * 64,
        loaded_dll_profile_sha256="A" * 64,
        policy_sha256="B" * 64,
        policy_version="1",
    )

def test_resume_key_is_canonical_and_uppercase():
    assert compute_resume_key(seed()) == compute_resume_key(seed())
    assert len(compute_resume_key(seed())) == 64
    assert compute_resume_key(seed()) == compute_resume_key(seed()).upper()

def test_every_bound_field_changes_resume_key():
    original = seed()
    for field_name in original.__dataclass_fields__:
        old = getattr(original, field_name)
        changed = "C" * len(old) if isinstance(old, str) else "C" * 64
        if changed == old:
            changed = "D" * len(old)
        assert compute_resume_key(replace(original, **{field_name: changed})) != compute_resume_key(original)
```

- [ ] **Step 2: Write exact layout and completed-only reuse tests**

```python
# tests/integration/test_attempt_store.py
from pathlib import Path

import pytest

from febio_cae_harness.attempt_store import AttemptStore, find_reusable_attempt
from febio_cae_harness.case_state import CaseState
from tests.helpers.phase1b import initialize_case

REQUIRED = {
    "input-manifest.json",
    "intent-revision.json",
    "normalized-config.json",
    "commands.json",
    "tool-fingerprints.json",
    "stage-events.jsonl",
    "solver",
    "raw-logs",
    "generated",
}

def config() -> dict[str, object]:
    return {
        "resume_key": "A" * 64,
        "input_manifest": {"input_record_set_digest": "1" * 64},
        "intent_revision": {"revision": 1, "sha256": "2" * 64},
        "normalized_config": {"timeout_seconds": 60},
        "commands": {"febio": ["febio4.exe", "-i", "input.feb", "-o", "solver.log", "-p", "solver.xplt"]},
        "tool_fingerprints": {"solver_sha256": "3" * 64},
    }

def test_start_creates_exact_attempt_layout(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    assert {path.name for path in attempt.root.iterdir()} == REQUIRED
    assert {path.name for path in attempt.paths.solver.iterdir()} == set()

def test_reuse_requires_completed_stage_and_identical_resume_key(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    assert find_reusable_attempt(case.case_dir, "A" * 64, "preflight") is None
    attempt.record_stage("preflight", "COMPLETED", {"evidence_sha256": "4" * 64})
    attempt.finalize({
        "status": "COMPLETED",
        "completed_stages": ["preflight"],
        "process_evidence_sha256": None,
    })
    reused = find_reusable_attempt(case.case_dir, "A" * 64, "preflight")
    assert reused is not None
    assert reused.attempt_id == attempt.attempt_id
    assert find_reusable_attempt(case.case_dir, "B" * 64, "preflight") is None

def test_generated_artifacts_are_create_new_and_ordinals_are_diagnostic_only(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    assert attempt.generated_dir == attempt.paths.generated
    assert attempt.next_generated_ordinal() == 1
    first = attempt.create_generated("diagnostic-01.feb", b"first")
    attempt.create_generated("patch-report.json", b"{}")
    assert first.path == attempt.generated_dir / "diagnostic-01.feb"
    assert first.bytes == 5
    assert attempt.next_generated_ordinal() == 2
    with pytest.raises(FileExistsError):
        attempt.create_generated("diagnostic-01.feb", b"different")
    with pytest.raises(ValueError, match="generated name must be one basename"):
        attempt.create_generated("../escape.feb", b"bad")
    assert first.path.read_bytes() == b"first"
```

- [ ] **Step 3: Write canonical model-evidence, binary32 coordinate, connectivity, schema, and drift tests**

```python
# tests/unit/test_model_evidence.py
import json
from pathlib import Path

import jsonschema
import pytest

from febio_cae_harness.attempt_store import AttemptStore
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.model_adoption import copy_create_new
from febio_cae_harness.model_evidence import create_model_evidence
from tests.helpers.phase1b import initialize_case
from tests.integration.test_attempt_store import config

FIXTURE = Path(__file__).parents[1] / "fixtures" / "feb" / "complete-small.feb"
COORDINATE_SIGNATURE = "815EA8057967304DC3D258B8238137250A78C43F6443CC275E8FB8BE68CD8E09"
CONNECTIVITY_SIGNATURE = "29AD04B9930DB0B0A2BF0550FC97609CB54831AD9BE3471AC05632AA85E1530E"

def staged_input(tmp_path: Path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    staged = copy_create_new(
        FIXTURE,
        attempt.paths.solver / "input.feb",
        FIXTURE.stat().st_size,
        sha256_file(FIXTURE),
        allow_unique_name=False,
    )
    return attempt, staged

def test_model_evidence_is_create_new_and_binds_full_model_and_coordinates(tmp_path):
    attempt, staged = staged_input(tmp_path)
    artifact = create_model_evidence(attempt, staged)
    assert artifact.path == attempt.root / "model-evidence.json"
    value = json.loads(artifact.path.read_text(encoding="utf-8"))
    assert value["kind"] == "model-evidence"
    assert value["source"] == {
        "path": str(staged.path.resolve(strict=True)),
        "bytes": staged.bytes,
        "sha256": staged.sha256,
    }
    assert value["node_count"] == 5
    assert value["element_count"] == 2
    assert value["excluded_domains"] == []
    assert value["domain_signature_version"] == "feb-domain-signature-v1"
    assert len(value["domain_signature_sha256"]) == 64
    assert value["connectivity_signature_sha256"] == CONNECTIVITY_SIGNATURE
    assert {
        item["domain_alias"]: (
            item["material_name"],
            item["element_count"],
            item["referenced_node_count"],
            item["element_type"],
            item["nodes_per_element"],
        )
        for item in value["domain_bindings"]
    } == {
        "deformable": ("soft", 1, 4, "TET4", 4),
        "rigid": ("tool", 1, 4, "TET4", 4),
    }
    assert all(
        item["connectivity_signature_version"]
        == "feb-domain-connectivity-signature-v1"
        and len(item["connectivity_signature_sha256"]) == 64
        for item in value["domain_bindings"]
    )
    assert value["initial_coordinate_signature"] == {
        "version": "feb-node-coordinate-f32-v1",
        "ordering": "ascending-node-id",
        "node_id_encoding": "uint64-big-endian",
        "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
        "node_count": 5,
        "component_count": 15,
        "sha256": COORDINATE_SIGNATURE,
    }
    assert len(value["model_signature_sha256"]) == 64
    schema = json.loads(
        (
            Path(__file__).parents[2]
            / "src" / "febio_cae_harness" / "schemas" / "model-evidence.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.validate(value, schema)
    with pytest.raises(FileExistsError):
        create_model_evidence(attempt, staged)

def test_model_evidence_binds_normalized_domain_exclusions_without_filtering_full_mesh(
    tmp_path,
):
    full_attempt, full_staged = staged_input(tmp_path / "full")
    excluded_attempt, excluded_staged = staged_input(tmp_path / "excluded")
    full_artifact = create_model_evidence(full_attempt, full_staged)
    excluded_artifact = create_model_evidence(
        excluded_attempt,
        excluded_staged,
        excluded_domains=("rigid",),
    )
    full = json.loads(full_artifact.path.read_text(encoding="utf-8"))
    excluded = json.loads(
        excluded_artifact.path.read_text(encoding="utf-8")
    )
    assert full["excluded_domains"] == []
    assert excluded["excluded_domains"] == ["rigid"]
    assert (
        excluded["domain_signature_sha256"]
        != full["domain_signature_sha256"]
    )
    assert (
        excluded["model_signature_sha256"]
        != full["model_signature_sha256"]
    )
    for field in (
        "node_count",
        "element_count",
        "connectivity_signature_sha256",
        "domain_bindings",
        "initial_coordinate_signature",
    ):
        assert excluded[field] == full[field]

def test_model_evidence_rejects_blank_domain_exclusion(tmp_path):
    attempt, staged = staged_input(tmp_path)
    with pytest.raises(ValueError, match="MODEL_EVIDENCE_EXCLUSION_INVALID"):
        create_model_evidence(attempt, staged, excluded_domains=(" ",))
    assert not (attempt.root / "model-evidence.json").exists()

def test_staged_feb_drift_fails_before_model_evidence_is_created(tmp_path):
    attempt, staged = staged_input(tmp_path)
    staged.path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="MODEL_EVIDENCE_SOURCE_DRIFT"):
        create_model_evidence(attempt, staged)
    assert not (attempt.root / "model-evidence.json").exists()
```

- [ ] **Step 4: Run all three tests and verify the exact RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_resume_key.py apps/febio_cae_harness/tests/integration/test_attempt_store.py apps/febio_cae_harness/tests/unit/test_model_evidence.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.attempt_store'`; after Tasks 2 implementation starts, the new test remains RED with `ModuleNotFoundError: No module named 'febio_cae_harness.model_evidence'` until Step 7.

- [ ] **Step 5: Implement the exact dataclasses and canonical key**

```python
# attempt_store.py
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.jsonio import ArtifactRef, atomic_create_artifact

@dataclass(frozen=True)
class AttemptPaths:
    solver: Path
    raw_logs: Path
    generated: Path
    stage_events: Path
    result: Path

@dataclass(frozen=True)
class ResumeInputs:
    input_record_set_digest: str
    source_selection_approval_record_digest: str | None
    contract_sha256: str
    analysis_intent_approval_record_sha256: str
    normalized_config_sha256: str
    harness_version: str
    build_provenance_sha256: str
    install_manifest_sha256: str
    wheel_sha256: str
    reviewed_source_commit: str
    solver_sha256: str
    solver_version: str
    fbs_runtime_tree_sha256: str
    loaded_dll_profile_sha256: str
    policy_sha256: str
    policy_version: str

def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

def compute_resume_key(inputs: ResumeInputs) -> str:
    return hashlib.sha256(_canonical_bytes(asdict(inputs))).hexdigest().upper()

def _write_json(path: Path, value: object) -> None:
    atomic_create_artifact(path, _canonical_bytes(value) + b"\n")
```

- [ ] **Step 6: Implement start, append-only stage events, finalize, and reuse**

```python
# append to attempt_store.py
@dataclass(frozen=True)
class AttemptStore:
    case_dir: Path
    attempt_id: str
    root: Path
    resume_key: str
    paths: AttemptPaths

    @property
    def generated_dir(self) -> Path:
        return self.paths.generated

    def next_generated_ordinal(self) -> int:
        ordinals = [
            int(match.group("ordinal"))
            for path in self.generated_dir.iterdir()
            if (
                match := re.fullmatch(
                    r"diagnostic-(?P<ordinal>[1-9][0-9]*)\.feb",
                    path.name,
                )
            )
        ]
        return max(ordinals, default=0) + 1

    def create_generated(self, name: str, data: bytes) -> ArtifactRef:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("generated name must be one basename")
        return atomic_create_artifact(self.generated_dir / name, data)

    @classmethod
    def start(cls, case: CaseStore, config: dict[str, object]) -> "AttemptStore":
        with case.locked() as transaction:
            projection = transaction.replay()
            if projection["harness"]["case_state"] != CaseState.MODEL_BUILT.value:
                raise RuntimeError("attempt start requires MODEL_BUILT")
            required = {
                "resume_key",
                "input_manifest",
                "intent_revision",
                "normalized_config",
                "commands",
                "tool_fingerprints",
            }
            if set(config) != required:
                raise ValueError(f"attempt config keys must equal {sorted(required)}")
            if not re.fullmatch(r"[0-9A-F]{64}", str(config["resume_key"])):
                raise ValueError("resume_key must be uppercase SHA-256")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            attempt_id = f"{timestamp}-{uuid.uuid4().hex[:8]}"
            root = case.case_dir / "90_Temporary" / "attempts" / attempt_id
            root.mkdir(parents=True, exist_ok=False)
            solver = root / "solver"
            raw_logs = root / "raw-logs"
            generated = root / "generated"
            solver.mkdir()
            raw_logs.mkdir()
            generated.mkdir()
            for filename, key in (
                ("input-manifest.json", "input_manifest"),
                ("intent-revision.json", "intent_revision"),
                ("normalized-config.json", "normalized_config"),
                ("commands.json", "commands"),
                ("tool-fingerprints.json", "tool_fingerprints"),
            ):
                _write_json(root / filename, config[key])
            atomic_create_artifact(root / "stage-events.jsonl", b"")
            return cls(
                case.case_dir,
                attempt_id,
                root,
                str(config["resume_key"]),
                AttemptPaths(
                    solver,
                    raw_logs,
                    generated,
                    root / "stage-events.jsonl",
                    root / "attempt-result.json",
                ),
            )

    def record_stage(self, name: str, status: str, evidence: dict[str, object]) -> None:
        record = {
            "name": name,
            "status": status,
            "evidence": evidence,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.paths.stage_events.open("ab", buffering=0) as stream:
            stream.write(_canonical_bytes(record) + b"\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())

    def finalize(self, result: dict[str, object]) -> None:
        payload = {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            "resume_key": self.resume_key,
            **result,
        }
        _write_json(self.paths.result, payload)

    @classmethod
    def open(cls, root: Path) -> "AttemptStore":
        result = json.loads((root / "attempt-result.json").read_text(encoding="utf-8"))
        case_dir = root.parents[2]
        return cls(
            case_dir,
            result["attempt_id"],
            root,
            result["resume_key"],
            AttemptPaths(
                root / "solver",
                root / "raw-logs",
                root / "generated",
                root / "stage-events.jsonl",
                root / "attempt-result.json",
            ),
        )

def find_reusable_attempt(case_dir: Path, resume_key: str, stage: str) -> AttemptStore | None:
    attempts = case_dir / "90_Temporary" / "attempts"
    if not attempts.exists():
        return None
    for result_path in sorted(attempts.glob("*/attempt-result.json"), reverse=True):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if (
            result.get("status") == "COMPLETED"
            and result.get("resume_key") == resume_key
            and stage in result.get("completed_stages", [])
        ):
            return AttemptStore.open(result_path.parent)
    return None
```

- [ ] **Step 7: Re-run the model-evidence test and verify its exact focused RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_model_evidence.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.model_evidence'`.

- [ ] **Step 8: Implement canonical model, connectivity, and all-node binary32 coordinate evidence**

```python
# model_evidence.py
from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping, Sequence

from febio_cae_harness.attempt_store import AttemptStore
from febio_cae_harness.feb_inspector import FebInspection, inspect_feb
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.jsonio import ArtifactRef, atomic_create_artifact

MODEL_SIGNATURE_VERSION = "feb-model-signature-v1"
CONNECTIVITY_SIGNATURE_VERSION = "feb-fbs-connectivity-signature-v1"
DOMAIN_CONNECTIVITY_SIGNATURE_VERSION = "feb-domain-connectivity-signature-v1"
INITIAL_COORDINATE_SIGNATURE_VERSION = "feb-node-coordinate-f32-v1"
INVARIANT_KINDS = {
    "domain", "material", "reference_closure", "load",
    "boundary", "contact", "output",
}

def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()

def node_coordinate_f32_signature(
    nodes: Mapping[int, Sequence[float]],
) -> dict[str, object]:
    encoded = bytearray()
    for node_id in sorted(nodes):
        coordinates = tuple(float(value) for value in nodes[node_id])
        if node_id <= 0 or node_id > (2**64 - 1):
            raise ValueError("MODEL_NODE_ID_OUT_OF_UINT64_RANGE")
        if len(coordinates) != 3 or not all(math.isfinite(value) for value in coordinates):
            raise ValueError("MODEL_NODE_COORDINATE_INVALID")
        try:
            encoded.extend(struct.pack(">Qfff", node_id, *coordinates))
        except (OverflowError, struct.error) as error:
            raise ValueError("MODEL_NODE_COORDINATE_BINARY32_OVERFLOW") from error
    return {
        "version": INITIAL_COORDINATE_SIGNATURE_VERSION,
        "ordering": "ascending-node-id",
        "node_id_encoding": "uint64-big-endian",
        "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
        "node_count": len(nodes),
        "component_count": len(nodes) * 3,
        "sha256": hashlib.sha256(encoded).hexdigest().upper(),
    }

def connectivity_signature(inspection: FebInspection) -> str:
    ordered_elements = sorted(
        inspection.elements,
        key=lambda element: (
            element.domain.casefold(),
            element.element_id,
            element.element_type.upper(),
            element.connectivity,
        ),
    )
    elements = [
        {
            "domain": element.domain,
            "element_id": element.element_id,
            "element_type": element.element_type.upper(),
            "connectivity_node_ids": list(element.connectivity),
        }
        for element in ordered_elements
    ]
    core = {
        "version": CONNECTIVITY_SIGNATURE_VERSION,
        "node_count": inspection.node_count,
        "element_count": len(inspection.elements),
        "node_ids": sorted(inspection.nodes),
        "elements": elements,
    }
    return _canonical_sha256(core)

def domain_bindings(inspection: FebInspection) -> list[dict[str, object]]:
    metadata = {str(item["name"]): item for item in inspection.domains}
    domain_names = {element.domain for element in inspection.elements}
    if set(metadata) != domain_names:
        raise RuntimeError("MODEL_DOMAIN_METADATA_MISMATCH")
    bindings: list[dict[str, object]] = []
    for domain_name in sorted(domain_names, key=str.casefold):
        elements = sorted(
            (
                element
                for element in inspection.elements
                if element.domain == domain_name
            ),
            key=lambda element: (
                element.element_id,
                element.element_type.upper(),
                element.connectivity,
            ),
        )
        referenced_nodes = {
            node_id
            for element in elements
            for node_id in element.connectivity
        }
        element_types = {
            (element.element_type.upper(), len(element.connectivity))
            for element in elements
        }
        if len(element_types) != 1:
            raise RuntimeError("MODEL_DOMAIN_MIXED_ELEMENT_TYPES_UNSUPPORTED")
        element_type, nodes_per_element = next(iter(element_types))
        core = {
            "version": DOMAIN_CONNECTIVITY_SIGNATURE_VERSION,
            "element_count": len(elements),
            "referenced_node_count": len(referenced_nodes),
            "elements": [
                {
                    "element_id": element.element_id,
                    "connectivity_node_ids": list(element.connectivity),
                }
                for element in elements
            ],
        }
        bindings.append(
            {
                "domain_alias": domain_name,
                "material_name": str(metadata[domain_name]["material"]),
                "element_count": len(elements),
                "referenced_node_count": len(referenced_nodes),
                "element_type": element_type,
                "nodes_per_element": nodes_per_element,
                "connectivity_signature_version": (
                    DOMAIN_CONNECTIVITY_SIGNATURE_VERSION
                ),
                "connectivity_signature_sha256": _canonical_sha256(core),
            }
        )
    return bindings

def create_model_evidence(
    attempt: AttemptStore,
    staged_feb: ArtifactRef,
    *,
    excluded_domains: tuple[str, ...] = (),
) -> ArtifactRef:
    raw_exclusions = tuple(str(item) for item in excluded_domains)
    if any(not item or item.strip() != item for item in raw_exclusions):
        raise ValueError("MODEL_EVIDENCE_EXCLUSION_INVALID")
    normalized_exclusions = tuple(
        sorted(set(raw_exclusions), key=lambda item: (item.casefold(), item))
    )
    expected_path = (attempt.paths.solver / "input.feb").resolve(strict=True)
    if staged_feb.path.resolve(strict=True) != expected_path:
        raise ValueError("MODEL_EVIDENCE_INPUT_PATH_INVALID")
    if (
        expected_path.stat().st_size != staged_feb.bytes
        or sha256_file(expected_path) != staged_feb.sha256
    ):
        raise RuntimeError("MODEL_EVIDENCE_SOURCE_DRIFT")
    inspection = inspect_feb(
        expected_path,
        excluded_domains=normalized_exclusions,
    )
    if (
        inspection.source_bytes != staged_feb.bytes
        or inspection.source_sha256 != staged_feb.sha256
        or sha256_file(expected_path) != staged_feb.sha256
    ):
        raise RuntimeError("MODEL_EVIDENCE_SOURCE_DRIFT")
    if set(inspection.invariant_signatures) != INVARIANT_KINDS:
        raise RuntimeError("MODEL_EVIDENCE_INVARIANT_KEYS_INVALID")
    model_core = {
        "version": MODEL_SIGNATURE_VERSION,
        "source_sha256": inspection.source_sha256,
        "excluded_domains": list(normalized_exclusions),
        "domain_signature_version": inspection.domain_signature_version,
        "domain_signature_sha256": inspection.domain_signature,
        "invariant_signatures": inspection.invariant_signatures,
    }
    payload = {
        "schema_version": 1,
        "kind": "model-evidence",
        "attempt_id": attempt.attempt_id,
        "source": {
            "path": str(expected_path),
            "bytes": staged_feb.bytes,
            "sha256": staged_feb.sha256,
        },
        "excluded_domains": list(normalized_exclusions),
        "model_signature_version": MODEL_SIGNATURE_VERSION,
        "model_signature_sha256": _canonical_sha256(model_core),
        "domain_signature_version": inspection.domain_signature_version,
        "domain_signature_sha256": inspection.domain_signature,
        "node_count": inspection.node_count,
        "element_count": len(inspection.elements),
        "connectivity_signature_version": CONNECTIVITY_SIGNATURE_VERSION,
        "connectivity_signature_sha256": connectivity_signature(inspection),
        "domain_bindings": domain_bindings(inspection),
        "initial_coordinate_signature": node_coordinate_f32_signature(inspection.nodes),
        "invariant_signatures": inspection.invariant_signatures,
    }
    path = attempt.root / "model-evidence.json"
    artifact = atomic_create_artifact(path, _canonical_bytes(payload) + b"\n")
    attempt.record_stage(
        "model-evidence",
        "COMPLETED",
        {
            "kind": "model-evidence",
            "path": str(artifact.path),
            "bytes": artifact.bytes,
            "sha256": artifact.sha256,
        },
    )
    return artifact
```

- [ ] **Step 9: Add exact JSON schemas and installed-resource entries**

`schemas/run-config.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "run-config.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "resume_key", "input_manifest", "intent_revision",
    "normalized_config", "commands", "tool_fingerprints"
  ],
  "properties": {
    "resume_key": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "input_manifest": {"type": "object"},
    "intent_revision": {"type": "object"},
    "normalized_config": {"type": "object"},
    "commands": {"type": "object"},
    "tool_fingerprints": {"type": "object"}
  }
}
```

`schemas/attempt-result.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "attempt-result.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "attempt_id", "resume_key", "status",
    "completed_stages", "process_evidence_sha256"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "attempt_id": {"type": "string", "minLength": 10},
    "resume_key": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "status": {"enum": ["COMPLETED", "FAILED", "CANCELLED", "PARTIAL"]},
    "completed_stages": {
      "type": "array",
      "items": {"type": "string"},
      "uniqueItems": true
    },
    "process_evidence_sha256": {
      "type": ["string", "null"],
      "pattern": "^[0-9A-F]{64}$"
    }
  }
}
```

`schemas/model-evidence.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "model-evidence.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "kind", "attempt_id", "source",
    "excluded_domains",
    "model_signature_version", "model_signature_sha256",
    "domain_signature_version", "domain_signature_sha256",
    "node_count", "element_count",
    "connectivity_signature_version", "connectivity_signature_sha256",
    "domain_bindings", "initial_coordinate_signature", "invariant_signatures"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "kind": {"const": "model-evidence"},
    "attempt_id": {"type": "string", "minLength": 10},
    "source": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "bytes", "sha256"],
      "properties": {
        "path": {"type": "string", "minLength": 1},
        "bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      }
    },
    "excluded_domains": {
      "type": "array",
      "items": {"type": "string", "minLength": 1},
      "uniqueItems": true
    },
    "model_signature_version": {"const": "feb-model-signature-v1"},
    "model_signature_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "domain_signature_version": {"const": "feb-domain-signature-v1"},
    "domain_signature_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "node_count": {"type": "integer", "minimum": 1},
    "element_count": {"type": "integer", "minimum": 1},
    "connectivity_signature_version": {
      "const": "feb-fbs-connectivity-signature-v1"
    },
    "connectivity_signature_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "domain_bindings": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "domain_alias", "material_name", "element_count",
          "referenced_node_count", "element_type", "nodes_per_element",
          "connectivity_signature_version",
          "connectivity_signature_sha256"
        ],
        "properties": {
          "domain_alias": {"type": "string", "minLength": 1},
          "material_name": {"type": "string", "minLength": 1},
          "element_count": {"type": "integer", "minimum": 1},
          "referenced_node_count": {"type": "integer", "minimum": 1},
          "element_type": {"type": "string", "minLength": 1},
          "nodes_per_element": {"type": "integer", "minimum": 1},
          "connectivity_signature_version": {
            "const": "feb-domain-connectivity-signature-v1"
          },
          "connectivity_signature_sha256": {
            "type": "string", "pattern": "^[0-9A-F]{64}$"
          }
        }
      }
    },
    "initial_coordinate_signature": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "version", "ordering", "node_id_encoding", "coordinate_encoding",
        "node_count", "component_count", "sha256"
      ],
      "properties": {
        "version": {"const": "feb-node-coordinate-f32-v1"},
        "ordering": {"const": "ascending-node-id"},
        "node_id_encoding": {"const": "uint64-big-endian"},
        "coordinate_encoding": {"const": "ieee754-binary32-big-endian-xyz"},
        "node_count": {"type": "integer", "minimum": 1},
        "component_count": {"type": "integer", "minimum": 3},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      }
    },
    "invariant_signatures": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "domain", "material", "reference_closure", "load",
        "boundary", "contact", "output"
      ],
      "properties": {
        "domain": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "material": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "reference_closure": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "load": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "boundary": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "contact": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "output": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      }
    }
  }
}
```

Add these exact paths to `EXPECTED_RESOURCES` in `tests/contract/test_installed_resources.py`:

```python
"schemas/attempt-result.schema.json",
"schemas/model-evidence.schema.json",
"schemas/run-config.schema.json",
```

- [ ] **Step 10: Run the GREEN commands, schema contract, and regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_resume_key.py apps/febio_cae_harness/tests/unit/test_model_evidence.py apps/febio_cae_harness/tests/integration/test_attempt_store.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_model_adoption.py apps/febio_cae_harness/tests/integration/test_case_store.py -q
```

Expected: both commands report only `passed`.

- [ ] **Step 11: Inspect, stage, and commit exactly Task 2 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/attempt_store.py apps/febio_cae_harness/src/febio_cae_harness/model_evidence.py apps/febio_cae_harness/src/febio_cae_harness/schemas/run-config.schema.json apps/febio_cae_harness/src/febio_cae_harness/schemas/attempt-result.schema.json apps/febio_cae_harness/src/febio_cae_harness/schemas/model-evidence.schema.json apps/febio_cae_harness/tests/unit/test_resume_key.py apps/febio_cae_harness/tests/unit/test_model_evidence.py apps/febio_cae_harness/tests/integration/test_attempt_store.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git diff --cached --check
git commit -m "feat: add resumable isolated attempts"
```

### Task 3: Per-attempt immutable run lease, cancellation, and clear tombstone

**Files:**
- Modify: `apps/febio_cae_harness/src/febio_cae_harness/attempt_store.py`
- Create: `apps/febio_cae_harness/tests/integration/test_run_lease.py`

**Interfaces:**
- Consumes: `AttemptStore`, `CaseStore.locked()`, create-new JSON writer
- Produces: `AttemptRunLock.acquire(attempt_root: Path, attempt_id: str, owner_token: str) -> AttemptRunLock`, `RunLease`, `current_run_lease(case: CaseStore) -> RunLease | None`, `begin_run_lease(case: CaseStore, attempt: AttemptStore) -> RunLease`, `bind_run_lease(case: CaseStore, lease: RunLease, pid: int, job_name: str) -> RunLease`, `request_cancel(case: CaseStore, attempt_id: str, owner_token: str) -> Path`, `read_cancel_request(lease: RunLease) -> bool`, `clear_run_lease(case: CaseStore, lease: RunLease) -> Path`

Every lease record lives under its own attempt and is immutable:
`run-lease-created.json`, `run-lease-bound.json`, `cancel-request.json`, and
`run-lease-cleared.json`. Clearing writes the last file as a tombstone; it
never deletes, truncates, or overwrites any CAE file. `current_run_lease()`
derives activity exclusively from created records without a matching cleared
record. No reused case-level `run-lease.json` exists.

- [ ] **Step 1: Write immutable history, ownership, cancellation, and reuse tests**

```python
# tests/integration/test_run_lease.py
import json
import multiprocessing
from pathlib import Path

import pytest

from febio_cae_harness.attempt_store import (
    AttemptRunLock,
    AttemptStore,
    begin_run_lease,
    bind_run_lease,
    clear_run_lease,
    current_run_lease,
    read_cancel_request,
    request_cancel,
)
from febio_cae_harness.case_state import CaseState
from tests.helpers.phase1b import initialize_case
from tests.integration.test_attempt_store import config

def _probe_attempt_lock(root: str, queue) -> None:
    try:
        lock = AttemptRunLock.acquire(Path(root), "attempt-a", "owner-a")
    except OSError:
        queue.put("blocked")
    else:
        lock.close()
        queue.put("acquired")

def test_pending_lease_blocks_second_owner_and_binding_is_create_new(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    lease = begin_run_lease(case, attempt)
    with pytest.raises(RuntimeError, match="CASE_RUNNING"):
        begin_run_lease(case, attempt)
    bound = bind_run_lease(case, lease, pid=1234, job_name="febio-attempt-1234")
    payload = json.loads(bound.binding_path.read_text(encoding="utf-8"))
    assert payload["event_type"] == "RUN_LEASE_BOUND"
    assert payload["pid"] == 1234
    assert payload["owner_token"] == lease.owner_token
    with pytest.raises(FileExistsError):
        bind_run_lease(case, lease, pid=9999, job_name="replacement")

def test_attempt_run_lock_is_exclusive_and_record_is_never_deleted(tmp_path):
    root = tmp_path / "attempt-a"
    root.mkdir()
    first = AttemptRunLock.acquire(root, "attempt-a", "owner-a")
    record_bytes = (root / "attempt-run-lock.json").read_bytes()
    try:
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        process = context.Process(target=_probe_attempt_lock, args=(str(root), queue))
        process.start()
        process.join(5)
        assert process.exitcode == 0
        assert queue.get(timeout=1) == "blocked"
    finally:
        first.close()
    assert (root / "attempt-run-lock.json").read_bytes() == record_bytes

def test_cancel_and_clear_are_immutable_owned_records(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    first = AttemptStore.start(case, config())
    lease = begin_run_lease(case, first)
    with pytest.raises(RuntimeError, match="foreign cancellation token"):
        request_cancel(case, first.attempt_id, "wrong-token")
    cancel_path = request_cancel(case, first.attempt_id, lease.owner_token)
    assert cancel_path == lease.cancel_request_path
    assert read_cancel_request(lease) is True
    cleared = clear_run_lease(case, lease)
    assert cleared == lease.cleared_path
    assert current_run_lease(case) is None
    assert lease.lease_path.exists()
    assert lease.binding_path.exists() is False
    assert lease.cancel_request_path.exists()
    assert lease.cleared_path.exists()
    with pytest.raises(FileExistsError):
        clear_run_lease(case, lease)

def test_cleared_history_never_blocks_a_new_attempt_or_changes_old_bytes(tmp_path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    first = AttemptStore.start(case, config())
    first_lease = begin_run_lease(case, first)
    first_created_bytes = first_lease.lease_path.read_bytes()
    clear_run_lease(case, first_lease)
    second = AttemptStore.start(case, config())
    second_lease = begin_run_lease(case, second)
    assert current_run_lease(case).attempt_id == second.attempt_id
    assert first_lease.lease_path.read_bytes() == first_created_bytes
    assert second_lease.lease_path.parent == second.root
    assert second_lease.lease_path != first_lease.lease_path
```

- [ ] **Step 2: Run the test and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_run_lease.py -q
```

Expected: collection fails with `ImportError: cannot import name 'begin_run_lease' from 'febio_cae_harness.attempt_store'`.

- [ ] **Step 3: Implement immutable records and active-lease projection**

```python
# append to attempt_store.py
import msvcrt

class AttemptRunLock:
    def __init__(self, stream):
        self._stream = stream

    @classmethod
    def acquire(
        cls,
        attempt_root: Path,
        attempt_id: str,
        owner_token: str,
    ) -> AttemptRunLock:
        path = attempt_root / "attempt-run-lock.json"
        if not path.exists():
            _write_json(
                path,
                {
                    "schema_version": 1,
                    "attempt_id": attempt_id,
                    "owner_token": owner_token,
                },
            )
        payload = _load_json(path)
        if (
            payload.get("attempt_id") != attempt_id
            or payload.get("owner_token") != owner_token
        ):
            raise RuntimeError("attempt run-lock ownership changed")
        stream = path.open("r+b", buffering=0)
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except BaseException:
            stream.close()
            raise
        return cls(stream)

    def close(self) -> None:
        if self._stream is None:
            return
        stream, self._stream = self._stream, None
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()

    def __enter__(self) -> AttemptRunLock:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

@dataclass(frozen=True)
class RunLease:
    case_dir: Path
    attempt_id: str
    owner_token: str
    started_at: str
    lease_path: Path
    binding_path: Path
    cancel_request_path: Path
    cleared_path: Path

def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))

def _lease_from_created(path: Path) -> RunLease:
    payload = _load_json(path)
    attempt_root = path.parent
    case_dir = attempt_root.parents[2]
    return RunLease(
        case_dir=case_dir,
        attempt_id=str(payload["attempt_id"]),
        owner_token=str(payload["owner_token"]),
        started_at=str(payload["started_at"]),
        lease_path=path,
        binding_path=attempt_root / "run-lease-bound.json",
        cancel_request_path=attempt_root / "cancel-request.json",
        cleared_path=attempt_root / "run-lease-cleared.json",
    )

def _current_run_lease_unlocked(case: CaseStore) -> RunLease | None:
    attempts = case.case_dir / "90_Temporary" / "attempts"
    if not attempts.exists():
        return None
    active = [
        _lease_from_created(path)
        for path in attempts.glob("*/run-lease-created.json")
        if not (path.parent / "run-lease-cleared.json").exists()
    ]
    if len(active) > 1:
        raise RuntimeError("RUN_LEASE_CONFLICT")
    return active[0] if active else None

def current_run_lease(case: CaseStore) -> RunLease | None:
    with case.locked() as transaction:
        transaction.replay()
        return _current_run_lease_unlocked(case)

def begin_run_lease(case: CaseStore, attempt: AttemptStore) -> RunLease:
    with case.locked() as transaction:
        transaction.replay()
        if _current_run_lease_unlocked(case) is not None:
            raise RuntimeError("CASE_RUNNING")
        owner_token = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        lease = RunLease(
            case_dir=case.case_dir,
            attempt_id=attempt.attempt_id,
            owner_token=owner_token,
            started_at=started_at,
            lease_path=attempt.root / "run-lease-created.json",
            binding_path=attempt.root / "run-lease-bound.json",
            cancel_request_path=attempt.root / "cancel-request.json",
            cleared_path=attempt.root / "run-lease-cleared.json",
        )
        _write_json(
            lease.lease_path,
            {
                "schema_version": 1,
                "event_type": "RUN_LEASE_CREATED",
                "attempt_id": lease.attempt_id,
                "owner_token": lease.owner_token,
                "started_at": lease.started_at,
            },
        )
        return lease

def _require_active_owned_lease(case: CaseStore, lease: RunLease) -> None:
    current = _current_run_lease_unlocked(case)
    if (
        current is None
        or current.attempt_id != lease.attempt_id
        or current.owner_token != lease.owner_token
    ):
        raise RuntimeError("run lease ownership changed")

def bind_run_lease(
    case: CaseStore,
    lease: RunLease,
    pid: int,
    job_name: str,
) -> RunLease:
    with case.locked() as transaction:
        transaction.replay()
        _require_active_owned_lease(case, lease)
        _write_json(
            lease.binding_path,
            {
                "schema_version": 1,
                "event_type": "RUN_LEASE_BOUND",
                "attempt_id": lease.attempt_id,
                "owner_token": lease.owner_token,
                "pid": pid,
                "job_name": job_name,
                "bound_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return lease

def request_cancel(case: CaseStore, attempt_id: str, owner_token: str) -> Path:
    with case.locked() as transaction:
        transaction.replay()
        current = _current_run_lease_unlocked(case)
        if (
            current is None
            or current.attempt_id != attempt_id
            or current.owner_token != owner_token
        ):
            raise RuntimeError("foreign cancellation token")
        _write_json(
            current.cancel_request_path,
            {
                "schema_version": 1,
                "event_type": "RUN_CANCEL_REQUESTED",
                "attempt_id": attempt_id,
                "owner_token": owner_token,
                "requested_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return current.cancel_request_path

def read_cancel_request(lease: RunLease) -> bool:
    if not lease.cancel_request_path.exists():
        return False
    payload = _load_json(lease.cancel_request_path)
    if payload.get("attempt_id") != lease.attempt_id or payload.get("owner_token") != lease.owner_token:
        raise RuntimeError("foreign cancellation token")
    return True

def clear_run_lease(case: CaseStore, lease: RunLease) -> Path:
    with case.locked() as transaction:
        transaction.replay()
        _require_active_owned_lease(case, lease)
        _write_json(
            lease.cleared_path,
            {
                "schema_version": 1,
                "event_type": "RUN_LEASE_CLEARED",
                "attempt_id": lease.attempt_id,
                "owner_token": lease.owner_token,
                "cleared_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return lease.cleared_path
```

- [ ] **Step 4: Run the GREEN command and prove no delete/overwrite path exists**

```powershell
if (rg -n "\\.unlink\\(|Remove-Item|run-lease\\.json|run-lease-binding\\.json" apps/febio_cae_harness/src/febio_cae_harness/attempt_store.py) { throw 'DESTRUCTIVE_OR_REUSED_LEASE_PATH_FOUND' }
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_run_lease.py apps/febio_cae_harness/tests/integration/test_attempt_store.py -q
```

Expected: `rg` returns no match and pytest exits `0` with only `passed`.

- [ ] **Step 5: Inspect, stage, and commit exactly Task 3 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/attempt_store.py apps/febio_cae_harness/tests/integration/test_run_lease.py
git diff --cached --check
git commit -m "feat: record immutable run lease history"
```

### Task 4: Authoritative RESULT_VERIFIED-only create-new promotion

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/promotion.py`
- Create: `apps/febio_cae_harness/tests/integration/test_promotion.py`

**Interfaces:**
- Consumes: `AttemptStore`, authoritative `CaseStore.open(case_dir)` and `with case.locked() as transaction: transaction.replay(); transaction.append(event_type, to_state, payload)`, `read_event_log(case.event_log)`, create-new `completion-decision.json`, create-new `result-verification.json`, `ArtifactRef` from `jsonio`, `copy_create_new`, `sha256_file`, `CaseState.RESULT_VERIFIED`
- Produces: `PromotionItem`, `promote_verified(attempt: AttemptStore) -> tuple[ArtifactRef, ...]`, create-new `promotion-event.json`, same-state authoritative `PROMOTION_COMPLETED` case event

The caller cannot supply a status or artifact list. Promotion opens the case,
replays authoritative state while holding the case lock, binds both current
record hashes to the authoritative `RESULT_VALIDATED` event, and derives every
source from the event-bound attempt records.

- [ ] **Step 1: Write spoof refusal, authoritative success, binding, and collision tests**

```python
# tests/integration/test_promotion.py
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import febio_cae_harness.promotion as promotion
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.hashing import sha256_file

def canonical_write(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

@dataclass
class AuthoritativeCase:
    case_dir: Path
    state: CaseState
    events: list[tuple[str, CaseState, dict[str, object]]] = field(default_factory=list)

    @property
    def event_log(self) -> Path:
        return self.case_dir / "05_Verification" / "harness" / "events.jsonl"

    @contextmanager
    def locked(self):
        yield self

    def replay(self):
        return {"harness": {"case_state": self.state.value}}

    def append(self, event_type, to_state, payload):
        self.events.append((event_type, to_state, payload))
        return {"event_type": event_type}

def create_attempt_records(
    root: Path,
    source: Path,
    completion_binding_override: str | None = None,
) -> None:
    completion = {
        "kind": "completion-decision",
        "status": "accepted",
        "case_state": "SOLVED",
        "attempt_id": root.name,
        "evidence_set_sha256": "E" * 64,
    }
    canonical_write(root / "completion-decision.json", completion)
    completion_hash = sha256_file(root / "completion-decision.json")
    result = {
        "kind": "result-validation",
        "status": "accepted",
        "case_state": "RESULT_VERIFIED",
        "attempt_id": root.name,
        "completion_decision_sha256": (
            completion_hash
            if completion_binding_override is None
            else completion_binding_override
        ),
        "artifacts": [
            {
                "role": "xplt",
                "source_path": str(source),
                "source_bytes": source.stat().st_size,
                "source_sha256": sha256_file(source),
                "destination_directory": "03_Result",
                "destination_name": "solver.xplt",
            }
        ],
    }
    canonical_write(root / "result-verification.json", result)

def attempt(case_dir: Path, root: Path):
    return type(
        "Attempt",
        (),
        {"case_dir": case_dir, "root": root, "attempt_id": root.name},
    )()

def bind_authoritative_result_event(monkeypatch, root: Path) -> None:
    payload = {
        "attempt_id": root.name,
        "completion_decision_sha256": sha256_file(root / "completion-decision.json"),
        "result_verification_sha256": sha256_file(root / "result-verification.json"),
    }
    monkeypatch.setattr(
        promotion,
        "read_event_log",
        lambda path: (
            SimpleNamespace(
                event_type="RESULT_VALIDATED",
                to_state=CaseState.RESULT_VERIFIED,
                payload=payload,
            ),
        ),
    )

def test_spoofed_result_record_cannot_override_authoritative_case_state(tmp_path, monkeypatch):
    case_dir = tmp_path / "case"
    (case_dir / "03_Result").mkdir(parents=True)
    root = case_dir / "90_Temporary" / "attempts" / "attempt-a"
    solver = root / "solver"
    solver.mkdir(parents=True)
    source = solver / "solver.xplt"
    source.write_bytes(b"verified-xplt")
    create_attempt_records(root, source)
    authoritative = AuthoritativeCase(case_dir, CaseState.RESULT_INCOMPLETE)
    monkeypatch.setattr(
        promotion.CaseStore,
        "open",
        lambda path: authoritative,
    )
    with pytest.raises(RuntimeError, match="authoritative case state is not RESULT_VERIFIED"):
        promotion.promote_verified(attempt(case_dir, root))
    assert list((case_dir / "03_Result").iterdir()) == []

def test_promotion_rehashes_authoritative_records_and_never_overwrites(tmp_path, monkeypatch):
    case_dir = tmp_path / "case"
    (case_dir / "03_Result").mkdir(parents=True)
    root = case_dir / "90_Temporary" / "attempts" / "attempt-a"
    solver = root / "solver"
    solver.mkdir(parents=True)
    source = solver / "solver.xplt"
    source.write_bytes(b"verified-xplt")
    create_attempt_records(root, source)
    (case_dir / "03_Result" / "solver.xplt").write_bytes(b"existing")
    authoritative = AuthoritativeCase(case_dir, CaseState.RESULT_VERIFIED)
    monkeypatch.setattr(promotion.CaseStore, "open", lambda path: authoritative)
    bind_authoritative_result_event(monkeypatch, root)
    refs = promotion.promote_verified(attempt(case_dir, root))
    assert refs[0].path.name != "solver.xplt"
    assert refs[0].path.read_bytes() == b"verified-xplt"
    assert (case_dir / "03_Result" / "solver.xplt").read_bytes() == b"existing"
    assert authoritative.events[-1][0] == "PROMOTION_COMPLETED"
    assert authoritative.events[-1][1] is CaseState.RESULT_VERIFIED
    event = json.loads((root / "promotion-event.json").read_text(encoding="utf-8"))
    assert event["completion_decision_sha256"] == sha256_file(
        root / "completion-decision.json"
    )
    assert event["result_verification_sha256"] == sha256_file(
        root / "result-verification.json"
    )

def test_tampered_completion_binding_blocks_before_copy(tmp_path, monkeypatch):
    case_dir = tmp_path / "case"
    (case_dir / "03_Result").mkdir(parents=True)
    root = case_dir / "90_Temporary" / "attempts" / "attempt-a"
    solver = root / "solver"
    solver.mkdir(parents=True)
    source = solver / "solver.xplt"
    source.write_bytes(b"verified-xplt")
    create_attempt_records(root, source, completion_binding_override="0" * 64)
    authoritative = AuthoritativeCase(case_dir, CaseState.RESULT_VERIFIED)
    monkeypatch.setattr(promotion.CaseStore, "open", lambda path: authoritative)
    bind_authoritative_result_event(monkeypatch, root)
    with pytest.raises(RuntimeError, match="completion decision binding mismatch"):
        promotion.promote_verified(attempt(case_dir, root))
    assert list((case_dir / "03_Result").iterdir()) == []

def test_post_validation_result_tamper_is_rejected_by_event_hash(tmp_path, monkeypatch):
    case_dir = tmp_path / "case"
    (case_dir / "03_Result").mkdir(parents=True)
    root = case_dir / "90_Temporary" / "attempts" / "attempt-a"
    solver = root / "solver"
    solver.mkdir(parents=True)
    source = solver / "solver.xplt"
    source.write_bytes(b"verified-xplt")
    create_attempt_records(root, source)
    authoritative = AuthoritativeCase(case_dir, CaseState.RESULT_VERIFIED)
    monkeypatch.setattr(promotion.CaseStore, "open", lambda path: authoritative)
    bind_authoritative_result_event(monkeypatch, root)
    result_path = root / "result-verification.json"
    result_path.write_bytes(result_path.read_bytes() + b" ")
    with pytest.raises(RuntimeError, match="authoritative result event hash drifted"):
        promotion.promote_verified(attempt(case_dir, root))
    assert list((case_dir / "03_Result").iterdir()) == []
```

- [ ] **Step 2: Run the tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_promotion.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.promotion'`.

- [ ] **Step 3: Implement authoritative record parsing and post-copy event ordering**

```python
# promotion.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from febio_cae_harness.attempt_store import AttemptStore, _write_json
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.events import read_event_log
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.jsonio import ArtifactRef
from febio_cae_harness.model_adoption import copy_create_new

@dataclass(frozen=True)
class PromotionItem:
    role: str
    source_path: Path
    source_bytes: int
    source_sha256: str
    destination_directory: str
    destination_name: str

def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))

def _parse_item(attempt: AttemptStore, value: dict[str, object]) -> PromotionItem:
    directory = str(value["destination_directory"])
    if directory not in {"03_Result", "04_Report", "05_Verification"}:
        raise ValueError("promotion destination is not a permanent case directory")
    source = Path(str(value["source_path"])).resolve(strict=True)
    if not source.is_relative_to(attempt.root.resolve(strict=True)):
        raise ValueError("promotion source is outside the verified attempt")
    destination_name = str(value["destination_name"])
    if Path(destination_name).name != destination_name:
        raise ValueError("promotion destination_name must be one basename")
    return PromotionItem(
        role=str(value["role"]),
        source_path=source,
        source_bytes=int(value["source_bytes"]),
        source_sha256=str(value["source_sha256"]).upper(),
        destination_directory=directory,
        destination_name=destination_name,
    )

def promote_verified(attempt: AttemptStore) -> tuple[ArtifactRef, ...]:
    case = CaseStore.open(attempt.case_dir)
    completion_path = attempt.root / "completion-decision.json"
    result_path = attempt.root / "result-verification.json"
    with case.locked() as transaction:
        projection = transaction.replay()
        state = str(projection["harness"]["case_state"])
        if state != CaseState.RESULT_VERIFIED.value:
            raise RuntimeError("authoritative case state is not RESULT_VERIFIED")
        validation_events = [
            event
            for event in read_event_log(case.event_log)
            if event.event_type == "RESULT_VALIDATED"
            and event.payload.get("attempt_id") == attempt.attempt_id
        ]
        if (
            not validation_events
            or validation_events[-1].to_state is not CaseState.RESULT_VERIFIED
        ):
            raise RuntimeError("authoritative RESULT_VALIDATED event is absent")
        validation_payload = validation_events[-1].payload
        completion = _load(completion_path)
        result = _load(result_path)
        if (
            completion.get("kind") != "completion-decision"
            or completion.get("status") != "accepted"
            or completion.get("case_state") != CaseState.SOLVED.value
            or completion.get("attempt_id") != attempt.attempt_id
        ):
            raise RuntimeError("authoritative completion decision is invalid")
        completion_sha256 = sha256_file(completion_path)
        if (
            result.get("kind") != "result-validation"
            or result.get("status") != "accepted"
            or result.get("case_state") != CaseState.RESULT_VERIFIED.value
            or result.get("attempt_id") != attempt.attempt_id
        ):
            raise RuntimeError("authoritative result verification is invalid")
        if result.get("completion_decision_sha256") != completion_sha256:
            raise RuntimeError("completion decision binding mismatch")
        result_sha256 = sha256_file(result_path)
        if (
            validation_payload.get("completion_decision_sha256") != completion_sha256
            or validation_payload.get("result_verification_sha256") != result_sha256
        ):
            raise RuntimeError("authoritative result event hash drifted")
        items = tuple(_parse_item(attempt, item) for item in result["artifacts"])
        promoted: list[ArtifactRef] = []
        for item in items:
            if item.source_path.stat().st_size != item.source_bytes:
                raise RuntimeError("promotion source byte count drifted")
            if sha256_file(item.source_path) != item.source_sha256:
                raise RuntimeError("promotion source hash drifted")
            promoted.append(
                copy_create_new(
                    item.source_path,
                    attempt.case_dir / item.destination_directory / item.destination_name,
                    item.source_bytes,
                    item.source_sha256,
                    allow_unique_name=True,
                )
            )
        event_records = [
            {
                "role": item.role,
                "source_sha256": item.source_sha256,
                "destination": str(ref.path.relative_to(attempt.case_dir)),
                "destination_bytes": ref.bytes,
                "destination_sha256": sha256_file(ref.path),
            }
            for item, ref in zip(items, promoted, strict=True)
        ]
        if any(
            record["source_sha256"] != record["destination_sha256"]
            for record in event_records
        ):
            raise RuntimeError("promoted artifact hash mismatch")
        event_path = attempt.root / "promotion-event.json"
        _write_json(
            event_path,
            {
                "schema_version": 1,
                "event_type": "PROMOTION_COMPLETED",
                "attempt_id": attempt.attempt_id,
                "completion_decision_sha256": completion_sha256,
                "result_verification_sha256": result_sha256,
                "artifacts": event_records,
            },
        )
        event_sha256 = sha256_file(event_path)
        transaction.append(
            "PROMOTION_COMPLETED",
            CaseState.RESULT_VERIFIED,
            {
                "attempt_id": attempt.attempt_id,
                "completion_decision_sha256": completion_sha256,
                "result_verification_sha256": result_sha256,
                "promotion_event_sha256": event_sha256,
            },
        )
        return tuple(promoted)
```

- [ ] **Step 4: Run the GREEN command, event replay, and immutable-copy regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_promotion.py apps/febio_cae_harness/tests/integration/test_model_adoption.py apps/febio_cae_harness/tests/integration/test_case_store.py -q
```

Expected: command exits `0`; spoof/tamper tests reject before copying, successful promotion preserves the existing destination and appends one authoritative same-state event.

- [ ] **Step 5: Inspect, stage, and commit exactly Task 4 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/promotion.py apps/febio_cae_harness/tests/integration/test_promotion.py
git diff --cached --check
git commit -m "feat: promote authoritative verified artifacts"
```

### Task 5: Typed production-only FEBio command

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/febio_command.py`
- Create: `apps/febio_cae_harness/tests/unit/test_febio_command.py`

**Interfaces:**
- Consumes: absolute solver/input/attempt paths and the Phase 1B attempt layout from Task 2
- Produces: `FebioRunRequest`, `ValidatedFebioCommand`, `validate_febio_request(request: FebioRunRequest) -> ValidatedFebioCommand`, `build_febio_argv(request: FebioRunRequest) -> list[str]`

- [ ] **Step 1: Write the exact typed-command tests**

```python
# tests/unit/test_febio_command.py
from pathlib import Path

import pytest

from febio_cae_harness.febio_command import (
    FebioRunRequest,
    build_febio_argv,
    validate_febio_request,
)

HASH = "A" * 64

def request(tmp_path: Path) -> FebioRunRequest:
    solver_dir = tmp_path / "attempt with spaces" / "solver"
    solver_dir.mkdir(parents=True)
    executable = tmp_path / "FEBio Studio 4.12" / "febio4.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic executable")
    input_feb = solver_dir / "input.feb"
    input_feb.write_bytes(b"<febio_spec/>")
    return FebioRunRequest(
        executable=executable.resolve(),
        expected_solver_sha256=HASH,
        input_feb=input_feb.resolve(),
        expected_input_sha256=HASH,
        working_directory=solver_dir.resolve(),
        output_log=solver_dir.resolve() / "solver.log",
        output_xplt=solver_dir.resolve() / "solver.xplt",
        timeout_seconds=3600.0,
        cancel_grace_seconds=15.0,
        process_tree_working_set_limit_mib=4096,
        inherited_environment_names=("SYSTEMROOT", "TEMP", "TMP"),
    )

def test_build_febio_argv_is_the_only_production_shape(tmp_path):
    value = request(tmp_path)
    assert build_febio_argv(value) == [
        str(value.executable),
        "-i", "input.feb",
        "-o", "solver.log",
        "-p", "solver.xplt",
    ]
    assert not hasattr(value, "extra_args")

@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"executable_name": "python.exe"}, "executable must be named febio4.exe"),
        ({"input_name": "..%2finput.feb"}, "input filename must be input.feb"),
        ({"log_name": "outside.log"}, "output_log must be solver.log"),
        ({"xplt_name": "outside.xplt"}, "output_xplt must be solver.xplt"),
    ],
)
def test_rejects_noncanonical_production_paths(tmp_path, change, message):
    value = request(tmp_path)
    if "executable_name" in change:
        replacement = value.executable.with_name(change["executable_name"])
        replacement.write_bytes(b"x")
        value = value.with_paths(executable=replacement)
    if "input_name" in change:
        replacement = value.working_directory / change["input_name"]
        replacement.write_bytes(b"x")
        value = value.with_paths(input_feb=replacement)
    if "log_name" in change:
        value = value.with_paths(output_log=value.working_directory.parent / change["log_name"])
    if "xplt_name" in change:
        value = value.with_paths(output_xplt=value.working_directory.parent / change["xplt_name"])
    with pytest.raises(ValueError, match=message):
        validate_febio_request(value)

def test_rejects_existing_output_or_shell_metacharacter_environment_name(tmp_path):
    value = request(tmp_path)
    value.output_log.write_bytes(b"stale")
    with pytest.raises(FileExistsError, match="output_log already exists"):
        validate_febio_request(value)
    clean = request(tmp_path / "clean")
    poisoned = clean.with_environment_names(("SYSTEMROOT", "PATH&whoami"))
    with pytest.raises(ValueError, match="invalid environment name"):
        validate_febio_request(poisoned)

def test_accepts_spaces_as_literal_path_characters(tmp_path):
    value = request(tmp_path)
    validated = validate_febio_request(value)
    assert validated.argv[0] == str(value.executable)
    assert validated.cwd == value.working_directory
    assert validated.stdout_path == value.working_directory / "stdout.raw"
    assert validated.stderr_path == value.working_directory / "stderr.raw"
```

- [ ] **Step 2: Run the command tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_febio_command.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.febio_command'`.

- [ ] **Step 3: Implement the immutable request and production validation**

```python
# febio_command.py
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

SHA256_RE = re.compile(r"[0-9A-Fa-f]{64}\Z")
ENVIRONMENT_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_()]*\Z")

@dataclass(frozen=True)
class FebioRunRequest:
    executable: Path
    expected_solver_sha256: str
    input_feb: Path
    expected_input_sha256: str
    working_directory: Path
    output_log: Path
    output_xplt: Path
    timeout_seconds: float
    cancel_grace_seconds: float
    process_tree_working_set_limit_mib: int
    inherited_environment_names: tuple[str, ...]

    def with_paths(
        self,
        *,
        executable: Path | None = None,
        input_feb: Path | None = None,
        output_log: Path | None = None,
        output_xplt: Path | None = None,
    ) -> FebioRunRequest:
        return replace(
            self,
            executable=self.executable if executable is None else executable,
            input_feb=self.input_feb if input_feb is None else input_feb,
            output_log=self.output_log if output_log is None else output_log,
            output_xplt=self.output_xplt if output_xplt is None else output_xplt,
        )

    def with_environment_names(self, names: tuple[str, ...]) -> FebioRunRequest:
        return replace(self, inherited_environment_names=names)

@dataclass(frozen=True)
class ValidatedFebioCommand:
    argv: tuple[str, ...]
    cwd: Path
    stdout_path: Path
    stderr_path: Path
    dump_path: Path

def build_febio_argv(request: FebioRunRequest) -> list[str]:
    return [
        str(request.executable),
        "-i", request.input_feb.name,
        "-o", request.output_log.name,
        "-p", request.output_xplt.name,
    ]

def _require_absolute(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute")

def validate_febio_request(request: FebioRunRequest) -> ValidatedFebioCommand:
    for label, path in (
        ("executable", request.executable),
        ("input_feb", request.input_feb),
        ("working_directory", request.working_directory),
        ("output_log", request.output_log),
        ("output_xplt", request.output_xplt),
    ):
        _require_absolute(path, label)
    if request.executable.name.lower() != "febio4.exe":
        raise ValueError("executable must be named febio4.exe")
    if request.input_feb.name != "input.feb":
        raise ValueError("input filename must be input.feb")
    if request.input_feb.parent != request.working_directory:
        raise ValueError("input_feb must be inside working_directory")
    if request.output_log != request.working_directory / "solver.log":
        raise ValueError("output_log must be solver.log in working_directory")
    if request.output_xplt != request.working_directory / "solver.xplt":
        raise ValueError("output_xplt must be solver.xplt in working_directory")
    if not request.executable.is_file():
        raise FileNotFoundError("FEBio executable does not exist")
    if not request.input_feb.is_file():
        raise FileNotFoundError("input.feb does not exist")
    for label, path in (("output_log", request.output_log), ("output_xplt", request.output_xplt)):
        if path.exists():
            raise FileExistsError(f"{label} already exists")
    if not SHA256_RE.fullmatch(request.expected_solver_sha256):
        raise ValueError("expected_solver_sha256 must be 64 hexadecimal characters")
    if not SHA256_RE.fullmatch(request.expected_input_sha256):
        raise ValueError("expected_input_sha256 must be 64 hexadecimal characters")
    if request.timeout_seconds <= 0 or request.cancel_grace_seconds <= 0:
        raise ValueError("timeouts must be positive")
    if request.process_tree_working_set_limit_mib <= 0:
        raise ValueError("process tree working-set limit must be positive")
    if len(set(request.inherited_environment_names)) != len(request.inherited_environment_names):
        raise ValueError("environment names must be unique")
    for name in request.inherited_environment_names:
        if ENVIRONMENT_NAME_RE.fullmatch(name) is None:
            raise ValueError(f"invalid environment name: {name}")
    return ValidatedFebioCommand(
        argv=tuple(build_febio_argv(request)),
        cwd=request.working_directory,
        stdout_path=request.working_directory / "stdout.raw",
        stderr_path=request.working_directory / "stderr.raw",
        dump_path=request.working_directory / "solver.dmp",
    )
```

- [ ] **Step 4: Run the focused GREEN command**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_febio_command.py -q
```

Expected: command exits `0` and pytest reports only `passed`.

- [ ] **Step 5: Inspect, stage, and commit exactly Task 5 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/febio_command.py apps/febio_cae_harness/tests/unit/test_febio_command.py
git diff --cached --check
git commit -m "feat: type the fixed FEBio production command"
```

### Task 6: Windows held-handle and Job Object primitives

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/windows_job.py`
- Create: `apps/febio_cae_harness/tests/integration/test_windows_job.py`

**Interfaces:**
- Consumes: validated argv/cwd/environment from Task 5 and Windows file/process APIs
- Produces: `PinnedFile`, `FileIdentity`, `JobObject`, `NativeProcess`, `launch_suspended()`, `query_process_image_path()`

- [ ] **Step 1: Write Windows-only tests for pinned identity and pre-resume Job ownership**

```python
# tests/integration/test_windows_job.py
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.windows_job import (
    JobObject,
    PinnedFile,
    create_inheritable_output,
    launch_suspended,
    query_process_image_path,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")

def test_pinned_file_hashes_held_handle_and_denies_replacement(tmp_path):
    target = tmp_path / "approved.bin"
    target.write_bytes(b"approved")
    expected = sha256_file(target)
    with PinnedFile.open_readonly(target) as pinned:
        assert pinned.sha256 == expected
        assert pinned.bytes == 8
        assert pinned.identity.volume_serial_number > 0
        assert pinned.identity.file_index > 0
        with pytest.raises(PermissionError):
            target.write_bytes(b"replaced")
        assert pinned.rehash() == expected
    target.write_bytes(b"replace allowed after close")

def test_suspended_process_is_assigned_before_resume_and_job_close_kills(tmp_path):
    stdout_path = tmp_path / "stdout.raw"
    stderr_path = tmp_path / "stderr.raw"
    with (
        create_inheritable_output(stdout_path) as stdout,
        create_inheritable_output(stderr_path) as stderr,
        JobObject.create_kill_on_close() as job,
    ):
        process = launch_suspended(
            argv=[
                sys.executable,
                "-c",
                "import pathlib,time; pathlib.Path('started.flag').write_text('yes'); time.sleep(60)",
            ],
            cwd=tmp_path,
            environment={"SYSTEMROOT": os.environ["SYSTEMROOT"]},
            stdout_handle=stdout.handle,
            stderr_handle=stderr.handle,
        )
        with process:
            assert not (tmp_path / "started.flag").exists()
            assert Path(query_process_image_path(process.process_handle)).samefile(sys.executable)
            job.assign(process.process_handle)
            assert job.active_processes() == 1
            process.resume()
            for _ in range(100):
                if (tmp_path / "started.flag").exists():
                    break
                time.sleep(0.01)
            assert (tmp_path / "started.flag").read_text() == "yes"
            pid = process.pid
            job.terminate(0xE0000001)
            assert job.wait_empty(5.0)
            assert not psutil.pid_exists(pid)

def test_assignment_failure_terminates_while_still_suspended(tmp_path, monkeypatch):
    stdout_path = tmp_path / "stdout.raw"
    stderr_path = tmp_path / "stderr.raw"
    with (
        create_inheritable_output(stdout_path) as stdout,
        create_inheritable_output(stderr_path) as stderr,
        JobObject.create_kill_on_close() as job,
    ):
        process = launch_suspended(
            [sys.executable, "-c", "open('must-not-exist','w').write('bad')"],
            tmp_path,
            {"SYSTEMROOT": os.environ["SYSTEMROOT"]},
            stdout.handle,
            stderr.handle,
        )
        pid = process.pid
        monkeypatch.setattr(job, "_assign", lambda handle: False)
        with pytest.raises(OSError, match="AssignProcessToJobObject"):
            job.assign_or_terminate_suspended(process)
        assert process.wait(5.0)
        assert not (tmp_path / "must-not-exist").exists()
        assert not psutil.pid_exists(pid)
        process.close()
```

- [ ] **Step 2: Run the native tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_windows_job.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.windows_job'`.

- [ ] **Step 3: Implement held-file hashing and native handle ownership**

```python
# windows_job.py — imports, constants, held files, and output handles
from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
CREATE_ALWAYS = 2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_WRITE_THROUGH = 0x80000000
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESHOWWINDOW = 0x00000001
STARTF_USESTDHANDLES = 0x00000100
SW_HIDE = 0
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
STILL_ACTIVE = 259
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1

class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]

class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]

@dataclass(frozen=True)
class FileIdentity:
    volume_serial_number: int
    file_index: int

def _raise_last_error(operation: str) -> None:
    raise ctypes.WinError(ctypes.get_last_error(), operation)

def _close_handle(handle: int) -> None:
    if handle and handle != INVALID_HANDLE_VALUE and not kernel32.CloseHandle(handle):
        _raise_last_error("CloseHandle")

def _file_information(handle: int) -> BY_HANDLE_FILE_INFORMATION:
    info = BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        _raise_last_error("GetFileInformationByHandle")
    return info

def _hash_handle(handle: int) -> tuple[int, str]:
    original = ctypes.c_longlong()
    if not kernel32.SetFilePointerEx(handle, 0, ctypes.byref(original), 1):
        _raise_last_error("SetFilePointerEx query")
    if not kernel32.SetFilePointerEx(handle, 0, None, 0):
        _raise_last_error("SetFilePointerEx rewind")
    digest = hashlib.sha256()
    total = 0
    buffer = ctypes.create_string_buffer(1024 * 1024)
    try:
        while True:
            count = wintypes.DWORD()
            if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                _raise_last_error("ReadFile")
            if count.value == 0:
                break
            digest.update(buffer.raw[: count.value])
            total += count.value
    finally:
        if not kernel32.SetFilePointerEx(handle, original.value, None, 0):
            _raise_last_error("SetFilePointerEx restore")
    return total, digest.hexdigest().upper()

class PinnedFile:
    def __init__(self, path: Path, handle: int):
        self.path = path.resolve(strict=True)
        self.handle = handle
        info = _file_information(handle)
        self.identity = FileIdentity(
            volume_serial_number=int(info.dwVolumeSerialNumber),
            file_index=(int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        )
        self.bytes, self.sha256 = _hash_handle(handle)

    @classmethod
    def open_readonly(cls, path: Path) -> Self:
        resolved = path.resolve(strict=True)
        handle = kernel32.CreateFileW(
            str(resolved),
            GENERIC_READ,
            FILE_SHARE_READ,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            _raise_last_error("CreateFileW readonly")
        return cls(resolved, handle)

    def rehash(self) -> str:
        size, digest = _hash_handle(self.handle)
        if size != self.bytes:
            raise RuntimeError("held file byte count changed")
        return digest

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, 0
            _close_handle(handle)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

class InheritableOutput:
    def __init__(self, path: Path, handle: int):
        self.path = path
        self.handle = handle

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, 0
            _close_handle(handle)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

def create_inheritable_output(path: Path) -> InheritableOutput:
    path.parent.mkdir(parents=True, exist_ok=True)
    attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_WRITE,
        FILE_SHARE_READ,
        ctypes.byref(attributes),
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        _raise_last_error("CreateFileW output")
    return InheritableOutput(path, handle)
```

- [ ] **Step 4: Complete the same module with suspended launch and Job Object operations**

```python
# windows_job.py — append below the Task 6 Step 3 definitions
class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]

class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]

class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]

class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]

class NativeProcess:
    def __init__(self, process_handle: int, thread_handle: int, pid: int, tid: int):
        self.process_handle = process_handle
        self.thread_handle = thread_handle
        self.pid = pid
        self.tid = tid
        self.resumed = False

    def resume(self) -> None:
        if self.resumed:
            raise RuntimeError("primary thread already resumed")
        if kernel32.ResumeThread(self.thread_handle) == 0xFFFFFFFF:
            _raise_last_error("ResumeThread")
        self.resumed = True

    def terminate(self, exit_code: int) -> None:
        if not kernel32.TerminateProcess(self.process_handle, exit_code):
            error = ctypes.get_last_error()
            if error != 5:
                raise ctypes.WinError(error, "TerminateProcess")

    def wait(self, timeout_seconds: float) -> bool:
        timeout_ms = max(0, min(0xFFFFFFFE, int(timeout_seconds * 1000)))
        result = kernel32.WaitForSingleObject(self.process_handle, timeout_ms)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        _raise_last_error("WaitForSingleObject")

    def exit_code(self) -> int:
        value = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(self.process_handle, ctypes.byref(value)):
            _raise_last_error("GetExitCodeProcess")
        return int(value.value)

    def close(self) -> None:
        if self.thread_handle:
            handle, self.thread_handle = self.thread_handle, 0
            _close_handle(handle)
        if self.process_handle:
            handle, self.process_handle = self.process_handle, 0
            _close_handle(handle)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

def _environment_block(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    entries = [f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].upper())]
    return ctypes.create_unicode_buffer("\0".join(entries) + "\0\0")

def launch_suspended(
    argv: list[str],
    cwd: Path,
    environment: dict[str, str],
    stdout_handle: int,
    stderr_handle: int,
) -> NativeProcess:
    if not argv:
        raise ValueError("argv must be nonempty")
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.dwFlags = STARTF_USESHOWWINDOW | STARTF_USESTDHANDLES
    startup.wShowWindow = SW_HIDE
    startup.hStdInput = kernel32.GetStdHandle(-10)
    startup.hStdOutput = stdout_handle
    startup.hStdError = stderr_handle
    info = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    environment_block = _environment_block(environment)
    created = kernel32.CreateProcessW(
        str(Path(argv[0]).resolve(strict=True)),
        command_line,
        None,
        None,
        True,
        CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        environment_block,
        str(cwd.resolve(strict=True)),
        ctypes.byref(startup),
        ctypes.byref(info),
    )
    if not created:
        _raise_last_error("CreateProcessW")
    return NativeProcess(
        int(info.hProcess),
        int(info.hThread),
        int(info.dwProcessId),
        int(info.dwThreadId),
    )

def query_process_image_path(process_handle: int) -> str:
    capacity = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(capacity.value)
    if not kernel32.QueryFullProcessImageNameW(
        process_handle,
        0,
        buffer,
        ctypes.byref(capacity),
    ):
        _raise_last_error("QueryFullProcessImageNameW")
    return buffer.value

class JobObject:
    def __init__(self, handle: int):
        self.handle = handle

    @classmethod
    def create_kill_on_close(cls) -> Self:
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            _raise_last_error("CreateJobObjectW")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            _close_handle(handle)
            _raise_last_error("SetInformationJobObject")
        return cls(int(handle))

    def _assign(self, process_handle: int) -> bool:
        return bool(kernel32.AssignProcessToJobObject(self.handle, process_handle))

    def assign(self, process_handle: int) -> None:
        if not self._assign(process_handle):
            _raise_last_error("AssignProcessToJobObject")

    def assign_or_terminate_suspended(self, process: NativeProcess) -> None:
        if self._assign(process.process_handle):
            return
        error = ctypes.get_last_error()
        process.terminate(0xE0000002)
        process.wait(5.0)
        raise ctypes.WinError(error, "AssignProcessToJobObject")

    def active_processes(self) -> int:
        value = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not kernel32.QueryInformationJobObject(
            self.handle,
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(value),
            ctypes.sizeof(value),
            None,
        ):
            _raise_last_error("QueryInformationJobObject")
        return int(value.ActiveProcesses)

    def terminate(self, exit_code: int) -> None:
        if not kernel32.TerminateJobObject(self.handle, exit_code):
            _raise_last_error("TerminateJobObject")

    def wait_empty(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() <= deadline:
            if self.active_processes() == 0:
                return True
            time.sleep(0.02)
        return self.active_processes() == 0

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, 0
            _close_handle(handle)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
```

- [ ] **Step 5: Run the GREEN command twice to catch leaked handles and timing races**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_windows_job.py -q
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_windows_job.py -q
```

Expected: each invocation exits `0` with `3 passed`; Task Manager shows no surviving test Python process, and each temporary directory is removable by pytest.

- [ ] **Step 6: Inspect, stage, and commit exactly Task 6 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/windows_job.py apps/febio_cae_harness/tests/integration/test_windows_job.py
git diff --cached --check
git commit -m "feat: own suspended processes with Windows jobs"
```

### Task 7: Owned process monitor and strict FEBio runner

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/owned_process.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/febio_runner.py`
- Modify: `apps/febio_cae_harness/src/febio_cae_harness/windows_job.py`
- Create: `apps/febio_cae_harness/tests/fixtures/process/fake_solver.py`
- Create: `apps/febio_cae_harness/tests/fixtures/process/fake_parent_child.py`
- Create: `apps/febio_cae_harness/tests/integration/test_febio_runner.py`

**Interfaces:**
- Consumes: `FebioRunRequest`, `ValidatedFebioCommand`, `PinnedFile`, `JobObject`, `RunLease`, `bind_run_lease()`, `read_cancel_request()`, `clear_run_lease()`
- Produces: internal `OwnedProcessRequest`, `ProcessEvidence.to_record() -> EvidenceRecord`, `run_owned_process(request: OwnedProcessRequest) -> ProcessEvidence`, public `run_febio(request: FebioRunRequest) -> ProcessEvidence`, `solver_artifact_records(evidence: ProcessEvidence) -> tuple[dict[str, object], dict[str, object]]`

- [ ] **Step 1: Add deterministic subprocess fixtures**

```python
# tests/fixtures/process/fake_solver.py
import argparse
import os
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("quiet", "fail", "memory", "partial"), required=True)
parser.add_argument("--seconds", type=float, default=60.0)
parser.add_argument("--mib", type=int, default=128)
args = parser.parse_args()

Path("solver.log").write_text("synthetic LOG prefix\n", encoding="utf-8")
Path("solver.xplt").write_bytes(b"synthetic XPLT prefix")
print(f"fake-solver pid={os.getpid()}", flush=True)
print("fake-solver stderr", file=__import__("sys").stderr, flush=True)
if args.mode == "fail":
    raise SystemExit(7)
if args.mode == "partial":
    Path("solver.dmp").write_bytes(b"partial dump")
    raise SystemExit(9)
if args.mode == "memory":
    allocation = bytearray(args.mib * 1024 * 1024)
    allocation[0] = 1
time.sleep(args.seconds)
```

```python
# tests/fixtures/process/fake_parent_child.py
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path("child.pid").write_text(str(child.pid), encoding="ascii")
time.sleep(60)
```

- [ ] **Step 2: Write generic lifecycle and strict-adapter integration tests**

```python
# tests/integration/test_febio_runner.py
import os
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

import febio_cae_harness.febio_runner as febio_runner
from febio_cae_harness.attempt_store import RunLease
from febio_cae_harness.febio_command import FebioRunRequest
from febio_cae_harness.febio_runner import solver_artifact_records
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.owned_process import OwnedProcessRequest, run_owned_process
from tests.helpers.phase1b import initialize_case
from febio_cae_harness.case_state import CaseState

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows lifecycle contract")
FIXTURES = Path(__file__).parents[1] / "fixtures" / "process"

def owned_request(
    tmp_path: Path,
    argv: tuple[str, ...],
    *,
    timeout: float = 10.0,
    memory_mib: int = 512,
    cancelled=lambda: False,
) -> OwnedProcessRequest:
    executable = Path(sys.executable).resolve()
    held_input = tmp_path / "input.feb"
    held_input.write_bytes(b"<febio_spec/>")
    return OwnedProcessRequest(
        argv=(str(executable), *argv),
        working_directory=tmp_path,
        environment={"SYSTEMROOT": os.environ["SYSTEMROOT"], "TEMP": str(tmp_path), "TMP": str(tmp_path)},
        executable=executable,
        expected_executable_sha256=sha256_file(executable),
        held_input=held_input,
        expected_input_sha256=sha256_file(held_input),
        stdout_path=tmp_path / "stdout.raw",
        stderr_path=tmp_path / "stderr.raw",
        observed_artifacts={
            "log": tmp_path / "solver.log",
            "xplt": tmp_path / "solver.xplt",
            "dump": tmp_path / "solver.dmp",
        },
        timeout_seconds=timeout,
        cancel_grace_seconds=3.0,
        process_tree_working_set_limit_mib=memory_mib,
        cancel_requested=cancelled,
        on_started=lambda pid, job_name: None,
    )

def test_nonzero_preserves_raw_streams_and_partial_outputs(tmp_path):
    request = owned_request(
        tmp_path,
        (str(FIXTURES / "fake_solver.py"), "--mode", "partial"),
    )
    evidence = run_owned_process(request)
    assert evidence.exit_code == 9
    assert evidence.termination_reason == "NONZERO_EXIT"
    assert "fake-solver pid=" in evidence.stdout_text
    assert "fake-solver stderr" in evidence.stderr_text
    assert evidence.artifacts_after["log"]["bytes"] > 0
    assert evidence.artifacts_after["xplt"]["bytes"] > 0
    assert evidence.artifacts_after["dump"]["bytes"] > 0
    records = solver_artifact_records(evidence)
    assert tuple(record["role"] for record in records) == (
        "attempt-solver-log",
        "attempt-solver-xplt",
    )
    assert all(set(record) == {"role", "path", "bytes", "sha256"} for record in records)
    command_record = evidence.to_record()
    assert command_record.kind == "process-evidence"
    assert set(command_record.to_payload()) == {"kind", "data"}
    assert "kind" not in command_record.data

@pytest.mark.parametrize(
    ("mode", "timeout", "memory_mib", "expected"),
    [
        ("quiet", 0.20, 512, "TIMEOUT"),
        ("memory", 10.0, 32, "MEMORY_LIMIT"),
    ],
)
def test_timeout_and_aggregate_memory_limit_terminate_owned_tree(
    tmp_path, mode, timeout, memory_mib, expected
):
    request = owned_request(
        tmp_path,
        (
            str(FIXTURES / "fake_solver.py"),
            "--mode", mode,
            "--seconds", "60",
            "--mib", "128",
        ),
        timeout=timeout,
        memory_mib=memory_mib,
    )
    evidence = run_owned_process(request)
    assert evidence.termination_reason == expected
    assert evidence.active_processes_after_finalize == 0
    assert all(not psutil.pid_exists(pid) for pid in evidence.observed_pids)

def test_cancel_is_polled_without_console_output_and_kills_child(tmp_path):
    marker = tmp_path / "cancel"
    timer = threading.Timer(0.30, marker.touch)
    timer.start()
    try:
        request = owned_request(
            tmp_path,
            (str(FIXTURES / "fake_parent_child.py"),),
            cancelled=marker.exists,
        )
        evidence = run_owned_process(request)
    finally:
        timer.cancel()
    child_pid = int((tmp_path / "child.pid").read_text(encoding="ascii"))
    assert evidence.termination_reason == "CANCELLED"
    assert evidence.active_processes_after_finalize == 0
    assert not psutil.pid_exists(child_pid)

def test_run_febio_passes_only_fixed_argv_binds_and_clears_lease(tmp_path, monkeypatch):
    solver_dir = tmp_path / "case" / "90_Temporary" / "attempts" / "a" / "solver"
    solver_dir.mkdir(parents=True)
    executable = tmp_path / "febio4.exe"
    executable.write_bytes(b"synthetic")
    input_feb = solver_dir / "input.feb"
    input_feb.write_bytes(b"<febio_spec/>")
    case = initialize_case(tmp_path / "case", CaseState.PREFLIGHT_PASSED)
    lease = RunLease(
        case.case_dir,
        "a",
        "owner",
        "2026-07-30T00:00:00+00:00",
        solver_dir.parent / "run-lease-created.json",
        solver_dir.parent / "run-lease-bound.json",
        solver_dir.parent / "cancel-request.json",
        solver_dir.parent / "run-lease-cleared.json",
    )
    lease.lease_path.write_text(
        '{"event_type":"RUN_LEASE_CREATED","attempt_id":"a","owner_token":"owner"}',
        encoding="utf-8",
    )
    captured = {}
    monkeypatch.setattr(febio_runner, "_load_run_context", lambda request: (case, lease))
    monkeypatch.setattr(
        febio_runner,
        "bind_run_lease",
        lambda current_case, current_lease, pid, job: captured.update(pid=pid, job=job),
    )
    monkeypatch.setattr(
        febio_runner,
        "clear_run_lease",
        lambda current_case, current_lease: captured.update(cleared=True),
    )

    def fake_owned(owned):
        captured["request"] = owned
        owned.on_started(41, "job-41")
        raise RuntimeError("sentinel")

    monkeypatch.setattr(febio_runner, "run_owned_process", fake_owned)
    value = FebioRunRequest(
        executable=executable.resolve(),
        expected_solver_sha256=sha256_file(executable),
        input_feb=input_feb.resolve(),
        expected_input_sha256=sha256_file(input_feb),
        working_directory=solver_dir.resolve(),
        output_log=solver_dir.resolve() / "solver.log",
        output_xplt=solver_dir.resolve() / "solver.xplt",
        timeout_seconds=10.0,
        cancel_grace_seconds=3.0,
        process_tree_working_set_limit_mib=512,
        inherited_environment_names=("SYSTEMROOT",),
    )
    with pytest.raises(RuntimeError, match="sentinel"):
        febio_runner.run_febio(value)
    assert captured["request"].argv == (
        str(executable.resolve()),
        "-i", "input.feb", "-o", "solver.log", "-p", "solver.xplt",
    )
    assert captured == {
        "request": captured["request"],
        "pid": 41,
        "job": "job-41",
        "cleared": True,
    }
```

- [ ] **Step 3: Run lifecycle tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_febio_runner.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.febio_runner'`.

- [ ] **Step 4: Implement the owned lifecycle, independent monitor, and evidence snapshot**

```python
# owned_process.py
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from febio_cae_harness.hashing import file_evidence
from febio_cae_harness.response import EvidenceRecord
from febio_cae_harness.windows_job import (
    STILL_ACTIVE,
    JobObject,
    PinnedFile,
    create_inheritable_output,
    launch_suspended,
    query_process_image_path,
)

@dataclass(frozen=True)
class OwnedProcessRequest:
    argv: tuple[str, ...]
    working_directory: Path
    environment: dict[str, str]
    executable: Path
    expected_executable_sha256: str
    held_input: Path
    expected_input_sha256: str
    stdout_path: Path
    stderr_path: Path
    observed_artifacts: dict[str, Path]
    timeout_seconds: float
    cancel_grace_seconds: float
    process_tree_working_set_limit_mib: int
    cancel_requested: Callable[[], bool]
    on_started: Callable[[int, str], None]

@dataclass(frozen=True)
class ProcessEvidence:
    kind: str
    argv: tuple[str, ...]
    cwd: str
    inherited_environment_names: tuple[str, ...]
    executable_path: str
    executable_sha256: str
    executable_identity: dict[str, int]
    input_path: str
    input_sha256: str
    input_identity: dict[str, int]
    pid: int
    observed_pids: tuple[int, ...]
    started_at: str
    ended_at: str
    elapsed_seconds: float
    exit_code: int
    termination_reason: str
    cpu_seconds: float
    peak_working_set_bytes: int
    maximum_working_set_mib: float
    peak_private_bytes: int
    job_object_assigned: bool
    active_processes_after_finalize: int
    stdout_path: str
    stderr_path: str
    stdout_text: str
    stderr_text: str
    solver_runtime_banner: str | None
    artifacts_before: dict[str, dict[str, object]]
    artifacts_after: dict[str, dict[str, object]]

    def to_record(self) -> EvidenceRecord:
        payload = asdict(self)
        kind = str(payload.pop("kind"))
        return EvidenceRecord(kind=kind, data=payload)

def _snapshot(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    value = file_evidence(path)
    value["exists"] = True
    value["mtime_ns"] = path.stat().st_mtime_ns
    return value

def _metrics(pid: int) -> tuple[set[int], float, int, int]:
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        return set(), 0.0, 0, 0
    pids: set[int] = set()
    cpu = 0.0
    working_set = 0
    private = 0
    for process in processes:
        try:
            pids.add(process.pid)
            times = process.cpu_times()
            memory = process.memory_info()
            cpu += float(times.user + times.system)
            working_set += int(memory.rss)
            private += int(getattr(memory, "private", memory.vms))
        except psutil.Error:
            continue
    return pids, cpu, working_set, private

def _fallback_kill(pids: set[int]) -> None:
    for pid in sorted(pids, reverse=True):
        try:
            process = psutil.Process(pid)
            process.kill()
            process.wait(timeout=2.0)
        except (psutil.Error, psutil.TimeoutExpired):
            continue

def run_owned_process(request: OwnedProcessRequest) -> ProcessEvidence:
    if not request.argv or Path(request.argv[0]).resolve(strict=True) != request.executable.resolve(strict=True):
        raise ValueError("argv executable does not match pinned executable")
    before = {name: _snapshot(path) for name, path in request.observed_artifacts.items()}
    if any(value["exists"] for value in before.values()):
        raise FileExistsError("observed output exists before launch")
    start_wall = datetime.now(timezone.utc)
    start_monotonic = time.monotonic()
    observed_pids: set[int] = set()
    cpu_seconds = 0.0
    peak_working_set = 0
    peak_private = 0
    exit_code = STILL_ACTIVE
    termination_reason = "RESOURCE_OR_TOOL_ERROR"
    job_name = f"FEBioCaeHarness-{uuid.uuid4().hex}"
    with (
        PinnedFile.open_readonly(request.executable) as executable,
        PinnedFile.open_readonly(request.held_input) as held_input,
        create_inheritable_output(request.stdout_path) as stdout,
        create_inheritable_output(request.stderr_path) as stderr,
        JobObject.create_kill_on_close(job_name) as job,
    ):
        if executable.sha256 != request.expected_executable_sha256.upper():
            raise RuntimeError("executable hash drifted before launch")
        if held_input.sha256 != request.expected_input_sha256.upper():
            raise RuntimeError("input hash drifted before launch")
        process = launch_suspended(
            list(request.argv),
            request.working_directory,
            request.environment,
            stdout.handle,
            stderr.handle,
        )
        try:
            suspended_image = Path(query_process_image_path(process.process_handle))
            if not suspended_image.samefile(executable.path):
                process.terminate(0xE0000010)
                process.wait(5.0)
                raise RuntimeError("suspended executable identity mismatch")
            if executable.rehash() != request.expected_executable_sha256.upper():
                process.terminate(0xE0000011)
                process.wait(5.0)
                raise RuntimeError("executable hash drifted after CreateProcessW")
            job.assign_or_terminate_suspended(process)
            request.on_started(process.pid, job_name)
            process.resume()
            observed_pids.add(process.pid)
            while True:
                pids, current_cpu, current_working_set, current_private = _metrics(process.pid)
                observed_pids.update(pids)
                cpu_seconds = max(cpu_seconds, current_cpu)
                peak_working_set = max(peak_working_set, current_working_set)
                peak_private = max(peak_private, current_private)
                elapsed = time.monotonic() - start_monotonic
                if request.cancel_requested():
                    termination_reason = "CANCELLED"
                    job.terminate(0xE0000020)
                    break
                if elapsed >= request.timeout_seconds:
                    termination_reason = "TIMEOUT"
                    job.terminate(0xE0000021)
                    break
                if current_working_set > request.process_tree_working_set_limit_mib * 1024 * 1024:
                    termination_reason = "MEMORY_LIMIT"
                    job.terminate(0xE0000022)
                    break
                exit_code = process.exit_code()
                if exit_code != STILL_ACTIVE:
                    termination_reason = "EXITED" if exit_code == 0 else "NONZERO_EXIT"
                    break
                time.sleep(0.05)
            if not job.wait_empty(request.cancel_grace_seconds):
                _fallback_kill(observed_pids)
                if not job.wait_empty(2.0):
                    termination_reason = "RESOURCE_OR_TOOL_ERROR"
            process.wait(request.cancel_grace_seconds)
            exit_code = process.exit_code()
            active_after = job.active_processes()
        except BaseException:
            if process.exit_code() == STILL_ACTIVE:
                process.terminate(0xE0000023)
                process.wait(5.0)
            raise
        finally:
            process.close()
        executable_identity = {
            "volume_serial_number": executable.identity.volume_serial_number,
            "file_index": executable.identity.file_index,
        }
        input_identity = {
            "volume_serial_number": held_input.identity.volume_serial_number,
            "file_index": held_input.identity.file_index,
        }
    end_wall = datetime.now(timezone.utc)
    stdout_text = request.stdout_path.read_bytes().decode("utf-8", errors="replace")
    stderr_text = request.stderr_path.read_bytes().decode("utf-8", errors="replace")
    after = {name: _snapshot(path) for name, path in request.observed_artifacts.items()}
    banner = next((line for line in stdout_text.splitlines() if line.strip()), None)
    return ProcessEvidence(
        kind="process-evidence",
        argv=request.argv,
        cwd=str(request.working_directory),
        inherited_environment_names=tuple(sorted(request.environment)),
        executable_path=str(request.executable),
        executable_sha256=request.expected_executable_sha256.upper(),
        executable_identity=executable_identity,
        input_path=str(request.held_input),
        input_sha256=request.expected_input_sha256.upper(),
        input_identity=input_identity,
        pid=min(observed_pids),
        observed_pids=tuple(sorted(observed_pids)),
        started_at=start_wall.isoformat(),
        ended_at=end_wall.isoformat(),
        elapsed_seconds=time.monotonic() - start_monotonic,
        exit_code=exit_code,
        termination_reason=termination_reason,
        cpu_seconds=cpu_seconds,
        peak_working_set_bytes=peak_working_set,
        maximum_working_set_mib=peak_working_set / (1024 * 1024),
        peak_private_bytes=peak_private,
        job_object_assigned=True,
        active_processes_after_finalize=active_after,
        stdout_path=str(request.stdout_path),
        stderr_path=str(request.stderr_path),
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        solver_runtime_banner=banner,
        artifacts_before=before,
        artifacts_after=after,
    )
```

- [ ] **Step 5: Amend `JobObject` to name the owned Job and implement the strict adapter**

Change the Task 6 factory to store the exact generated name:

```python
# windows_job.py — replace JobObject constructor/factory with this version
class JobObject:
    def __init__(self, handle: int, name: str):
        self.handle = handle
        self.name = name

    @classmethod
    def create_kill_on_close(cls, name: str | None = None) -> Self:
        actual_name = name or f"FEBioCaeHarness-{os.getpid()}-{time.monotonic_ns()}"
        handle = kernel32.CreateJobObjectW(None, actual_name)
        if not handle:
            _raise_last_error("CreateJobObjectW")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            _close_handle(handle)
            _raise_last_error("SetInformationJobObject")
        return cls(int(handle), actual_name)
```

Keep the existing `assign`, `assign_or_terminate_suspended`, `active_processes`, `terminate`, `wait_empty`, `close`, and context-manager methods immediately below that replacement.

```python
# febio_runner.py
from __future__ import annotations

import json
import os
from pathlib import Path

from febio_cae_harness.attempt_store import (
    AttemptRunLock,
    RunLease,
    bind_run_lease,
    clear_run_lease,
    current_run_lease,
    read_cancel_request,
)
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.febio_command import FebioRunRequest, validate_febio_request
from febio_cae_harness.owned_process import (
    OwnedProcessRequest,
    ProcessEvidence,
    run_owned_process,
)

def _load_run_context(request: FebioRunRequest) -> tuple[CaseStore, RunLease]:
    attempt_root = request.working_directory.parent
    if attempt_root.parent.name != "attempts" or attempt_root.parent.parent.name != "90_Temporary":
        raise ValueError("working directory is not inside a canonical attempt")
    case_dir = attempt_root.parent.parent.parent
    case = CaseStore.open(case_dir)
    attempt_id = attempt_root.name
    lease = current_run_lease(case)
    if lease is None:
        raise RuntimeError("active run lease is absent")
    if lease.attempt_id != attempt_id:
        raise RuntimeError("run lease belongs to a different attempt")
    return case, lease

def _allowlisted_environment(names: tuple[str, ...]) -> dict[str, str]:
    missing = [name for name in names if name not in os.environ]
    if missing:
        raise RuntimeError(f"required inherited environment variable is absent: {missing[0]}")
    return {name: os.environ[name] for name in names}

def run_febio(request: FebioRunRequest) -> ProcessEvidence:
    command = validate_febio_request(request)
    case, lease = _load_run_context(request)
    owned = OwnedProcessRequest(
        argv=command.argv,
        working_directory=command.cwd,
        environment=_allowlisted_environment(request.inherited_environment_names),
        executable=request.executable,
        expected_executable_sha256=request.expected_solver_sha256,
        held_input=request.input_feb,
        expected_input_sha256=request.expected_input_sha256,
        stdout_path=command.stdout_path,
        stderr_path=command.stderr_path,
        observed_artifacts={
            "log": request.output_log,
            "xplt": request.output_xplt,
            "dump": command.dump_path,
        },
        timeout_seconds=request.timeout_seconds,
        cancel_grace_seconds=request.cancel_grace_seconds,
        process_tree_working_set_limit_mib=request.process_tree_working_set_limit_mib,
        cancel_requested=lambda: read_cancel_request(lease),
        on_started=lambda pid, job_name: bind_run_lease(case, lease, pid, job_name),
    )
    with AttemptRunLock.acquire(
        request.working_directory.parent,
        lease.attempt_id,
        lease.owner_token,
    ):
        try:
            return run_owned_process(owned)
        finally:
            clear_run_lease(case, lease)

def solver_artifact_records(
    evidence: ProcessEvidence,
) -> tuple[dict[str, object], dict[str, object]]:
    records: list[dict[str, object]] = []
    for key, role in (
        ("log", "attempt-solver-log"),
        ("xplt", "attempt-solver-xplt"),
    ):
        value = evidence.artifacts_after[key]
        if value.get("exists") is not True:
            raise RuntimeError(f"{role} is absent")
        records.append(
            {
                "role": role,
                "path": value["path"],
                "bytes": value["bytes"],
                "sha256": value["sha256"],
            }
        )
    return records[0], records[1]
```

- [ ] **Step 6: Run the GREEN command and all Task 3–7 regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_febio_runner.py apps/febio_cae_harness/tests/integration/test_windows_job.py apps/febio_cae_harness/tests/integration/test_run_lease.py apps/febio_cae_harness/tests/unit/test_febio_command.py -q
```

Expected: command exits `0`, every test reports `passed`, no child PID from the fixtures survives, and the timeout/cancel cases retain their raw streams and partial artifacts.

- [ ] **Step 7: Inspect, stage, and commit exactly Task 7 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/owned_process.py apps/febio_cae_harness/src/febio_cae_harness/febio_runner.py apps/febio_cae_harness/src/febio_cae_harness/windows_job.py apps/febio_cae_harness/tests/fixtures/process/fake_solver.py apps/febio_cae_harness/tests/fixtures/process/fake_parent_child.py apps/febio_cae_harness/tests/integration/test_febio_runner.py
git diff --cached --check
git commit -m "feat: run FEBio in an auditable Windows job"
```

### Task 8: Structured FEBio LOG evidence

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/log_parser.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/log-evidence.schema.json`
- Create: `apps/febio_cae_harness/tests/fixtures/log/normal-fixed20.txt`
- Create: `apps/febio_cae_harness/tests/fixtures/log/recovered-warning.txt`
- Create: `apps/febio_cae_harness/tests/fixtures/log/init-only.txt`
- Create: `apps/febio_cae_harness/tests/fixtures/log/negative-jacobian.txt`
- Create: `apps/febio_cae_harness/tests/fixtures/log/nonconvergence.txt`
- Create: `apps/febio_cae_harness/tests/fixtures/log/missing-reference.txt`
- Create: `apps/febio_cae_harness/tests/fixtures/log/unknown-warning.txt`
- Create: `apps/febio_cae_harness/tests/unit/test_log_parser.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**
- Consumes: fresh `solver.log`, the fixed command basenames, approved solver version, arbitrary exact target-time contract, `sha256_file()`
- Produces: `LogExpectation`, `LogEvidence(kind="log-verification")`, `LogEvidence.to_record() -> EvidenceRecord`, `parse_febio_log(path: Path, expected: LogExpectation) -> LogEvidence`

- [ ] **Step 1: Create seven sanitized FEBio 4.12-format `.txt` fixtures; never unignore `*.log`**

```text
# tests/fixtures/log/normal-fixed20.txt
version 4.12.0
FILES USED
Input file : input.feb
Plot file  : solver.xplt
Log file   : solver.log
===== beginning time step 1 : 0.05 =====
------- converged at time : 0.05
===== beginning time step 2 : 0.10 =====
------- converged at time : 0.10
===== beginning time step 3 : 0.15 =====
------- converged at time : 0.15
===== beginning time step 4 : 0.20 =====
------- converged at time : 0.20
===== beginning time step 5 : 0.25 =====
------- converged at time : 0.25
===== beginning time step 6 : 0.30 =====
------- converged at time : 0.30
===== beginning time step 7 : 0.35 =====
------- converged at time : 0.35
===== beginning time step 8 : 0.40 =====
------- converged at time : 0.40
===== beginning time step 9 : 0.45 =====
------- converged at time : 0.45
===== beginning time step 10 : 0.50 =====
------- converged at time : 0.50
===== beginning time step 11 : 0.55 =====
------- converged at time : 0.55
===== beginning time step 12 : 0.60 =====
------- converged at time : 0.60
===== beginning time step 13 : 0.65 =====
------- converged at time : 0.65
===== beginning time step 14 : 0.70 =====
------- converged at time : 0.70
===== beginning time step 15 : 0.75 =====
------- converged at time : 0.75
===== beginning time step 16 : 0.80 =====
------- converged at time : 0.80
===== beginning time step 17 : 0.85 =====
------- converged at time : 0.85
===== beginning time step 18 : 0.90 =====
------- converged at time : 0.90
===== beginning time step 19 : 0.95 =====
------- converged at time : 0.95
===== beginning time step 20 : 1.00 =====
------- converged at time : 1.00
Number of time steps completed .................... : 20
Elapsed time : 0:05:29
Total elapsed time .............. : 0:05:30 (329.872 sec)
Peak memory  : 384 MB
N O R M A L   T E R M I N A T I O N
```

```text
# tests/fixtures/log/recovered-warning.txt
version 4.12.0
FILES USED
Input file : input.feb
Plot file  : solver.xplt
Log file   : solver.log
===== beginning time step 1 : 1.0 =====
* WARNING *
* Problem is diverging. Stiffness matrix will now be reformed *
* WARNING *
* Max nr of iterations reached. *
------- converged at time : 1.0
Number of time steps completed .................... : 1
N O R M A L   T E R M I N A T I O N
```

```text
# tests/fixtures/log/init-only.txt
version 4.12.0
FILES USED
Input file : input.feb
Plot file  : solver.xplt
Log file   : solver.log
Initialization complete
```

```text
# tests/fixtures/log/negative-jacobian.txt
version 4.12.0
FILES USED
Input file : input.feb
Plot file  : solver.xplt
Log file   : solver.log
ERROR: Negative jacobian detected at element 42
```

```text
# tests/fixtures/log/nonconvergence.txt
version 4.12.0
FILES USED
Input file : input.feb
Plot file  : solver.xplt
Log file   : solver.log
===== beginning time step 1 : 1.0 =====
* WARNING *
* Max nr of iterations reached. *
ERROR: problem is not converging
```

```text
# tests/fixtures/log/missing-reference.txt
version 4.12.0
FILES USED
Input file : input.feb
Plot file  : solver.xplt
Log file   : solver.log
ERROR: Cannot find material reference 17
```

```text
# tests/fixtures/log/unknown-warning.txt
version 4.12.0
FILES USED
Input file : input.feb
Plot file  : solver.xplt
Log file   : solver.log
===== beginning time step 1 : 1.0 =====
* WARNING *
* synthetic unrecognized warning *
------- converged at time : 1.0
Number of time steps completed .................... : 1
N O R M A L   T E R M I N A T I O N
```

- [ ] **Step 2: Write exact marker, ordering, basename, warning, and schema tests**

```python
# tests/unit/test_log_parser.py
import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

from febio_cae_harness.log_parser import LogExpectation, parse_febio_log

FIXTURES = Path(__file__).parents[1] / "fixtures" / "log"

def expectation(times=tuple(index / 20 for index in range(1, 21))):
    return LogExpectation(
        expected_input_basename="input.feb",
        expected_log_basename="solver.log",
        expected_plot_basename="solver.xplt",
        expected_solver_version="4.12.0",
        expected_times=times,
        time_abs_tol=0.0,
    )

def test_normal_fixed20_requires_only_explicit_convergence_markers():
    evidence = parse_febio_log(FIXTURES / "normal-fixed20.txt", expectation())
    assert evidence.kind == "log-verification"
    assert evidence.status == "accepted"
    assert evidence.converged_times == expectation().expected_times
    assert evidence.completed_steps == 20
    assert evidence.elapsed_seconds == pytest.approx(329.872)
    assert evidence.normal_termination is True
    assert evidence.blocking_reasons == ()

def test_recovered_iteration_warning_requires_later_convergence_and_normal_end():
    evidence = parse_febio_log(FIXTURES / "recovered-warning.txt", expectation((1.0,)))
    assert evidence.status == "accepted"
    assert evidence.recovered_warnings == (
        "Problem is diverging. Stiffness matrix will now be reformed",
        "Max nr of iterations reached.",
    )
    assert evidence.unresolved_warnings == ()

def test_known_warning_after_final_convergence_is_not_recovered(tmp_path):
    original = (FIXTURES / "recovered-warning.txt").read_text(encoding="utf-8")
    warning = "* WARNING *\n* Max nr of iterations reached. *\n"
    changed = original.replace(warning, "").replace(
        "------- converged at time : 1.0\n",
        "------- converged at time : 1.0\n" + warning,
    )
    path = tmp_path / "late-warning.txt"
    path.write_text(changed, encoding="utf-8")
    evidence = parse_febio_log(path, expectation((1.0,)))
    assert evidence.status == "rejected"
    assert "UNKNOWN_WARNING" in evidence.blocking_reasons

@pytest.mark.parametrize(
    ("fixture", "times", "reason"),
    [
        ("init-only.txt", (1.0,), "CONVERGED_TIMES_MISMATCH"),
        ("nonconvergence.txt", (1.0,), "MISSING_NORMAL_TERMINATION"),
        ("unknown-warning.txt", (1.0,), "UNKNOWN_WARNING"),
    ],
)
def test_incomplete_or_unresolved_logs_are_rejected(fixture, times, reason):
    evidence = parse_febio_log(FIXTURES / fixture, expectation(times))
    assert evidence.status == "rejected"
    assert reason in evidence.blocking_reasons

def test_duplicate_extra_and_missing_convergence_times_are_rejected(tmp_path):
    original = (FIXTURES / "normal-fixed20.txt").read_text(encoding="utf-8")
    for name, changed in (
        ("duplicate.txt", original.replace(
            "------- converged at time : 1.00",
            "------- converged at time : 0.95\n------- converged at time : 1.00",
        )),
        ("extra.txt", original.replace(
            "Number of time steps completed",
            "------- converged at time : 1.01\nNumber of time steps completed",
        )),
        ("missing.txt", original.replace("------- converged at time : 0.50\n", "")),
    ):
        path = tmp_path / name
        path.write_text(changed, encoding="utf-8")
        evidence = parse_febio_log(path, expectation())
        assert evidence.status == "rejected"
        assert "CONVERGED_TIMES_MISMATCH" in evidence.blocking_reasons

def test_files_used_solver_version_and_normal_marker_order_are_exact(tmp_path):
    original = (FIXTURES / "recovered-warning.txt").read_text(encoding="utf-8")
    mutations = (
        ("input", original.replace("input.feb", "other.feb"), "INPUT_BASENAME_MISMATCH"),
        ("plot", original.replace("solver.xplt", "other.xplt"), "PLOT_BASENAME_MISMATCH"),
        ("log", original.replace("solver.log", "other.log"), "LOG_BASENAME_MISMATCH"),
        ("version", original.replace("4.12.0", "4.11.0"), "SOLVER_VERSION_MISMATCH"),
        (
            "order",
            original.replace("N O R M A L   T E R M I N A T I O N\n", "").replace(
                "===== beginning time step",
                "N O R M A L   T E R M I N A T I O N\n===== beginning time step",
            ),
            "NORMAL_TERMINATION_BEFORE_FINAL_CONVERGENCE",
        ),
    )
    for name, text, expected_reason in mutations:
        path = tmp_path / f"{name}.txt"
        path.write_text(text, encoding="utf-8")
        assert expected_reason in parse_febio_log(
            path, expectation((1.0,))
        ).blocking_reasons

def test_log_evidence_matches_installed_schema():
    evidence = parse_febio_log(FIXTURES / "normal-fixed20.txt", expectation())
    schema_path = (
        Path(__file__).parents[2]
        / "src" / "febio_cae_harness" / "schemas" / "log-evidence.schema.json"
    )
    jsonschema.validate(evidence.to_dict(), json.loads(schema_path.read_text(encoding="utf-8")))
    record = evidence.to_record()
    assert record.kind == "log-verification"
    assert set(record.to_payload()) == {"kind", "data"}
    assert "kind" not in record.data
```

- [ ] **Step 3: Run the parser tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_log_parser.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.log_parser'`.

- [ ] **Step 4: Implement explicit-marker parsing and fail-closed verification**

```python
# log_parser.py
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.response import EvidenceRecord

CONVERGED_RE = re.compile(r"^------- converged at time : (?P<time>[-+0-9.eE]+)$")
STEP_RE = re.compile(
    r"^===== beginning time step (?P<step>\d+) : (?P<time>[-+0-9.eE]+) =====$"
)
VERSION_RE = re.compile(
    r"^(?:FEBio\s+)?version\s+(?P<version>[0-9]+(?:\.[0-9]+){1,2})$",
    re.IGNORECASE,
)
FILE_RE = re.compile(r"^(?P<role>Input|Plot|Log) file\s*:\s*(?P<path>.+)$")
COMPLETED_RE = re.compile(
    r"^(?:Number of time steps completed|number of completed time steps)"
    r"\s*\.*\s*:?\s*(?P<count>\d+)$",
    re.IGNORECASE,
)
ELAPSED_CLOCK_RE = re.compile(
    r"^Elapsed time\s*:\s*(?P<hours>\d+):(?P<minutes>\d{2}):"
    r"(?P<seconds>\d{2}(?:\.\d+)?)$"
)
ELAPSED_SECONDS_RE = re.compile(
    r"^Elapsed time\s*:\s*(?P<seconds>[-+0-9.eE]+)\s*sec$"
)
TOTAL_ELAPSED_RE = re.compile(
    r"^Total elapsed time\s*\.*\s*:\s*(?P<hours>\d+):"
    r"(?P<minutes>\d{2}):(?P<clock_seconds>\d{2}(?:\.\d+)?)"
    r"\s*\((?P<seconds>[-+0-9.eE]+)\s*sec\)$"
)
PEAK_RE = re.compile(r"^Peak memory\s*:\s*(?P<mib>[-+0-9.eE]+)\s*MB$")
WARNING_BANNER_RE = re.compile(r"^\*\s*WARNING\s*\*$", re.IGNORECASE)
DECORATED_LINE_RE = re.compile(r"^\*\s*(?P<message>.*?)\s*\*$")
NORMAL_MARKER = "N O R M A L   T E R M I N A T I O N"
RECOVERABLE_WARNING_PREFIXES = (
    "problem is diverging",
    "max nr of iterations reached",
)

@dataclass(frozen=True)
class LogExpectation:
    expected_input_basename: str
    expected_log_basename: str
    expected_plot_basename: str
    expected_solver_version: str
    expected_times: tuple[float, ...]
    time_abs_tol: float

@dataclass(frozen=True)
class BeginningStep:
    line_index: int
    step: int
    time: float

@dataclass(frozen=True)
class LogEvidence:
    kind: str
    status: str
    path: str
    bytes: int
    sha256: str
    solver_version: str | None
    files_used: dict[str, str]
    beginning_steps: tuple[BeginningStep, ...]
    converged_times: tuple[float, ...]
    converged_line_indices: tuple[int, ...]
    completed_steps: int | None
    normal_termination: bool
    normal_termination_line_index: int | None
    warning_lines: tuple[str, ...]
    error_lines: tuple[str, ...]
    recovered_warnings: tuple[str, ...]
    unresolved_warnings: tuple[str, ...]
    elapsed_seconds: float | None
    peak_memory_mib: float | None
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_record(self) -> EvidenceRecord:
        payload = self.to_dict()
        kind = str(payload.pop("kind"))
        return EvidenceRecord(kind=kind, data=payload)

def _ordered_times_match(
    actual: tuple[float, ...],
    expected: tuple[float, ...],
    tolerance: float,
) -> bool:
    return len(actual) == len(expected) and all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
        for left, right in zip(actual, expected, strict=True)
    )

def parse_febio_log(path: Path, expected: LogExpectation) -> LogEvidence:
    raw = path.read_bytes()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    solver_version: str | None = None
    files_used: dict[str, str] = {}
    steps: list[BeginningStep] = []
    converged: list[tuple[int, float]] = []
    warnings: list[tuple[int, str]] = []
    errors: list[str] = []
    completed_steps: int | None = None
    normal_line: int | None = None
    elapsed_seconds: float | None = None
    peak_memory_mib: float | None = None
    warning_pending = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if WARNING_BANNER_RE.fullmatch(stripped):
            warning_pending = True
            continue
        if warning_pending:
            decorated = DECORATED_LINE_RE.fullmatch(stripped)
            if decorated and decorated.group("message").strip("* ").strip():
                warnings.append(
                    (index, decorated.group("message").strip())
                )
                warning_pending = False
                continue
            if not stripped or not stripped.strip("* "):
                continue
            warnings.append((index, "MALFORMED_WARNING_BANNER"))
            warning_pending = False
        if match := VERSION_RE.fullmatch(stripped):
            solver_version = match.group("version")
        elif match := FILE_RE.fullmatch(stripped):
            files_used[match.group("role").lower()] = Path(match.group("path").strip()).name
        elif match := STEP_RE.fullmatch(stripped):
            steps.append(BeginningStep(index, int(match.group("step")), float(match.group("time"))))
        elif match := CONVERGED_RE.fullmatch(stripped):
            converged.append((index, float(match.group("time"))))
        elif match := COMPLETED_RE.fullmatch(stripped):
            completed_steps = int(match.group("count"))
        elif match := TOTAL_ELAPSED_RE.fullmatch(stripped):
            elapsed_seconds = float(match.group("seconds"))
        elif match := ELAPSED_CLOCK_RE.fullmatch(stripped):
            elapsed_seconds = (
                int(match.group("hours")) * 3600
                + int(match.group("minutes")) * 60
                + float(match.group("seconds"))
            )
        elif match := ELAPSED_SECONDS_RE.fullmatch(stripped):
            elapsed_seconds = float(match.group("seconds"))
        elif match := PEAK_RE.fullmatch(stripped):
            peak_memory_mib = float(match.group("mib"))
        elif stripped == NORMAL_MARKER:
            normal_line = index
        elif stripped.upper().startswith("WARNING:"):
            warnings.append((index, stripped.removeprefix("WARNING:").strip()))
        elif stripped.upper().startswith(("ERROR:", "FATAL:")):
            errors.append(stripped)
    if warning_pending:
        warnings.append((len(lines), "MALFORMED_WARNING_BANNER"))
    actual_times = tuple(value for _, value in converged)
    reasons: list[str] = []
    if solver_version != expected.expected_solver_version:
        reasons.append("SOLVER_VERSION_MISMATCH")
    for role, expected_name, reason in (
        ("input", expected.expected_input_basename, "INPUT_BASENAME_MISMATCH"),
        ("plot", expected.expected_plot_basename, "PLOT_BASENAME_MISMATCH"),
        ("log", expected.expected_log_basename, "LOG_BASENAME_MISMATCH"),
    ):
        if files_used.get(role) != expected_name:
            reasons.append(reason)
    if not _ordered_times_match(actual_times, expected.expected_times, expected.time_abs_tol):
        reasons.append("CONVERGED_TIMES_MISMATCH")
    if completed_steps != len(expected.expected_times):
        reasons.append("COMPLETED_STEP_COUNT_MISMATCH")
    if normal_line is None:
        reasons.append("MISSING_NORMAL_TERMINATION")
    elif converged and normal_line <= converged[-1][0]:
        reasons.append("NORMAL_TERMINATION_BEFORE_FINAL_CONVERGENCE")
    recovered: list[str] = []
    unresolved: list[str] = []
    for warning_line, warning in warnings:
        if (
            warning.casefold().startswith(RECOVERABLE_WARNING_PREFIXES)
            and normal_line is not None
            and any(
                warning_line < line_index < normal_line
                for line_index, _ in converged
            )
        ):
            recovered.append(warning)
        else:
            unresolved.append(warning)
    if unresolved:
        reasons.append("UNKNOWN_WARNING")
    if errors:
        reasons.append("ERROR_OR_FATAL_MARKER")
    return LogEvidence(
        kind="log-verification",
        status="accepted" if not reasons else "rejected",
        path=str(path.resolve(strict=True)),
        bytes=len(raw),
        sha256=sha256_file(path),
        solver_version=solver_version,
        files_used=files_used,
        beginning_steps=tuple(steps),
        converged_times=actual_times,
        converged_line_indices=tuple(index for index, _ in converged),
        completed_steps=completed_steps,
        normal_termination=normal_line is not None,
        normal_termination_line_index=normal_line,
        warning_lines=tuple(value for _, value in warnings),
        error_lines=tuple(errors),
        recovered_warnings=tuple(recovered),
        unresolved_warnings=tuple(unresolved),
        elapsed_seconds=elapsed_seconds,
        peak_memory_mib=peak_memory_mib,
        blocking_reasons=tuple(dict.fromkeys(reasons)),
    )
```

- [ ] **Step 5: Add the complete log-evidence schema and installed-resource entry**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-cae-harness.local/schemas/log-evidence.schema.json",
  "type": "object",
  "required": [
    "kind", "status", "path", "bytes", "sha256", "solver_version",
    "files_used", "beginning_steps", "converged_times",
    "converged_line_indices", "completed_steps", "normal_termination",
    "normal_termination_line_index", "warning_lines", "error_lines",
    "recovered_warnings", "unresolved_warnings", "elapsed_seconds",
    "peak_memory_mib", "blocking_reasons"
  ],
  "properties": {
    "kind": {"const": "log-verification"},
    "status": {"enum": ["accepted", "rejected"]},
    "path": {"type": "string", "minLength": 1},
    "bytes": {"type": "integer", "minimum": 0},
    "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "solver_version": {"type": ["string", "null"]},
    "files_used": {
      "type": "object",
      "additionalProperties": {"type": "string"}
    },
    "beginning_steps": {"type": "array", "items": {"type": "object"}},
    "converged_times": {"type": "array", "items": {"type": "number"}},
    "converged_line_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
    "completed_steps": {"type": ["integer", "null"], "minimum": 0},
    "normal_termination": {"type": "boolean"},
    "normal_termination_line_index": {"type": ["integer", "null"], "minimum": 0},
    "warning_lines": {"type": "array", "items": {"type": "string"}},
    "error_lines": {"type": "array", "items": {"type": "string"}},
    "recovered_warnings": {"type": "array", "items": {"type": "string"}},
    "unresolved_warnings": {"type": "array", "items": {"type": "string"}},
    "elapsed_seconds": {"type": ["number", "null"], "minimum": 0},
    "peak_memory_mib": {"type": ["number", "null"], "minimum": 0},
    "blocking_reasons": {"type": "array", "items": {"type": "string"}}
  },
  "additionalProperties": false
}
```

Add this exact path to `EXPECTED_RESOURCES`:

```python
"schemas/log-evidence.schema.json",
```

- [ ] **Step 6: Run the GREEN command, schema, and installed-wheel regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_log_parser.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: command exits `0`; all tests report `passed`; the installed wheel and sdist each contain exactly one `schemas/log-evidence.schema.json`.

- [ ] **Step 7: Inspect, stage, and commit exactly Task 8 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/log_parser.py apps/febio_cae_harness/src/febio_cae_harness/schemas/log-evidence.schema.json apps/febio_cae_harness/tests/fixtures/log/normal-fixed20.txt apps/febio_cae_harness/tests/fixtures/log/recovered-warning.txt apps/febio_cae_harness/tests/fixtures/log/init-only.txt apps/febio_cae_harness/tests/fixtures/log/negative-jacobian.txt apps/febio_cae_harness/tests/fixtures/log/nonconvergence.txt apps/febio_cae_harness/tests/fixtures/log/missing-reference.txt apps/febio_cae_harness/tests/fixtures/log/unknown-warning.txt apps/febio_cae_harness/tests/unit/test_log_parser.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git diff --cached --check
git commit -m "feat: parse strict FEBio log evidence"
```

### Task 9: Engineering failure class, phase, and stable fingerprint

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/failure.py`
- Create: `apps/febio_cae_harness/tests/unit/test_failure.py`

**Interfaces:**
- Consumes: `LogEvidence`, source text at `LogEvidence.path`, input FEB hash, intent/contract hash
- Produces: `FailureClass`, `FailureEvidence`, `classify_log_failure(log: LogEvidence, input_sha256: str, contract_sha256: str) -> FailureEvidence | None`

- [ ] **Step 1: Write phase-sensitive classification and fingerprint tests**

```python
# tests/unit/test_failure.py
from pathlib import Path

import pytest

from febio_cae_harness.failure import FailureClass, classify_log_failure
from febio_cae_harness.log_parser import LogExpectation, parse_febio_log

FIXTURES = Path(__file__).parents[1] / "fixtures" / "log"
HASH_A = "A" * 64
HASH_B = "B" * 64

def expectation(times=(1.0,)):
    return LogExpectation("input.feb", "solver.log", "solver.xplt", "4.12.0", times, 0.0)

def classify(path: Path, times=(1.0,)):
    return classify_log_failure(
        parse_febio_log(path, expectation(times)),
        HASH_A,
        HASH_B,
    )

def test_negative_jacobian_before_any_step_is_initial_mesh_error():
    failure = classify(FIXTURES / "negative-jacobian.txt")
    assert failure is not None
    assert failure.failure_class is FailureClass.INITIAL_MESH_ERROR
    assert failure.phase == "INITIAL"
    assert failure.element_ids == (42,)
    assert failure.automatic_retry_allowed is True

def test_negative_jacobian_after_beginning_step_is_deformation_mesh_error(tmp_path):
    path = tmp_path / "deformation.txt"
    path.write_text(
        "\n".join(
            (
                "FEBio version 4.12.0",
                "FILES USED",
                "Input file : input.feb",
                "Plot file : solver.xplt",
                "Log file : solver.log",
                "===== beginning time step 3 : 0.15 =====",
                "ERROR: Negative jacobian at element 91 node 12",
            )
        ),
        encoding="utf-8",
    )
    failure = classify(path)
    assert failure.failure_class is FailureClass.DEFORMATION_MESH_ERROR
    assert failure.phase == "DEFORMATION"
    assert failure.failing_step == 3
    assert failure.failing_time == 0.15
    assert failure.element_ids == (91,)
    assert failure.node_ids == (12,)

def test_unresolved_jacobian_phase_blocks_automatic_retry(tmp_path):
    path = tmp_path / "unresolved.txt"
    path.write_text(
        "\n".join(
            (
                "FEBio version 4.12.0",
                "FILES USED",
                "Input file : input.feb",
                "Plot file : solver.xplt",
                "Log file : solver.log",
                "------- converged at time : 0.10",
                "ERROR: Negative jacobian at element 3",
            )
        ),
        encoding="utf-8",
    )
    failure = classify(path)
    assert failure.failure_class is FailureClass.GEOMETRY_ERROR
    assert failure.phase == "UNRESOLVED"
    assert failure.automatic_retry_allowed is False

@pytest.mark.parametrize(
    ("fixture", "expected_class"),
    [
        ("missing-reference.txt", FailureClass.INPUT_OR_REFERENCE_ERROR),
        ("nonconvergence.txt", FailureClass.NONLINEAR_CONVERGENCE_ERROR),
        ("unknown-warning.txt", FailureClass.RESULT_EVIDENCE_ERROR),
        ("init-only.txt", FailureClass.RESULT_EVIDENCE_ERROR),
    ],
)
def test_failure_classes_are_fail_closed(fixture, expected_class):
    assert classify(FIXTURES / fixture).failure_class is expected_class

def test_recovered_warning_has_no_failure():
    assert classify(FIXTURES / "recovered-warning.txt") is None

def test_fingerprint_is_stable_but_binds_input_and_contract():
    first = classify(FIXTURES / "negative-jacobian.txt")
    second = classify(FIXTURES / "negative-jacobian.txt")
    changed = classify_log_failure(
        parse_febio_log(FIXTURES / "negative-jacobian.txt", expectation()),
        "C" * 64,
        HASH_B,
    )
    assert first.fingerprint_sha256 == second.fingerprint_sha256
    assert first.fingerprint_sha256 != changed.fingerprint_sha256
```

- [ ] **Step 2: Run classification tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_failure.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.failure'`.

- [ ] **Step 3: Implement normalized failure evidence and phase resolution**

```python
# failure.py
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from febio_cae_harness.log_parser import CONVERGED_RE, STEP_RE, LogEvidence

ELEMENT_RE = re.compile(r"\belement\s+(?P<id>\d+)\b", re.IGNORECASE)
NODE_RE = re.compile(r"\bnode\s+(?P<id>\d+)\b", re.IGNORECASE)

class FailureClass(StrEnum):
    INPUT_OR_REFERENCE_ERROR = "INPUT_OR_REFERENCE_ERROR"
    GEOMETRY_ERROR = "GEOMETRY_ERROR"
    INITIAL_MESH_ERROR = "INITIAL_MESH_ERROR"
    DEFORMATION_MESH_ERROR = "DEFORMATION_MESH_ERROR"
    MODEL_SEMANTICS_ERROR = "MODEL_SEMANTICS_ERROR"
    NONLINEAR_CONVERGENCE_ERROR = "NONLINEAR_CONVERGENCE_ERROR"
    RESULT_EVIDENCE_ERROR = "RESULT_EVIDENCE_ERROR"
    RESOURCE_OR_TOOL_ERROR = "RESOURCE_OR_TOOL_ERROR"

@dataclass(frozen=True)
class FailureEvidence:
    failure_class: FailureClass
    phase: str
    normalized_fatal_lines: tuple[str, ...]
    last_converged_time: float | None
    failing_step: int | None
    failing_time: float | None
    element_ids: tuple[int, ...]
    node_ids: tuple[int, ...]
    input_sha256: str
    contract_sha256: str
    automatic_retry_allowed: bool
    fingerprint_sha256: str

def _normalize(line: str) -> str:
    return " ".join(line.strip().split())

def _fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()

def _latest_step(lines: list[str], failure_index: int) -> tuple[int | None, float | None]:
    candidates = [
        (int(match.group("step")), float(match.group("time")))
        for index, line in enumerate(lines[:failure_index])
        if (match := STEP_RE.fullmatch(line.strip()))
    ]
    return candidates[-1] if candidates else (None, None)

def classify_log_failure(
    log: LogEvidence,
    input_sha256: str,
    contract_sha256: str,
) -> FailureEvidence | None:
    if log.status == "accepted":
        return None
    lines = Path(log.path).read_bytes().decode("utf-8", errors="replace").splitlines()
    fatal_markers = tuple(
        _normalize(line)
        for line in lines
        if line.strip().upper().startswith(("ERROR:", "FATAL:"))
    )
    fatal = (
        fatal_markers
        + tuple(
            f"WARNING: {_normalize(warning)}"
            for warning in log.unresolved_warnings
        )
    )
    lowered = "\n".join(fatal).lower()
    negative_indices = [
        index for index, line in enumerate(lines) if "negative jacobian" in line.lower()
    ]
    failure_class = FailureClass.RESULT_EVIDENCE_ERROR
    phase = "EVIDENCE"
    failing_step: int | None = None
    failing_time: float | None = None
    automatic_retry_allowed = False
    if negative_indices:
        failure_index = negative_indices[0]
        prior_step = any(STEP_RE.fullmatch(line.strip()) for line in lines[:failure_index])
        prior_convergence = any(
            CONVERGED_RE.fullmatch(line.strip()) for line in lines[:failure_index]
        )
        failing_step, failing_time = _latest_step(lines, failure_index)
        if not prior_step and not prior_convergence:
            failure_class = FailureClass.INITIAL_MESH_ERROR
            phase = "INITIAL"
            automatic_retry_allowed = True
        elif prior_step:
            failure_class = FailureClass.DEFORMATION_MESH_ERROR
            phase = "DEFORMATION"
            automatic_retry_allowed = True
        else:
            failure_class = FailureClass.GEOMETRY_ERROR
            phase = "UNRESOLVED"
    elif any(token in lowered for token in ("cannot find", "missing reference", "invalid reference")):
        failure_class = FailureClass.INPUT_OR_REFERENCE_ERROR
        phase = "INPUT"
    elif any(token in lowered for token in ("not converging", "max nr of iterations reached")):
        failure_class = FailureClass.NONLINEAR_CONVERGENCE_ERROR
        phase = "SOLVE"
        automatic_retry_allowed = True
        if log.beginning_steps:
            failing_step = log.beginning_steps[-1].step
            failing_time = log.beginning_steps[-1].time
    elif any(token in lowered for token in ("initialization error", "model initialization")):
        failure_class = FailureClass.MODEL_SEMANTICS_ERROR
        phase = "INITIALIZATION"
    element_ids = tuple(sorted({
        int(match.group("id"))
        for line in fatal
        for match in ELEMENT_RE.finditer(line)
    }))
    node_ids = tuple(sorted({
        int(match.group("id"))
        for line in fatal
        for match in NODE_RE.finditer(line)
    }))
    core: dict[str, object] = {
        "failure_class": failure_class.value,
        "phase": phase,
        "normalized_fatal_lines": fatal,
        "last_converged_time": log.converged_times[-1] if log.converged_times else None,
        "failing_step": failing_step,
        "failing_time": failing_time,
        "element_ids": element_ids,
        "node_ids": node_ids,
        "input_sha256": input_sha256,
        "contract_sha256": contract_sha256,
        "automatic_retry_allowed": automatic_retry_allowed,
    }
    return FailureEvidence(
        failure_class=failure_class,
        phase=phase,
        normalized_fatal_lines=fatal,
        last_converged_time=core["last_converged_time"],
        failing_step=failing_step,
        failing_time=failing_time,
        element_ids=element_ids,
        node_ids=node_ids,
        input_sha256=input_sha256,
        contract_sha256=contract_sha256,
        automatic_retry_allowed=automatic_retry_allowed,
        fingerprint_sha256=_fingerprint(core),
    )
```

- [ ] **Step 4: Run the GREEN command and parser regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_failure.py apps/febio_cae_harness/tests/unit/test_log_parser.py -q
```

Expected: command exits `0`; all tests report `passed`; the initial/deformation/unresolved cases have three distinct classes/phases and stable uppercase fingerprints.

- [ ] **Step 5: Inspect, stage, and commit exactly Task 9 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/failure.py apps/febio_cae_harness/tests/unit/test_failure.py
git diff --cached --check
git commit -m "feat: classify FEBio engineering failures"
```

### Task 10: Locked official-FBS runtime and create-new bootstrap

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/data/fbs-cp313-win_amd64.lock.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/fbs_protocol.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/fbs-runtime-profile.schema.json`
- Create: `apps/febio_cae_harness/scripts/bootstrap-fbs-runtime.ps1`
- Create: `apps/febio_cae_harness/tests/unit/test_fbs_runtime_lock.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**
- Consumes: CPython 3.13.5 embeddable x64 archive, official `fbs.cp313-win_amd64.pyd`, exact FEBioStudio `bin`, package-resource access
- Produces: `RuntimeFile`, `FbsRuntimeLock`, `FbsRuntimeProfile`, `load_runtime_lock()`, `runtime_tree_sha256()`, `verify_runtime_tree()`, create-new `%LOCALAPPDATA%\FEBioCaeHarness\runtimes\fbs-cp313\runtime.json`

- [ ] **Step 1: Add the complete 35-file package-data lock**

Create `data/fbs-cp313-win_amd64.lock.json` with this complete content. The worker implementing Task 10 must use this repeated manifest directly and must not retrieve entries from any other plan or document.

```json
{
  "schema_version": 1,
  "runtime": "CPython 3.13.5 embeddable x64",
  "cpython_archive_source": "https://www.python.org/ftp/python/3.13.5/python-3.13.5-embed-amd64.zip",
  "cpython_archive_bytes": 10903542,
  "cpython_archive_sha256": "7D2650FD9D1B9D002D4A315D5F354247FD6A44F30517C7EF577B08F57A0FB6D9",
  "runtime_file_count": 35,
  "runtime_tree_sha256": "1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0",
  "python_exe_sha256": "5341746F92483A93E44C313DE830F2FBA2956F0759094404A16B2FED06C9A2ED",
  "python313_dll_sha256": "0565965617D94274D7F2C2958D0BEF33392CD9D2F346F99D8E1BEDBDF264EE85",
  "python313_zip_sha256": "8E1679691980AF64F5DEA76582BEC7E970F3B8AF744222C90B41720F5302AD0D",
  "python313_pth_sha256": "35DDF94682FF9AA713A8D63557242AD00F3F28FDD39337F02C3BDA4C0F791577",
  "fbs_module_source": "https://repo.febio.org/download/fbs.cp313-win_amd64.pyd",
  "fbs_module_sha256": "66D8B0154059229E6EFD963AFFCA51E9954B5B6ED5DEB597DD87D212EAEC3458",
  "python313_pth_utf8": "python313.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n",
  "files": [
    {"relative_path": "_asyncio.pyd", "bytes": 72160, "sha256": "ADAAF0C703641F6DBED30D101A5E23C17CC9454C36303394B9E28A52EA457471"},
    {"relative_path": "_bz2.pyd", "bytes": 85984, "sha256": "965F199679AFA9B31D537D98C3CA8403AFD6B9E58E1A463AE47697AE4BF12771"},
    {"relative_path": "_ctypes.pyd", "bytes": 133088, "sha256": "697D05CAC7C167C00CCF22EA4FDBC7A8DB93AB9C6421061191558E42478068C5"},
    {"relative_path": "_decimal.pyd", "bytes": 280032, "sha256": "90045140E45EDCFE4F4859B3190184FAFF1249220011330A9D01319745766607"},
    {"relative_path": "_elementtree.pyd", "bytes": 136536, "sha256": "6FCC5528CE81F4514FB11CC7248080FD335A3C60D898E845D3341EE589887DA1"},
    {"relative_path": "_hashlib.pyd", "bytes": 69976, "sha256": "1430E4A2ED19EDA840668A292C39FF44488B598F53E903A61739A86B779ECBFE"},
    {"relative_path": "_lzma.pyd", "bytes": 160088, "sha256": "B78F5A8476139FF04731046459EFD047BB8F52DC92C5B2082EABF2929C0CA02D"},
    {"relative_path": "_multiprocessing.pyd", "bytes": 37344, "sha256": "7B21C5B0EBEE82B0D85724F245857D65E23F82C6AAF392EFCD4F800462025D92"},
    {"relative_path": "_overlapped.pyd", "bytes": 57680, "sha256": "FAE5E0E822434DA7B1707B9AE4C77B8FA7D1D7B810E7E2F5CACF04449C714086"},
    {"relative_path": "_queue.pyd", "bytes": 34272, "sha256": "08050F94EFE7BDD9D7CBE85B1196DE391CAC1B30F4A4918610CB174AE529A5DB"},
    {"relative_path": "_socket.pyd", "bytes": 86872, "sha256": "C63E8E6A369CBE86E57C9823FB48BC5D4E7BB18455B9B001986B4768C49007DA"},
    {"relative_path": "_sqlite3.pyd", "bytes": 130256, "sha256": "443B801D2A372B67155044A928BE68AF0A677D1302655E5599131180DDD87659"},
    {"relative_path": "_ssl.pyd", "bytes": 181456, "sha256": "11572F6EB63E43CDC2908812506FFCDAB21BE2BE5931F1E38D856C15F5A79E6C"},
    {"relative_path": "_uuid.pyd", "bytes": 27856, "sha256": "6A3E6D89E71A803609E6E765A592011427A5B6E7A4766BBCA7790B601BB66DBE"},
    {"relative_path": "_wmi.pyd", "bytes": 40144, "sha256": "A7CC096244A497219269A3EE1CF2526A2B613D73FA566749F8F2408F5F4117D4"},
    {"relative_path": "_zoneinfo.pyd", "bytes": 50520, "sha256": "E11282095DED02AD6A71A08E91ADD86C2DE151553AD51CF1AAEA3257B82036B7"},
    {"relative_path": "fbs.pyd", "bytes": 2653184, "sha256": "66D8B0154059229E6EFD963AFFCA51E9954B5B6ED5DEB597DD87D212EAEC3458"},
    {"relative_path": "libcrypto-3.dll", "bytes": 5231472, "sha256": "CCFFFDDCD3DEFB8D899026298AF9AF43BC186130F8483D77E97C93233D5F27D7"},
    {"relative_path": "libffi-8.dll", "bytes": 39696, "sha256": "EFF52743773EB550FCC6CE3EFC37C85724502233B6B002A35496D828BD7B280A"},
    {"relative_path": "libssl-3.dll", "bytes": 794992, "sha256": "007142039F04D04E0ED607BDA53DE095E5BC6A8A10D26ECEDDE94EA7D2D7EEFE"},
    {"relative_path": "LICENSE.txt", "bytes": 33861, "sha256": "62BEC384DF47B0328307DB41455FF6EA2559E5546B394AC69148561B21703120"},
    {"relative_path": "pyexpat.pyd", "bytes": 204768, "sha256": "567F19A92479E66B652FFAADBDDBA26B7C5DDA43D5E97C67A4A76A076021B736"},
    {"relative_path": "python.cat", "bytes": 567498, "sha256": "DDE779D7C2FC3BB3BA0F110E672EDF7CFFBCF580B99DCEE233707199E9A98453"},
    {"relative_path": "python.exe", "bytes": 105816, "sha256": "5341746F92483A93E44C313DE830F2FBA2956F0759094404A16B2FED06C9A2ED"},
    {"relative_path": "python3.dll", "bytes": 72536, "sha256": "85D02D4C7E28C0F183415DC2BE5FE8E06AA7FA0567673C75C65C0031F59E1E8B"},
    {"relative_path": "python313._pth", "bytes": 80, "sha256": "35DDF94682FF9AA713A8D63557242AD00F3F28FDD39337F02C3BDA4C0F791577"},
    {"relative_path": "python313.dll", "bytes": 6110416, "sha256": "0565965617D94274D7F2C2958D0BEF33392CD9D2F346F99D8E1BEDBDF264EE85"},
    {"relative_path": "python313.zip", "bytes": 3775881, "sha256": "8E1679691980AF64F5DEA76582BEC7E970F3B8AF744222C90B41720F5302AD0D"},
    {"relative_path": "pythonw.exe", "bytes": 104280, "sha256": "2BAEE3C6F04A723423ECCAADAD48507386EB7AC0BDF90928D927F83EC64C4FEB"},
    {"relative_path": "select.pyd", "bytes": 33112, "sha256": "D1E486DE9653640BE7C3A9BED04AA716B29EA76A69E1DE758DD9FA708F2C9D38"},
    {"relative_path": "sqlite3.dll", "bytes": 1583584, "sha256": "17F4C92427F5C8FD968FFEE09B93D7D07C44AFFE910209342B846BE9410D3895"},
    {"relative_path": "unicodedata.pyd", "bytes": 712024, "sha256": "E7D0375A7064B1C8916CCA7CABF7E3DF559FC8463DFDF831F403E95C79499121"},
    {"relative_path": "vcruntime140.dll", "bytes": 120400, "sha256": "052AD6A20D375957E82AA6A3C441EA548D89BE0981516CA7EB306E063D5027F4"},
    {"relative_path": "vcruntime140_1.dll", "bytes": 49776, "sha256": "6A99BC0128E0C7D6CBBF615FCC26909565E17D4CA3451B97F8987F9C6ACBC6C8"},
    {"relative_path": "winsound.pyd", "bytes": 31200, "sha256": "670C5DC4CF2A8A44F5899F7B4238EC2D40EAD833DB33C2999E97C6C652BD5C88"}
  ]
}
```

The tree hash uses UTF-8 lines
`relative_path<TAB>bytes<TAB>SHA256`, sorted with Windows
`StringComparer.OrdinalIgnoreCase`, joined by LF, and with no trailing LF.
That exact ordering of the 35 entries yields the pinned
`1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0`.
The `_pth` bytes use CRLF and are exactly 80 bytes, which yields the pinned
`35DDF94682FF9AA713A8D63557242AD00F3F28FDD39337F02C3BDA4C0F791577`;
`import site` remains commented.

- [ ] **Step 2: Write lock, ordinal-manifest, `_pth`, extra-file, and no-binary tests**

```python
# tests/unit/test_fbs_runtime_lock.py
import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae_harness.fbs_protocol import (
    FbsRuntimeLock,
    RuntimeFile,
    load_runtime_lock,
    runtime_tree_sha256,
    verify_runtime_tree,
)

EXPECTED_TREE = "1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0"
EXPECTED_PTH = (
    "python313.zip\r\n.\r\n\r\n"
    "# Uncomment to run site.main() automatically\r\n"
    "#import site\r\n"
)

def test_packaged_lock_repeats_all_35_exact_entries():
    lock = load_runtime_lock()
    assert lock.runtime == "CPython 3.13.5 embeddable x64"
    assert len(lock.files) == lock.runtime_file_count == 35
    assert len({item.relative_path for item in lock.files}) == 35
    assert runtime_tree_sha256(lock.files) == lock.runtime_tree_sha256 == EXPECTED_TREE
    assert lock.python313_pth_utf8 == EXPECTED_PTH
    assert "#import site" in lock.python313_pth_utf8
    assert "\r\nimport site\r\n" not in lock.python313_pth_utf8
    assert next(item for item in lock.files if item.relative_path == "fbs.pyd").sha256 == (
        "66D8B0154059229E6EFD963AFFCA51E9954B5B6ED5DEB597DD87D212EAEC3458"
    )

def test_verify_runtime_tree_rejects_missing_extra_and_wrong_bytes(tmp_path):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"a")
    second.write_bytes(b"bb")
    files = (
        RuntimeFile("a.bin", 1, hashlib.sha256(b"a").hexdigest().upper()),
        RuntimeFile("b.bin", 2, hashlib.sha256(b"bb").hexdigest().upper()),
    )
    lock = FbsRuntimeLock.for_test(files)
    assert verify_runtime_tree(tmp_path, lock) == runtime_tree_sha256(files)
    (tmp_path / "extra.bin").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="unlisted runtime file"):
        verify_runtime_tree(tmp_path, lock)
    (tmp_path / "extra.bin").unlink()
    second.write_bytes(b"wrong")
    with pytest.raises(RuntimeError, match="runtime file bytes mismatch"):
        verify_runtime_tree(tmp_path, lock)
    second.unlink()
    with pytest.raises(RuntimeError, match="runtime file set mismatch"):
        verify_runtime_tree(tmp_path, lock)

def test_repository_tracks_lock_only_and_no_runtime_binary():
    repository = Path(__file__).parents[4]
    tracked = subprocess.check_output(
        ["git", "ls-files", "apps/febio_cae_harness/src/febio_cae_harness/data"],
        cwd=repository,
        text=True,
    ).splitlines()
    assert tracked == [
        "apps/febio_cae_harness/src/febio_cae_harness/data/fbs-cp313-win_amd64.lock.json"
    ]
    assert not any(Path(path).suffix.lower() in {".dll", ".exe", ".pyd", ".zip"} for path in tracked)
```

- [ ] **Step 3: Run the lock tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_fbs_runtime_lock.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.fbs_protocol'`.

- [ ] **Step 4: Implement the shared canonical manifest verifier**

```python
# fbs_protocol.py — runtime-lock portion
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from febio_cae_harness.hashing import sha256_file

@dataclass(frozen=True)
class RuntimeFile:
    relative_path: str
    bytes: int
    sha256: str

@dataclass(frozen=True)
class FbsRuntimeLock:
    schema_version: int
    runtime: str
    runtime_file_count: int
    runtime_tree_sha256: str
    python313_pth_utf8: str
    files: tuple[RuntimeFile, ...]
    metadata: dict[str, object]

    @classmethod
    def for_test(cls, runtime_files: tuple[RuntimeFile, ...]) -> FbsRuntimeLock:
        return cls(
            schema_version=1,
            runtime="test",
            runtime_file_count=len(runtime_files),
            runtime_tree_sha256=runtime_tree_sha256(runtime_files),
            python313_pth_utf8="",
            files=runtime_files,
            metadata={},
        )

@dataclass(frozen=True)
class FbsRuntimeProfile:
    schema_version: int
    lock_resource_sha256: str
    runtime_tree_sha256: str
    loaded_dll_manifest: tuple[dict[str, object], ...]
    loaded_dll_manifest_sha256: str
    febio_bin: str
    probe_command: tuple[str, ...]
    python_version: str
    fbs_version: str
    created_at: str
    bootstrap_script_sha256: str

def runtime_tree_sha256(runtime_files: tuple[RuntimeFile, ...]) -> str:
    lines = [
        f"{item.relative_path}\t{item.bytes}\t{item.sha256.upper()}"
        for item in sorted(
            runtime_files,
            key=lambda value: (value.relative_path.casefold(), value.relative_path),
        )
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest().upper()

def load_runtime_lock(path: Path | None = None) -> FbsRuntimeLock:
    if path is None:
        resource = files("febio_cae_harness").joinpath(
            "data/fbs-cp313-win_amd64.lock.json"
        )
        value = json.loads(resource.read_text(encoding="utf-8"))
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
    runtime_files = tuple(
        RuntimeFile(
            relative_path=str(item["relative_path"]),
            bytes=int(item["bytes"]),
            sha256=str(item["sha256"]).upper(),
        )
        for item in value["files"]
    )
    metadata = {
        key: item
        for key, item in value.items()
        if key not in {
            "schema_version", "runtime", "runtime_file_count",
            "runtime_tree_sha256", "python313_pth_utf8", "files",
        }
    }
    lock = FbsRuntimeLock(
        schema_version=int(value["schema_version"]),
        runtime=str(value["runtime"]),
        runtime_file_count=int(value["runtime_file_count"]),
        runtime_tree_sha256=str(value["runtime_tree_sha256"]).upper(),
        python313_pth_utf8=str(value["python313_pth_utf8"]),
        files=runtime_files,
        metadata=metadata,
    )
    if len(lock.files) != lock.runtime_file_count:
        raise RuntimeError("runtime_file_count does not match files")
    if runtime_tree_sha256(lock.files) != lock.runtime_tree_sha256:
        raise RuntimeError("runtime_tree_sha256 does not match files")
    return lock

def verify_runtime_tree(
    root: Path,
    lock: FbsRuntimeLock,
    *,
    allow_runtime_profile: bool = False,
) -> str:
    expected_names = {item.relative_path for item in lock.files}
    allowed_names = expected_names | ({"runtime.json"} if allow_runtime_profile else set())
    actual_names = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    extra = actual_names - allowed_names
    if extra:
        raise RuntimeError(f"unlisted runtime file: {sorted(extra)[0]}")
    if not expected_names.issubset(actual_names):
        raise RuntimeError("runtime file set mismatch")
    for item in lock.files:
        path = root / Path(item.relative_path)
        if path.stat().st_size != item.bytes:
            raise RuntimeError(f"runtime file bytes mismatch: {item.relative_path}")
        if sha256_file(path) != item.sha256:
            raise RuntimeError(f"runtime file hash mismatch: {item.relative_path}")
    pth = (root / "python313._pth").read_bytes().decode("utf-8")
    if lock.python313_pth_utf8 and pth != lock.python313_pth_utf8:
        raise RuntimeError("python313._pth content mismatch")
    actual_tree = runtime_tree_sha256(lock.files)
    if actual_tree != lock.runtime_tree_sha256:
        raise RuntimeError("runtime tree hash mismatch")
    return actual_tree
```

- [ ] **Step 5: Add the exact runtime-profile schema**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-cae-harness.local/schemas/fbs-runtime-profile.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "lock_resource_sha256", "runtime_tree_sha256",
    "loaded_dll_manifest", "loaded_dll_manifest_sha256", "febio_bin",
    "probe_command", "python_version", "fbs_version", "created_at",
    "bootstrap_script_sha256"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "lock_resource_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "runtime_tree_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "loaded_dll_manifest": {
      "type": "array",
      "minItems": 1,
      "uniqueItems": true,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["path", "bytes", "sha256"],
        "properties": {
          "path": {"type": "string", "minLength": 1},
          "bytes": {"type": "integer", "minimum": 1},
          "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
        }
      }
    },
    "loaded_dll_manifest_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "febio_bin": {"type": "string", "minLength": 3},
    "probe_command": {"type": "array", "minItems": 6, "items": {"type": "string"}},
    "python_version": {"type": "string", "minLength": 1},
    "fbs_version": {"type": "string", "minLength": 1},
    "created_at": {"type": "string", "format": "date-time"},
    "bootstrap_script_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
  }
}
```
- [ ] **Step 6: Implement create-new bootstrap and an isolated loaded-DLL probe**

```powershell
# scripts/bootstrap-fbs-runtime.ps1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SourceRuntime,
    [Parameter(Mandatory=$true)][string]$FebioBin,
    [Parameter(Mandatory=$true)][string]$HarnessPython
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$SourceRuntime = [IO.Path]::GetFullPath($SourceRuntime)
$FebioBin = [IO.Path]::GetFullPath($FebioBin)
$HarnessPython = [IO.Path]::GetFullPath($HarnessPython)
$PackageRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\src\febio_cae_harness'))
$LockPath = Join-Path $PackageRoot 'data\fbs-cp313-win_amd64.lock.json'
$SchemaPath = Join-Path $PackageRoot 'schemas\fbs-runtime-profile.schema.json'
$Destination = Join-Path $env:LOCALAPPDATA 'FEBioCaeHarness\runtimes\fbs-cp313'
$RuntimeJson = Join-Path $Destination 'runtime.json'

foreach ($RequiredFile in @($LockPath, $SchemaPath, $HarnessPython, (Join-Path $FebioBin 'febio4.exe'))) {
    if (-not [IO.File]::Exists($RequiredFile)) {
        throw "FBS_BOOTSTRAP_REQUIRED_FILE_MISSING: $RequiredFile"
    }
}
if (-not [IO.Directory]::Exists($SourceRuntime)) {
    throw "FBS_BOOTSTRAP_SOURCE_MISSING: $SourceRuntime"
}

function Get-UpperSha256 {
    param([Parameter(Mandatory=$true)][string]$LiteralPath)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $LiteralPath).Hash.ToUpperInvariant()
}

function Get-TreeHash {
    param(
        [Parameter(Mandatory=$true)][string]$Root,
        [Parameter(Mandatory=$true)]$Entries
    )
    $Lines = New-Object 'System.Collections.Generic.List[string]'
    foreach ($Entry in $Entries) {
        $Path = Join-Path $Root ([string]$Entry.relative_path)
        if (-not [IO.File]::Exists($Path)) {
            throw "FBS_RUNTIME_FILE_MISSING: $($Entry.relative_path)"
        }
        $Info = [IO.FileInfo]$Path
        if ($Info.Length -ne [int64]$Entry.bytes) {
            throw "FBS_RUNTIME_SIZE_MISMATCH: $($Entry.relative_path)"
        }
        $Hash = Get-UpperSha256 -LiteralPath $Path
        if ($Hash -ne [string]$Entry.sha256) {
            throw "FBS_RUNTIME_HASH_MISMATCH: $($Entry.relative_path)"
        }
        $Lines.Add(("{0}`t{1}`t{2}" -f $Entry.relative_path, $Info.Length, $Hash))
    }
    $Array = $Lines.ToArray()
    [Array]::Sort($Array, [StringComparer]::OrdinalIgnoreCase)
    $Bytes = [Text.Encoding]::UTF8.GetBytes([string]::Join("`n", $Array))
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-', '')
    }
    finally {
        $Hasher.Dispose()
    }
}

function Assert-ExactRuntimeFiles {
    param(
        [Parameter(Mandatory=$true)][string]$Root,
        [Parameter(Mandatory=$true)]$Entries,
        [switch]$AllowProfile
    )
    $Expected = @{}
    foreach ($Entry in $Entries) {
        $Expected[[string]$Entry.relative_path] = $true
    }
    if ($AllowProfile) {
        $Expected['runtime.json'] = $true
    }
    $Actual = @(
        Get-ChildItem -LiteralPath $Root -File -Recurse |
        ForEach-Object {
            $_.FullName.Substring($Root.Length).TrimStart('\').Replace('\', '/')
        }
    )
    foreach ($Relative in $Actual) {
        if (-not $Expected.ContainsKey($Relative)) {
            throw "FBS_RUNTIME_UNLISTED_FILE: $Relative"
        }
    }
    if ($Actual.Count -ne $Expected.Count) {
        throw "FBS_RUNTIME_FILE_COUNT_MISMATCH: actual=$($Actual.Count) expected=$($Expected.Count)"
    }
}

function Copy-CreateNew {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Target
    )
    $Parent = [IO.Path]::GetDirectoryName($Target)
    [IO.Directory]::CreateDirectory($Parent) | Out-Null
    $Input = [IO.File]::Open($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $Output = [IO.File]::Open($Target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        try {
            $Input.CopyTo($Output)
            $Output.Flush($true)
        }
        finally {
            $Output.Dispose()
        }
    }
    finally {
        $Input.Dispose()
    }
}

function Invoke-CapturedProcess {
    param(
        [Parameter(Mandatory=$true)][string]$Executable,
        [Parameter(Mandatory=$true)][string]$Arguments
    )
    $Start = New-Object Diagnostics.ProcessStartInfo
    $Start.FileName = $Executable
    $Start.Arguments = $Arguments
    $Start.UseShellExecute = $false
    $Start.CreateNoWindow = $true
    $Start.RedirectStandardOutput = $true
    $Start.RedirectStandardError = $true
    $Start.EnvironmentVariables.Remove('PYTHONHOME')
    $Start.EnvironmentVariables.Remove('PYTHONPATH')
    $Process = New-Object Diagnostics.Process
    $Process.StartInfo = $Start
    if (-not $Process.Start()) {
        throw 'FBS_PROBE_CREATE_PROCESS_FAILED'
    }
    $Stdout = $Process.StandardOutput.ReadToEnd()
    $Stderr = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()
    if ($Process.ExitCode -ne 0) {
        throw "FBS_PROBE_FAILED: exit=$($Process.ExitCode) stderr=$Stderr"
    }
    return $Stdout
}

$Lock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Lock.runtime_file_count -ne 35 -or $Lock.files.Count -ne 35) {
    throw 'FBS_LOCK_FILE_COUNT_NOT_35'
}
Assert-ExactRuntimeFiles -Root $SourceRuntime -Entries $Lock.files
$SourceTreeHash = Get-TreeHash -Root $SourceRuntime -Entries $Lock.files
if ($SourceTreeHash -ne $Lock.runtime_tree_sha256) {
    throw "FBS_SOURCE_TREE_HASH_MISMATCH: $SourceTreeHash"
}
$Pth = [Text.Encoding]::UTF8.GetString(
    [IO.File]::ReadAllBytes((Join-Path $SourceRuntime 'python313._pth'))
)
if ($Pth -cne [string]$Lock.python313_pth_utf8) {
    throw 'FBS_PYTHON313_PTH_CONTENT_MISMATCH'
}

if ([IO.Directory]::Exists($Destination)) {
    Assert-ExactRuntimeFiles -Root $Destination -Entries $Lock.files -AllowProfile
    $ExistingTreeHash = Get-TreeHash -Root $Destination -Entries $Lock.files
    if ($ExistingTreeHash -ne $Lock.runtime_tree_sha256 -or -not [IO.File]::Exists($RuntimeJson)) {
        throw 'FBS_RUNTIME_DESTINATION_ALREADY_DIFFERENT'
    }
    & $HarnessPython -c "import json,jsonschema,sys; jsonschema.validate(json.load(open(sys.argv[1],encoding='utf-8')),json.load(open(sys.argv[2],encoding='utf-8')))" $RuntimeJson $SchemaPath
    if ($LASTEXITCODE -ne 0) {
        throw 'FBS_RUNTIME_EXISTING_PROFILE_INVALID'
    }
    Write-Output $RuntimeJson
    exit 0
}

[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Destination)) | Out-Null
[IO.Directory]::CreateDirectory($Destination) | Out-Null
foreach ($Entry in $Lock.files) {
    Copy-CreateNew `
        -Source (Join-Path $SourceRuntime ([string]$Entry.relative_path)) `
        -Target (Join-Path $Destination ([string]$Entry.relative_path))
}
Assert-ExactRuntimeFiles -Root $Destination -Entries $Lock.files
$DestinationTreeHash = Get-TreeHash -Root $Destination -Entries $Lock.files
if ($DestinationTreeHash -ne $Lock.runtime_tree_sha256) {
    throw "FBS_DESTINATION_TREE_HASH_MISMATCH: $DestinationTreeHash"
}

$ProbePath = Join-Path ([IO.Path]::GetTempPath()) ("febio-fbs-probe-" + [Guid]::NewGuid().ToString('N') + '.py')
$ProbeSource = @'
import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--runtime", required=True)
parser.add_argument("--febio-bin", required=True)
args = parser.parse_args()
runtime = pathlib.Path(args.runtime).resolve(strict=True)
febio_bin = pathlib.Path(args.febio_bin).resolve(strict=True)
sys.path.insert(0, str(runtime))
with os.add_dll_directory(str(febio_bin)):
    import fbs

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
modules = (ctypes.c_void_p * 4096)()
needed = ctypes.c_ulong()
handle = kernel32.GetCurrentProcess()
if not psapi.EnumProcessModulesEx(
    handle,
    modules,
    ctypes.sizeof(modules),
    ctypes.byref(needed),
    0x03,
):
    raise ctypes.WinError(ctypes.get_last_error(), "EnumProcessModulesEx")
windows_root = pathlib.Path(os.environ["WINDIR"]).resolve()
paths = set()
for index in range(needed.value // ctypes.sizeof(ctypes.c_void_p)):
    buffer = ctypes.create_unicode_buffer(32768)
    if psapi.GetModuleFileNameExW(handle, modules[index], buffer, len(buffer)):
        path = pathlib.Path(buffer.value).resolve(strict=True)
        try:
            path.relative_to(windows_root)
        except ValueError:
            paths.add(path)
records = []
for path in sorted(paths, key=lambda value: str(value).casefold()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    records.append(
        {
            "path": path.as_posix(),
            "bytes": path.stat().st_size,
            "sha256": digest,
        }
    )
print(
    json.dumps(
        {
            "python_version": sys.version.split()[0],
            "fbs_version": str(getattr(fbs, "__version__", "unreported")),
            "loaded_dll_manifest": records,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
)
'@
$Utf8NoBom = New-Object Text.UTF8Encoding($false)
$ProbeStream = [IO.File]::Open($ProbePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try {
    $ProbeBytes = $Utf8NoBom.GetBytes($ProbeSource)
    $ProbeStream.Write($ProbeBytes, 0, $ProbeBytes.Length)
    $ProbeStream.Flush($true)
}
finally {
    $ProbeStream.Dispose()
}

try {
    $FbsPython = Join-Path $Destination 'python.exe'
    $ProbeArguments = '-I -B -S "' + $ProbePath + '" --runtime "' + $Destination + '" --febio-bin "' + $FebioBin + '"'
    $Probe = Invoke-CapturedProcess -Executable $FbsPython -Arguments $ProbeArguments |
        ConvertFrom-Json
}
finally {
    [IO.File]::Delete($ProbePath)
}

$DllLines = @(
    $Probe.loaded_dll_manifest |
    ForEach-Object { "{0}`t{1}`t{2}" -f $_.path, $_.bytes, $_.sha256 }
)
[Array]::Sort($DllLines, [StringComparer]::Ordinal)
$DllBytes = [Text.Encoding]::UTF8.GetBytes([string]::Join("`n", $DllLines))
$DllHasher = [Security.Cryptography.SHA256]::Create()
try {
    $DllManifestHash = ([BitConverter]::ToString($DllHasher.ComputeHash($DllBytes))).Replace('-', '')
}
finally {
    $DllHasher.Dispose()
}

$Profile = [ordered]@{
    schema_version = 1
    lock_resource_sha256 = Get-UpperSha256 -LiteralPath $LockPath
    runtime_tree_sha256 = $DestinationTreeHash
    loaded_dll_manifest = @($Probe.loaded_dll_manifest)
    loaded_dll_manifest_sha256 = $DllManifestHash
    febio_bin = ([IO.DirectoryInfo]$FebioBin).FullName
    probe_command = @(
        $FbsPython, '-I', '-B', '-S', $ProbePath,
        '--runtime', $Destination, '--febio-bin', $FebioBin
    )
    python_version = [string]$Probe.python_version
    fbs_version = [string]$Probe.fbs_version
    created_at = [DateTimeOffset]::UtcNow.ToString('o')
    bootstrap_script_sha256 = Get-UpperSha256 -LiteralPath $PSCommandPath
}
$ProfileJson = ($Profile | ConvertTo-Json -Depth 8 -Compress) + "`n"
$RuntimeStream = [IO.File]::Open($RuntimeJson, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try {
    $ProfileBytes = $Utf8NoBom.GetBytes($ProfileJson)
    $RuntimeStream.Write($ProfileBytes, 0, $ProfileBytes.Length)
    $RuntimeStream.Flush($true)
}
finally {
    $RuntimeStream.Dispose()
}

& $HarnessPython -c "import json,jsonschema,sys; jsonschema.validate(json.load(open(sys.argv[1],encoding='utf-8')),json.load(open(sys.argv[2],encoding='utf-8')))" $RuntimeJson $SchemaPath
if ($LASTEXITCODE -ne 0) {
    throw 'FBS_RUNTIME_PROFILE_SCHEMA_INVALID'
}
Assert-ExactRuntimeFiles -Root $Destination -Entries $Lock.files -AllowProfile
Write-Output $RuntimeJson
```

- [ ] **Step 7: Run the GREEN commands for package resources and both distributions**

Add these exact paths to `EXPECTED_RESOURCES`:

```python
"data/fbs-cp313-win_amd64.lock.json",
"schemas/fbs-runtime-profile.schema.json",
```

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_fbs_runtime_lock.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
& .\.venv\Scripts\python.exe -m build apps/febio_cae_harness
```

Expected: pytest exits `0`; wheel and sdist builds exit `0`; each archive contains the lock JSON and runtime-profile schema, and no `.exe`, `.dll`, `.pyd`, or runtime `.zip`.

- [ ] **Step 8: Bootstrap from freshly verified official downloads**

```powershell
$BootstrapSource = Join-Path ([IO.Path]::GetTempPath()) ('febio-fbs-cp313-source-' + [Guid]::NewGuid().ToString('N'))
$Archive = Join-Path ([IO.Path]::GetTempPath()) ('python-3.13.5-embed-amd64-' + [Guid]::NewGuid().ToString('N') + '.zip')
$FbsDownload = Join-Path $BootstrapSource 'fbs.pyd'
New-Item -ItemType Directory -Path $BootstrapSource -ErrorAction Stop | Out-Null
try {
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.13.5/python-3.13.5-embed-amd64.zip' -OutFile $Archive
    if ((Get-Item -LiteralPath $Archive).Length -ne 10903542) { throw 'CPYTHON_ARCHIVE_SIZE_MISMATCH' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash -ne '7D2650FD9D1B9D002D4A315D5F354247FD6A44F30517C7EF577B08F57A0FB6D9') { throw 'CPYTHON_ARCHIVE_HASH_MISMATCH' }
    Expand-Archive -LiteralPath $Archive -DestinationPath $BootstrapSource
    Invoke-WebRequest -UseBasicParsing -Uri 'https://repo.febio.org/download/fbs.cp313-win_amd64.pyd' -OutFile $FbsDownload
    if ((Get-Item -LiteralPath $FbsDownload).Length -ne 2653184) { throw 'FBS_MODULE_SIZE_MISMATCH' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $FbsDownload).Hash -ne '66D8B0154059229E6EFD963AFFCA51E9954B5B6ED5DEB597DD87D212EAEC3458') { throw 'FBS_MODULE_HASH_MISMATCH' }
    & .\apps\febio_cae_harness\scripts\bootstrap-fbs-runtime.ps1 `
        -SourceRuntime $BootstrapSource `
        -FebioBin 'C:\Program Files\FEBioStudio\bin' `
        -HarnessPython (Resolve-Path .\.venv\Scripts\python.exe)
    if ($LASTEXITCODE -ne 0) { throw "FBS_BOOTSTRAP_EXIT_$LASTEXITCODE" }
}
finally {
    if (Test-Path -LiteralPath $Archive -PathType Leaf) {
        Remove-Item -LiteralPath $Archive -Force
    }
    if (Test-Path -LiteralPath $BootstrapSource -PathType Container) {
        Remove-Item -LiteralPath $BootstrapSource -Recurse -Force
    }
}
```

Expected: the script prints exactly one canonical `runtime.json` path and exits `0`; a second invocation against a different runtime is rejected without changing the existing destination.

- [ ] **Step 9: Independently recompute all 35 runtime hashes and the tree hash in PowerShell**

```powershell
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'FEBioCaeHarness\runtimes\fbs-cp313'
$Lock = Get-Content -LiteralPath '.\apps\febio_cae_harness\src\febio_cae_harness\data\fbs-cp313-win_amd64.lock.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$Lines = @()
foreach ($Entry in $Lock.files) {
    $Path = Join-Path $RuntimeRoot $Entry.relative_path
    $Info = Get-Item -LiteralPath $Path
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    if ($Info.Length -ne $Entry.bytes -or $Hash -ne $Entry.sha256) {
        throw "FBS_RUNTIME_ENTRY_MISMATCH: $($Entry.relative_path)"
    }
    $Lines += "{0}`t{1}`t{2}" -f $Entry.relative_path, $Info.Length, $Hash
}
[Array]::Sort($Lines, [StringComparer]::OrdinalIgnoreCase)
$Bytes = [Text.Encoding]::UTF8.GetBytes([string]::Join("`n", $Lines))
$Hasher = [Security.Cryptography.SHA256]::Create()
try {
    $TreeHash = ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-', '')
}
finally {
    $Hasher.Dispose()
}
if ($Lines.Count -ne 35) { throw "FBS_RUNTIME_COUNT_$($Lines.Count)" }
if ($TreeHash -ne '1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0') {
    throw "FBS_RUNTIME_TREE_HASH_MISMATCH: $TreeHash"
}
$Profile = Get-Content -LiteralPath (Join-Path $RuntimeRoot 'runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Profile.runtime_tree_sha256 -ne $TreeHash) { throw 'FBS_PROFILE_TREE_HASH_MISMATCH' }
```

Expected: no exception, `$Lines.Count` is `35`, and `$TreeHash` is exactly `1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0`.

- [ ] **Step 10: Inspect, stage, and commit exactly Task 10 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/data/fbs-cp313-win_amd64.lock.json apps/febio_cae_harness/src/febio_cae_harness/fbs_protocol.py apps/febio_cae_harness/src/febio_cae_harness/schemas/fbs-runtime-profile.schema.json apps/febio_cae_harness/scripts/bootstrap-fbs-runtime.ps1 apps/febio_cae_harness/tests/unit/test_fbs_runtime_lock.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git diff --cached --check
git commit -m "feat: lock the official FBS runtime"
```

### Task 11: Isolated official-FBS protocol, mesh, field, and kinematic evidence

**Files:**
- Modify: `apps/febio_cae_harness/src/febio_cae_harness/fbs_protocol.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/fbs_client.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/fbs_worker.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/fbs-request.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/fbs-evidence.schema.json`
- Create: `apps/febio_cae_harness/tests/fixtures/fbs/fake_fbs.py`
- Create: `apps/febio_cae_harness/tests/unit/test_fbs_protocol.py`
- Create: `apps/febio_cae_harness/tests/unit/test_fbs_worker.py`
- Create: `apps/febio_cae_harness/tests/integration/test_fbs_client.py`
- Create: `apps/febio_cae_harness/tests/integration/test_fbs_real_runtime_contract.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**
- Consumes: verified Task 10 runtime/profile, fresh immutable XPLT, canonical Task 2 model-evidence `domain_bindings`, arbitrary intent target states/field/population/kinematic requests
- Produces: `FbsRuntime`, `XpltRequest.from_model_evidence(xplt_path, model_evidence_path, ...)` which derives domain/connectivity/coordinate authority without caller-supplied hashes, `XpltEvidence(kind="fbs-verification")`, `XpltEvidence.to_record() -> EvidenceRecord`, `match_target_states()`, all-node `feb-node-coordinate-f32-v1`, empty-name-safe one-to-one FEB domain aliases using `feb-domain-connectivity-signature-v1`, locked official enum resolution, `inspect_xplt(request: XpltRequest, runtime: FbsRuntime) -> XpltEvidence`, protocol version `feb-fbs-connectivity-signature-v1`

- [ ] **Step 1: Write protocol tests for unique matching and transitive request hashes**

```python
# tests/unit/test_fbs_protocol.py
import json
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae_harness.fbs_protocol import (
    FbsRuntime,
    XpltRequest,
    match_target_states,
)
from febio_cae_harness.hashing import sha256_file

def domain_binding() -> dict[str, object]:
    return {
        "domain_alias": "Part2",
        "material_name": "ABS",
        "element_count": 1,
        "referenced_node_count": 4,
        "element_type": "TET4",
        "nodes_per_element": 4,
        "connectivity_signature_version": (
            "feb-domain-connectivity-signature-v1"
        ),
        "connectivity_signature_sha256": "B" * 64,
    }

def write_model_evidence(attempt: Path) -> Path:
    solver = attempt / "solver"
    solver.mkdir(parents=True)
    input_feb = solver / "input.feb"
    input_feb.write_bytes(b"FEB")
    model_path = attempt / "model-evidence.json"
    model = {
        "schema_version": 1,
        "kind": "model-evidence",
        "attempt_id": "attempt-test",
        "source": {
            "path": str(input_feb.resolve()),
            "bytes": input_feb.stat().st_size,
            "sha256": sha256_file(input_feb),
        },
        "model_signature_version": "feb-model-signature-v1",
        "model_signature_sha256": "1" * 64,
        "domain_signature_version": "feb-domain-signature-v1",
        "domain_signature_sha256": "2" * 64,
        "node_count": 4,
        "element_count": 1,
        "connectivity_signature_version": (
            "feb-fbs-connectivity-signature-v1"
        ),
        "connectivity_signature_sha256": "C" * 64,
        "domain_bindings": [domain_binding()],
        "initial_coordinate_signature": {
            "version": "feb-node-coordinate-f32-v1",
            "ordering": "ascending-node-id",
            "node_id_encoding": "uint64-big-endian",
            "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
            "node_count": 4,
            "component_count": 12,
            "sha256": "A" * 64,
        },
        "invariant_signatures": {
            "domain": "2" * 64,
            "material": "3" * 64,
            "reference_closure": "4" * 64,
            "load": "5" * 64,
            "boundary": "6" * 64,
            "contact": "7" * 64,
            "output": "8" * 64,
        },
    }
    model_path.write_text(
        json.dumps(model, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return model_path

def request(tmp_path: Path) -> XpltRequest:
    attempt = tmp_path / "attempt"
    model_path = write_model_evidence(attempt)
    xplt = attempt / "solver" / "solver.xplt"
    xplt.write_bytes(b"XPLT")
    return XpltRequest.from_model_evidence(
        xplt,
        model_path,
        expected_times=(0.05, 0.10),
        allow_zero_state=False,
        allow_unrequested_states=False,
        time_abs_tol=1.0e-6,
        populations=(
            {
                "name": "abs",
                "domain_aliases": ["Part2"],
                "require_disjoint": True,
            },
        ),
        fields=(
            {
                "name": "stress",
                "operation": "MAT3DS.EFFECTIVE",
                "association": "elemData",
                "population": "abs",
            },
        ),
        initial_coordinates=(),
        kinematic_checks=(),
    )

def test_match_target_states_is_unique_and_never_interpolates():
    assert match_target_states(
        actual=(0.0, 0.050000000745, 0.10000000149),
        expected=(0.05, 0.10),
        tolerance=1.0e-6,
        allow_unrequested_states=True,
    ) == ((0, 1), (1, 2))
    with pytest.raises(ValueError, match="MISSING_TARGET_STATE"):
        match_target_states((0.0, 0.10), (0.05, 0.10), 1.0e-6, True)
    with pytest.raises(ValueError, match="AMBIGUOUS_TARGET_STATE"):
        match_target_states((0.05, 0.0500005, 0.10), (0.05, 0.10), 1.0e-6, True)
    with pytest.raises(ValueError, match="UNREQUESTED_STATE"):
        match_target_states((0.0, 0.05, 0.10), (0.05, 0.10), 1.0e-6, False)

def test_request_hash_changes_for_every_semantic_contract_field(tmp_path):
    original = request(tmp_path)
    changes = (
        replace(original, expected_times=(0.05,)),
        replace(original, allow_zero_state=True),
        replace(original, allow_unrequested_states=True),
        replace(original, time_abs_tol=5.0e-7),
        replace(original, fields=()),
        replace(original, populations=()),
        replace(
            original,
            domain_bindings=(
                {
                    "domain_alias": "Part2",
                    "material_name": "ABS",
                    "element_count": 1,
                    "referenced_node_count": 4,
                    "element_type": "TET4",
                    "nodes_per_element": 4,
                    "connectivity_signature_version": (
                        "feb-domain-connectivity-signature-v1"
                    ),
                    "connectivity_signature_sha256": "C" * 64,
                },
            ),
        ),
        replace(original, expected_connectivity_signature_sha256="A" * 64),
        replace(original, expected_initial_coordinate_signature_sha256="B" * 64),
    )
    assert all(value.request_sha256 != original.request_sha256 for value in changes)

def test_runtime_has_no_generic_python_or_dll_search_path():
    fields = set(FbsRuntime.__dataclass_fields__)
    assert fields == {
        "python_exe",
        "fbs_module",
        "febio_bin",
        "expected_runtime_tree_sha256",
        "expected_loaded_dll_manifest_sha256",
    }

def test_request_rejects_full_node_coordinate_payload(tmp_path):
    original = request(tmp_path)
    with pytest.raises(
        ValueError,
        match="FBS_COORDINATE_SPOT_CHECK_LIMIT_EXCEEDED",
    ):
        XpltRequest.from_model_evidence(
            original.xplt_path,
            original.model_evidence_path,
            expected_times=original.expected_times,
            allow_zero_state=original.allow_zero_state,
            allow_unrequested_states=original.allow_unrequested_states,
            time_abs_tol=original.time_abs_tol,
            populations=original.populations,
            fields=original.fields,
            initial_coordinates=tuple(
                {
                    "node_id": index + 1,
                    "decimal": ["0", "0", "0"],
                }
                for index in range(33)
            ),
            kinematic_checks=original.kinematic_checks,
        )
```

- [ ] **Step 2: Add a complete standard-library fake of the used official FBS surface**

```python
# tests/fixtures/fbs/fake_fbs.py
from types import SimpleNamespace

class Value:
    def __init__(self, value):
        self.val = value

class Field:
    def __init__(self, name):
        self.name = name

class DataManager:
    def __init__(self):
        self._fields = [Field("displacement"), Field("stress"), Field("relative volume")]
    def DataFields(self):
        return len(self._fields)
    def DataField(self, index):
        return self._fields[index]

class Node:
    def __init__(self, node_id, coordinate):
        self._id = node_id
        self.r = coordinate
    def GetID(self):
        return self._id

class Element:
    def __init__(self, element_id, nodes):
        self._id = element_id
        self._nodes = nodes
    def GetID(self):
        return self._id
    def Nodes(self):
        return len(self._nodes)
    def Node(self, local):
        return self._nodes[local]

class Partition:
    def __init__(self, name, material_id, elements):
        self.name = name
        self._material_id = material_id
        self._elements = elements
    def GetMatID(self):
        return self._material_id
    def ElementList(self):
        return self._elements

class Mesh:
    def __init__(self):
        self._nodes = [
            Node(101, (0.0, 0.0, 0.0)),
            Node(102, (1.0, 0.0, 0.0)),
            Node(103, (0.0, 1.0, 0.0)),
            Node(104, (0.0, 0.0, 1.0)),
            Node(201, (2.0, 0.0, 0.0)),
            Node(202, (3.0, 0.0, 0.0)),
            Node(203, (2.0, 1.0, 0.0)),
            Node(204, (2.0, 0.0, 1.0)),
        ]
        self._elements = [Element(501, (0, 1, 2, 3)), Element(601, (4, 5, 6, 7))]
        self._partitions = [
            Partition("", 0, (0,)),
            Partition("", 1, (1,)),
        ]
    def Nodes(self):
        return len(self._nodes)
    def Elements(self):
        return len(self._elements)
    def Node(self, index):
        return self._nodes[index]
    def Element(self, index):
        return self._elements[index]
    def MeshPartitions(self):
        return len(self._partitions)
    def MeshPartition(self, index):
        return self._partitions[index]

class Model:
    def __init__(self):
        self._mesh = Mesh()
        self._states = [SimpleNamespace(time=0.5), SimpleNamespace(time=1.0)]
        self._manager = DataManager()
        self._materials = [
            SimpleNamespace(name="ABS"),
            SimpleNamespace(name="rigid-material"),
        ]
    def States(self):
        return len(self._states)
    def State(self, index):
        return self._states[index]
    def GetFEMesh(self, index):
        assert index == 0
        return self._mesh
    def GetDataManager(self):
        return self._manager
    def GetDataField(self, name):
        return Field(name)
    def Material(self, index):
        return self._materials[index]
    def Evaluate(self, field, operation, state):
        if field.name == "displacement":
            values = {0: 1.0, 1: 2.0, 2: 2.0, 6: 3.0}
            return SimpleNamespace(
                nodeData=[Value(values[operation]) for _ in range(8)],
                elemData=[Value(999.0), Value(999.0)],
            )
        if field.name == "stress":
            return SimpleNamespace(
                nodeData=[Value(999.0) for _ in range(8)],
                elemData=[Value(10.0), Value(20.0)],
            )
        if field.name == "relative volume":
            return SimpleNamespace(
                nodeData=[Value(999.0) for _ in range(8)],
                elemData=[Value(1.0), Value(1.0)],
            )
        raise KeyError(field.name)

class MAT3DS:
    EFFECTIVE = 6
    P2 = 8

class Post:
    MAT3DS = MAT3DS

    @staticmethod
    def ReadPlotFile(path):
        if open(path, "rb").read() != b"XPLT":
            raise ValueError("corrupt plot")
        return Model()

post = Post()
__version__ = "fake-1"
```

- [ ] **Step 3: Write worker tests for index/ID conversion, association, connectivity, float32, and rigid vectors**

```python
# tests/unit/test_fbs_worker.py
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from febio_cae_harness import fbs_worker

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fbs" / "fake_fbs.py"

def worker_request(tmp_path: Path) -> tuple[Path, Path]:
    module_dir = tmp_path / "module"
    module_dir.mkdir(parents=True)
    shutil.copyfile(FIXTURE, module_dir / "fbs.py")
    xplt = tmp_path / "solver.xplt"
    xplt.write_bytes(b"XPLT")
    request = {
        "schema_version": 1,
        "request_id": "B" * 64,
        "xplt": {
            "path": str(xplt),
            "bytes": 4,
            "sha256": hashlib.sha256(b"XPLT").hexdigest().upper(),
        },
        "fbs_module": str(module_dir / "fbs.py"),
        "febio_bin": str(tmp_path),
        "expected_times": [0.5, 1.0],
        "allow_zero_state": False,
        "allow_unrequested_states": False,
        "time_abs_tol": 1.0e-6,
        "populations": [
            {
                "name": "abs",
                "domain_aliases": ["Part2"],
                "require_disjoint": True,
            },
            {
                "name": "rigid",
                "domain_aliases": ["rigid-domain"],
                "require_disjoint": True,
            },
        ],
        "domain_bindings": [
            {
                "domain_alias": "Part2",
                "material_name": "ABS",
                "element_count": 1,
                "referenced_node_count": 4,
                "element_type": "TET4",
                "nodes_per_element": 4,
                "connectivity_signature_version": (
                    "feb-domain-connectivity-signature-v1"
                ),
                "connectivity_signature_sha256": (
                    "56751D855C07F3A866AEA97E1ADFF72B67F23985F0C6FE8C48CF211151830DED"
                ),
            },
            {
                "domain_alias": "rigid-domain",
                "material_name": "rigid-material",
                "element_count": 1,
                "referenced_node_count": 4,
                "element_type": "TET4",
                "nodes_per_element": 4,
                "connectivity_signature_version": (
                    "feb-domain-connectivity-signature-v1"
                ),
                "connectivity_signature_sha256": (
                    "E5CF54B8D280291BDB2CF2D9E127FFE0BDF188111C2738A1240DD33C34C1CC2B"
                ),
            },
        ],
        "fields": [
            {"name": "displacement", "operation": 0, "association": "nodeData", "population": "rigid"},
            {
                "name": "stress",
                "operation": "MAT3DS.EFFECTIVE",
                "association": "elemData",
                "population": "abs",
            },
            {"name": "relative volume", "operation": 0, "association": "elemData", "population": "abs"},
        ],
        "initial_coordinates": [
            {"node_id": 101, "decimal": ["0.0", "0.0", "0.0"]},
            {"node_id": 102, "decimal": ["1.0", "0.0", "0.0"]},
        ],
        "expected_connectivity_signature_sha256": "813A02EDBD8DF6F733DF0D7EBBCAE22DF72E993FAC26E344D3CCBEAD9237B49A",
        "expected_initial_coordinate_signature_sha256": "1E02F9C121842A8546B2A9E6D139CC7767951CCCF083F0B3CC73DE84DD3BFC95",
        "kinematic_checks": [
            {
                "field": "displacement",
                "population": "rigid",
                "expected_by_time": [
                    {"time": 0.5, "vector": [1.0, 2.0, 2.0], "magnitude": 3.0},
                    {"time": 1.0, "vector": [1.0, 2.0, 2.0], "magnitude": 3.0},
                ],
                "abs_tol": 1.0e-6,
            }
        ],
    }
    model_path = tmp_path / "model-evidence.json"
    model = {
        "kind": "model-evidence",
        "domain_bindings": request["domain_bindings"],
        "connectivity_signature_sha256": (
            request["expected_connectivity_signature_sha256"]
        ),
        "initial_coordinate_signature": {
            "sha256": request[
                "expected_initial_coordinate_signature_sha256"
            ]
        },
    }
    model_path.write_text(
        json.dumps(model, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    request["model_evidence"] = {
        "path": str(model_path.resolve()),
        "bytes": model_path.stat().st_size,
        "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest().upper(),
    }
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    return request_path, response_path

def test_worker_emits_indices_and_distinct_feb_ids(tmp_path, monkeypatch):
    request_path, response_path = worker_request(tmp_path)
    monkeypatch.setattr(fbs_worker, "_loaded_dll_manifest", lambda: [])
    assert fbs_worker.main(["--request", str(request_path), "--response", str(response_path)]) == 0
    evidence = json.loads(response_path.read_text(encoding="utf-8"))
    assert evidence["kind"] == "fbs-verification"
    assert evidence["status"] == "accepted"
    assert evidence["nonzero_target_states"] == 2
    assert evidence["zero_state_count"] == 0
    assert evidence["unrequested_state_count"] == 0
    assert evidence["final_time"] == 1.0
    assert evidence["maximum_target_time_abs_error"] == 0.0
    populations = {item["name"]: item for item in evidence["populations"]}
    assert populations["abs"]["element_indices"] == [0]
    assert populations["abs"]["element_ids"] == [501]
    assert populations["abs"]["node_indices"] == [0, 1, 2, 3]
    assert populations["abs"]["node_ids"] == [101, 102, 103, 104]
    assert evidence["fields"][0]["association"] == "nodeData"
    assert evidence["fields"][1]["association"] == "elemData"
    assert evidence["fields"][1]["operation"] == 6
    assert evidence["fields"][1]["operation_source"] == "MAT3DS.EFFECTIVE"
    assert {
        item["domain_alias"] for item in evidence["partitions"]
    } == {"Part2", "rigid-domain"}
    assert all(item["partition_name"] == "" for item in evidence["partitions"])
    assert evidence["kinematic_checks"][0]["full_vector_checked"] is True
    assert evidence["kinematic_checks"][0]["operation6_consistent"] is True
    assert (
        evidence["kinematic_checks"][0]["all_nodes_all_target_states_checked"]
        is True
    )
    assert evidence["kinematic_checks"][0]["maximum_norm_consistency_error"] == 0.0
    assert evidence["initial_coordinate_check"]["all_binary32_bits_equal"] is True
    assert evidence["initial_coordinate_signature"]["sha256"] == (
        "1E02F9C121842A8546B2A9E6D139CC7767951CCCF083F0B3CC73DE84DD3BFC95"
    )
    assert evidence["connectivity"]["version"] == "feb-fbs-connectivity-signature-v1"

def test_worker_rejects_missing_field_and_model_signature_mismatches(
    tmp_path, monkeypatch
):
    for mutation, error in (
        (lambda value: value["fields"][1].update(name="missing"), "FIELD_NOT_IN_INVENTORY"),
        (
            lambda value: value["fields"].append(
                dict(value["fields"][0])
            ),
            "FIELD_REQUEST_DUPLICATE",
        ),
        (
            lambda value: value.update(
                expected_connectivity_signature_sha256="0" * 64
            ),
            "MODEL_EVIDENCE_REQUEST_BINDING_MISMATCH",
        ),
        (
            lambda value: value.update(
                expected_initial_coordinate_signature_sha256="0" * 64
            ),
            "MODEL_EVIDENCE_REQUEST_BINDING_MISMATCH",
        ),
        (
            lambda value: value["domain_bindings"][0].update(
                material_name="wrong"
            ),
            "FBS_DOMAIN_ALIAS_MISSING",
        ),
        (
            lambda value: value["domain_bindings"][0].update(
                nodes_per_element=10
            ),
            "FBS_ELEMENT_NODE_CARDINALITY_MISMATCH",
        ),
    ):
        request_path, response_path = worker_request(
            tmp_path / f"{len(list(tmp_path.iterdir())):02d}-{error}"
        )
        value = json.loads(request_path.read_text(encoding="utf-8"))
        mutation(value)
        if error.startswith("FBS_"):
            model_path = Path(value["model_evidence"]["path"])
            model = json.loads(model_path.read_text(encoding="utf-8"))
            model["domain_bindings"] = value["domain_bindings"]
            model_path.write_text(
                json.dumps(model, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            value["model_evidence"]["bytes"] = model_path.stat().st_size
            value["model_evidence"]["sha256"] = hashlib.sha256(
                model_path.read_bytes()
            ).hexdigest().upper()
        request_path.write_text(json.dumps(value), encoding="utf-8")
        monkeypatch.setattr(fbs_worker, "_loaded_dll_manifest", lambda: [])
        assert fbs_worker.main(["--request", str(request_path), "--response", str(response_path)]) == 2
        evidence = json.loads(response_path.read_text(encoding="utf-8"))
        assert evidence["status"] == "rejected"
        assert evidence["error_code"] == error

def test_worker_subprocess_isolated_command_has_no_package_import_dependency(tmp_path):
    request_path, response_path = worker_request(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-I", "-B", "-S",
            str(Path(fbs_worker.__file__).resolve()),
            "--request", str(request_path),
            "--response", str(response_path),
        ],
        env={
            "SYSTEMROOT": __import__("os").environ["SYSTEMROOT"],
            "WINDIR": __import__("os").environ["WINDIR"],
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(response_path.read_text(encoding="utf-8"))["status"] == "accepted"

def test_kinematic_time_matching_uses_request_tolerance(tmp_path, monkeypatch):
    request_path, response_path = worker_request(tmp_path)
    value = json.loads(request_path.read_text(encoding="utf-8"))
    value["time_abs_tol"] = 1.0e-3
    for item in value["kinematic_checks"][0]["expected_by_time"]:
        item["time"] += 5.0e-4
    request_path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(fbs_worker, "_loaded_dll_manifest", lambda: [])
    assert fbs_worker.main(
        ["--request", str(request_path), "--response", str(response_path)]
    ) == 0
```

- [ ] **Step 4: Run protocol/worker tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_fbs_protocol.py apps/febio_cae_harness/tests/unit/test_fbs_worker.py -q
```

Expected: collection fails with `ImportError: cannot import name 'FbsRuntime' from 'febio_cae_harness.fbs_protocol'`.

- [ ] **Step 5: Extend the protocol module with immutable request/evidence types**

```python
# append to fbs_protocol.py
import math
from typing import Any

from febio_cae_harness.response import EvidenceRecord
from febio_cae_harness.schema import validate_schema

@dataclass(frozen=True)
class FbsRuntime:
    python_exe: Path
    fbs_module: Path
    febio_bin: Path
    expected_runtime_tree_sha256: str
    expected_loaded_dll_manifest_sha256: str

@dataclass(frozen=True)
class XpltRequest:
    xplt_path: Path
    xplt_bytes: int
    xplt_sha256: str
    model_evidence_path: Path
    model_evidence_bytes: int
    model_evidence_sha256: str
    expected_times: tuple[float, ...]
    allow_zero_state: bool
    allow_unrequested_states: bool
    time_abs_tol: float
    populations: tuple[dict[str, object], ...]
    domain_bindings: tuple[dict[str, object], ...]
    fields: tuple[dict[str, object], ...]
    initial_coordinates: tuple[dict[str, object], ...]
    expected_connectivity_signature_sha256: str | None
    expected_initial_coordinate_signature_sha256: str
    kinematic_checks: tuple[dict[str, object], ...]

    @classmethod
    def from_model_evidence(
        cls,
        xplt_path: Path,
        model_evidence_path: Path,
        *,
        expected_times: tuple[float, ...],
        allow_zero_state: bool,
        allow_unrequested_states: bool,
        time_abs_tol: float,
        populations: tuple[dict[str, object], ...],
        fields: tuple[dict[str, object], ...],
        initial_coordinates: tuple[dict[str, object], ...],
        kinematic_checks: tuple[dict[str, object], ...],
    ) -> XpltRequest:
        resolved = xplt_path.resolve(strict=True)
        model_path = model_evidence_path.resolve(strict=True)
        if model_path != (resolved.parent.parent / "model-evidence.json"):
            raise ValueError("FBS_MODEL_EVIDENCE_PATH_INVALID")
        model = json.loads(model_path.read_text(encoding="utf-8"))
        validate_schema("model-evidence", model)
        if len(initial_coordinates) > 32:
            raise ValueError("FBS_COORDINATE_SPOT_CHECK_LIMIT_EXCEEDED")
        return cls(
            xplt_path=resolved,
            xplt_bytes=resolved.stat().st_size,
            xplt_sha256=sha256_file(resolved),
            model_evidence_path=model_path,
            model_evidence_bytes=model_path.stat().st_size,
            model_evidence_sha256=sha256_file(model_path),
            expected_times=expected_times,
            allow_zero_state=allow_zero_state,
            allow_unrequested_states=allow_unrequested_states,
            time_abs_tol=time_abs_tol,
            populations=populations,
            domain_bindings=tuple(
                dict(item) for item in model["domain_bindings"]
            ),
            fields=fields,
            initial_coordinates=initial_coordinates,
            expected_connectivity_signature_sha256=str(
                model["connectivity_signature_sha256"]
            ),
            expected_initial_coordinate_signature_sha256=(
                str(model["initial_coordinate_signature"]["sha256"])
            ),
            kinematic_checks=kinematic_checks,
        )

    def semantic_dict(self) -> dict[str, object]:
        return {
            "xplt": {
                "path": str(self.xplt_path),
                "bytes": self.xplt_bytes,
                "sha256": self.xplt_sha256,
            },
            "model_evidence": {
                "path": str(self.model_evidence_path),
                "bytes": self.model_evidence_bytes,
                "sha256": self.model_evidence_sha256,
            },
            "expected_times": list(self.expected_times),
            "allow_zero_state": self.allow_zero_state,
            "allow_unrequested_states": self.allow_unrequested_states,
            "time_abs_tol": self.time_abs_tol,
            "populations": list(self.populations),
            "domain_bindings": list(self.domain_bindings),
            "fields": list(self.fields),
            "initial_coordinates": list(self.initial_coordinates),
            "expected_connectivity_signature_sha256": (
                self.expected_connectivity_signature_sha256
            ),
            "expected_initial_coordinate_signature_sha256": (
                self.expected_initial_coordinate_signature_sha256
            ),
            "kinematic_checks": list(self.kinematic_checks),
        }

    @property
    def request_sha256(self) -> str:
        encoded = json.dumps(
            self.semantic_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest().upper()

    def worker_dict(self, runtime: FbsRuntime) -> dict[str, object]:
        value = self.semantic_dict()
        return {
            "schema_version": 1,
            "request_id": self.request_sha256,
            **value,
            "fbs_module": str(runtime.fbs_module),
            "febio_bin": str(runtime.febio_bin),
        }

@dataclass(frozen=True)
class XpltEvidence:
    payload: dict[str, object]

    @property
    def kind(self) -> str:
        return str(self.payload["kind"])

    @property
    def status(self) -> str:
        return str(self.payload["status"])

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)

    def to_record(self) -> EvidenceRecord:
        payload = self.to_dict()
        kind = str(payload.pop("kind"))
        return EvidenceRecord(kind=kind, data=payload)

def match_target_states(
    actual: tuple[float, ...],
    expected: tuple[float, ...],
    tolerance: float,
    allow_unrequested_states: bool,
) -> tuple[tuple[int, int], ...]:
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("STATE_TIME_TOLERANCE_INVALID")
    matches: list[tuple[int, int]] = []
    matched_actual: set[int] = set()
    for expected_index, target in enumerate(expected):
        candidates = [
            actual_index
            for actual_index, value in enumerate(actual)
            if math.isclose(value, target, rel_tol=0.0, abs_tol=tolerance)
        ]
        if not candidates:
            raise ValueError(f"MISSING_TARGET_STATE:{target}")
        if len(candidates) != 1:
            raise ValueError(f"AMBIGUOUS_TARGET_STATE:{target}")
        actual_index = candidates[0]
        if actual_index in matched_actual:
            raise ValueError(f"AMBIGUOUS_TARGET_STATE:{target}")
        matched_actual.add(actual_index)
        matches.append((expected_index, actual_index))
    if not allow_unrequested_states and len(matched_actual) != len(actual):
        raise ValueError("UNREQUESTED_STATE")
    return tuple(matches)

def loaded_dll_manifest_sha256(records: list[dict[str, object]]) -> str:
    lines = [
        f"{record['path']}\t{record['bytes']}\t{record['sha256']}"
        for record in sorted(records, key=lambda value: str(value["path"]))
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest().upper()
```

- [ ] **Step 6: Implement the standard-library-only worker**

```python
# fbs_worker.py
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import math
import os
import pathlib
import struct
import sys
from typing import Any

CONNECTIVITY_VERSION = "feb-fbs-connectivity-signature-v1"
DOMAIN_CONNECTIVITY_SIGNATURE_VERSION = "feb-domain-connectivity-signature-v1"
INITIAL_COORDINATE_SIGNATURE_VERSION = "feb-node-coordinate-f32-v1"

class EvidenceError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail

def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest().upper()

def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()

def _verify_model_evidence_binding(value: dict[str, object]) -> None:
    record = value["model_evidence"]
    path = pathlib.Path(record["path"]).resolve(strict=True)
    if path.stat().st_size != int(record["bytes"]):
        raise EvidenceError("MODEL_EVIDENCE_BYTES_MISMATCH", str(path))
    if _sha256(path) != str(record["sha256"]):
        raise EvidenceError("MODEL_EVIDENCE_HASH_MISMATCH", str(path))
    model = json.loads(path.read_text(encoding="utf-8"))
    if (
        model.get("kind") != "model-evidence"
        or model.get("domain_bindings") != value["domain_bindings"]
        or model.get("connectivity_signature_sha256")
        != value["expected_connectivity_signature_sha256"]
        or model.get("initial_coordinate_signature", {}).get("sha256")
        != value["expected_initial_coordinate_signature_sha256"]
    ):
        raise EvidenceError("MODEL_EVIDENCE_REQUEST_BINDING_MISMATCH", str(path))

def _write_create_new(path: pathlib.Path, value: object) -> None:
    encoded = (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def _field_name(field: object) -> str:
    value = getattr(field, "name")
    return str(value() if callable(value) else value)

def _vec3(value: object) -> tuple[float, float, float]:
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        return float(value.x), float(value.y), float(value.z)
    return float(value[0]), float(value[1]), float(value[2])

def _scalar(value: object) -> float:
    raw = getattr(value, "val", value)
    if isinstance(raw, (list, tuple)):
        if len(raw) != 1:
            raise EvidenceError("FIELD_VALUE_NOT_SCALAR", repr(raw))
        raw = raw[0]
    result = float(raw)
    if not math.isfinite(result):
        raise EvidenceError("FIELD_VALUE_NOT_FINITE", repr(raw))
    return result

def _loaded_dll_manifest() -> list[dict[str, object]]:
    if os.name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    modules = (ctypes.c_void_p * 4096)()
    needed = ctypes.c_ulong()
    process = kernel32.GetCurrentProcess()
    if not psapi.EnumProcessModulesEx(
        process, modules, ctypes.sizeof(modules), ctypes.byref(needed), 0x03
    ):
        raise ctypes.WinError(ctypes.get_last_error(), "EnumProcessModulesEx")
    windows_root = pathlib.Path(os.environ["WINDIR"]).resolve()
    paths: set[pathlib.Path] = set()
    for index in range(needed.value // ctypes.sizeof(ctypes.c_void_p)):
        buffer = ctypes.create_unicode_buffer(32768)
        if psapi.GetModuleFileNameExW(process, modules[index], buffer, len(buffer)):
            path = pathlib.Path(buffer.value).resolve(strict=True)
            try:
                path.relative_to(windows_root)
            except ValueError:
                paths.add(path)
    return [
        {"path": path.as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(paths, key=lambda value: str(value).casefold())
    ]

def _match_states(
    actual: list[float],
    expected: list[float],
    tolerance: float,
    allow_unrequested: bool,
) -> list[tuple[int, int]]:
    matched: set[int] = set()
    result: list[tuple[int, int]] = []
    for expected_index, target in enumerate(expected):
        candidates = [
            index
            for index, value in enumerate(actual)
            if math.isclose(value, target, rel_tol=0.0, abs_tol=tolerance)
        ]
        if not candidates:
            raise EvidenceError("MISSING_TARGET_STATE", str(target))
        if len(candidates) != 1 or candidates[0] in matched:
            raise EvidenceError("AMBIGUOUS_TARGET_STATE", str(target))
        matched.add(candidates[0])
        result.append((expected_index, candidates[0]))
    if not allow_unrequested and len(matched) != len(actual):
        raise EvidenceError("UNREQUESTED_STATE", repr(actual))
    return result

def _inventory(model: object, mesh: object) -> tuple[list[str], list[dict[str, object]]]:
    manager = model.GetDataManager()
    fields = [_field_name(manager.DataField(index)) for index in range(manager.DataFields())]
    partitions: list[dict[str, object]] = []
    for partition_index in range(mesh.MeshPartitions()):
        partition = mesh.MeshPartition(partition_index)
        name_value = getattr(partition, "name", "")
        partition_name = str(name_value() if callable(name_value) else name_value)
        material_name_value = model.Material(partition.GetMatID()).name
        material_name = str(
            material_name_value() if callable(material_name_value) else material_name_value
        )
        element_indices = [int(value) for value in partition.ElementList()]
        elements: list[dict[str, object]] = []
        node_indices: set[int] = set()
        for element_index in element_indices:
            element = mesh.Element(element_index)
            connectivity_indices = [
                int(element.Node(local)) for local in range(element.Nodes())
            ]
            node_indices.update(connectivity_indices)
            elements.append(
                {
                    "element_index": element_index,
                    "element_id": int(element.GetID()),
                    "node_count": int(element.Nodes()),
                    "connectivity_node_indices": connectivity_indices,
                    "connectivity_node_ids": [
                        int(mesh.Node(index).GetID()) for index in connectivity_indices
                    ],
                }
            )
        ordered_nodes = sorted(node_indices)
        partitions.append(
            {
                "partition_index": partition_index,
                "partition_name": partition_name,
                "material_name": material_name,
                "element_indices": element_indices,
                "element_ids": [int(mesh.Element(index).GetID()) for index in element_indices],
                "node_indices": ordered_nodes,
                "node_ids": [int(mesh.Node(index).GetID()) for index in ordered_nodes],
                "elements": elements,
            }
        )
    return sorted(fields), partitions

def _partition_signature(partition: dict[str, object]) -> str:
    core = {
        "version": DOMAIN_CONNECTIVITY_SIGNATURE_VERSION,
        "element_count": len(partition["elements"]),
        "referenced_node_count": len(partition["node_ids"]),
        "elements": [
            {
                "element_id": element["element_id"],
                "connectivity_node_ids": element["connectivity_node_ids"],
            }
            for element in sorted(
                partition["elements"],
                key=lambda item: (
                    int(item["element_id"]),
                    tuple(item["connectivity_node_ids"]),
                ),
            )
        ],
    }
    return _canonical_sha256(core)

def _alias_partitions(
    partitions: list[dict[str, object]],
    bindings: list[dict[str, object]],
) -> list[dict[str, object]]:
    if (
        len({str(item["domain_alias"]) for item in bindings})
        != len(bindings)
    ):
        raise EvidenceError("FEB_DOMAIN_ALIAS_DUPLICATE", "")
    used_aliases: set[str] = set()
    for partition in partitions:
        signature = _partition_signature(partition)
        candidates = [
            binding
            for binding in bindings
            if binding["connectivity_signature_version"]
            == DOMAIN_CONNECTIVITY_SIGNATURE_VERSION
            and binding["material_name"] == partition["material_name"]
            and int(binding["element_count"]) == len(partition["element_ids"])
            and int(binding["referenced_node_count"]) == len(partition["node_ids"])
            and binding["connectivity_signature_sha256"] == signature
        ]
        if not candidates:
            raise EvidenceError(
                "FBS_DOMAIN_ALIAS_MISSING",
                f"partition={partition['partition_index']}",
            )
        if len(candidates) != 1:
            raise EvidenceError(
                "FBS_DOMAIN_ALIAS_AMBIGUOUS",
                f"partition={partition['partition_index']}",
            )
        binding = candidates[0]
        alias = str(binding["domain_alias"])
        if alias in used_aliases:
            raise EvidenceError("FBS_DOMAIN_ALIAS_COLLISION", alias)
        used_aliases.add(alias)
        expected_nodes = int(binding["nodes_per_element"])
        if any(
            int(element["node_count"]) != expected_nodes
            for element in partition["elements"]
        ):
            raise EvidenceError("FBS_ELEMENT_NODE_CARDINALITY_MISMATCH", alias)
        partition["domain_alias"] = alias
        partition["domain_connectivity_signature_sha256"] = signature
        for element in partition["elements"]:
            element["element_type"] = str(binding["element_type"]).upper()
    expected_aliases = {str(item["domain_alias"]) for item in bindings}
    if used_aliases != expected_aliases:
        raise EvidenceError("FBS_UNBOUND_DOMAIN_ALIAS", repr(expected_aliases - used_aliases))
    return partitions

def _populations(
    requests: list[dict[str, object]],
    partitions: list[dict[str, object]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    occupied_elements: set[int] = set()
    for request in requests:
        selected = [
            item
            for item in partitions
            if item["domain_alias"] in request["domain_aliases"]
        ]
        if len(selected) != len(request["domain_aliases"]):
            raise EvidenceError("POPULATION_PARTITION_MATCH", str(request["name"]))
        element_indices = sorted({
            value for item in selected for value in item["element_indices"]
        })
        node_indices = sorted({value for item in selected for value in item["node_indices"]})
        if not element_indices or not node_indices:
            raise EvidenceError("POPULATION_EMPTY", str(request["name"]))
        if request.get("require_disjoint") and occupied_elements.intersection(element_indices):
            raise EvidenceError("POPULATION_OVERLAP", str(request["name"]))
        occupied_elements.update(element_indices)
        element_id_by_index = {
            element["element_index"]: element["element_id"]
            for item in selected
            for element in item["elements"]
        }
        node_id_by_index = {
            index: node_id
            for item in selected
            for index, node_id in zip(item["node_indices"], item["node_ids"])
        }
        results.append(
            {
                "name": request["name"],
                "domain_aliases": sorted(
                    {str(item["domain_alias"]) for item in selected},
                    key=str.casefold,
                ),
                "material_names": sorted(
                    {str(item["material_name"]) for item in selected},
                    key=str.casefold,
                ),
                "element_indices": element_indices,
                "element_ids": [element_id_by_index[index] for index in element_indices],
                "node_indices": node_indices,
                "node_ids": [node_id_by_index[index] for index in node_indices],
            }
        )
    return results

def _resolve_operation(fbs: object, requested: object) -> int:
    if isinstance(requested, int) and not isinstance(requested, bool):
        return requested
    if requested == "MAT3DS.EFFECTIVE":
        try:
            return int(fbs.post.MAT3DS.EFFECTIVE)
        except (AttributeError, TypeError, ValueError) as error:
            raise EvidenceError(
                "FBS_OPERATION_ENUM_UNAVAILABLE",
                "MAT3DS.EFFECTIVE",
            ) from error
    raise EvidenceError("FBS_OPERATION_INVALID", str(requested))

def _evaluate_fields(
    fbs: object,
    model: object,
    field_inventory: list[str],
    requests: list[dict[str, object]],
    populations: list[dict[str, object]],
    state_matches: list[tuple[int, int]],
    actual_times: list[float],
) -> list[dict[str, object]]:
    population_by_name = {item["name"]: item for item in populations}
    request_keys = [
        (
            str(item["name"]),
            str(item["association"]),
            item["operation"],
            str(item["population"]),
        )
        for item in requests
    ]
    if len(set(request_keys)) != len(request_keys):
        raise EvidenceError("FIELD_REQUEST_DUPLICATE", repr(request_keys))
    results: list[dict[str, object]] = []
    for request in requests:
        name = str(request["name"])
        if name not in field_inventory:
            raise EvidenceError("FIELD_NOT_IN_INVENTORY", name)
        association = str(request["association"])
        if association not in {"nodeData", "elemData"}:
            raise EvidenceError("FIELD_ASSOCIATION_INVALID", association)
        population = population_by_name[str(request["population"])]
        selected_indices = (
            population["node_indices"] if association == "nodeData"
            else population["element_indices"]
        )
        state_records: list[dict[str, object]] = []
        field = model.GetDataField(name)
        operation = _resolve_operation(fbs, request["operation"])
        for expected_index, actual_index in state_matches:
            evaluated = model.Evaluate(field, operation, actual_index)
            collection = getattr(evaluated, association, ())
            if not collection or max(selected_indices) >= len(collection):
                raise EvidenceError("FIELD_COLLECTION_EMPTY", f"{name}:{association}")
            values = [_scalar(collection[index]) for index in selected_indices]
            state_records.append(
                {
                    "expected_index": expected_index,
                    "state_index": actual_index,
                    "state_time": actual_times[actual_index],
                    "value_count": len(values),
                    "minimum": min(values),
                    "maximum": max(values),
                    "values_sha256": _canonical_sha256(values),
                    "all_finite": True,
                }
            )
        results.append(
            {
                "name": name,
                "operation": operation,
                "operation_source": request["operation"],
                "association": association,
                "population": population["name"],
                "selected_indices_kind": (
                    "node_indices" if association == "nodeData" else "element_indices"
                ),
                "states": state_records,
            }
        )
    return results

def _coordinate_check(
    mesh: object,
    requested: list[dict[str, object]],
) -> dict[str, object]:
    index_by_id = {int(mesh.Node(index).GetID()): index for index in range(mesh.Nodes())}
    maximum_raw_error = 0.0
    checked = 0
    for record in requested:
        node_id = int(record["node_id"])
        if node_id not in index_by_id:
            raise EvidenceError("INITIAL_COORDINATE_NODE_MISSING", str(node_id))
        actual = _vec3(mesh.Node(index_by_id[node_id]).r)
        for decimal_text, actual_value in zip(record["decimal"], actual):
            decimal_value = float(str(decimal_text))
            expected_bits = struct.pack("<f", decimal_value)
            actual_bits = struct.pack("<f", actual_value)
            if expected_bits != actual_bits:
                raise EvidenceError("INITIAL_COORDINATE_FLOAT32_MISMATCH", str(node_id))
            maximum_raw_error = max(maximum_raw_error, abs(decimal_value - actual_value))
            checked += 1
    return {
        "checked_component_count": checked,
        "all_binary32_bits_equal": True,
        "maximum_raw_decimal_error": maximum_raw_error,
    }

def _all_node_coordinate_signature(mesh: object) -> dict[str, object]:
    records: list[tuple[int, tuple[float, float, float]]] = []
    seen_ids: set[int] = set()
    for index in range(mesh.Nodes()):
        node = mesh.Node(index)
        node_id = int(node.GetID())
        if node_id in seen_ids:
            raise EvidenceError("DUPLICATE_NODE_ID", str(node_id))
        seen_ids.add(node_id)
        coordinates = _vec3(node.r)
        if node_id <= 0 or node_id > (2**64 - 1):
            raise EvidenceError("NODE_ID_OUT_OF_UINT64_RANGE", str(node_id))
        if not all(math.isfinite(value) for value in coordinates):
            raise EvidenceError("INITIAL_COORDINATE_NONFINITE", str(node_id))
        records.append((node_id, coordinates))
    encoded = bytearray()
    try:
        for node_id, coordinates in sorted(records):
            encoded.extend(struct.pack(">Qfff", node_id, *coordinates))
    except (OverflowError, struct.error) as error:
        raise EvidenceError(
            "INITIAL_COORDINATE_BINARY32_OVERFLOW",
            type(error).__name__,
        ) from error
    return {
        "version": INITIAL_COORDINATE_SIGNATURE_VERSION,
        "ordering": "ascending-node-id",
        "node_id_encoding": "uint64-big-endian",
        "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
        "node_count": len(records),
        "component_count": len(records) * 3,
        "sha256": hashlib.sha256(encoded).hexdigest().upper(),
    }

def _connectivity_core(
    mesh: object,
    partitions: list[dict[str, object]],
) -> dict[str, object]:
    elements = [
        {
            "domain": partition["domain_alias"],
            "element_id": element["element_id"],
            "element_type": str(element["element_type"]).upper(),
            "connectivity_node_ids": element["connectivity_node_ids"],
        }
        for partition in partitions
        for element in partition["elements"]
    ]
    elements.sort(
        key=lambda item: (
            str(item["domain"]).casefold(),
            int(item["element_id"]),
            str(item["element_type"]),
            tuple(item["connectivity_node_ids"]),
        )
    )
    return {
        "version": CONNECTIVITY_VERSION,
        "node_count": int(mesh.Nodes()),
        "element_count": int(mesh.Elements()),
        "node_ids": sorted(
            int(mesh.Node(index).GetID()) for index in range(mesh.Nodes())
        ),
        "elements": elements,
    }

def _kinematic_checks(
    model: object,
    requests: list[dict[str, object]],
    populations: list[dict[str, object]],
    state_matches: list[tuple[int, int]],
    actual_times: list[float],
    time_abs_tol: float,
) -> list[dict[str, object]]:
    population_by_name = {item["name"]: item for item in populations}
    results: list[dict[str, object]] = []
    for request in requests:
        population = population_by_name[str(request["population"])]
        expected_by_time = {
            float(item["time"]): item for item in request["expected_by_time"]
        }
        field = model.GetDataField(str(request["field"]))
        maximum_vector_error = 0.0
        maximum_magnitude_error = 0.0
        maximum_norm_consistency_error = 0.0
        checked_node_states = 0
        for _, state_index in state_matches:
            time_value = actual_times[state_index]
            candidates = [
                item
                for target, item in expected_by_time.items()
                if math.isclose(
                    target,
                    time_value,
                    rel_tol=0.0,
                    abs_tol=time_abs_tol,
                )
            ]
            if len(candidates) != 1:
                raise EvidenceError("KINEMATIC_TARGET_STATE", str(time_value))
            expected = candidates[0]
            component_collections = [
                model.Evaluate(field, operation, state_index).nodeData
                for operation in (0, 1, 2)
            ]
            magnitude_collection = model.Evaluate(field, 6, state_index).nodeData
            for node_index in population["node_indices"]:
                vector = tuple(
                    _scalar(collection[node_index]) for collection in component_collections
                )
                magnitude = _scalar(magnitude_collection[node_index])
                calculated = math.sqrt(sum(value * value for value in vector))
                maximum_vector_error = max(
                    maximum_vector_error,
                    max(abs(value - target) for value, target in zip(vector, expected["vector"])),
                )
                maximum_magnitude_error = max(
                    maximum_magnitude_error,
                    abs(magnitude - float(expected["magnitude"])),
                )
                maximum_norm_consistency_error = max(
                    maximum_norm_consistency_error,
                    abs(magnitude - calculated),
                )
                checked_node_states += 1
        tolerance = float(request["abs_tol"])
        if maximum_vector_error > tolerance:
            raise EvidenceError("RIGID_VECTOR_MISMATCH", str(maximum_vector_error))
        if maximum_magnitude_error > tolerance:
            raise EvidenceError("RIGID_MAGNITUDE_MISMATCH", str(maximum_magnitude_error))
        if maximum_norm_consistency_error > tolerance:
            raise EvidenceError(
                "RIGID_NORM_CONSISTENCY_MISMATCH",
                str(maximum_norm_consistency_error),
            )
        results.append(
            {
                "field": request["field"],
                "population": request["population"],
                "operations": [0, 1, 2, 6],
                "node_count": len(population["node_indices"]),
                "full_vector_checked": True,
                "operation6_consistent": True,
                "maximum_vector_error": maximum_vector_error,
                "maximum_magnitude_error": maximum_magnitude_error,
                "maximum_norm_consistency_error": (
                    maximum_norm_consistency_error
                ),
                "all_nodes_all_target_states_checked": (
                    checked_node_states
                    == len(population["node_indices"]) * len(state_matches)
                ),
            }
        )
    return results

def inspect(value: dict[str, object]) -> dict[str, object]:
    _verify_model_evidence_binding(value)
    xplt = pathlib.Path(value["xplt"]["path"]).resolve(strict=True)
    if xplt.stat().st_size != int(value["xplt"]["bytes"]):
        raise EvidenceError("XPLT_BYTES_MISMATCH", str(xplt))
    if _sha256(xplt) != str(value["xplt"]["sha256"]):
        raise EvidenceError("XPLT_HASH_MISMATCH", str(xplt))
    module = pathlib.Path(value["fbs_module"]).resolve(strict=True)
    febio_bin = pathlib.Path(value["febio_bin"]).resolve(strict=True)
    sys.path.insert(0, str(module.parent))
    sys.modules.pop("fbs", None)
    with os.add_dll_directory(str(febio_bin)):
        fbs = importlib.import_module("fbs")
        model = fbs.post.ReadPlotFile(str(xplt))
    if model is None:
        raise EvidenceError("FBS_READER_RETURNED_NONE", str(xplt))
    state_count = int(model.States())
    if state_count == 0 and not value["allow_zero_state"]:
        raise EvidenceError("ZERO_STATE_XPLT", str(xplt))
    actual_times = [float(model.State(index).time) for index in range(state_count)]
    state_matches = _match_states(
        actual_times,
        [float(item) for item in value["expected_times"]],
        float(value["time_abs_tol"]),
        bool(value["allow_unrequested_states"]),
    )
    matched_actual_indices = {right for _, right in state_matches}
    maximum_target_time_abs_error = max(
        (
            abs(
                float(value["expected_times"][left])
                - actual_times[right]
            )
            for left, right in state_matches
        ),
        default=0.0,
    )
    zero_state_count = sum(
        math.isclose(
            time_value,
            0.0,
            rel_tol=0.0,
            abs_tol=float(value["time_abs_tol"]),
        )
        for time_value in actual_times
    )
    mesh = model.GetFEMesh(0)
    if int(mesh.Nodes()) <= 0 or int(mesh.Elements()) <= 0:
        raise EvidenceError("EMPTY_FBS_MESH", str(xplt))
    field_inventory, partitions = _inventory(model, mesh)
    partitions = _alias_partitions(partitions, value["domain_bindings"])
    populations = _populations(value["populations"], partitions)
    connectivity_core = _connectivity_core(mesh, partitions)
    connectivity_hash = _canonical_sha256(connectivity_core)
    expected_connectivity = value.get("expected_connectivity_signature_sha256")
    if expected_connectivity is not None and expected_connectivity != connectivity_hash:
        raise EvidenceError("FEB_FBS_CONNECTIVITY_MISMATCH", connectivity_hash)
    coordinate_signature = _all_node_coordinate_signature(mesh)
    if (
        coordinate_signature["sha256"]
        != value["expected_initial_coordinate_signature_sha256"]
    ):
        raise EvidenceError(
            "INITIAL_COORDINATE_SIGNATURE_MISMATCH",
            str(coordinate_signature["sha256"]),
        )
    fields = _evaluate_fields(
        fbs,
        model,
        field_inventory,
        value["fields"],
        populations,
        state_matches,
        actual_times,
    )
    return {
        "kind": "fbs-verification",
        "status": "accepted",
        "request_id": value["request_id"],
        "model_evidence_sha256": value["model_evidence"]["sha256"],
        "xplt": {
            "path": str(xplt),
            "bytes": xplt.stat().st_size,
            "sha256": _sha256(xplt),
        },
        "reader": {
            "python_version": sys.version.split()[0],
            "fbs_version": str(getattr(fbs, "__version__", "unreported")),
            "loaded_dll_manifest": _loaded_dll_manifest(),
        },
        "state_count": state_count,
        "state_times": actual_times,
        "nonzero_target_states": sum(
            not math.isclose(
                float(value["expected_times"][left]),
                0.0,
                rel_tol=0.0,
                abs_tol=float(value["time_abs_tol"]),
            )
            for left, _ in state_matches
        ),
        "zero_state_count": zero_state_count,
        "unrequested_state_count": (
            state_count - len(matched_actual_indices)
        ),
        "final_time": actual_times[-1],
        "maximum_target_time_abs_error": maximum_target_time_abs_error,
        "state_matches": [
            {"expected_index": left, "state_index": right}
            for left, right in state_matches
        ],
        "field_inventory": field_inventory,
        "mesh": {
            "node_count": int(mesh.Nodes()),
            "element_count": int(mesh.Elements()),
            "partition_count": int(mesh.MeshPartitions()),
        },
        "partitions": partitions,
        "populations": populations,
        "connectivity": {
            "version": CONNECTIVITY_VERSION,
            "node_count": int(mesh.Nodes()),
            "element_count": int(mesh.Elements()),
            "signature_sha256": connectivity_hash,
        },
        "initial_coordinate_check": _coordinate_check(
            mesh, value["initial_coordinates"]
        ),
        "initial_coordinate_signature": coordinate_signature,
        "fields": fields,
        "kinematic_checks": _kinematic_checks(
            model,
            value["kinematic_checks"],
            populations,
            state_matches,
            actual_times,
            float(value["time_abs_tol"]),
        ),
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    arguments = parser.parse_args(argv)
    response_path = pathlib.Path(arguments.response)
    try:
        request = json.loads(pathlib.Path(arguments.request).read_text(encoding="utf-8"))
        response = inspect(request)
        exit_code = 0
    except EvidenceError as error:
        response = {
            "kind": "fbs-verification",
            "status": "rejected",
            "error_code": error.code,
            "detail": error.detail,
        }
        exit_code = 2
    except BaseException as error:
        response = {
            "kind": "fbs-verification",
            "status": "rejected",
            "error_code": "FBS_WORKER_ERROR",
            "detail": f"{type(error).__name__}: {error}",
        }
        exit_code = 3
    _write_create_new(response_path, response)
    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Write client tests for the exact isolated command, profile binding, crash, and timeout**

```python
# tests/integration/test_fbs_client.py
import hashlib
import json
import subprocess
from pathlib import Path

import jsonschema
import pytest

import febio_cae_harness.fbs_client as client
from febio_cae_harness.fbs_protocol import (
    FbsRuntime,
    XpltRequest,
    loaded_dll_manifest_sha256,
)
from tests.unit.test_fbs_protocol import write_model_evidence

def setup_request(tmp_path: Path) -> tuple[XpltRequest, FbsRuntime]:
    attempt = tmp_path / "attempt"
    solver = attempt / "solver"
    model_path = write_model_evidence(attempt)
    xplt = solver / "solver.xplt"
    xplt.write_bytes(b"XPLT")
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    python_exe = runtime_root / "python.exe"
    fbs_module = runtime_root / "fbs.pyd"
    febio_bin = tmp_path / "FEBioStudio" / "bin"
    febio_bin.mkdir(parents=True)
    python_exe.write_bytes(b"python")
    fbs_module.write_bytes(b"fbs")
    request = XpltRequest.from_model_evidence(
        xplt,
        model_path,
        expected_times=(1.0,),
        allow_zero_state=False,
        allow_unrequested_states=False,
        time_abs_tol=1.0e-6,
        populations=(),
        fields=(),
        initial_coordinates=(),
        kinematic_checks=(),
    )
    runtime = FbsRuntime(
        python_exe=python_exe,
        fbs_module=fbs_module,
        febio_bin=febio_bin,
        expected_runtime_tree_sha256="A" * 64,
        expected_loaded_dll_manifest_sha256=loaded_dll_manifest_sha256([]),
    )
    return request, runtime

def accepted_response(request: XpltRequest) -> dict[str, object]:
    return {
        "kind": "fbs-verification",
        "status": "accepted",
        "request_id": request.request_sha256,
        "model_evidence_sha256": request.model_evidence_sha256,
        "xplt": {
            "path": str(request.xplt_path),
            "bytes": request.xplt_bytes,
            "sha256": request.xplt_sha256,
        },
        "reader": {
            "python_version": "3.13.5",
            "fbs_version": "test",
            "loaded_dll_manifest": [],
        },
        "state_count": 1,
        "state_times": [1.0],
        "nonzero_target_states": 1,
        "zero_state_count": 0,
        "unrequested_state_count": 0,
        "final_time": 1.0,
        "maximum_target_time_abs_error": 0.0,
        "state_matches": [{"expected_index": 0, "state_index": 0}],
        "field_inventory": [],
        "mesh": {"node_count": 1, "element_count": 1, "partition_count": 0},
        "partitions": [],
        "populations": [],
        "connectivity": {
            "version": "feb-fbs-connectivity-signature-v1",
            "node_count": 1,
            "element_count": 1,
            "signature_sha256": request.expected_connectivity_signature_sha256,
        },
        "initial_coordinate_check": {
            "checked_component_count": 0,
            "all_binary32_bits_equal": True,
            "maximum_raw_decimal_error": 0.0,
        },
        "initial_coordinate_signature": {
            "version": "feb-node-coordinate-f32-v1",
            "ordering": "ascending-node-id",
            "node_id_encoding": "uint64-big-endian",
            "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
            "node_count": 1,
            "component_count": 3,
            "sha256": request.expected_initial_coordinate_signature_sha256,
        },
        "fields": [],
        "kinematic_checks": [],
    }

def test_build_worker_command_is_exact_and_has_no_shell(tmp_path):
    request, runtime = setup_request(tmp_path)
    worker = tmp_path / "fbs_worker.py"
    request_json = tmp_path / "request.json"
    response_json = tmp_path / "response.json"
    assert client.build_worker_command(runtime, worker, request_json, response_json) == [
        str(runtime.python_exe),
        "-I", "-B", "-S", str(worker),
        "--request", str(request_json),
        "--response", str(response_json),
    ]

def test_inspect_xplt_removes_python_paths_and_binds_loaded_dlls(
    tmp_path, monkeypatch
):
    request, runtime = setup_request(tmp_path)
    monkeypatch.setenv("PYTHONHOME", "poison")
    monkeypatch.setenv("PYTHONPATH", "poison")
    monkeypatch.setattr(client, "verify_runtime", lambda value: None)
    observed = {}

    def fake_run(command, **options):
        observed["command"] = command
        observed["options"] = options
        request_value = json.loads(Path(command[-3]).read_text(encoding="utf-8"))
        assert request_value["request_id"] == request.request_sha256
        Path(command[-1]).write_text(
            json.dumps(accepted_response(request)),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    evidence = client.inspect_xplt(request, runtime)
    assert evidence.kind == "fbs-verification"
    assert evidence.status == "accepted"
    assert evidence.payload["parent_pre_worker_xplt_sha256"] == request.xplt_sha256
    assert evidence.payload["worker_xplt_sha256"] == request.xplt_sha256
    assert evidence.payload["parent_post_worker_xplt_sha256"] == request.xplt_sha256
    jsonschema.validate(
        evidence.to_dict(),
        json.loads(client._resource_bytes("schemas/fbs-evidence.schema.json")),
    )
    record = evidence.to_record()
    assert set(record.to_payload()) == {"kind", "data"}
    assert "kind" not in record.data
    assert "PYTHONHOME" not in observed["options"]["env"]
    assert "PYTHONPATH" not in observed["options"]["env"]
    assert observed["options"]["shell"] is False
    assert observed["command"][1:5] == ["-I", "-B", "-S", observed["command"][4]]

def test_inspect_xplt_rejects_worker_xplt_identity_drift(tmp_path, monkeypatch):
    request, runtime = setup_request(tmp_path)
    monkeypatch.setattr(client, "verify_runtime", lambda value: None)

    def fake_run(command, **options):
        response = accepted_response(request)
        response["xplt"]["sha256"] = "0" * 64
        Path(command[-1]).write_text(json.dumps(response), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    with pytest.raises(
        client.FbsInspectionError,
        match="FBS_WORKER_XPLT_BINDING_DRIFT",
    ):
        client.inspect_xplt(request, runtime)

def test_nonzero_worker_cannot_return_accepted_evidence(tmp_path, monkeypatch):
    request, runtime = setup_request(tmp_path)
    monkeypatch.setattr(client, "verify_runtime", lambda value: None)

    def fake_run(command, **options):
        Path(command[-1]).write_text(
            json.dumps(accepted_response(request)),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 7, b"", b"worker error")

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    with pytest.raises(
        client.FbsInspectionError,
        match="FBS_WORKER_NONZERO_WITH_ACCEPTED_RESPONSE",
    ):
        client.inspect_xplt(request, runtime)

@pytest.mark.parametrize("failure", ("timeout", "crash"))
def test_timeout_or_native_crash_without_response_is_resource_error(
    tmp_path, monkeypatch, failure
):
    request, runtime = setup_request(tmp_path)
    monkeypatch.setattr(client, "verify_runtime", lambda value: None)

    def fake_run(command, **options):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return subprocess.CompletedProcess(command, 0xC0000005, b"", b"native crash")

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    expected = "FBS_WORKER_TIMEOUT" if failure == "timeout" else "FBS_WORKER_NO_RESPONSE"
    with pytest.raises(client.FbsInspectionError, match=expected):
        client.inspect_xplt(request, runtime)
```

- [ ] **Step 8: Add the opt-in real-runtime probe and verify the client RED**

```python
# tests/integration/test_fbs_real_runtime_contract.py
import json
import os
import subprocess
from pathlib import Path

import pytest

RUNTIME_ENV = "FEBIO_CAE_FBS_RUNTIME_ROOT"
XPLT_ENV = "FEBIO_CAE_FBS_CONTRACT_XPLT"
EXPECTATION_ENV = "FEBIO_CAE_FBS_CONTRACT_EXPECTATION"

PROBE = r"""
import importlib
import json
import os
import pathlib
import sys

runtime = pathlib.Path(sys.argv[1]).resolve(strict=True)
xplt = pathlib.Path(sys.argv[2]).resolve(strict=True)
profile = json.loads((runtime / "runtime.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(runtime))
with os.add_dll_directory(str(pathlib.Path(profile["febio_bin"]))):
    fbs = importlib.import_module("fbs")
    model = fbs.post.ReadPlotFile(str(xplt))
mesh = model.GetFEMesh(0)
partition_names = []
material_names = []
element_node_counts = []
partition_element_counts = []
partition_referenced_node_counts = []
for index in range(mesh.MeshPartitions()):
    partition = mesh.MeshPartition(index)
    raw_name = getattr(partition, "name", "")
    partition_names.append(str(raw_name() if callable(raw_name) else raw_name))
    raw_material = model.Material(partition.GetMatID()).name
    material_names.append(
        str(raw_material() if callable(raw_material) else raw_material)
    )
    element_indices = tuple(int(item) for item in partition.ElementList())
    partition_element_counts.append(len(element_indices))
    referenced_nodes = set()
    for element_index in element_indices:
        element = mesh.Element(element_index)
        referenced_nodes.update(
            int(element.Node(local))
            for local in range(element.Nodes())
        )
    partition_referenced_node_counts.append(len(referenced_nodes))
    first_element = element_indices[0]
    element_node_counts.append(int(mesh.Element(first_element).Nodes()))
stress = model.GetDataField("stress")
evaluated = model.Evaluate(
    stress,
    int(fbs.post.MAT3DS.EFFECTIVE),
    int(model.States()) - 1,
)
nonempty = [
    name
    for name in ("nodeData", "elemData", "faceData", "edgeData")
    if len(getattr(evaluated, name, ())) > 0
]
print(json.dumps({
    "effective": int(fbs.post.MAT3DS.EFFECTIVE),
    "p2": int(fbs.post.MAT3DS.P2),
    "partition_names": partition_names,
    "material_names": material_names,
    "element_has_type": hasattr(mesh.Element(0), "Type"),
    "element_node_counts": element_node_counts,
    "partition_element_counts": partition_element_counts,
    "partition_referenced_node_counts": partition_referenced_node_counts,
    "stress_nonempty_associations": nonempty,
}, sort_keys=True))
"""

def _required_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required for the opt-in real-FBS probe")
    return Path(value).resolve(strict=True)

def test_official_fbs_412_contract_matches_worker_assumptions():
    runtime = _required_path(RUNTIME_ENV)
    xplt = _required_path(XPLT_ENV)
    expectation_path = _required_path(EXPECTATION_ENV)
    expected = json.loads(expectation_path.read_text(encoding="utf-8"))
    assert set(expected) == {
        "partition_names",
        "material_names",
        "element_node_counts",
        "partition_element_counts",
        "partition_referenced_node_counts",
    }
    completed = subprocess.run(
        [
            str(runtime / "python.exe"),
            "-I", "-B", "-S", "-c", PROBE,
            str(runtime), str(xplt),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert value["effective"] == 6
    assert value["p2"] == 8
    for key in expected:
        assert value[key] == expected[key]
    assert value["element_has_type"] is False
    assert "elemData" in value["stress_nonempty_associations"]
    assert len(value["stress_nonempty_associations"]) > 1
```

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_fbs_client.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.fbs_client'`; Step 9 then implements that client.

- [ ] **Step 9: Implement runtime/profile verification and the Python 3.12 client**

```python
# fbs_client.py
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from importlib.resources import files
from pathlib import Path

import jsonschema

from febio_cae_harness.fbs_protocol import (
    FbsRuntime,
    XpltEvidence,
    XpltRequest,
    load_runtime_lock,
    loaded_dll_manifest_sha256,
    verify_runtime_tree,
)
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.jsonio import atomic_create_artifact

class FbsInspectionError(RuntimeError):
    """Official-FBS runtime or worker evidence failed closed."""

def build_worker_command(
    runtime: FbsRuntime,
    worker: Path,
    request_json: Path,
    response_json: Path,
) -> list[str]:
    return [
        str(runtime.python_exe),
        "-I", "-B", "-S", str(worker),
        "--request", str(request_json),
        "--response", str(response_json),
    ]

def _resource_bytes(relative_path: str) -> bytes:
    return files("febio_cae_harness").joinpath(relative_path).read_bytes()

def verify_runtime(runtime: FbsRuntime) -> None:
    lock = load_runtime_lock()
    root = runtime.python_exe.parent.resolve(strict=True)
    if runtime.python_exe.resolve(strict=True) != root / "python.exe":
        raise FbsInspectionError("FBS_PYTHON_PATH_INVALID")
    if runtime.fbs_module.resolve(strict=True) != root / "fbs.pyd":
        raise FbsInspectionError("FBS_MODULE_PATH_INVALID")
    actual_tree = verify_runtime_tree(root, lock, allow_runtime_profile=True)
    if actual_tree != runtime.expected_runtime_tree_sha256:
        raise FbsInspectionError("FBS_RUNTIME_TREE_DRIFT")
    profile_path = root / "runtime.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    schema = json.loads(_resource_bytes("schemas/fbs-runtime-profile.schema.json"))
    jsonschema.validate(profile, schema)
    lock_hash = hashlib.sha256(
        _resource_bytes("data/fbs-cp313-win_amd64.lock.json")
    ).hexdigest().upper()
    if profile["lock_resource_sha256"] != lock_hash:
        raise FbsInspectionError("FBS_LOCK_RESOURCE_DRIFT")
    if profile["runtime_tree_sha256"] != actual_tree:
        raise FbsInspectionError("FBS_PROFILE_TREE_DRIFT")
    if Path(profile["febio_bin"]).resolve(strict=True) != runtime.febio_bin.resolve(strict=True):
        raise FbsInspectionError("FBS_FEBIO_BIN_DRIFT")
    if (
        profile["loaded_dll_manifest_sha256"]
        != runtime.expected_loaded_dll_manifest_sha256
    ):
        raise FbsInspectionError("FBS_EXPECTED_DLL_PROFILE_DRIFT")
    if (
        loaded_dll_manifest_sha256(profile["loaded_dll_manifest"])
        != runtime.expected_loaded_dll_manifest_sha256
    ):
        raise FbsInspectionError("FBS_PROFILE_DLL_MANIFEST_INVALID")

def inspect_xplt(request: XpltRequest, runtime: FbsRuntime) -> XpltEvidence:
    verify_runtime(runtime)
    if (
        request.model_evidence_path.stat().st_size
        != request.model_evidence_bytes
        or sha256_file(request.model_evidence_path)
        != request.model_evidence_sha256
    ):
        raise FbsInspectionError("MODEL_EVIDENCE_DRIFT_BEFORE_FBS")
    if request.xplt_path.parent.name != "solver":
        raise FbsInspectionError("XPLT_NOT_IN_ATTEMPT_SOLVER_DIR")
    if (
        request.xplt_path.stat().st_size != request.xplt_bytes
        or sha256_file(request.xplt_path) != request.xplt_sha256
    ):
        raise FbsInspectionError("XPLT_DRIFT_BEFORE_FBS")
    parent_pre_worker_xplt_sha256 = sha256_file(request.xplt_path)
    attempt_root = request.xplt_path.parent.parent
    protocol_dir = attempt_root / "raw-logs"
    protocol_dir.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    request_path = protocol_dir / f"fbs-request-{nonce}.json"
    response_path = protocol_dir / f"fbs-response-{nonce}.json"
    stdout_path = protocol_dir / f"fbs-stdout-{nonce}.raw"
    stderr_path = protocol_dir / f"fbs-stderr-{nonce}.raw"
    request_payload = request.worker_dict(runtime)
    request_schema = json.loads(_resource_bytes("schemas/fbs-request.schema.json"))
    jsonschema.validate(request_payload, request_schema)
    request_bytes = (
        json.dumps(
            request_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    atomic_create_artifact(request_path, request_bytes)
    worker = Path(__file__).with_name("fbs_worker.py").resolve(strict=True)
    command = build_worker_command(runtime, worker, request_path, response_path)
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    try:
        completed = subprocess.run(
            command,
            cwd=attempt_root,
            env=environment,
            shell=False,
            capture_output=True,
            timeout=max(30.0, 5.0 * len(request.expected_times)),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        atomic_create_artifact(stdout_path, error.stdout or b"")
        atomic_create_artifact(stderr_path, error.stderr or b"")
        raise FbsInspectionError("FBS_WORKER_TIMEOUT") from error
    atomic_create_artifact(stdout_path, completed.stdout)
    atomic_create_artifact(stderr_path, completed.stderr)
    if not response_path.exists():
        raise FbsInspectionError(
            f"FBS_WORKER_NO_RESPONSE: exit={completed.returncode}"
        )
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    if payload["kind"] != "fbs-verification":
        raise FbsInspectionError("FBS_EVIDENCE_KIND_INVALID")
    if payload.get("request_id", request.request_sha256) != request.request_sha256:
        raise FbsInspectionError("FBS_RESPONSE_REQUEST_BINDING_INVALID")
    parent_post_worker_xplt_sha256 = sha256_file(request.xplt_path)
    if (
        request.model_evidence_path.stat().st_size
        != request.model_evidence_bytes
        or sha256_file(request.model_evidence_path)
        != request.model_evidence_sha256
    ):
        raise FbsInspectionError("MODEL_EVIDENCE_DRIFT_AFTER_FBS")
    if (
        request.xplt_path.stat().st_size != request.xplt_bytes
        or parent_post_worker_xplt_sha256 != request.xplt_sha256
    ):
        raise FbsInspectionError("XPLT_DRIFT_AFTER_FBS")
    if payload["status"] == "accepted":
        if completed.returncode != 0:
            raise FbsInspectionError(
                "FBS_WORKER_NONZERO_WITH_ACCEPTED_RESPONSE"
            )
        if (
            payload["model_evidence_sha256"]
            != request.model_evidence_sha256
        ):
            raise FbsInspectionError("FBS_MODEL_EVIDENCE_BINDING_DRIFT")
        if payload["xplt"] != {
            "path": str(request.xplt_path),
            "bytes": request.xplt_bytes,
            "sha256": request.xplt_sha256,
        }:
            raise FbsInspectionError("FBS_WORKER_XPLT_BINDING_DRIFT")
        payload.update(
            {
                "parent_pre_worker_xplt_sha256": (
                    parent_pre_worker_xplt_sha256
                ),
                "worker_xplt_sha256": payload["xplt"]["sha256"],
                "parent_post_worker_xplt_sha256": (
                    parent_post_worker_xplt_sha256
                ),
            }
        )
        schema = json.loads(_resource_bytes("schemas/fbs-evidence.schema.json"))
        jsonschema.validate(payload, schema)
        loaded = payload["reader"]["loaded_dll_manifest"]
        if (
            loaded_dll_manifest_sha256(loaded)
            != runtime.expected_loaded_dll_manifest_sha256
        ):
            raise FbsInspectionError("FBS_LOADED_DLL_PROFILE_DRIFT")
        if (
            payload["initial_coordinate_signature"]["sha256"]
            != request.expected_initial_coordinate_signature_sha256
        ):
            raise FbsInspectionError("FBS_COORDINATE_SIGNATURE_BINDING_DRIFT")
        if (
            request.expected_connectivity_signature_sha256 is not None
            and payload["connectivity"]["signature_sha256"]
            != request.expected_connectivity_signature_sha256
        ):
            raise FbsInspectionError("FBS_CONNECTIVITY_SIGNATURE_BINDING_DRIFT")
    else:
        schema = json.loads(_resource_bytes("schemas/fbs-evidence.schema.json"))
        jsonschema.validate(payload, schema)
        if completed.returncode == 0:
            raise FbsInspectionError("FBS_REJECTED_WITH_ZERO_EXIT")
    return XpltEvidence(payload)
```

- [ ] **Step 10: Add strict request/evidence schemas and installed resources**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-cae-harness.local/schemas/fbs-request.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "request_id", "xplt", "model_evidence",
    "fbs_module", "febio_bin",
    "expected_times", "allow_zero_state", "allow_unrequested_states",
    "time_abs_tol", "populations", "domain_bindings",
    "fields", "initial_coordinates",
    "expected_connectivity_signature_sha256",
    "expected_initial_coordinate_signature_sha256", "kinematic_checks"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "request_id": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "xplt": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "bytes", "sha256"],
      "properties": {
        "path": {"type": "string", "minLength": 1},
        "bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      }
    },
    "model_evidence": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "bytes", "sha256"],
      "properties": {
        "path": {"type": "string", "minLength": 1},
        "bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      }
    },
    "fbs_module": {"type": "string", "minLength": 1},
    "febio_bin": {"type": "string", "minLength": 1},
    "expected_times": {
      "type": "array", "minItems": 1, "uniqueItems": true,
      "items": {"type": "number"}
    },
    "allow_zero_state": {"type": "boolean"},
    "allow_unrequested_states": {"type": "boolean"},
    "time_abs_tol": {"type": "number", "minimum": 0},
    "populations": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "domain_aliases", "require_disjoint"],
        "properties": {
          "name": {"type": "string", "minLength": 1},
          "domain_aliases": {
            "type": "array", "minItems": 1, "uniqueItems": true,
            "items": {"type": "string", "minLength": 1}
          },
          "require_disjoint": {"type": "boolean"}
        }
      }
    },
    "domain_bindings": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "domain_alias", "material_name", "element_count",
          "referenced_node_count", "element_type", "nodes_per_element",
          "connectivity_signature_version",
          "connectivity_signature_sha256"
        ],
        "properties": {
          "domain_alias": {"type": "string", "minLength": 1},
          "material_name": {"type": "string", "minLength": 1},
          "element_count": {"type": "integer", "minimum": 1},
          "referenced_node_count": {"type": "integer", "minimum": 1},
          "element_type": {"type": "string", "minLength": 1},
          "nodes_per_element": {"type": "integer", "minimum": 1},
          "connectivity_signature_version": {
            "const": "feb-domain-connectivity-signature-v1"
          },
          "connectivity_signature_sha256": {
            "type": "string", "pattern": "^[0-9A-F]{64}$"
          }
        }
      }
    },
    "fields": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "operation", "association", "population"],
        "properties": {
          "name": {"type": "string", "minLength": 1},
          "operation": {
            "oneOf": [
              {"type": "integer", "minimum": 0},
              {"const": "MAT3DS.EFFECTIVE"}
            ]
          },
          "association": {"enum": ["nodeData", "elemData"]},
          "population": {"type": "string", "minLength": 1}
        }
      }
    },
    "initial_coordinates": {
      "type": "array", "maxItems": 32,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["node_id", "decimal"],
        "properties": {
          "node_id": {"type": "integer", "minimum": 1},
          "decimal": {
            "type": "array", "minItems": 3, "maxItems": 3,
            "items": {
              "oneOf": [
                {"type": "number"},
                {"type": "string", "minLength": 1}
              ]
            }
          }
        }
      }
    },
    "expected_connectivity_signature_sha256": {
      "type": ["string", "null"], "pattern": "^[0-9A-F]{64}$"
    },
    "expected_initial_coordinate_signature_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "kinematic_checks": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "field", "population", "expected_by_time", "abs_tol"
        ],
        "properties": {
          "field": {"type": "string", "minLength": 1},
          "population": {"type": "string", "minLength": 1},
          "expected_by_time": {
            "type": "array", "minItems": 1,
            "items": {
              "type": "object",
              "additionalProperties": false,
              "required": ["time", "vector", "magnitude"],
              "properties": {
                "time": {"type": "number"},
                "vector": {
                  "type": "array", "minItems": 3, "maxItems": 3,
                  "items": {"type": "number"}
                },
                "magnitude": {"type": "number", "minimum": 0}
              }
            }
          },
          "abs_tol": {"type": "number", "minimum": 0}
        }
      }
    }
  }
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-cae-harness.local/schemas/fbs-evidence.schema.json",
  "oneOf": [
    {
      "type": "object",
      "required": [
        "kind", "status", "request_id", "model_evidence_sha256",
        "xplt", "reader", "state_count",
        "parent_pre_worker_xplt_sha256", "worker_xplt_sha256",
        "parent_post_worker_xplt_sha256",
        "state_times", "nonzero_target_states", "zero_state_count",
        "unrequested_state_count", "final_time",
        "maximum_target_time_abs_error", "state_matches",
        "field_inventory", "mesh",
        "partitions", "populations", "connectivity",
        "initial_coordinate_check", "initial_coordinate_signature",
        "fields", "kinematic_checks"
      ],
      "properties": {
        "kind": {"const": "fbs-verification"},
        "status": {"const": "accepted"},
        "request_id": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "model_evidence_sha256": {
          "type": "string", "pattern": "^[0-9A-F]{64}$"
        },
        "xplt": {
          "type": "object",
          "additionalProperties": false,
          "required": ["path", "bytes", "sha256"],
          "properties": {
            "path": {"type": "string", "minLength": 1},
            "bytes": {"type": "integer", "minimum": 1},
            "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
          }
        },
        "parent_pre_worker_xplt_sha256": {
          "type": "string", "pattern": "^[0-9A-F]{64}$"
        },
        "worker_xplt_sha256": {
          "type": "string", "pattern": "^[0-9A-F]{64}$"
        },
        "parent_post_worker_xplt_sha256": {
          "type": "string", "pattern": "^[0-9A-F]{64}$"
        },
        "reader": {
          "type": "object",
          "additionalProperties": false,
          "required": ["python_version", "fbs_version", "loaded_dll_manifest"],
          "properties": {
            "python_version": {"type": "string", "minLength": 1},
            "fbs_version": {"type": "string", "minLength": 1},
            "loaded_dll_manifest": {
              "type": "array",
              "items": {
                "type": "object",
                "additionalProperties": false,
                "required": ["path", "bytes", "sha256"],
                "properties": {
                  "path": {"type": "string", "minLength": 1},
                  "bytes": {"type": "integer", "minimum": 1},
                  "sha256": {
                    "type": "string", "pattern": "^[0-9A-F]{64}$"
                  }
                }
              }
            }
          }
        },
        "state_count": {"type": "integer", "minimum": 1},
        "state_times": {"type": "array", "minItems": 1, "items": {"type": "number"}},
        "nonzero_target_states": {"type": "integer", "minimum": 0},
        "zero_state_count": {"type": "integer", "minimum": 0},
        "unrequested_state_count": {"type": "integer", "minimum": 0},
        "final_time": {"type": "number"},
        "maximum_target_time_abs_error": {"type": "number", "minimum": 0},
        "state_matches": {
          "type": "array", "minItems": 1,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["expected_index", "state_index"],
            "properties": {
              "expected_index": {"type": "integer", "minimum": 0},
              "state_index": {"type": "integer", "minimum": 0}
            }
          }
        },
        "field_inventory": {"type": "array", "items": {"type": "string"}},
        "mesh": {
          "type": "object",
          "additionalProperties": false,
          "required": ["node_count", "element_count", "partition_count"],
          "properties": {
            "node_count": {"type": "integer", "minimum": 1},
            "element_count": {"type": "integer", "minimum": 1},
            "partition_count": {"type": "integer", "minimum": 0}
          }
        },
        "partitions": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "partition_index", "partition_name", "material_name",
              "domain_alias", "domain_connectivity_signature_sha256",
              "element_indices", "element_ids", "node_indices",
              "node_ids", "elements"
            ],
            "properties": {
              "partition_index": {"type": "integer", "minimum": 0},
              "partition_name": {"type": "string"},
              "material_name": {"type": "string", "minLength": 1},
              "domain_alias": {"type": "string", "minLength": 1},
              "domain_connectivity_signature_sha256": {
                "type": "string", "pattern": "^[0-9A-F]{64}$"
              },
              "element_indices": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 0}
              },
              "element_ids": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 1}
              },
              "node_indices": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 0}
              },
              "node_ids": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 1}
              },
              "elements": {
                "type": "array", "minItems": 1,
                "items": {
                  "type": "object",
                  "additionalProperties": false,
                  "required": [
                    "element_index", "element_id", "node_count",
                    "connectivity_node_indices",
                    "connectivity_node_ids", "element_type"
                  ],
                  "properties": {
                    "element_index": {"type": "integer", "minimum": 0},
                    "element_id": {"type": "integer", "minimum": 1},
                    "node_count": {"type": "integer", "minimum": 1},
                    "connectivity_node_indices": {
                      "type": "array", "minItems": 1,
                      "items": {"type": "integer", "minimum": 0}
                    },
                    "connectivity_node_ids": {
                      "type": "array", "minItems": 1,
                      "items": {"type": "integer", "minimum": 1}
                    },
                    "element_type": {"type": "string", "minLength": 1}
                  }
                }
              }
            }
          }
        },
        "populations": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "name", "domain_aliases", "material_names",
              "element_indices", "element_ids", "node_indices", "node_ids"
            ],
            "properties": {
              "name": {"type": "string", "minLength": 1},
              "domain_aliases": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "string", "minLength": 1}
              },
              "material_names": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "string", "minLength": 1}
              },
              "element_indices": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 0}
              },
              "element_ids": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 1}
              },
              "node_indices": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 0}
              },
              "node_ids": {
                "type": "array", "minItems": 1, "uniqueItems": true,
                "items": {"type": "integer", "minimum": 1}
              }
            }
          }
        },
        "connectivity": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "version", "node_count", "element_count", "signature_sha256"
          ],
          "properties": {
            "version": {"const": "feb-fbs-connectivity-signature-v1"},
            "node_count": {"type": "integer", "minimum": 1},
            "element_count": {"type": "integer", "minimum": 1},
            "signature_sha256": {
              "type": "string", "pattern": "^[0-9A-F]{64}$"
            }
          }
        },
        "initial_coordinate_check": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "checked_component_count", "all_binary32_bits_equal",
            "maximum_raw_decimal_error"
          ],
          "properties": {
            "checked_component_count": {"type": "integer", "minimum": 0},
            "all_binary32_bits_equal": {"const": true},
            "maximum_raw_decimal_error": {"type": "number", "minimum": 0}
          }
        },
        "initial_coordinate_signature": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "version", "ordering", "node_id_encoding", "coordinate_encoding",
            "node_count", "component_count", "sha256"
          ],
          "properties": {
            "version": {"const": "feb-node-coordinate-f32-v1"},
            "ordering": {"const": "ascending-node-id"},
            "node_id_encoding": {"const": "uint64-big-endian"},
            "coordinate_encoding": {"const": "ieee754-binary32-big-endian-xyz"},
            "node_count": {"type": "integer", "minimum": 1},
            "component_count": {"type": "integer", "minimum": 3},
            "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
          }
        },
        "fields": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "name", "operation", "operation_source", "association",
              "population", "selected_indices_kind", "states"
            ],
            "properties": {
              "name": {"type": "string", "minLength": 1},
              "operation": {"type": "integer", "minimum": 0},
              "operation_source": {
                "oneOf": [
                  {"type": "integer", "minimum": 0},
                  {"const": "MAT3DS.EFFECTIVE"}
                ]
              },
              "association": {"enum": ["nodeData", "elemData"]},
              "population": {"type": "string", "minLength": 1},
              "selected_indices_kind": {
                "enum": ["node_indices", "element_indices"]
              },
              "states": {
                "type": "array", "minItems": 1,
                "items": {
                  "type": "object",
                  "additionalProperties": false,
                  "required": [
                    "expected_index", "state_index", "state_time",
                    "value_count", "minimum", "maximum",
                    "values_sha256", "all_finite"
                  ],
                  "properties": {
                    "expected_index": {"type": "integer", "minimum": 0},
                    "state_index": {"type": "integer", "minimum": 0},
                    "state_time": {"type": "number"},
                    "value_count": {"type": "integer", "minimum": 1},
                    "minimum": {"type": "number"},
                    "maximum": {"type": "number"},
                    "values_sha256": {
                      "type": "string", "pattern": "^[0-9A-F]{64}$"
                    },
                    "all_finite": {"const": true}
                  }
                }
              }
            }
          }
        },
        "kinematic_checks": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "field", "population", "operations", "node_count",
              "full_vector_checked", "operation6_consistent",
              "maximum_vector_error", "maximum_magnitude_error",
              "maximum_norm_consistency_error",
              "all_nodes_all_target_states_checked"
            ],
            "properties": {
              "field": {"type": "string", "minLength": 1},
              "population": {"type": "string", "minLength": 1},
              "operations": {"const": [0, 1, 2, 6]},
              "node_count": {"type": "integer", "minimum": 1},
              "full_vector_checked": {"const": true},
              "operation6_consistent": {"const": true},
              "maximum_vector_error": {"type": "number", "minimum": 0},
              "maximum_magnitude_error": {"type": "number", "minimum": 0},
              "maximum_norm_consistency_error": {
                "type": "number", "minimum": 0
              },
              "all_nodes_all_target_states_checked": {"const": true}
            }
          }
        }
      },
      "additionalProperties": false
    },
    {
      "type": "object",
      "required": ["kind", "status", "error_code", "detail"],
      "properties": {
        "kind": {"const": "fbs-verification"},
        "status": {"const": "rejected"},
        "error_code": {"type": "string", "minLength": 1},
        "detail": {"type": "string"}
      },
      "additionalProperties": false
    }
  ]
}
```

Add these exact paths to `EXPECTED_RESOURCES`:

```python
"schemas/fbs-evidence.schema.json",
"schemas/fbs-request.schema.json",
```

- [ ] **Step 11: Run the GREEN commands for fake-FBS, client, package, and the CAE-side real-runtime contract**

Before this step, the CAE-side verification workflow must create-new stage the
known contract XPLT and a separately reviewed canonical expectation JSON under
`02_CAE\<case>\90_Temporary\...`. The expectation has exactly the five
model-specific keys asserted in Step 8; its values are taken from the reviewed
external model evidence, never copied into tool source. Set
`FEBIO_CAE_FBS_CONTRACT_XPLT` and
`FEBIO_CAE_FBS_CONTRACT_EXPECTATION` to those two files. Record and compare
their bytes and SHA-256 before and after the probe. The tool test reads them
but never copies real CAE bytes or identifiers into the repository and never
creates or deletes a CAE artifact.

Use `apply_patch` in the CAE case to create the expectation file with these
canonical JSON values; fail if the target already exists:

```json
{"element_node_counts":[10,4],"material_names":["ABS_Terluran_GP22_23C_QS_N-mm","M2_Screw_Rigid"],"partition_element_counts":[47289,2642],"partition_names":["",""],"partition_referenced_node_counts":[82066,713]}
```

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_fbs_protocol.py apps/febio_cae_harness/tests/unit/test_fbs_worker.py apps/febio_cae_harness/tests/integration/test_fbs_client.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
$env:FEBIO_CAE_FBS_RUNTIME_ROOT = Join-Path $env:LOCALAPPDATA 'FEBioCaeHarness\runtimes\fbs-cp313'
if (-not (Test-Path -LiteralPath $env:FEBIO_CAE_FBS_RUNTIME_ROOT -PathType Container)) { throw 'FBS_CONTRACT_RUNTIME_MISSING' }
if ([string]::IsNullOrWhiteSpace($env:FEBIO_CAE_FBS_CONTRACT_XPLT)) { throw 'FBS_CONTRACT_XPLT_ENV_REQUIRED' }
if ([string]::IsNullOrWhiteSpace($env:FEBIO_CAE_FBS_CONTRACT_EXPECTATION)) { throw 'FBS_CONTRACT_EXPECTATION_ENV_REQUIRED' }
$CaeRoot = [IO.Path]::GetFullPath('C:\Users\backo\OneDrive\Documents\FEBio\02_CAE')
$ContractXplt = [IO.Path]::GetFullPath($env:FEBIO_CAE_FBS_CONTRACT_XPLT)
$ContractExpectation = [IO.Path]::GetFullPath(
    $env:FEBIO_CAE_FBS_CONTRACT_EXPECTATION
)
foreach ($ContractArtifact in @($ContractXplt, $ContractExpectation)) {
    if (-not $ContractArtifact.StartsWith(
        $CaeRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) { throw 'FBS_CONTRACT_ARTIFACT_MUST_BE_UNDER_02_CAE' }
    if (-not (Test-Path -LiteralPath $ContractArtifact -PathType Leaf)) {
        throw "FBS_CONTRACT_ARTIFACT_MISSING: $ContractArtifact"
    }
}
$env:FEBIO_CAE_FBS_CONTRACT_XPLT = $ContractXplt
$env:FEBIO_CAE_FBS_CONTRACT_EXPECTATION = $ContractExpectation
$BeforeContractArtifacts = @(
    foreach ($ContractArtifact in @($ContractXplt, $ContractExpectation)) {
        [pscustomobject]@{
            path = $ContractArtifact
            bytes = (Get-Item -LiteralPath $ContractArtifact).Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ContractArtifact).Hash
        }
    }
)
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_fbs_real_runtime_contract.py -q
foreach ($ExpectedArtifact in $BeforeContractArtifacts) {
    if (
        (Get-Item -LiteralPath $ExpectedArtifact.path).Length -ne
            $ExpectedArtifact.bytes -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $ExpectedArtifact.path).Hash -ne
            $ExpectedArtifact.sha256
    ) { throw "FBS_CONTRACT_ARTIFACT_DRIFT: $($ExpectedArtifact.path)" }
}
```

Expected: both commands exit `0` with no skip in the real-runtime probe. The
locked FEBio 4.12 probe proves `MAT3DS.EFFECTIVE=6` (`P2=8`), both historical
partition names are empty while materials are exactly
`ABS_Terluran_GP22_23C_QS_N-mm` and `M2_Screw_Rigid`, the FBS element lacks
`Type()`, the two partitions have 10-node and 4-node elements, 47,289 / 2,642
elements, and 82,066 / 713 referenced nodes respectively, and non-selected
field associations may also be nonempty. Those model-specific expectations
exist only in the reviewed external JSON. Fake-worker tests use neutral aliases
`Part2` / `rigid-domain` and prove authoritative FEB element-type injection,
selected-association-only evaluation, compact full-mesh
connectivity/coordinate signatures, and full rigid-vector checks.

- [ ] **Step 12: Inspect, stage, and commit exactly Task 11 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/fbs_protocol.py apps/febio_cae_harness/src/febio_cae_harness/fbs_client.py apps/febio_cae_harness/src/febio_cae_harness/fbs_worker.py apps/febio_cae_harness/src/febio_cae_harness/schemas/fbs-request.schema.json apps/febio_cae_harness/src/febio_cae_harness/schemas/fbs-evidence.schema.json apps/febio_cae_harness/tests/fixtures/fbs/fake_fbs.py apps/febio_cae_harness/tests/unit/test_fbs_protocol.py apps/febio_cae_harness/tests/unit/test_fbs_worker.py apps/febio_cae_harness/tests/integration/test_fbs_client.py apps/febio_cae_harness/tests/integration/test_fbs_real_runtime_contract.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git diff --cached --check
git commit -m "feat: validate XPLT through isolated official FBS"
```

### Task 12: Lock-held preflight and transitive drift gate

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/preflight.py`
- Create: `apps/febio_cae_harness/tests/unit/test_preflight.py`

**Interfaces:**
- Consumes: authoritative case transaction, staged/adopted FEB, canonical `attempt/model-evidence.json`, intent approval, solver, `FbsRuntime`, runtime/profile verifier, policy, and their approved SHA-256 values
- Produces: `PreflightInputs`, `PreflightDecision`, `run_preflight(case: CaseStore, inputs: PreflightInputs) -> PreflightDecision`, create-new `preflight-decision.json`, authoritative transition to `PREFLIGHT_PASSED`

- [ ] **Step 1: Write success and one-field-at-a-time drift tests**

```python
# tests/unit/test_preflight.py
from dataclasses import replace
from pathlib import Path

import pytest

import febio_cae_harness.preflight as preflight
from febio_cae_harness.attempt_store import AttemptStore
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.fbs_protocol import FbsRuntime
from febio_cae_harness.hashing import sha256_file
from tests.helpers.phase1b import initialize_case
from tests.integration.test_attempt_store import config

def inputs(tmp_path: Path, attempt: AttemptStore) -> preflight.PreflightInputs:
    files = {}
    for name in (
        "adopted.feb", "input.feb", "intent.json", "febio4.exe", "policy.json",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode("ascii"))
        files[name] = path.resolve()
    model_evidence = attempt.root / "model-evidence.json"
    model_evidence.write_bytes(b"model-evidence")
    files["model-evidence.json"] = model_evidence.resolve()
    runtime_root = tmp_path / "runtime"
    febio_bin = tmp_path / "bin"
    runtime_root.mkdir()
    febio_bin.mkdir()
    (runtime_root / "python.exe").write_bytes(b"python")
    (runtime_root / "fbs.pyd").write_bytes(b"fbs")
    return preflight.PreflightInputs(
        attempt=attempt,
        adopted_feb=preflight.ExpectedArtifact(files["adopted.feb"], sha256_file(files["adopted.feb"])),
        staged_feb=preflight.ExpectedArtifact(files["input.feb"], sha256_file(files["input.feb"])),
        model_evidence=preflight.ExpectedArtifact(
            files["model-evidence.json"],
            sha256_file(files["model-evidence.json"]),
        ),
        intent_approval=preflight.ExpectedArtifact(files["intent.json"], sha256_file(files["intent.json"])),
        solver=preflight.ExpectedArtifact(files["febio4.exe"], sha256_file(files["febio4.exe"])),
        fbs_runtime=FbsRuntime(
            runtime_root / "python.exe",
            runtime_root / "fbs.pyd",
            febio_bin,
            "A" * 64,
            "B" * 64,
        ),
        policy=preflight.ExpectedArtifact(files["policy.json"], sha256_file(files["policy.json"])),
        resume_key=attempt.resume_key,
    )

def test_preflight_rehashes_every_input_under_transaction(tmp_path, monkeypatch):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    value = inputs(tmp_path, attempt)
    monkeypatch.setattr(preflight, "verify_runtime", lambda runtime: None)
    decision = preflight.run_preflight(case, value)
    assert decision.status == "accepted"
    assert decision.checked_roles == (
        "adopted-feb", "staged-feb", "model-evidence",
        "intent-approval", "solver", "policy",
        "fbs-runtime-tree", "fbs-loaded-dll-profile", "resume-key",
    )
    assert case.state is CaseState.PREFLIGHT_PASSED
    assert (attempt.root / "preflight-decision.json").exists()

@pytest.mark.parametrize(
    "field_name",
    (
        "adopted_feb", "staged_feb", "model_evidence",
        "intent_approval", "solver", "policy",
    ),
)
def test_any_file_drift_blocks_launch_before_state_change(tmp_path, monkeypatch, field_name):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    value = inputs(tmp_path, attempt)
    target = getattr(value, field_name)
    target.path.write_bytes(b"drift")
    monkeypatch.setattr(preflight, "verify_runtime", lambda runtime: None)
    with pytest.raises(RuntimeError, match="PREFLIGHT_HASH_DRIFT"):
        preflight.run_preflight(case, value)
    assert case.state is CaseState.MODEL_BUILT
    assert not (attempt.root / "preflight-decision.json").exists()

def test_runtime_profile_drift_blocks_launch(tmp_path, monkeypatch):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    value = inputs(tmp_path, attempt)
    monkeypatch.setattr(
        preflight,
        "verify_runtime",
        lambda runtime: (_ for _ in ()).throw(RuntimeError("FBS_RUNTIME_TREE_DRIFT")),
    )
    with pytest.raises(RuntimeError, match="FBS_RUNTIME_TREE_DRIFT"):
        preflight.run_preflight(case, value)
    assert case.state is CaseState.MODEL_BUILT
```

- [ ] **Step 2: Run preflight tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_preflight.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.preflight'`.

- [ ] **Step 3: Implement the lock-held drift decision**

```python
# preflight.py
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from febio_cae_harness.attempt_store import AttemptStore, _write_json
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.fbs_client import verify_runtime
from febio_cae_harness.fbs_protocol import FbsRuntime
from febio_cae_harness.hashing import sha256_file

@dataclass(frozen=True)
class ExpectedArtifact:
    path: Path
    sha256: str

@dataclass(frozen=True)
class PreflightInputs:
    attempt: AttemptStore
    adopted_feb: ExpectedArtifact
    staged_feb: ExpectedArtifact
    model_evidence: ExpectedArtifact
    intent_approval: ExpectedArtifact
    solver: ExpectedArtifact
    fbs_runtime: FbsRuntime
    policy: ExpectedArtifact
    resume_key: str

@dataclass(frozen=True)
class PreflightDecision:
    status: str
    attempt_id: str
    checked_roles: tuple[str, ...]
    evidence: tuple[dict[str, object], ...]

def run_preflight(case: CaseStore, inputs: PreflightInputs) -> PreflightDecision:
    with case.locked() as transaction:
        projection = transaction.replay()
        if projection["harness"]["case_state"] != CaseState.MODEL_BUILT.value:
            raise RuntimeError("preflight requires MODEL_BUILT")
        if inputs.attempt.case_dir != case.case_dir:
            raise RuntimeError("attempt belongs to another case")
        if (
            inputs.model_evidence.path.resolve(strict=True)
            != (inputs.attempt.root / "model-evidence.json").resolve(strict=True)
        ):
            raise RuntimeError("PREFLIGHT_MODEL_EVIDENCE_PATH_INVALID")
        if inputs.resume_key != inputs.attempt.resume_key:
            raise RuntimeError("PREFLIGHT_RESUME_KEY_DRIFT")
        evidence: list[dict[str, object]] = []
        for role, expected in (
            ("adopted-feb", inputs.adopted_feb),
            ("staged-feb", inputs.staged_feb),
            ("model-evidence", inputs.model_evidence),
            ("intent-approval", inputs.intent_approval),
            ("solver", inputs.solver),
            ("policy", inputs.policy),
        ):
            actual = sha256_file(expected.path)
            if actual != expected.sha256:
                raise RuntimeError(f"PREFLIGHT_HASH_DRIFT:{role}")
            evidence.append(
                {
                    "role": role,
                    "path": str(expected.path.resolve(strict=True)),
                    "bytes": expected.path.stat().st_size,
                    "sha256": actual,
                }
            )
        verify_runtime(inputs.fbs_runtime)
        evidence.extend(
            (
                {
                    "role": "fbs-runtime-tree",
                    "sha256": inputs.fbs_runtime.expected_runtime_tree_sha256,
                },
                {
                    "role": "fbs-loaded-dll-profile",
                    "sha256": inputs.fbs_runtime.expected_loaded_dll_manifest_sha256,
                },
                {"role": "resume-key", "sha256": inputs.resume_key},
            )
        )
        decision = PreflightDecision(
            status="accepted",
            attempt_id=inputs.attempt.attempt_id,
            checked_roles=tuple(item["role"] for item in evidence),
            evidence=tuple(evidence),
        )
        decision_path = inputs.attempt.root / "preflight-decision.json"
        _write_json(
            decision_path,
            {"schema_version": 1, **asdict(decision)},
        )
        transaction.append(
            "PREFLIGHT_PASSED",
            CaseState.PREFLIGHT_PASSED,
            {
                "attempt_id": inputs.attempt.attempt_id,
                "preflight_decision_sha256": sha256_file(decision_path),
                "resume_key": inputs.resume_key,
            },
        )
        return decision
```

- [ ] **Step 4: Run the GREEN command for preflight, runtime, adoption, and case regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_preflight.py apps/febio_cae_harness/tests/unit/test_fbs_runtime_lock.py apps/febio_cae_harness/tests/integration/test_model_adoption.py apps/febio_cae_harness/tests/integration/test_case_store.py -q
```

Expected: command exits `0`; drift cases leave no decision artifact and no state transition; accepted evidence binds all nine roles, including the canonical model-evidence hash.

- [ ] **Step 5: Inspect, stage, and commit exactly Task 12 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/preflight.py apps/febio_cae_harness/tests/unit/test_preflight.py
git diff --cached --check
git commit -m "feat: gate launch on transitive preflight hashes"
```

### Task 13: Composite SOLVED decision and intent-specific RESULT_VERIFIED gate

**Files:**
- Create: `apps/febio_cae_harness/src/febio_cae_harness/completion.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/result_validation.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/completion-decision.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/result-verification.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_completion.py`
- Create: `apps/febio_cae_harness/tests/unit/test_result_validation.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**
- Consumes: authoritative `PREFLIGHT_PASSED` transaction/event hash, Task 7 process record, Task 8 LOG record, Task 9 `FailureEvidence` payload, Task 11 FBS record plus the exact intent-derived `XpltRequest` whose `request_sha256` produced it, immutable canonical `attempt/model-evidence.json`, intent-specific geometry/kinematic requirements
- Produces: `CompletionDecision(kind="completion-decision")`, `CompletionDecision.to_record() -> EvidenceRecord`, `decide_completion(..., fbs_request: XpltRequest | None)`, create-new process/LOG/FBS evidence records and `completion-decision.json`, `ResultValidationInputs(fbs_request=...)`, `ResultValidation(kind="result-validation")`, `ResultValidation.to_record() -> EvidenceRecord`, `validate_result()`, create-new `result-verification.json`. `None` is permitted only when a fresh XPLT does not exist or the exact request cannot be constructed; it always adds `FBS_REQUEST_UNAVAILABLE` and can never reach `SOLVED`.

- [ ] **Step 1: Write composite success and each independent SOLVED blocker**

```python
# tests/unit/test_completion.py
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from febio_cae_harness.attempt_store import AttemptStore
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.completion import decide_completion
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.schema import validate_schema
from tests.helpers.phase1b import initialize_case
from tests.integration.test_attempt_store import config

def evidence(tmp_path: Path):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    case.state = CaseState.PREFLIGHT_PASSED
    log_path = attempt.paths.solver / "solver.log"
    xplt_path = attempt.paths.solver / "solver.xplt"
    log_path.write_bytes(b"LOG")
    xplt_path.write_bytes(b"XPLT")
    log_artifact = {
        "path": str(log_path), "exists": True, "bytes": 3,
        "sha256": sha256_file(log_path), "mtime_ns": log_path.stat().st_mtime_ns,
    }
    xplt_artifact = {
        "path": str(xplt_path), "exists": True, "bytes": 4,
        "sha256": sha256_file(xplt_path), "mtime_ns": xplt_path.stat().st_mtime_ns,
    }
    process = {
        "kind": "process-evidence",
        "exit_code": 0,
        "termination_reason": "EXITED",
        "job_object_assigned": True,
        "active_processes_after_finalize": 0,
        "artifacts_before": {
            "log": {"path": str(log_path), "exists": False},
            "xplt": {"path": str(xplt_path), "exists": False},
        },
        "artifacts_after": {"log": log_artifact, "xplt": xplt_artifact},
    }
    log = {
        "kind": "log-verification",
        "status": "accepted",
        "path": str(log_path),
        "bytes": 3,
        "sha256": sha256_file(log_path),
        "solver_version": "4.12.0",
        "files_used": {
            "input": "input.feb",
            "plot": "solver.xplt",
            "log": "solver.log",
        },
        "beginning_steps": [],
        "converged_times": [1.0],
        "converged_line_indices": [1],
        "completed_steps": 1,
        "normal_termination": True,
        "normal_termination_line_index": 2,
        "warning_lines": [],
        "error_lines": [],
        "recovered_warnings": [],
        "blocking_reasons": [],
        "unresolved_warnings": [],
        "elapsed_seconds": 1.0,
        "peak_memory_mib": 1.0,
    }
    fbs = {
        "kind": "fbs-verification",
        "status": "accepted",
        "request_id": "B" * 64,
        "model_evidence_sha256": "A" * 64,
        "xplt": {
            "path": str(xplt_path), "bytes": 4, "sha256": sha256_file(xplt_path),
        },
        "parent_pre_worker_xplt_sha256": sha256_file(xplt_path),
        "worker_xplt_sha256": sha256_file(xplt_path),
        "parent_post_worker_xplt_sha256": sha256_file(xplt_path),
        "reader": {
            "python_version": "3.13.5",
            "fbs_version": "test",
            "loaded_dll_manifest": [],
        },
        "state_count": 1,
        "state_times": [1.0],
        "nonzero_target_states": 1,
        "zero_state_count": 0,
        "unrequested_state_count": 0,
        "final_time": 1.0,
        "maximum_target_time_abs_error": 0.0,
        "state_matches": [{"expected_index": 0, "state_index": 0}],
        "field_inventory": ["stress"],
        "mesh": {
            "node_count": 1,
            "element_count": 1,
            "partition_count": 1,
        },
        "partitions": [
            {
                "partition_index": 0,
                "partition_name": "",
                "material_name": "ABS",
                "domain_alias": "Part2",
                "domain_connectivity_signature_sha256": "E" * 64,
                "element_indices": [0],
                "element_ids": [1],
                "node_indices": [0],
                "node_ids": [1],
                "elements": [
                    {
                        "element_index": 0,
                        "element_id": 1,
                        "node_count": 1,
                        "connectivity_node_indices": [0],
                        "connectivity_node_ids": [1],
                        "element_type": "SYNTHETIC1",
                    }
                ],
            }
        ],
        "populations": [
            {
                "name": "Part2",
                "domain_aliases": ["Part2"],
                "material_names": ["ABS"],
                "element_indices": [0],
                "element_ids": [1],
                "node_indices": [0],
                "node_ids": [1],
            }
        ],
        "connectivity": {
            "version": "feb-fbs-connectivity-signature-v1",
            "node_count": 1,
            "element_count": 1,
            "signature_sha256": "C" * 64,
        },
        "initial_coordinate_signature": {
            "version": "feb-node-coordinate-f32-v1",
            "ordering": "ascending-node-id",
            "node_id_encoding": "uint64-big-endian",
            "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
            "node_count": 1,
            "component_count": 3,
            "sha256": "D" * 64,
        },
        "initial_coordinate_check": {
            "checked_component_count": 0,
            "all_binary32_bits_equal": True,
            "maximum_raw_decimal_error": 0.0,
        },
        "fields": [
            {
                "name": "stress",
                "operation": 6,
                "operation_source": "MAT3DS.EFFECTIVE",
                "association": "elemData",
                "population": "Part2",
                "selected_indices_kind": "element_indices",
                "states": [
                    {
                        "expected_index": 0,
                        "state_index": 0,
                        "state_time": 1.0,
                        "value_count": 1,
                        "minimum": 1.0,
                        "maximum": 1.0,
                        "values_sha256": "F" * 64,
                        "all_finite": True,
                    }
                ],
            }
        ],
        "kinematic_checks": [],
    }
    fbs_request = SimpleNamespace(
        request_sha256="B" * 64,
        expected_times=(1.0,),
        populations=(
            {"name": "Part2", "domain_aliases": ["Part2"]},
        ),
        fields=(
            {
                "name": "stress",
                "operation": "MAT3DS.EFFECTIVE",
                "association": "elemData",
                "population": "Part2",
            },
        ),
        kinematic_checks=(),
    )
    return case, attempt, process, log, fbs, fbs_request

def test_solved_requires_process_log_and_fbs_together(tmp_path):
    case, attempt, process, log, fbs, request = evidence(tmp_path)
    decision = decide_completion(
        case, attempt, process, log, fbs, None, request
    )
    assert decision.kind == "completion-decision"
    assert decision.status == "accepted"
    assert decision.case_state == "SOLVED"
    assert case.state is CaseState.SOLVED
    assert len(decision.evidence_records) == 3
    assert (attempt.root / "completion-decision.json").exists()
    validate_schema(
        "completion-decision",
        json.loads(
            (attempt.root / "completion-decision.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    record = decision.to_record()
    assert set(record.to_payload()) == {"kind", "data"}
    assert "kind" not in record.data

@pytest.mark.parametrize(
    ("target", "mutation", "reason"),
    [
        ("process", lambda value: value.update(exit_code=7), "PROCESS_NOT_SUCCESSFUL"),
        ("process", lambda value: value.update(active_processes_after_finalize=1), "PROCESS_TREE_NOT_EMPTY"),
        ("log", lambda value: value.update(status="rejected"), "LOG_NOT_ACCEPTED"),
        ("log", lambda value: value.update(unresolved_warnings=["unknown"]), "LOG_WARNING_UNRESOLVED"),
        ("fbs", lambda value: value.update(status="rejected"), "FBS_NOT_ACCEPTED"),
        ("fbs", lambda value: value.update(state_matches=[]), "FBS_TARGET_STATES_MISSING"),
        ("fbs", lambda value: value.update(fields=[]), "FBS_FIELDS_MISSING"),
        ("fbs", lambda value: value.update(worker_xplt_sha256="0" * 64), "FBS_XPLT_TOCTOU_HASH_MISMATCH"),
        ("fbs", lambda value: value.update(initial_coordinate_signature={}), "FBS_INITIAL_COORDINATE_SIGNATURE_INVALID"),
        ("fbs", lambda value: value["fields"][0]["states"][0].update(value_count=0), "FBS_FIELD_EMPTY"),
    ],
)
def test_each_composite_condition_blocks_solved(tmp_path, target, mutation, reason):
    case, attempt, process, log, fbs, request = evidence(tmp_path)
    values = {"process": process, "log": log, "fbs": fbs}
    mutation(values[target])
    decision = decide_completion(
        case, attempt, process, log, fbs, None, request
    )
    assert decision.status == "rejected"
    assert reason in decision.blocking_reasons
    assert case.state is not CaseState.SOLVED

def test_engineering_log_failure_preserves_typed_failure_class(tmp_path):
    case, attempt, process, log, fbs, request = evidence(tmp_path)
    log["status"] = "rejected"
    failure = {"failure_class": "INITIAL_MESH_ERROR", "phase": "INITIAL"}
    decision = decide_completion(
        case, attempt, process, log, fbs, failure, request
    )
    assert decision.status == "rejected"
    assert decision.case_state == "SOLVE_FAILED"
    assert decision.failure_class == "INITIAL_MESH_ERROR"


def test_missing_exact_fbs_request_can_never_reach_solved(tmp_path):
    case, attempt, process, log, fbs, _ = evidence(tmp_path)
    decision = decide_completion(
        case, attempt, process, log, fbs, None, None
    )
    assert decision.status == "rejected"
    assert decision.case_state == "RESULT_INCOMPLETE"
    assert "FBS_REQUEST_UNAVAILABLE" in decision.blocking_reasons
```

- [ ] **Step 2: Write authoritative result/model/mesh/geometry/rigid checks**

```python
# tests/unit/test_result_validation.py
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import febio_cae_harness.result_validation as result_validation
from febio_cae_harness.attempt_store import AttemptStore
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.result_validation import (
    ResultValidationInputs,
    validate_result,
)
from febio_cae_harness.schema import validate_schema
from tests.helpers.phase1b import initialize_case
from tests.integration.test_attempt_store import config

MODEL_SIGNATURE = "A" * 64
CONNECTIVITY_SIGNATURE = "C" * 64
COORDINATE_SIGNATURE = "D" * 64

def canonical_write(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )

def setup(
    tmp_path: Path,
    monkeypatch,
    *,
    fbs_connectivity: str = CONNECTIVITY_SIGNATURE,
    fbs_coordinate: str = COORDINATE_SIGNATURE,
    model_excluded_domains: tuple[str, ...] = ("rigid-domain",),
):
    case = initialize_case(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = AttemptStore.start(case, config())
    case.state = CaseState.SOLVED
    input_feb = attempt.paths.solver / "input.feb"
    log = attempt.paths.solver / "solver.log"
    xplt = attempt.paths.solver / "solver.xplt"
    input_feb.write_bytes(b"FEB")
    log.write_bytes(b"LOG")
    xplt.write_bytes(b"XPLT")
    process = {
        "kind": "process-evidence",
        "artifacts_after": {
            "log": {
                "path": str(log.resolve()),
                "bytes": log.stat().st_size,
                "sha256": sha256_file(log),
            },
            "xplt": {
                "path": str(xplt.resolve()),
                "bytes": xplt.stat().st_size,
                "sha256": sha256_file(xplt),
            },
        },
    }
    log_evidence = {
        "kind": "log-verification",
        "path": str(log.resolve()),
        "bytes": log.stat().st_size,
        "sha256": sha256_file(log),
    }
    canonical_write(attempt.root / "process-evidence.json", process)
    canonical_write(attempt.root / "log-verification.json", log_evidence)
    fbs = {
        "kind": "fbs-verification",
        "status": "accepted",
        "request_id": "B" * 64,
        "xplt": {
            "path": str(xplt.resolve()),
            "bytes": xplt.stat().st_size,
            "sha256": sha256_file(xplt),
        },
        "parent_pre_worker_xplt_sha256": sha256_file(xplt),
        "worker_xplt_sha256": sha256_file(xplt),
        "parent_post_worker_xplt_sha256": sha256_file(xplt),
        "state_matches": [{"expected_index": 0, "state_index": 0}],
        "mesh": {"node_count": 8, "element_count": 2, "partition_count": 2},
        "connectivity": {
            "version": "feb-fbs-connectivity-signature-v1",
            "node_count": 8,
            "element_count": 2,
            "signature_sha256": fbs_connectivity,
        },
        "partitions": [
            {
                "partition_name": "",
                "domain_alias": "Part2",
                "element_ids": [501],
                "node_ids": [101, 102, 103, 104],
            },
            {
                "partition_name": "",
                "domain_alias": "rigid-domain",
                "element_ids": [601],
                "node_ids": [201, 202, 203, 204],
            },
        ],
        "populations": [
            {
                "name": "Part2",
                "domain_aliases": ["Part2"],
                "material_names": ["ABS"],
                "element_ids": [501],
                "node_ids": [101, 102, 103, 104],
            }
        ],
        "fields": [
            {
                "name": "stress",
                "operation": 6,
                "operation_source": "MAT3DS.EFFECTIVE",
                "association": "elemData",
                "population": "Part2",
                "states": [{"value_count": 1, "all_finite": True}],
            }
        ],
        "initial_coordinate_check": {
            "checked_component_count": 24,
            "all_binary32_bits_equal": True,
            "maximum_raw_decimal_error": 2.0e-8,
        },
        "initial_coordinate_signature": {
            "version": "feb-node-coordinate-f32-v1",
            "ordering": "ascending-node-id",
            "node_id_encoding": "uint64-big-endian",
            "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
            "node_count": 8,
            "component_count": 24,
            "sha256": fbs_coordinate,
        },
        "kinematic_checks": [
            {
                "field": "displacement",
                "population": "rigid-domain",
                "full_vector_checked": True,
                "operation6_consistent": True,
                "maximum_vector_error": 5.0e-7,
                "maximum_magnitude_error": 5.0e-7,
                "maximum_norm_consistency_error": 5.0e-7,
                "all_nodes_all_target_states_checked": True,
            }
        ],
    }
    canonical_write(attempt.root / "fbs-verification.json", fbs)
    completion = {
        "kind": "completion-decision",
        "status": "accepted",
        "case_state": "SOLVED",
        "attempt_id": attempt.attempt_id,
        "evidence_records": [
            {
                "role": "process-evidence",
                "path": "process-evidence.json",
                "bytes": (
                    attempt.root / "process-evidence.json"
                ).stat().st_size,
                "sha256": sha256_file(
                    attempt.root / "process-evidence.json"
                ),
            },
            {
                "role": "log-verification",
                "path": "log-verification.json",
                "bytes": (
                    attempt.root / "log-verification.json"
                ).stat().st_size,
                "sha256": sha256_file(
                    attempt.root / "log-verification.json"
                ),
            },
            {
                "role": "fbs-verification",
                "path": "fbs-verification.json",
                "bytes": (
                    attempt.root / "fbs-verification.json"
                ).stat().st_size,
                "sha256": sha256_file(attempt.root / "fbs-verification.json"),
            }
        ],
        "evidence_set_sha256": "E" * 64,
        "blocking_reasons": [],
        "failure_class": None,
    }
    canonical_write(attempt.root / "completion-decision.json", completion)
    model = {
        "schema_version": 1,
        "kind": "model-evidence",
        "attempt_id": attempt.attempt_id,
        "source": {
            "path": str(input_feb.resolve()),
            "bytes": input_feb.stat().st_size,
            "sha256": sha256_file(input_feb),
        },
        "excluded_domains": list(model_excluded_domains),
        "model_signature_version": "feb-model-signature-v1",
        "model_signature_sha256": MODEL_SIGNATURE,
        "domain_signature_version": "feb-domain-signature-v1",
        "domain_signature_sha256": "1" * 64,
        "node_count": 8,
        "element_count": 2,
        "connectivity_signature_version": "feb-fbs-connectivity-signature-v1",
        "connectivity_signature_sha256": CONNECTIVITY_SIGNATURE,
        "domain_bindings": [
            {
                "domain_alias": "Part2",
                "material_name": "ABS",
                "element_count": 1,
                "referenced_node_count": 4,
                "element_type": "TET4",
                "nodes_per_element": 4,
                "connectivity_signature_version": (
                    "feb-domain-connectivity-signature-v1"
                ),
                "connectivity_signature_sha256": "8" * 64,
            },
            {
                "domain_alias": "rigid-domain",
                "material_name": "rigid-material",
                "element_count": 1,
                "referenced_node_count": 4,
                "element_type": "TET4",
                "nodes_per_element": 4,
                "connectivity_signature_version": (
                    "feb-domain-connectivity-signature-v1"
                ),
                "connectivity_signature_sha256": "9" * 64,
            },
        ],
        "initial_coordinate_signature": {
            "version": "feb-node-coordinate-f32-v1",
            "ordering": "ascending-node-id",
            "node_id_encoding": "uint64-big-endian",
            "coordinate_encoding": "ieee754-binary32-big-endian-xyz",
            "node_count": 8,
            "component_count": 24,
            "sha256": COORDINATE_SIGNATURE,
        },
        "invariant_signatures": {
            "domain": "1" * 64,
            "material": "2" * 64,
            "reference_closure": "3" * 64,
            "load": "4" * 64,
            "boundary": "5" * 64,
            "contact": "6" * 64,
            "output": "7" * 64,
        },
    }
    model_path = attempt.root / "model-evidence.json"
    canonical_write(model_path, model)
    fbs_path = attempt.root / "fbs-verification.json"
    fbs["model_evidence_sha256"] = sha256_file(model_path)
    canonical_write(fbs_path, fbs)
    completion["evidence_records"][2].update(
        bytes=fbs_path.stat().st_size,
        sha256=sha256_file(fbs_path),
    )
    completion_path = attempt.root / "completion-decision.json"
    canonical_write(completion_path, completion)
    preflight = {
        "schema_version": 1,
        "status": "accepted",
        "attempt_id": attempt.attempt_id,
        "checked_roles": ["model-evidence"],
        "evidence": [
            {
                "role": "model-evidence",
                "path": str(model_path.resolve()),
                "bytes": model_path.stat().st_size,
                "sha256": sha256_file(model_path),
            }
        ],
    }
    preflight_path = attempt.root / "preflight-decision.json"
    canonical_write(preflight_path, preflight)
    monkeypatch.setattr(
        result_validation,
        "read_event_log",
        lambda path: (
            SimpleNamespace(
                event_type="PREFLIGHT_PASSED",
                to_state=CaseState.PREFLIGHT_PASSED,
                payload={
                    "attempt_id": attempt.attempt_id,
                    "preflight_decision_sha256": sha256_file(preflight_path),
                },
            ),
            SimpleNamespace(
                event_type="COMPLETION_DECIDED",
                to_state=CaseState.SOLVED,
                payload={
                    "attempt_id": attempt.attempt_id,
                    "completion_decision_sha256": sha256_file(
                        attempt.root / "completion-decision.json"
                    ),
                },
            ),
        ),
    )
    monkeypatch.setattr(
        result_validation,
        "inspect_feb",
        lambda path, *, excluded_domains=(): SimpleNamespace(
            domain_signature=(
                "1" * 64
                if excluded_domains == ("rigid-domain",)
                else "0" * 64
            )
        ),
    )
    inputs = ResultValidationInputs(
        attempt=attempt,
        model_evidence_path=model_path,
        fbs_request=SimpleNamespace(
            request_sha256="B" * 64,
            model_evidence_path=model_path,
            model_evidence_bytes=model_path.stat().st_size,
            model_evidence_sha256=sha256_file(model_path),
            xplt_path=xplt.resolve(),
            xplt_bytes=xplt.stat().st_size,
            xplt_sha256=sha256_file(xplt),
            expected_times=(1.0,),
            populations=(
                {
                    "name": "Part2",
                    "domain_aliases": ["Part2"],
                    "require_disjoint": True,
                },
            ),
            fields=(
                {
                    "name": "stress",
                    "operation": "MAT3DS.EFFECTIVE",
                    "association": "elemData",
                    "population": "Part2",
                },
            ),
            kinematic_checks=(
                {
                    "field": "displacement",
                    "population": "rigid-domain",
                },
            ),
        ),
        require_initial_coordinate_bit_equality=True,
        require_rigid_full_vector=True,
        rigid_abs_tol=1.0e-6,
    )
    return case, attempt, inputs

def rewrite_bound_fbs(attempt, mutation) -> None:
    fbs_path = attempt.root / "fbs-verification.json"
    fbs = json.loads(fbs_path.read_text(encoding="utf-8"))
    mutation(fbs)
    canonical_write(fbs_path, fbs)
    completion_path = attempt.root / "completion-decision.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    record = next(
        item
        for item in completion["evidence_records"]
        if item["role"] == "fbs-verification"
    )
    record.update(
        bytes=fbs_path.stat().st_size,
        sha256=sha256_file(fbs_path),
    )
    canonical_write(completion_path, completion)

def test_result_verified_binds_completion_model_mesh_geometry_and_rigid(
    tmp_path, monkeypatch
):
    case, attempt, inputs = setup(tmp_path, monkeypatch)
    result = validate_result(case, inputs)
    assert result.kind == "result-validation"
    assert result.status == "accepted"
    assert result.case_state == "RESULT_VERIFIED"
    assert case.state is CaseState.RESULT_VERIFIED
    assert {item["role"] for item in result.artifacts} == {"xplt", "solver-log"}
    assert result.model_signature_sha256 == MODEL_SIGNATURE
    assert result.node_count == 8
    assert result.element_count == 2
    assert result.connectivity_signature_sha256 == CONNECTIVITY_SIGNATURE
    assert result.initial_coordinate_signature_sha256 == COORDINATE_SIGNATURE
    assert result.connectivity_signature_match is True
    assert result.binary32_initial_coordinate_exact_match is True
    assert result.population_counts_by_partition["Part2"] == {
        "elements": 1,
        "referenced_nodes": 4,
    }
    assert result.request_keys == ("stress:elemData:6:Part2",)
    assert result.empty_array_count == 0
    assert result.nonfinite_value_count == 0
    validate_schema(
        "result-verification",
        json.loads(
            (attempt.root / "result-verification.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    record = result.to_record()
    assert set(record.to_payload()) == {"kind", "data"}
    assert "kind" not in record.data

def test_connectivity_mismatch_is_result_incomplete(tmp_path, monkeypatch):
    case, attempt, inputs = setup(
        tmp_path,
        monkeypatch,
        fbs_connectivity="E" * 64,
    )
    result = validate_result(case, inputs)
    assert result.status == "rejected"
    assert "CONNECTIVITY_SIGNATURE_MISMATCH" in result.blocking_reasons
    assert case.state is CaseState.RESULT_INCOMPLETE

def test_bound_domain_exclusions_are_reused_for_result_reinspection(
    tmp_path,
    monkeypatch,
):
    case, attempt, inputs = setup(
        tmp_path,
        monkeypatch,
        model_excluded_domains=("Part2",),
    )
    result = validate_result(case, inputs)
    assert result.status == "rejected"
    assert "DOMAIN_SIGNATURE_MISMATCH" in result.blocking_reasons
    assert case.state is CaseState.RESULT_INCOMPLETE

def test_all_node_coordinate_mismatch_is_result_incomplete(tmp_path, monkeypatch):
    case, attempt, inputs = setup(
        tmp_path,
        monkeypatch,
        fbs_coordinate="E" * 64,
    )
    result = validate_result(case, inputs)
    assert result.status == "rejected"
    assert "INITIAL_COORDINATE_SIGNATURE_MISMATCH" in result.blocking_reasons
    assert case.state is CaseState.RESULT_INCOMPLETE

def test_missing_approved_field_request_is_result_incomplete(
    tmp_path,
    monkeypatch,
):
    case, attempt, inputs = setup(tmp_path, monkeypatch)
    rewrite_bound_fbs(attempt, lambda value: value.update(fields=[]))
    result = validate_result(case, inputs)
    assert result.status == "rejected"
    assert (
        "FBS_FIELD_REQUEST_COVERAGE_MISMATCH"
        in result.blocking_reasons
    )

def test_model_evidence_path_must_be_the_canonical_attempt_record(tmp_path, monkeypatch):
    case, attempt, inputs = setup(tmp_path, monkeypatch)
    spoof = attempt.root / "generated" / "model-evidence.json"
    spoof.write_bytes(inputs.model_evidence_path.read_bytes())
    with pytest.raises(ValueError, match="MODEL_EVIDENCE_PATH_INVALID"):
        validate_result(case, replace(inputs, model_evidence_path=spoof))

def test_model_evidence_hash_must_match_authoritative_preflight(tmp_path, monkeypatch):
    case, attempt, inputs = setup(tmp_path, monkeypatch)
    inputs.model_evidence_path.write_bytes(
        inputs.model_evidence_path.read_bytes() + b" "
    )
    with pytest.raises(RuntimeError, match="MODEL_EVIDENCE_PREFLIGHT_HASH_DRIFT"):
        validate_result(case, inputs)

@pytest.mark.parametrize(
    ("relative_path", "error"),
    (
        ("solver/solver.log", "SOLVER_LOG_CURRENT_IDENTITY_DRIFT"),
        ("solver/solver.xplt", "SOLVER_XPLT_CURRENT_IDENTITY_DRIFT"),
    ),
)
def test_post_solved_solver_artifact_mutation_blocks_result_validation(
    tmp_path,
    monkeypatch,
    relative_path,
    error,
):
    case, attempt, inputs = setup(tmp_path, monkeypatch)
    (attempt.root / relative_path).write_bytes(b"post-solved drift")
    with pytest.raises(RuntimeError, match=error):
        validate_result(case, inputs)
```

- [ ] **Step 3: Run both tests and verify the exact RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_completion.py apps/febio_cae_harness/tests/unit/test_result_validation.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.completion'`.

- [ ] **Step 4: Implement transitive composite completion**

```python
# completion.py
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from febio_cae_harness.attempt_store import AttemptStore, _write_json
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.failure import FailureClass
from febio_cae_harness.fbs_protocol import XpltRequest
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.response import EvidenceRecord
from febio_cae_harness.schema import validate_schema

@dataclass(frozen=True)
class CompletionDecision:
    kind: str
    status: str
    case_state: str
    attempt_id: str
    evidence_records: tuple[dict[str, object], ...]
    evidence_set_sha256: str
    blocking_reasons: tuple[str, ...]
    failure_class: str | None

    def to_record(self) -> EvidenceRecord:
        payload = asdict(self)
        kind = str(payload.pop("kind"))
        return EvidenceRecord(kind=kind, data=payload)

def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()

def _fresh(process: dict[str, object], key: str) -> bool:
    before = process["artifacts_before"][key]
    after = process["artifacts_after"][key]
    return before.get("exists") is False and after.get("exists") is True

def _request_contract_blockers(
    request: XpltRequest,
    fbs: dict[str, object],
) -> list[str]:
    reasons: list[str] = []
    if fbs.get("request_id") != request.request_sha256:
        reasons.append("FBS_REQUEST_ID_MISMATCH")
    expected_fields = {
        (
            str(item["name"]),
            str(item["association"]),
            item["operation"],
            str(item["population"]),
        )
        for item in request.fields
    }
    observed_fields = {
        (
            str(item["name"]),
            str(item["association"]),
            item.get("operation_source"),
            str(item["population"]),
        )
        for item in fbs.get("fields", [])
    }
    if not expected_fields or observed_fields != expected_fields:
        reasons.append("FBS_FIELD_REQUEST_COVERAGE_MISMATCH")
    expected_populations = {
        str(item["name"]): frozenset(
            str(value) for value in item["domain_aliases"]
        )
        for item in request.populations
    }
    observed_populations = {
        str(item["name"]): frozenset(
            str(value) for value in item["domain_aliases"]
        )
        for item in fbs.get("populations", [])
    }
    if observed_populations != expected_populations:
        reasons.append("FBS_POPULATION_COVERAGE_MISMATCH")
    if len(fbs.get("state_matches", [])) != len(request.expected_times):
        reasons.append("FBS_STATE_REQUEST_COVERAGE_MISMATCH")
    expected_kinematics = {
        (str(item["field"]), str(item["population"]))
        for item in request.kinematic_checks
    }
    observed_kinematics = {
        (str(item["field"]), str(item["population"]))
        for item in fbs.get("kinematic_checks", [])
    }
    if observed_kinematics != expected_kinematics:
        reasons.append("FBS_KINEMATIC_REQUEST_COVERAGE_MISMATCH")
    return reasons

def _completion_blockers(
    process: dict[str, object],
    log: dict[str, object],
    fbs: dict[str, object],
    request: XpltRequest | None,
) -> list[str]:
    reasons = (
        ["FBS_REQUEST_UNAVAILABLE"]
        if request is None
        else _request_contract_blockers(request, fbs)
    )
    if (
        process.get("exit_code") != 0
        or process.get("termination_reason") != "EXITED"
        or process.get("job_object_assigned") is not True
    ):
        reasons.append("PROCESS_NOT_SUCCESSFUL")
    if process.get("active_processes_after_finalize") != 0:
        reasons.append("PROCESS_TREE_NOT_EMPTY")
    for key in ("log", "xplt"):
        if not _fresh(process, key):
            reasons.append(f"{key.upper()}_NOT_FRESH")
    if log.get("status") != "accepted":
        reasons.append("LOG_NOT_ACCEPTED")
    if log.get("unresolved_warnings"):
        reasons.append("LOG_WARNING_UNRESOLVED")
    if log.get("sha256") != process["artifacts_after"]["log"].get("sha256"):
        reasons.append("LOG_PROCESS_HASH_MISMATCH")
    if fbs.get("status") != "accepted":
        reasons.append("FBS_NOT_ACCEPTED")
    if fbs.get("xplt", {}).get("sha256") != process["artifacts_after"]["xplt"].get("sha256"):
        reasons.append("FBS_PROCESS_XPLT_HASH_MISMATCH")
    if any(
        fbs.get(field)
        != process["artifacts_after"]["xplt"].get("sha256")
        for field in (
            "parent_pre_worker_xplt_sha256",
            "worker_xplt_sha256",
            "parent_post_worker_xplt_sha256",
        )
    ):
        reasons.append("FBS_XPLT_TOCTOU_HASH_MISMATCH")
    if not fbs.get("state_matches"):
        reasons.append("FBS_TARGET_STATES_MISSING")
    connectivity = fbs.get("connectivity", {})
    if (
        connectivity.get("version") != "feb-fbs-connectivity-signature-v1"
        or not isinstance(connectivity.get("node_count"), int)
        or connectivity.get("node_count", 0) <= 0
        or not isinstance(connectivity.get("element_count"), int)
        or connectivity.get("element_count", 0) <= 0
        or not isinstance(connectivity.get("signature_sha256"), str)
        or len(connectivity.get("signature_sha256", "")) != 64
    ):
        reasons.append("FBS_CONNECTIVITY_SIGNATURE_INVALID")
    coordinate = fbs.get("initial_coordinate_signature", {})
    if (
        coordinate.get("version") != "feb-node-coordinate-f32-v1"
        or not isinstance(coordinate.get("node_count"), int)
        or coordinate.get("node_count", 0) <= 0
        or coordinate.get("component_count") != coordinate.get("node_count", 0) * 3
        or not isinstance(coordinate.get("sha256"), str)
        or len(coordinate.get("sha256", "")) != 64
    ):
        reasons.append("FBS_INITIAL_COORDINATE_SIGNATURE_INVALID")
    if not fbs.get("fields"):
        reasons.append("FBS_FIELDS_MISSING")
    for field in fbs.get("fields", []):
        if not field.get("states"):
            reasons.append("FBS_FIELD_EMPTY")
            continue
        for state in field["states"]:
            if state.get("value_count", 0) <= 0:
                reasons.append("FBS_FIELD_EMPTY")
            if state.get("all_finite") is not True:
                reasons.append("FBS_FIELD_NONFINITE")
    return list(dict.fromkeys(reasons))

def decide_completion(
    case: CaseStore,
    attempt: AttemptStore,
    process: dict[str, object],
    log: dict[str, object],
    fbs: dict[str, object],
    log_failure: dict[str, object] | None,
    fbs_request: XpltRequest | None,
) -> CompletionDecision:
    records = (
        ("process-evidence", "process-evidence.json", process),
        ("log-verification", "log-verification.json", log),
        ("fbs-verification", "fbs-verification.json", fbs),
    )
    with case.locked() as transaction:
        projection = transaction.replay()
        if projection["harness"]["case_state"] != CaseState.PREFLIGHT_PASSED.value:
            raise RuntimeError("completion requires PREFLIGHT_PASSED")
        blockers = _completion_blockers(
            process,
            log,
            fbs,
            fbs_request,
        )
        typed_failure = (
            None
            if log_failure is None
            else FailureClass(str(log_failure["failure_class"]))
        )
        if typed_failure is not None:
            blockers.append("LOG_ENGINEERING_FAILURE")
            blockers = list(dict.fromkeys(blockers))
        if not blockers:
            validate_schema("log-evidence", log)
            validate_schema("fbs-evidence", fbs)
        evidence_records: list[dict[str, object]] = []
        for role, name, value in records:
            if value.get("kind") != role:
                raise RuntimeError(f"evidence kind mismatch: {role}")
            path = attempt.root / name
            _write_json(path, value)
            evidence_records.append(
                {
                    "role": role,
                    "path": name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        if not blockers:
            target = CaseState.SOLVED
            failure_class = None
        elif typed_failure not in {None, FailureClass.RESULT_EVIDENCE_ERROR}:
            target = CaseState.SOLVE_FAILED
            failure_class = typed_failure.value
        elif any(
            reason.startswith("PROCESS_") or reason.endswith("_NOT_FRESH")
            for reason in blockers
        ):
            target = CaseState.SOLVE_FAILED
            failure_class = FailureClass.RESOURCE_OR_TOOL_ERROR.value
        else:
            target = CaseState.RESULT_INCOMPLETE
            failure_class = FailureClass.RESULT_EVIDENCE_ERROR.value
        evidence_set_sha256 = _canonical_hash(evidence_records)
        decision = CompletionDecision(
            kind="completion-decision",
            status="accepted" if not blockers else "rejected",
            case_state=target.value,
            attempt_id=attempt.attempt_id,
            evidence_records=tuple(evidence_records),
            evidence_set_sha256=evidence_set_sha256,
            blocking_reasons=tuple(blockers),
            failure_class=failure_class,
        )
        path = attempt.root / "completion-decision.json"
        decision_payload = json.loads(
            json.dumps(
                asdict(decision),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        validate_schema("completion-decision", decision_payload)
        _write_json(path, decision_payload)
        transaction.append(
            "COMPLETION_DECIDED",
            target,
            {
                "attempt_id": attempt.attempt_id,
                "completion_decision_sha256": sha256_file(path),
                "evidence_set_sha256": evidence_set_sha256,
                "blocking_reasons": blockers,
                "failure_class": failure_class,
            },
        )
        return decision
```

- [ ] **Step 5: Implement authoritative model/result validation**

```python
# result_validation.py
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from febio_cae_harness.attempt_store import AttemptStore, _write_json
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.events import read_event_log
from febio_cae_harness.feb_inspector import inspect_feb
from febio_cae_harness.fbs_protocol import XpltRequest
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.response import EvidenceRecord
from febio_cae_harness.schema import validate_schema

@dataclass(frozen=True)
class ResultValidationInputs:
    attempt: AttemptStore
    model_evidence_path: Path
    fbs_request: XpltRequest
    require_initial_coordinate_bit_equality: bool
    require_rigid_full_vector: bool
    rigid_abs_tol: float

@dataclass(frozen=True)
class ResultValidation:
    kind: str
    status: str
    case_state: str
    attempt_id: str
    completion_decision_sha256: str
    model_evidence_sha256: str
    fbs_evidence_sha256: str
    model_signature_sha256: str
    node_count: int
    element_count: int
    connectivity_signature_version: str
    connectivity_signature_sha256: str
    initial_coordinate_signature_version: str
    initial_coordinate_signature_sha256: str
    connectivity_signature_match: bool
    binary32_initial_coordinate_exact_match: bool
    population_counts_by_partition: dict[str, dict[str, int]]
    populations_disjoint: bool
    request_keys: tuple[str, ...]
    empty_array_count: int
    nonfinite_value_count: int
    ambiguous_collection_count: int
    rigid_kinematics: dict[str, object]
    input_feb_sha256_before: str
    input_feb_sha256_after: str
    domain_signature_before: str
    domain_signature_after: str
    blocking_reasons: tuple[str, ...]
    artifacts: tuple[dict[str, object], ...]

    def to_record(self) -> EvidenceRecord:
        payload = asdict(self)
        kind = str(payload.pop("kind"))
        return EvidenceRecord(kind=kind, data=payload)

def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))

def _artifact(
    role: str,
    source: Path,
    directory: str,
    destination_name: str,
) -> dict[str, object]:
    return {
        "role": role,
        "source_path": str(source.resolve(strict=True)),
        "source_bytes": source.stat().st_size,
        "source_sha256": sha256_file(source),
        "destination_directory": directory,
        "destination_name": destination_name,
    }

def _authoritative_event(
    case: CaseStore,
    event_type: str,
    attempt_id: str,
    to_state: CaseState,
):
    matches = [
        event
        for event in read_event_log(case.event_log)
        if event.event_type == event_type
        and event.payload.get("attempt_id") == attempt_id
    ]
    if not matches or matches[-1].to_state is not to_state:
        raise RuntimeError(f"AUTHORITATIVE_{event_type}_EVENT_ABSENT")
    return matches[-1]

def _bound_record(
    records: object,
    role: str,
) -> dict[str, object]:
    matches = [
        record
        for record in records
        if isinstance(record, dict) and record.get("role") == role
    ]
    if len(matches) != 1:
        raise RuntimeError(f"AUTHORITATIVE_{role.upper()}_RECORD_COUNT")
    return matches[0]

def _record_path(attempt: AttemptStore, record: dict[str, object]) -> Path:
    supplied = Path(str(record["path"]))
    return (
        supplied.resolve(strict=True)
        if supplied.is_absolute()
        else (attempt.root / supplied).resolve(strict=True)
    )

def _matches_current_artifact(
    record: object,
    expected_path: Path,
    expected_sha256: str,
) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        supplied = Path(str(record["path"])).resolve(strict=True)
        return (
            supplied == expected_path.resolve(strict=True)
            and int(record["bytes"]) == expected_path.stat().st_size
            and str(record["sha256"]) == expected_sha256
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False

def _population_counts(
    partitions: object,
) -> tuple[dict[str, dict[str, int]], bool]:
    counts: dict[str, dict[str, int]] = {}
    occupied: set[int] = set()
    disjoint = True
    for partition in partitions:
        name = str(partition["domain_alias"])
        if name in counts:
            raise RuntimeError("FBS_PARTITION_NAME_AMBIGUOUS")
        element_ids = tuple(int(value) for value in partition["element_ids"])
        node_ids = tuple(int(value) for value in partition["node_ids"])
        current = set(element_ids)
        if occupied.intersection(current):
            disjoint = False
        occupied.update(current)
        counts[name] = {
            "elements": len(element_ids),
            "referenced_nodes": len(set(node_ids)),
        }
    return counts, disjoint

def _request_metrics(
    fbs: dict[str, object],
) -> tuple[tuple[str, ...], int, int]:
    keys: list[str] = []
    empty = 0
    nonfinite = 0
    for field in fbs.get("fields", []):
        keys.append(
            ":".join(
                (
                    str(field["name"]),
                    str(field["association"]),
                    str(field["operation"]),
                    str(field["population"]),
                )
            )
        )
        for state in field["states"]:
            if int(state["value_count"]) <= 0:
                empty += 1
            if state["all_finite"] is not True:
                nonfinite += 1
    return tuple(keys), empty, nonfinite

def _request_coverage_blockers(
    request: XpltRequest,
    fbs: dict[str, object],
) -> list[str]:
    reasons: list[str] = []
    if fbs.get("request_id") != request.request_sha256:
        reasons.append("FBS_REQUEST_ID_MISMATCH")
    expected_fields = {
        (
            str(item["name"]),
            str(item["association"]),
            item["operation"],
            str(item["population"]),
        )
        for item in request.fields
    }
    observed_fields = {
        (
            str(item["name"]),
            str(item["association"]),
            item.get("operation_source"),
            str(item["population"]),
        )
        for item in fbs.get("fields", [])
    }
    if not expected_fields or observed_fields != expected_fields:
        reasons.append("FBS_FIELD_REQUEST_COVERAGE_MISMATCH")
    expected_populations = {
        str(item["name"]): tuple(
            sorted(
                (str(value) for value in item["domain_aliases"]),
                key=str.casefold,
            )
        )
        for item in request.populations
    }
    observed_populations = {
        str(item["name"]): tuple(
            sorted(
                (str(value) for value in item["domain_aliases"]),
                key=str.casefold,
            )
        )
        for item in fbs.get("populations", [])
    }
    if (
        not expected_populations
        or observed_populations != expected_populations
    ):
        reasons.append("FBS_POPULATION_COVERAGE_MISMATCH")
    matches = fbs.get("state_matches", [])
    if (
        len(matches) != len(request.expected_times)
        or {int(item["expected_index"]) for item in matches}
        != set(range(len(request.expected_times)))
    ):
        reasons.append("FBS_STATE_REQUEST_COVERAGE_MISMATCH")
    expected_kinematics = {
        (str(item["field"]), str(item["population"]))
        for item in request.kinematic_checks
    }
    observed_kinematics = {
        (str(item["field"]), str(item["population"]))
        for item in fbs.get("kinematic_checks", [])
    }
    if observed_kinematics != expected_kinematics:
        reasons.append("FBS_KINEMATIC_REQUEST_COVERAGE_MISMATCH")
    return reasons

def _rigid_summary(fbs: dict[str, object]) -> dict[str, object]:
    checks = tuple(fbs.get("kinematic_checks", []))
    return {
        "maximum_component_abs_error_mm": max(
            (float(item["maximum_vector_error"]) for item in checks),
            default=0.0,
        ),
        "maximum_magnitude_abs_error_mm": max(
            (float(item["maximum_magnitude_error"]) for item in checks),
            default=0.0,
        ),
        "maximum_norm_consistency_abs_error_mm": max(
            (
                float(item["maximum_norm_consistency_error"])
                for item in checks
            ),
            default=0.0,
        ),
        "all_nodes_all_target_states_checked": bool(checks) and all(
            item.get("all_nodes_all_target_states_checked") is True
            for item in checks
        ),
    }

def validate_result(
    case: CaseStore,
    inputs: ResultValidationInputs,
) -> ResultValidation:
    attempt = inputs.attempt
    preflight_path = attempt.root / "preflight-decision.json"
    completion_path = attempt.root / "completion-decision.json"
    fbs_path = attempt.root / "fbs-verification.json"
    canonical_model_path = attempt.root / "model-evidence.json"
    if (
        inputs.model_evidence_path.resolve(strict=True)
        != canonical_model_path.resolve(strict=True)
    ):
        raise ValueError("MODEL_EVIDENCE_PATH_INVALID")
    with case.locked() as transaction:
        projection = transaction.replay()
        if projection["harness"]["case_state"] != CaseState.SOLVED.value:
            raise RuntimeError("result validation requires SOLVED")
        preflight_event = _authoritative_event(
            case,
            "PREFLIGHT_PASSED",
            attempt.attempt_id,
            CaseState.PREFLIGHT_PASSED,
        )
        if (
            sha256_file(preflight_path)
            != preflight_event.payload.get("preflight_decision_sha256")
        ):
            raise RuntimeError("PREFLIGHT_DECISION_EVENT_HASH_DRIFT")
        completion_event = _authoritative_event(
            case,
            "COMPLETION_DECIDED",
            attempt.attempt_id,
            CaseState.SOLVED,
        )
        completion_sha256 = sha256_file(completion_path)
        if (
            completion_sha256
            != completion_event.payload.get("completion_decision_sha256")
        ):
            raise RuntimeError("COMPLETION_DECISION_EVENT_HASH_DRIFT")
        preflight = _load(preflight_path)
        if (
            preflight.get("status") != "accepted"
            or preflight.get("attempt_id") != attempt.attempt_id
        ):
            raise RuntimeError("AUTHORITATIVE_PREFLIGHT_NOT_ACCEPTED")
        model_record = _bound_record(
            preflight.get("evidence", []),
            "model-evidence",
        )
        if (
            _record_path(attempt, model_record)
            != canonical_model_path.resolve(strict=True)
            or int(model_record["bytes"]) != canonical_model_path.stat().st_size
            or str(model_record["sha256"]) != sha256_file(canonical_model_path)
        ):
            raise RuntimeError("MODEL_EVIDENCE_PREFLIGHT_HASH_DRIFT")
        completion = _load(completion_path)
        process_path = attempt.root / "process-evidence.json"
        log_evidence_path = attempt.root / "log-verification.json"
        evidence_paths = {
            "process-evidence": process_path,
            "log-verification": log_evidence_path,
            "fbs-verification": fbs_path,
        }
        for role, evidence_path in evidence_paths.items():
            record = _bound_record(
                completion.get("evidence_records", []),
                role,
            )
            if (
                _record_path(attempt, record)
                != evidence_path.resolve(strict=True)
                or int(record["bytes"]) != evidence_path.stat().st_size
                or str(record["sha256"]) != sha256_file(evidence_path)
            ):
                raise RuntimeError(f"{role.upper()}_EVIDENCE_BINDING_DRIFT")
        process = _load(process_path)
        log_evidence = _load(log_evidence_path)
        fbs = _load(fbs_path)
        model = _load(inputs.model_evidence_path)
        validate_schema("model-evidence", model)
        if model.get("attempt_id") != attempt.attempt_id:
            raise RuntimeError("MODEL_EVIDENCE_ATTEMPT_MISMATCH")
        if (
            fbs.get("model_evidence_sha256")
            != sha256_file(canonical_model_path)
        ):
            raise RuntimeError("FBS_MODEL_EVIDENCE_HASH_DRIFT")
        if (
            completion.get("kind") != "completion-decision"
            or completion.get("status") != "accepted"
            or completion.get("case_state") != CaseState.SOLVED.value
            or completion.get("attempt_id") != attempt.attempt_id
        ):
            raise RuntimeError("authoritative completion is not accepted")
        solver_log = (attempt.paths.solver / "solver.log").resolve(strict=True)
        solver_xplt = (
            attempt.paths.solver / "solver.xplt"
        ).resolve(strict=True)
        current_log_sha256 = sha256_file(solver_log)
        current_xplt_sha256 = sha256_file(solver_xplt)
        if (
            inputs.fbs_request.model_evidence_path.resolve(strict=True)
            != canonical_model_path.resolve(strict=True)
            or inputs.fbs_request.model_evidence_bytes
            != canonical_model_path.stat().st_size
            or inputs.fbs_request.model_evidence_sha256
            != sha256_file(canonical_model_path)
        ):
            raise RuntimeError("FBS_REQUEST_MODEL_EVIDENCE_DRIFT")
        if (
            not _matches_current_artifact(
                process.get("artifacts_after", {}).get("log"),
                solver_log,
                current_log_sha256,
            )
            or not _matches_current_artifact(
                log_evidence,
                solver_log,
                current_log_sha256,
            )
        ):
            raise RuntimeError("SOLVER_LOG_CURRENT_IDENTITY_DRIFT")
        if (
            not _matches_current_artifact(
                process.get("artifacts_after", {}).get("xplt"),
                solver_xplt,
                current_xplt_sha256,
            )
            or not _matches_current_artifact(
                fbs.get("xplt"),
                solver_xplt,
                current_xplt_sha256,
            )
            or any(
                fbs.get(field) != current_xplt_sha256
                for field in (
                    "parent_pre_worker_xplt_sha256",
                    "worker_xplt_sha256",
                    "parent_post_worker_xplt_sha256",
                )
            )
        ):
            raise RuntimeError("SOLVER_XPLT_CURRENT_IDENTITY_DRIFT")
        if (
            inputs.fbs_request.xplt_path.resolve(strict=True) != solver_xplt
            or inputs.fbs_request.xplt_bytes != solver_xplt.stat().st_size
            or inputs.fbs_request.xplt_sha256 != current_xplt_sha256
        ):
            raise RuntimeError("FBS_REQUEST_XPLT_IDENTITY_DRIFT")
        model_source = Path(str(model["source"]["path"])).resolve(strict=True)
        expected_source = (attempt.paths.solver / "input.feb").resolve(strict=True)
        if model_source != expected_source:
            raise RuntimeError("MODEL_EVIDENCE_SOURCE_PATH_INVALID")
        input_sha256_before = str(model["source"]["sha256"])
        input_sha256_after = sha256_file(model_source)
        after_inspection = inspect_feb(
            model_source,
            excluded_domains=tuple(
                str(item) for item in model["excluded_domains"]
            ),
        )
        domain_before = str(model["domain_signature_sha256"])
        domain_after = after_inspection.domain_signature
        model_connectivity = str(model["connectivity_signature_sha256"])
        fbs_connectivity = str(fbs["connectivity"]["signature_sha256"])
        model_coordinate = model["initial_coordinate_signature"]
        fbs_coordinate = fbs["initial_coordinate_signature"]
        connectivity_match = (
            fbs["connectivity"].get("version")
            == model.get("connectivity_signature_version")
            and fbs_connectivity == model_connectivity
        )
        coordinate_match = (
            fbs_coordinate.get("version") == model_coordinate.get("version")
            and fbs_coordinate.get("node_count") == model_coordinate.get("node_count")
            and fbs_coordinate.get("component_count")
            == model_coordinate.get("component_count")
            and fbs_coordinate.get("sha256") == model_coordinate.get("sha256")
        )
        blockers = _request_coverage_blockers(inputs.fbs_request, fbs)
        if (
            model_source.stat().st_size != int(model["source"]["bytes"])
            or input_sha256_after != input_sha256_before
        ):
            blockers.append("INPUT_FEB_HASH_MISMATCH")
        if domain_after != domain_before:
            blockers.append("DOMAIN_SIGNATURE_MISMATCH")
        if (
            fbs["mesh"].get("node_count") != model.get("node_count")
            or fbs_coordinate.get("node_count") != model.get("node_count")
        ):
            blockers.append("NODE_COUNT_MISMATCH")
        if (
            fbs["mesh"].get("element_count") != model.get("element_count")
            or fbs["connectivity"].get("element_count") != model.get("element_count")
        ):
            blockers.append("ELEMENT_COUNT_MISMATCH")
        if not connectivity_match:
            blockers.append("CONNECTIVITY_SIGNATURE_MISMATCH")
        if (
            inputs.require_initial_coordinate_bit_equality
            and (
                not coordinate_match
                or fbs["initial_coordinate_check"].get(
                    "all_binary32_bits_equal"
                )
                is not True
            )
        ):
            blockers.append("INITIAL_COORDINATE_SIGNATURE_MISMATCH")
        if inputs.require_rigid_full_vector:
            for check in fbs.get("kinematic_checks", []):
                if (
                    check.get("full_vector_checked") is not True
                    or check.get("operation6_consistent") is not True
                    or check.get("all_nodes_all_target_states_checked") is not True
                    or check.get("maximum_vector_error", float("inf")) > inputs.rigid_abs_tol
                    or check.get("maximum_magnitude_error", float("inf")) > inputs.rigid_abs_tol
                    or check.get(
                        "maximum_norm_consistency_error",
                        float("inf"),
                    )
                    > inputs.rigid_abs_tol
                ):
                    blockers.append("RIGID_KINEMATICS_MISMATCH")
            if not fbs.get("kinematic_checks"):
                blockers.append("RIGID_KINEMATICS_MISSING")
        population_counts, populations_disjoint = _population_counts(
            fbs.get("partitions", [])
        )
        if not populations_disjoint:
            blockers.append("FBS_PARTITION_OVERLAP")
        request_keys, empty_count, nonfinite_count = _request_metrics(fbs)
        if empty_count:
            blockers.append("FBS_FIELD_EMPTY")
        if nonfinite_count:
            blockers.append("FBS_FIELD_NONFINITE")
        rigid_summary = _rigid_summary(fbs)
        target = CaseState.RESULT_VERIFIED if not blockers else CaseState.RESULT_INCOMPLETE
        artifacts = (
            _artifact(
                "xplt",
                attempt.paths.solver / "solver.xplt",
                "03_Result",
                "solver.xplt",
            ),
            _artifact(
                "solver-log",
                attempt.paths.solver / "solver.log",
                "05_Verification",
                "solver.log",
            ),
        ) if not blockers else ()
        result = ResultValidation(
            kind="result-validation",
            status="accepted" if not blockers else "rejected",
            case_state=target.value,
            attempt_id=attempt.attempt_id,
            completion_decision_sha256=completion_sha256,
            model_evidence_sha256=sha256_file(inputs.model_evidence_path),
            fbs_evidence_sha256=sha256_file(fbs_path),
            model_signature_sha256=str(model["model_signature_sha256"]),
            node_count=int(model["node_count"]),
            element_count=int(model["element_count"]),
            connectivity_signature_version=str(
                model["connectivity_signature_version"]
            ),
            connectivity_signature_sha256=model_connectivity,
            initial_coordinate_signature_version=str(
                model_coordinate["version"]
            ),
            initial_coordinate_signature_sha256=str(model_coordinate["sha256"]),
            connectivity_signature_match=connectivity_match,
            binary32_initial_coordinate_exact_match=coordinate_match,
            population_counts_by_partition=population_counts,
            populations_disjoint=populations_disjoint,
            request_keys=request_keys,
            empty_array_count=empty_count,
            nonfinite_value_count=nonfinite_count,
            ambiguous_collection_count=0,
            rigid_kinematics=rigid_summary,
            input_feb_sha256_before=input_sha256_before,
            input_feb_sha256_after=input_sha256_after,
            domain_signature_before=domain_before,
            domain_signature_after=domain_after,
            blocking_reasons=tuple(dict.fromkeys(blockers)),
            artifacts=artifacts,
        )
        path = attempt.root / "result-verification.json"
        result_payload = json.loads(
            json.dumps(
                asdict(result),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        validate_schema("result-verification", result_payload)
        _write_json(path, result_payload)
        transaction.append(
            "RESULT_VALIDATED",
            target,
            {
                "attempt_id": attempt.attempt_id,
                "result_verification_sha256": sha256_file(path),
                "completion_decision_sha256": result.completion_decision_sha256,
                "blocking_reasons": list(result.blocking_reasons),
            },
        )
        return result
```

- [ ] **Step 6: Add strict completion/result schemas and installed-resource entries**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-cae-harness.local/schemas/completion-decision.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "kind", "status", "case_state", "attempt_id", "evidence_records",
    "evidence_set_sha256", "blocking_reasons", "failure_class"
  ],
  "properties": {
    "kind": {"const": "completion-decision"},
    "status": {"enum": ["accepted", "rejected"]},
    "case_state": {"enum": ["SOLVED", "SOLVE_FAILED", "RESULT_INCOMPLETE"]},
    "attempt_id": {"type": "string", "minLength": 1},
    "evidence_records": {
      "type": "array", "minItems": 3, "maxItems": 3,
      "uniqueItems": true,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["role", "path", "bytes", "sha256"],
        "properties": {
          "role": {
            "enum": [
              "process-evidence", "log-verification", "fbs-verification"
            ]
          },
          "path": {"type": "string", "minLength": 1},
          "bytes": {"type": "integer", "minimum": 1},
          "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
        }
      }
    },
    "evidence_set_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "blocking_reasons": {"type": "array", "items": {"type": "string"}},
    "failure_class": {
      "enum": [
        null,
        "INPUT_OR_REFERENCE_ERROR", "GEOMETRY_ERROR",
        "INITIAL_MESH_ERROR", "DEFORMATION_MESH_ERROR",
        "MODEL_SEMANTICS_ERROR", "NONLINEAR_CONVERGENCE_ERROR",
        "RESULT_EVIDENCE_ERROR", "RESOURCE_OR_TOOL_ERROR"
      ]
    }
  }
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-cae-harness.local/schemas/result-verification.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "kind", "status", "case_state", "attempt_id",
    "completion_decision_sha256", "model_evidence_sha256",
    "fbs_evidence_sha256", "model_signature_sha256",
    "node_count", "element_count",
    "connectivity_signature_version", "connectivity_signature_sha256",
    "initial_coordinate_signature_version",
    "initial_coordinate_signature_sha256",
    "connectivity_signature_match",
    "binary32_initial_coordinate_exact_match",
    "population_counts_by_partition", "populations_disjoint",
    "request_keys", "empty_array_count", "nonfinite_value_count",
    "ambiguous_collection_count", "rigid_kinematics",
    "input_feb_sha256_before", "input_feb_sha256_after",
    "domain_signature_before", "domain_signature_after",
    "blocking_reasons", "artifacts"
  ],
  "properties": {
    "kind": {"const": "result-validation"},
    "status": {"enum": ["accepted", "rejected"]},
    "case_state": {"enum": ["RESULT_VERIFIED", "RESULT_INCOMPLETE"]},
    "attempt_id": {"type": "string", "minLength": 1},
    "completion_decision_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "model_evidence_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "fbs_evidence_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "model_signature_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "node_count": {"type": "integer", "minimum": 1},
    "element_count": {"type": "integer", "minimum": 1},
    "connectivity_signature_version": {
      "const": "feb-fbs-connectivity-signature-v1"
    },
    "connectivity_signature_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "initial_coordinate_signature_version": {
      "const": "feb-node-coordinate-f32-v1"
    },
    "initial_coordinate_signature_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "connectivity_signature_match": {"type": "boolean"},
    "binary32_initial_coordinate_exact_match": {"type": "boolean"},
    "population_counts_by_partition": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "additionalProperties": false,
        "required": ["elements", "referenced_nodes"],
        "properties": {
          "elements": {"type": "integer", "minimum": 1},
          "referenced_nodes": {"type": "integer", "minimum": 1}
        }
      }
    },
    "populations_disjoint": {"type": "boolean"},
    "request_keys": {
      "type": "array", "uniqueItems": true,
      "items": {"type": "string", "minLength": 1}
    },
    "empty_array_count": {"type": "integer", "minimum": 0},
    "nonfinite_value_count": {"type": "integer", "minimum": 0},
    "ambiguous_collection_count": {"type": "integer", "minimum": 0},
    "rigid_kinematics": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "maximum_component_abs_error_mm",
        "maximum_magnitude_abs_error_mm",
        "maximum_norm_consistency_abs_error_mm",
        "all_nodes_all_target_states_checked"
      ],
      "properties": {
        "maximum_component_abs_error_mm": {"type": "number", "minimum": 0},
        "maximum_magnitude_abs_error_mm": {"type": "number", "minimum": 0},
        "maximum_norm_consistency_abs_error_mm": {
          "type": "number", "minimum": 0
        },
        "all_nodes_all_target_states_checked": {"type": "boolean"}
      }
    },
    "input_feb_sha256_before": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "input_feb_sha256_after": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "domain_signature_before": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "domain_signature_after": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "blocking_reasons": {
      "type": "array", "uniqueItems": true,
      "items": {"type": "string", "minLength": 1}
    },
    "artifacts": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "role", "source_path", "source_bytes", "source_sha256",
          "destination_directory", "destination_name"
        ],
        "properties": {
          "role": {"enum": ["xplt", "solver-log"]},
          "source_path": {"type": "string", "minLength": 1},
          "source_bytes": {"type": "integer", "minimum": 1},
          "source_sha256": {
            "type": "string", "pattern": "^[0-9A-F]{64}$"
          },
          "destination_directory": {
            "enum": ["03_Result", "05_Verification"]
          },
          "destination_name": {
            "enum": ["solver.xplt", "solver.log"]
          }
        }
      }
    }
  },
  "allOf": [
    {
      "if": {"properties": {"status": {"const": "accepted"}}},
      "then": {
        "properties": {
          "case_state": {"const": "RESULT_VERIFIED"},
          "blocking_reasons": {"maxItems": 0},
          "artifacts": {"minItems": 2, "maxItems": 2}
        }
      }
    },
    {
      "if": {"properties": {"status": {"const": "rejected"}}},
      "then": {
        "properties": {
          "case_state": {"const": "RESULT_INCOMPLETE"},
          "blocking_reasons": {"minItems": 1},
          "artifacts": {"maxItems": 0}
        }
      }
    }
  ]
}
```

Add these exact paths to `EXPECTED_RESOURCES`:

```python
"schemas/completion-decision.schema.json",
"schemas/result-verification.schema.json",
```

- [ ] **Step 7: Run the GREEN commands for composite, promotion, schema, and full regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_completion.py apps/febio_cae_harness/tests/unit/test_result_validation.py apps/febio_cae_harness/tests/integration/test_promotion.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit apps/febio_cae_harness/tests/integration apps/febio_cae_harness/tests/contract -q
```

Expected: both commands exit `0`; `SOLVED` requires all process/LOG/FBS conditions, `RESULT_VERIFIED` requires model/count/connectivity/coordinate/rigid conditions, and spoofed promotion remains blocked.

- [ ] **Step 8: Inspect, stage, and commit exactly Task 13 files**

```powershell
git status --short
git add apps/febio_cae_harness/src/febio_cae_harness/completion.py apps/febio_cae_harness/src/febio_cae_harness/result_validation.py apps/febio_cae_harness/src/febio_cae_harness/schemas/completion-decision.schema.json apps/febio_cae_harness/src/febio_cae_harness/schemas/result-verification.schema.json apps/febio_cae_harness/tests/unit/test_completion.py apps/febio_cae_harness/tests/unit/test_result_validation.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git diff --cached --check
git commit -m "feat: require composite solved and verified results"
```
