# FEBio CAE Harness Phase 1C Orchestration and Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 1Aのケース・意図契約とPhase 1Bの実行証拠を結合し、意図依存診断、許可済み診断再実行、機械可読CLI、決定論的監査レポートを実装する。

**Architecture:** 診断は証拠から提案を作るだけとし、FEB変更はversioned policyとAnalysis Intentの積集合だけをbyte-preserving patcherが実行する。CLI orchestratorは状態機械の唯一の公開入口となり、レポートはevent/evidenceのhash拘束済み投影だけから生成する。

**Tech Stack:** Python 3.12、jsonschema 4.26.0、pytest 9.1.1、PowerShell 5.1、FEBio XML 4.0

## Global Constraints

- このサブプランは`2026-07-30-febio-llm-cae-harness-phase1.md`の
  Sections 3、4、6、8にあるPhase 1C受入契約を実行可能なTDD粒度へ展開し、
  正本の禁止事項と状態遷移を緩和しない。
- 作業場所は`codex/febio-cae-harness-phase1`の隔離worktreeだけとする。
- 実CAEデータをGitへ入れず、テストは小型合成fixtureだけを使う。
- Phase 1はメッシュ、材料、荷重、拘束、接触、剛体条件、load controller、Outputを自動変更しない。
- 自動patchは`purpose="diagnostic"`かつ`eligible_for_promotion=false`の新attemptだけへcreate-newする。
- 各TaskでREDを観測してから最小実装を書き、focused test、関連regression、`git diff --check`、明示的`git add`、commitの順に進める。

---

## Task 1: Versioned diagnostic-control policy

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/data/policies/febio-4.12-diagnostic-controls.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/diagnostic-control-policy.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/diagnostic_policy.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`
- Create: `apps/febio_cae_harness/tests/unit/test_diagnostic_policy.py`

**Interfaces:**

- Consumes: `febio_cae_harness.schema.validate_schema(name: str, instance: object) -> None` from Phase 1A.
- Produces: `DiagnosticSelector`, `DiagnosticControlPolicy`, and `load_diagnostic_policy() -> DiagnosticControlPolicy`.

- [ ] **Step 1: Write the failing policy contract test**

```python
from decimal import Decimal

import pytest

from febio_cae_harness.diagnostic_policy import load_diagnostic_policy


def test_febio_412_policy_is_closed_and_versioned() -> None:
    policy = load_diagnostic_policy()

    assert policy.schema_version == 1
    assert policy.febio_version == "4.12.0"
    assert policy.maximum_automatic_attempts == 3
    assert tuple(item.selector_id for item in policy.selectors) == (
        "solid.max_refs",
        "solid.qn.max_ups",
        "solid.line_search_iterations",
        "control.fixed_step_refinement",
    )
    assert policy.selector("solid.max_refs").allowed_target_values == (20, 25, 30)
    assert policy.selector("solid.qn.max_ups").allowed_target_values == (15, 20, 25)
    assert policy.selector("solid.line_search_iterations").allowed_target_values == (10, 15, 20)
    refinement = policy.selector("control.fixed_step_refinement")
    assert refinement.allowed_multipliers == (Decimal("2"), Decimal("4"))
    assert refinement.maximum_time_steps == 400


@pytest.mark.parametrize(
    "selector_id",
    (
        "mesh.element_size",
        "material.elastic_modulus",
        "contact.friction",
        "boundary.fixed",
        "solver.dtol",
        "solver.ls_check_jacobians",
    ),
)
def test_policy_rejects_every_unlisted_selector(selector_id: str) -> None:
    with pytest.raises(KeyError, match="DIAGNOSTIC_SELECTOR_NOT_ALLOWED"):
        load_diagnostic_policy().selector(selector_id)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_diagnostic_policy.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.diagnostic_policy'`.

- [ ] **Step 3: Add the exact closed policy resource**

```json
{
  "$schema": "../../schemas/diagnostic-control-policy.schema.json",
  "schema_version": 1,
  "febio_version": "4.12.0",
  "maximum_automatic_attempts": 3,
  "selectors": [
    {
      "id": "solid.max_refs",
      "path_template": "/febio_spec/Step/step[@id='{step_id}']/Control/solver[@type='solid']/max_refs",
      "expected_matches": 1,
      "value_type": "integer",
      "allowed_target_values": [20, 25, 30],
      "monotonic": "nondecreasing"
    },
    {
      "id": "solid.qn.max_ups",
      "path_template": "/febio_spec/Step/step[@id='{step_id}']/Control/solver[@type='solid']/qn_method/max_ups",
      "expected_matches": 1,
      "value_type": "integer",
      "allowed_target_values": [15, 20, 25],
      "monotonic": "nondecreasing"
    },
    {
      "id": "solid.line_search_iterations",
      "path_template": "/febio_spec/Step/step[@id='{step_id}']/Control/solver[@type='solid']/lsiter",
      "expected_matches": 1,
      "value_type": "integer",
      "allowed_target_values": [10, 15, 20],
      "monotonic": "nondecreasing"
    },
    {
      "id": "control.fixed_step_refinement",
      "paths": [
        "/febio_spec/Step/step[@id='{step_id}']/Control/time_steps",
        "/febio_spec/Step/step[@id='{step_id}']/Control/step_size"
      ],
      "expected_matches_per_path": 1,
      "value_types": ["integer", "decimal"],
      "allowed_multipliers": [2, 4],
      "maximum_time_steps": 400,
      "invariants": ["new_time_steps * new_step_size == old_time_steps * old_step_size"]
    }
  ],
  "always_denied_elements": [
    "Mesh",
    "MeshDomains",
    "Material",
    "Boundary",
    "Loads",
    "Contact",
    "Rigid",
    "LoadData",
    "Output",
    "time_stepper",
    "dtol",
    "etol",
    "rtol",
    "lstol",
    "ls_check_jacobians",
    "linear_solver",
    "equation_scheme"
  ]
}
```

Create `diagnostic-control-policy.schema.json` as a closed schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/diagnostic-control-policy.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "$schema",
    "schema_version",
    "febio_version",
    "maximum_automatic_attempts",
    "selectors",
    "always_denied_elements"
  ],
  "properties": {
    "$schema": {"type": "string", "minLength": 1},
    "schema_version": {"const": 1},
    "febio_version": {"const": "4.12.0"},
    "maximum_automatic_attempts": {
      "type": "integer",
      "minimum": 0,
      "maximum": 3
    },
    "selectors": {
      "type": "array",
      "minItems": 4,
      "maxItems": 4,
      "items": {
        "oneOf": [
          {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "id",
              "path_template",
              "expected_matches",
              "value_type",
              "allowed_target_values",
              "monotonic"
            ],
            "properties": {
              "id": {
                "enum": [
                  "solid.max_refs",
                  "solid.qn.max_ups",
                  "solid.line_search_iterations"
                ]
              },
              "path_template": {"type": "string", "minLength": 1},
              "expected_matches": {"const": 1},
              "value_type": {"const": "integer"},
              "allowed_target_values": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": true,
                "items": {"type": "integer", "minimum": 1}
              },
              "monotonic": {"const": "nondecreasing"}
            }
          },
          {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "id",
              "paths",
              "expected_matches_per_path",
              "value_types",
              "allowed_multipliers",
              "maximum_time_steps",
              "invariants"
            ],
            "properties": {
              "id": {"const": "control.fixed_step_refinement"},
              "paths": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "uniqueItems": true,
                "items": {"type": "string", "minLength": 1}
              },
              "expected_matches_per_path": {"const": 1},
              "value_types": {"const": ["integer", "decimal"]},
              "allowed_multipliers": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": true,
                "items": {"enum": [2, 4]}
              },
              "maximum_time_steps": {
                "type": "integer",
                "minimum": 1,
                "maximum": 400
              },
              "invariants": {
                "const": [
                  "new_time_steps * new_step_size == old_time_steps * old_step_size"
                ]
              }
            }
          }
        ]
      }
    },
    "always_denied_elements": {
      "type": "array",
      "minItems": 1,
      "uniqueItems": true,
      "items": {"type": "string", "minLength": 1}
    }
  }
}
```

- [ ] **Step 4: Implement the minimal typed loader**

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from importlib.resources import files
import json

from febio_cae_harness.schema import validate_schema


@dataclass(frozen=True)
class DiagnosticSelector:
    selector_id: str
    path_template: str | None = None
    paths: tuple[str, ...] = ()
    expected_matches: int | None = None
    expected_matches_per_path: int | None = None
    value_type: str | None = None
    value_types: tuple[str, ...] = ()
    allowed_target_values: tuple[int, ...] = ()
    allowed_multipliers: tuple[Decimal, ...] = ()
    maximum_time_steps: int | None = None
    monotonic: str | None = None
    invariants: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticControlPolicy:
    schema_version: int
    febio_version: str
    maximum_automatic_attempts: int
    selectors: tuple[DiagnosticSelector, ...]
    always_denied_elements: tuple[str, ...]

    def selector(self, selector_id: str) -> DiagnosticSelector:
        for item in self.selectors:
            if item.selector_id == selector_id:
                return item
        raise KeyError(f"DIAGNOSTIC_SELECTOR_NOT_ALLOWED: {selector_id}")


def load_diagnostic_policy() -> DiagnosticControlPolicy:
    resource = files("febio_cae_harness").joinpath(
        "data/policies/febio-4.12-diagnostic-controls.json"
    )
    raw = json.loads(resource.read_text(encoding="utf-8"))
    validate_schema("diagnostic-control-policy", raw)
    selectors = tuple(
        DiagnosticSelector(
            selector_id=item["id"],
            path_template=item.get("path_template"),
            paths=tuple(item.get("paths", ())),
            expected_matches=item.get("expected_matches"),
            expected_matches_per_path=item.get("expected_matches_per_path"),
            value_type=item.get("value_type"),
            value_types=tuple(item.get("value_types", ())),
            allowed_target_values=tuple(item.get("allowed_target_values", ())),
            allowed_multipliers=tuple(
                Decimal(str(value)) for value in item.get("allowed_multipliers", ())
            ),
            maximum_time_steps=item.get("maximum_time_steps"),
            monotonic=item.get("monotonic"),
            invariants=tuple(item.get("invariants", ())),
        )
        for item in raw["selectors"]
    )
    return DiagnosticControlPolicy(
        schema_version=raw["schema_version"],
        febio_version=raw["febio_version"],
        maximum_automatic_attempts=raw["maximum_automatic_attempts"],
        selectors=selectors,
        always_denied_elements=tuple(raw["always_denied_elements"]),
    )
```

- [ ] **Step 5: Run GREEN and resource regression**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_diagnostic_policy.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add apps/febio_cae_harness/src/febio_cae_harness/data/policies/febio-4.12-diagnostic-controls.json apps/febio_cae_harness/src/febio_cae_harness/schemas/diagnostic-control-policy.schema.json apps/febio_cae_harness/src/febio_cae_harness/diagnostic_policy.py apps/febio_cae_harness/tests/unit/test_diagnostic_policy.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: pin FEBio diagnostic control policy"
```

## Task 2: Byte-preserving diagnostic FEB patcher

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/numerical_patch.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/patch-report.schema.json`
- Create: `apps/febio_cae_harness/tests/fixtures/feb/diagnostic-controls-small.feb`
- Create: `apps/febio_cae_harness/tests/unit/test_numerical_patch.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: `AttemptStore.create_generated(name: str, data: bytes) -> ArtifactRef`, `IntentRevision.contract`, and `DiagnosticControlPolicy`.
- Produces: `PatchReplacement`, `PatchReport`, and `apply_numerical_patch(attempt: AttemptStore, source_feb: Path, proposal: ChangeProposal, intent: IntentRevision, policy: DiagnosticControlPolicy) -> PatchReport`.

- [ ] **Step 1: Write failing tests for a single allowed scalar and outside-byte identity**

```python
import inspect
from pathlib import Path

import pytest

from febio_cae_harness.numerical_patch import apply_numerical_patch


def test_max_refs_patch_changes_only_approved_text_span(
    diagnostic_attempt,
    approved_diagnostic_intent,
    diagnostic_policy,
    control_feb: Path,
) -> None:
    proposal = {
        "selector_id": "solid.max_refs",
        "step_id": "1",
        "before": 15,
        "after": 20,
        "purpose": "diagnostic",
        "eligible_for_promotion": False,
    }

    report = apply_numerical_patch(
        diagnostic_attempt,
        control_feb,
        proposal,
        approved_diagnostic_intent,
        diagnostic_policy,
    )

    assert report.changed_paths == (
        "/febio_spec/Step/step[@id='1']/Control/solver[@type='solid']/max_refs",
    )
    assert report.outside_bytes_identical is True
    assert report.eligible_for_promotion is False
    assert report.artifact.path.parent == diagnostic_attempt.generated_dir
    assert b"<max_refs>20</max_refs>" in report.artifact.path.read_bytes()


def test_arbitrary_destination_is_not_an_api_parameter() -> None:
    assert "destination_feb" not in inspect.signature(
        apply_numerical_patch
    ).parameters


@pytest.mark.parametrize(
    "selector_id",
    ("mesh.element_size", "solver.dtol", "contact.friction"),
)
def test_unlisted_patch_is_rejected(
    selector_id,
    diagnostic_attempt,
    approved_diagnostic_intent,
    diagnostic_policy,
    control_feb,
) -> None:
    proposal = {
        "selector_id": selector_id,
        "step_id": "1",
        "before": 1,
        "after": 2,
        "purpose": "diagnostic",
        "eligible_for_promotion": False,
    }
    with pytest.raises(ValueError, match="DIAGNOSTIC_SELECTOR_NOT_ALLOWED"):
        apply_numerical_patch(
            diagnostic_attempt,
            control_feb,
            proposal,
            approved_diagnostic_intent,
            diagnostic_policy,
        )
```

- [ ] **Step 2: Run RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_numerical_patch.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'febio_cae_harness.numerical_patch'`.

- [ ] **Step 3: Implement secure structural span discovery**

Do not locate a value by tag name or old text alone: a production FEB may have
the same control value in several steps. Use Expat byte offsets and the complete
policy path (including `step[@id]` and `solver[@type]`) to locate each unique
scalar. The implementation is:

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from xml.parsers import expat
import re
import xml.etree.ElementTree as ET

from febio_cae_harness.feb_inspector import inspect_feb_bytes
from febio_cae_harness.jsonio import ArtifactRef

if TYPE_CHECKING:
    from febio_cae_harness.attempt_store import AttemptStore
    from febio_cae_harness.diagnostic_policy import DiagnosticControlPolicy
    from febio_cae_harness.diagnosis import ChangeProposal
    from febio_cae_harness.intent import IntentRevision


FORBIDDEN_XML = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
ASCII_NUMBER = re.compile(
    br"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?"
)
NAME = r"[A-Za-z_][A-Za-z0-9_.:-]*"
SEGMENT = re.compile(
    rf"^(?P<tag>{NAME})(?P<predicates>(?:\[@{NAME}='[^']*'\])*)$"
)
PREDICATE = re.compile(rf"\[@(?P<name>{NAME})='(?P<value>[^']*)'\]")
XML_SPACE = b" \t\r\n"


@dataclass
class _Frame:
    tag: str
    attributes: dict[str, str]
    semantic_path: str
    content_start: int
    child_counts: dict[str, int]
    has_element_child: bool = False


@dataclass(frozen=True)
class ScalarSpan:
    structural_path: str
    semantic_path: str
    start: int
    end: int
    raw_value: bytes


@dataclass(frozen=True)
class PatchReplacement:
    structural_path: str
    before: str
    after: str
    source_start: int
    source_end: int


@dataclass(frozen=True)
class PatchReport:
    artifact: ArtifactRef
    source_sha256: str
    patched_sha256: str
    replacements: tuple[PatchReplacement, ...]
    changed_paths: tuple[str, ...]
    outside_bytes_identical: bool
    eligible_for_promotion: bool


def _opening_tag_end(data: bytes, start: int) -> int:
    quote: int | None = None
    for index in range(start, len(data)):
        byte = data[index]
        if quote is None and byte in (ord("'"), ord('"')):
            quote = byte
        elif quote == byte:
            quote = None
        elif quote is None and byte == ord(">"):
            return index + 1
    raise ValueError("XML_OPENING_TAG_UNTERMINATED")


def _parse_selector(path: str) -> tuple[tuple[str, dict[str, str]], ...]:
    if not path.startswith("/") or "//" in path:
        raise ValueError("DIAGNOSTIC_SELECTOR_PATH_INVALID")
    segments: list[tuple[str, dict[str, str]]] = []
    for raw_segment in path[1:].split("/"):
        matched = SEGMENT.fullmatch(raw_segment)
        if matched is None:
            raise ValueError("DIAGNOSTIC_SELECTOR_PATH_INVALID")
        predicates = matched.group("predicates")
        attributes = {
            match.group("name"): match.group("value")
            for match in PREDICATE.finditer(predicates)
        }
        if "".join(
            f"[@{match.group('name')}='{match.group('value')}']"
            for match in PREDICATE.finditer(predicates)
        ) != predicates:
            raise ValueError("DIAGNOSTIC_SELECTOR_PREDICATE_INVALID")
        segments.append((matched.group("tag"), attributes))
    return tuple(segments)


def _stack_matches(
    stack: list[_Frame],
    selector: tuple[tuple[str, dict[str, str]], ...],
) -> bool:
    if len(stack) != len(selector):
        return False
    return all(
        frame.tag == tag
        and all(frame.attributes.get(name) == value for name, value in attrs.items())
        for frame, (tag, attrs) in zip(stack, selector, strict=True)
    )


def _discover_scalar_spans(
    data: bytes,
    structural_paths: tuple[str, ...],
) -> dict[str, tuple[ScalarSpan, ...]]:
    if FORBIDDEN_XML.search(data):
        raise ValueError("UNSAFE_XML_DECLARATION")
    compiled = {
        path: _parse_selector(path)
        for path in structural_paths
    }
    discovered: dict[str, list[ScalarSpan]] = {
        path: []
        for path in structural_paths
    }
    stack: list[_Frame] = []
    root_counts: dict[str, int] = {}
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.ExternalEntityRefHandler = lambda *args: 0

    def on_start(tag: str, attributes: dict[str, str]) -> None:
        if stack:
            parent = stack[-1]
            parent.has_element_child = True
            ordinal = parent.child_counts.get(tag, 0) + 1
            parent.child_counts[tag] = ordinal
            parent_path = parent.semantic_path
        else:
            ordinal = root_counts.get(tag, 0) + 1
            root_counts[tag] = ordinal
            parent_path = ""
        frame = _Frame(
            tag=tag,
            attributes=dict(attributes),
            semantic_path=f"{parent_path}/{tag}[{ordinal}]",
            content_start=_opening_tag_end(data, parser.CurrentByteIndex),
            child_counts={},
        )
        stack.append(frame)

    def on_end(tag: str) -> None:
        frame = stack[-1]
        if frame.tag != tag:
            raise ValueError("XML_STACK_MISMATCH")
        for structural_path, selector in compiled.items():
            if not _stack_matches(stack, selector):
                continue
            if frame.has_element_child:
                raise ValueError("DIAGNOSTIC_SELECTOR_NOT_SCALAR")
            raw_content = data[frame.content_start:parser.CurrentByteIndex]
            stripped = raw_content.strip(XML_SPACE)
            if (
                not stripped
                or b"&" in stripped
                or ASCII_NUMBER.fullmatch(stripped) is None
            ):
                raise ValueError("DIAGNOSTIC_SELECTOR_NOT_ASCII_NUMBER")
            leading = len(raw_content) - len(raw_content.lstrip(XML_SPACE))
            trailing_index = len(raw_content.rstrip(XML_SPACE))
            discovered[structural_path].append(
                ScalarSpan(
                    structural_path=structural_path,
                    semantic_path=frame.semantic_path,
                    start=frame.content_start + leading,
                    end=frame.content_start + trailing_index,
                    raw_value=stripped,
                )
            )
        stack.pop()

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as error:
        raise ValueError("FEB_XML_PARSE_ERROR") from error
    if stack:
        raise ValueError("XML_STACK_NOT_EMPTY")
    return {path: tuple(values) for path, values in discovered.items()}
```

- [ ] **Step 4: Implement canonical semantic comparison and the exact scalar gate**

```python
def _secure_root(data: bytes) -> ET.Element:
    if FORBIDDEN_XML.search(data):
        raise ValueError("UNSAFE_XML_DECLARATION")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError("FEB_XML_PARSE_ERROR") from error


def _canonical_tuples(data: bytes) -> dict[str, tuple[object, ...]]:
    root = _secure_root(data)
    rows: dict[str, tuple[object, ...]] = {}

    def visit(node: ET.Element, path: str, ordinal: int) -> None:
        here = f"{path}/{node.tag}[{ordinal}]"
        rows[here] = (
            node.tag,
            tuple(sorted(node.attrib.items())),
            (node.text or "").strip(),
        )
        counts: dict[str, int] = {}
        for child in list(node):
            counts[child.tag] = counts.get(child.tag, 0) + 1
            visit(child, here, counts[child.tag])

    visit(root, "", 1)
    return rows


def _replace_spans(
    data: bytes,
    replacements: tuple[tuple[int, int, bytes], ...],
) -> bytes:
    ordered = sorted(replacements)
    if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("DIAGNOSTIC_SPANS_OVERLAP")
    output = data
    for start, end, value in sorted(replacements, reverse=True):
        output = output[:start] + value + output[end:]
    return output


def _outside_digest(data: bytes, spans: tuple[tuple[int, int], ...]) -> str:
    pieces: list[bytes] = []
    cursor = 0
    for start, end in sorted(spans):
        pieces.append(data[cursor:start])
        cursor = end
    pieces.append(data[cursor:])
    return sha256(b"".join(pieces)).hexdigest().upper()


def _field(proposal: object, name: str) -> object:
    if isinstance(proposal, Mapping):
        return proposal[name]
    return getattr(proposal, name)


def _as_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("DIAGNOSTIC_VALUE_NOT_DECIMAL") from error


def _require_invariant_signatures(source: bytes, changed: bytes) -> None:
    keys = (
        "domain",
        "material",
        "reference_closure",
        "load",
        "boundary",
        "contact",
        "output",
    )
    before = inspect_feb_bytes(
        source,
        source_name="diagnostic-source.feb",
    ).invariant_signatures
    after = inspect_feb_bytes(
        changed,
        source_name="diagnostic-candidate.feb",
    ).invariant_signatures
    if set(before) != set(keys) or set(after) != set(keys):
        raise ValueError("DIAGNOSTIC_INVARIANT_SIGNATURE_INVENTORY")
    for key in keys:
        if before[key] != after[key]:
            raise ValueError(f"DIAGNOSTIC_{key.upper()}_SIGNATURE_CHANGED")


def apply_numerical_patch(
    attempt: AttemptStore,
    source_feb: Path,
    proposal: ChangeProposal | Mapping[str, object],
    intent: IntentRevision,
    policy: DiagnosticControlPolicy,
) -> PatchReport:
    if _field(proposal, "purpose") != "diagnostic":
        raise ValueError("DIAGNOSTIC_PURPOSE_REQUIRED")
    if _field(proposal, "eligible_for_promotion") is not False:
        raise ValueError("DIAGNOSTIC_PROMOTION_PROHIBITED")
    selector_id = str(_field(proposal, "selector_id"))
    selector = policy.selector(selector_id)
    if selector.value_type != "integer":
        raise ValueError("DIAGNOSTIC_SELECTOR_REQUIRES_COUPLED_BRANCH")
    matches = [
        change
        for change in intent.contract["allowed_numerical_changes"]
        if change["selector_id"] == selector_id
    ]
    if len(matches) != 1:
        raise ValueError("INTENT_SELECTOR_NOT_APPROVED")
    approved = matches[0]
    after = int(_field(proposal, "after"))
    if after not in approved["allowed_target_values"]:
        raise ValueError("INTENT_VALUE_NOT_APPROVED")
    if after not in selector.allowed_target_values:
        raise ValueError("POLICY_VALUE_NOT_ALLOWED")

    source = source_feb.read_bytes()
    if selector.path_template is None:
        raise ValueError("DIAGNOSTIC_SCALAR_PATH_TEMPLATE_MISSING")
    rendered_paths = (
        selector.path_template.format(
            step_id=str(_field(proposal, "step_id"))
        ),
    )
    discovered = _discover_scalar_spans(source, rendered_paths)
    spans: list[ScalarSpan] = []
    for path in rendered_paths:
        matches = discovered[path]
        if len(matches) != selector.expected_matches:
            raise ValueError("DIAGNOSTIC_SELECTOR_MATCH_COUNT")
        spans.extend(matches)
    if len(spans) != 1:
        raise ValueError("DIAGNOSTIC_SCALAR_REQUIRES_ONE_SPAN")
    span = spans[0]
    before_value = _as_decimal(span.raw_value.decode("ascii"))
    if before_value != _as_decimal(_field(proposal, "before")):
        raise ValueError("DIAGNOSTIC_BEFORE_VALUE_DRIFT")
    if selector.monotonic == "nondecreasing" and Decimal(after) < before_value:
        raise ValueError("DIAGNOSTIC_MONOTONIC_POLICY_VIOLATION")

    replacement = str(after).encode("ascii")
    changed = _replace_spans(source, ((span.start, span.end, replacement),))
    changed_spans = _discover_scalar_spans(changed, rendered_paths)
    if _as_decimal(changed_spans[span.structural_path][0].raw_value.decode("ascii")) != Decimal(after):
        raise ValueError("DIAGNOSTIC_AFTER_VALUE_MISMATCH")
    before_rows = _canonical_tuples(source)
    after_rows = _canonical_tuples(changed)
    semantic_delta = {
        key
        for key in set(before_rows) | set(after_rows)
        if before_rows.get(key) != after_rows.get(key)
    }
    if semantic_delta != {span.semantic_path}:
        raise ValueError("DIAGNOSTIC_OUTSIDE_PATH_CHANGED")
    before_outside = _outside_digest(source, ((span.start, span.end),))
    new_span = changed_spans[span.structural_path][0]
    after_outside = _outside_digest(changed, ((new_span.start, new_span.end),))
    if before_outside != after_outside:
        raise ValueError("DIAGNOSTIC_OUTSIDE_BYTES_CHANGED")
    _require_invariant_signatures(source, changed)

    artifact = attempt.create_generated(
        f"diagnostic-{attempt.next_generated_ordinal():02d}.feb",
        changed,
    )
    if artifact.sha256 != sha256(changed).hexdigest().upper():
        raise ValueError("DIAGNOSTIC_ARTIFACT_HASH_MISMATCH")
    return PatchReport(
        artifact=artifact,
        source_sha256=sha256(source).hexdigest().upper(),
        patched_sha256=artifact.sha256,
        replacements=(
            PatchReplacement(
                structural_path=span.structural_path,
                before=span.raw_value.decode("ascii"),
                after=str(after),
                source_start=span.start,
                source_end=span.end,
            ),
        ),
        changed_paths=(span.structural_path,),
        outside_bytes_identical=True,
        eligible_for_promotion=False,
    )
```

- [ ] **Step 5: Add coupled fixed-step tests before implementing that branch**

```python
def test_fixed_step_refinement_preserves_final_time(
    diagnostic_attempt,
    approved_refinement_intent,
    diagnostic_policy,
    control_feb,
) -> None:
    proposal = {
        "selector_id": "control.fixed_step_refinement",
        "step_id": "1",
        "before": {"time_steps": 20, "step_size": "0.05"},
        "after": {"multiplier": 2},
        "purpose": "diagnostic",
        "eligible_for_promotion": False,
    }
    report = apply_numerical_patch(
        diagnostic_attempt,
        control_feb,
        proposal,
        approved_refinement_intent,
        diagnostic_policy,
    )
    data = report.artifact.path.read_text(encoding="utf-8")
    assert "<time_steps>40</time_steps>" in data
    assert "<step_size>0.025</step_size>" in data
    assert report.changed_paths == (
        "/febio_spec/Step/step[@id='1']/Control/step_size",
        "/febio_spec/Step/step[@id='1']/Control/time_steps",
    )
```

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_numerical_patch.py::test_fixed_step_refinement_preserves_final_time -q
```

Expected before the coupled branch: FAIL with
`DIAGNOSTIC_SELECTOR_VALUE_TYPE_UNSUPPORTED`.

- [ ] **Step 6: Implement coupled Decimal arithmetic and run GREEN**

Add this helper. In `apply_numerical_patch`, replace the current
`selector.value_type` check with:

```python
if selector.value_types == ("integer", "decimal"):
    return _apply_coupled_fixed_step(
        attempt,
        source_feb,
        proposal,
        intent,
        policy,
        selector,
    )
if selector.value_type != "integer":
    raise ValueError("DIAGNOSTIC_SELECTOR_VALUE_TYPE_UNSUPPORTED")
```

Then implement:

```python
def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _apply_coupled_fixed_step(
    attempt: AttemptStore,
    source_feb: Path,
    proposal: ChangeProposal | Mapping[str, object],
    intent: IntentRevision,
    policy: DiagnosticControlPolicy,
    selector: object,
) -> PatchReport:
    selector_id = str(_field(proposal, "selector_id"))
    matches = [
        change
        for change in intent.contract["allowed_numerical_changes"]
        if change["selector_id"] == selector_id
    ]
    if len(matches) != 1:
        raise ValueError("INTENT_SELECTOR_NOT_APPROVED")
    approved = matches[0]
    after = _field(proposal, "after")
    before = _field(proposal, "before")
    if not isinstance(after, Mapping) or not isinstance(before, Mapping):
        raise ValueError("DIAGNOSTIC_COUPLED_VALUE_REQUIRED")
    multiplier = _as_decimal(after["multiplier"])
    policy_multipliers = tuple(selector.allowed_multipliers)
    intent_multipliers = tuple(
        _as_decimal(value)
        for value in approved["allowed_multipliers"]
    )
    if multiplier not in policy_multipliers:
        raise ValueError("POLICY_VALUE_NOT_ALLOWED")
    if multiplier not in intent_multipliers:
        raise ValueError("INTENT_VALUE_NOT_APPROVED")
    old_time_steps = int(before["time_steps"])
    old_step_size = _as_decimal(before["step_size"])
    if multiplier != multiplier.to_integral_value():
        raise ValueError("DIAGNOSTIC_MULTIPLIER_NOT_INTEGER")
    new_time_steps = old_time_steps * int(multiplier)
    new_step_size = old_step_size / multiplier
    if (
        selector.maximum_time_steps is None
        or new_time_steps > selector.maximum_time_steps
    ):
        raise ValueError("DIAGNOSTIC_MAXIMUM_TIME_STEPS")
    if (
        Decimal(new_time_steps) * new_step_size
        != Decimal(old_time_steps) * old_step_size
    ):
        raise ValueError("DIAGNOSTIC_FINAL_TIME_CHANGED")

    source = source_feb.read_bytes()
    rendered_paths = tuple(
        path.format(step_id=str(_field(proposal, "step_id")))
        for path in selector.paths
    )
    discovered = _discover_scalar_spans(source, rendered_paths)
    spans: dict[str, ScalarSpan] = {}
    for path in rendered_paths:
        matches = discovered[path]
        if len(matches) != selector.expected_matches_per_path:
            raise ValueError("DIAGNOSTIC_SELECTOR_MATCH_COUNT")
        spans[path] = matches[0]
    time_steps_path = next(path for path in rendered_paths if path.endswith("/time_steps"))
    step_size_path = next(path for path in rendered_paths if path.endswith("/step_size"))
    if int(spans[time_steps_path].raw_value.decode("ascii")) != old_time_steps:
        raise ValueError("DIAGNOSTIC_BEFORE_VALUE_DRIFT")
    if _as_decimal(spans[step_size_path].raw_value.decode("ascii")) != old_step_size:
        raise ValueError("DIAGNOSTIC_BEFORE_VALUE_DRIFT")

    values = {
        time_steps_path: str(new_time_steps).encode("ascii"),
        step_size_path: _decimal_text(new_step_size).encode("ascii"),
    }
    raw_replacements = tuple(
        (spans[path].start, spans[path].end, values[path])
        for path in rendered_paths
    )
    changed = _replace_spans(source, raw_replacements)
    changed_spans = _discover_scalar_spans(changed, rendered_paths)
    if int(changed_spans[time_steps_path][0].raw_value.decode("ascii")) != new_time_steps:
        raise ValueError("DIAGNOSTIC_AFTER_VALUE_MISMATCH")
    if (
        _as_decimal(changed_spans[step_size_path][0].raw_value.decode("ascii"))
        != new_step_size
    ):
        raise ValueError("DIAGNOSTIC_AFTER_VALUE_MISMATCH")

    before_rows = _canonical_tuples(source)
    after_rows = _canonical_tuples(changed)
    semantic_delta = {
        key
        for key in set(before_rows) | set(after_rows)
        if before_rows.get(key) != after_rows.get(key)
    }
    expected_delta = {span.semantic_path for span in spans.values()}
    if semantic_delta != expected_delta:
        raise ValueError("DIAGNOSTIC_OUTSIDE_PATH_CHANGED")
    before_ranges = tuple((span.start, span.end) for span in spans.values())
    after_ranges = tuple(
        (changed_spans[path][0].start, changed_spans[path][0].end)
        for path in rendered_paths
    )
    if _outside_digest(source, before_ranges) != _outside_digest(changed, after_ranges):
        raise ValueError("DIAGNOSTIC_OUTSIDE_BYTES_CHANGED")
    _require_invariant_signatures(source, changed)

    artifact = attempt.create_generated(
        f"diagnostic-{attempt.next_generated_ordinal():02d}.feb",
        changed,
    )
    replacements = tuple(
        PatchReplacement(
            structural_path=path,
            before=spans[path].raw_value.decode("ascii"),
            after=values[path].decode("ascii"),
            source_start=spans[path].start,
            source_end=spans[path].end,
        )
        for path in sorted(rendered_paths)
    )
    return PatchReport(
        artifact=artifact,
        source_sha256=sha256(source).hexdigest().upper(),
        patched_sha256=artifact.sha256,
        replacements=replacements,
        changed_paths=tuple(item.structural_path for item in replacements),
        outside_bytes_identical=True,
        eligible_for_promotion=False,
    )
```

Add exact unit cases for multiplier `2`, multiplier `4`, maximum-step overflow,
nonintegral multiplier, final-time drift, decreasing scalar target, duplicate
selector match, the same value in two different steps, forbidden DTD/entity
input, a nonnumeric scalar, and a destination collision. The
duplicate/same-value cases must prove that
the full structural selector, not tag/value search, controls the patch.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_numerical_patch.py apps/febio_cae_harness/tests/unit/test_feb_inspector.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: all selected tests pass and the output FEB remains `eligible_for_promotion=false`.

- [ ] **Step 7: Commit Task 2**

```powershell
git add apps/febio_cae_harness/src/febio_cae_harness/numerical_patch.py apps/febio_cae_harness/src/febio_cae_harness/schemas/patch-report.schema.json apps/febio_cae_harness/tests/fixtures/feb/diagnostic-controls-small.feb apps/febio_cae_harness/tests/unit/test_numerical_patch.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: patch approved diagnostic controls only"
```

## Task 3: Intent-aware diagnosis and retry ledger

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/diagnosis.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/retry_ledger.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/diagnosis.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/change-proposal.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_diagnosis.py`
- Create: `apps/febio_cae_harness/tests/unit/test_retry_ledger.py`
- Modify: `apps/febio_cae_harness/src/febio_cae_harness/events.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: `EvidenceBundle`, `FailureClass`, `IntentRevision`, and `DiagnosticControlPolicy`.
- Produces: `IntentImpact`, `ChangeProposal`, `Diagnosis`, `RetryLedger`, `diagnose_failure(bundle: EvidenceBundle, intent: IntentRevision) -> Diagnosis`, and `RetryLedger.reserve(proposal: ChangeProposal) -> RetryReservation`.

- [ ] **Step 1: Write the failing decision-table tests**

```python
import pytest

from febio_cae_harness.diagnosis import IntentImpact, diagnose_failure


@pytest.mark.parametrize(
    ("failure_class", "overlaps_roi", "expected_impact", "automatic"),
    (
        ("NONLINEAR_CONVERGENCE_ERROR", False, IntentImpact.INTENT_PRESERVING, True),
        ("INITIAL_MESH_ERROR", False, IntentImpact.INTENT_SENSITIVE, False),
        ("DEFORMATION_MESH_ERROR", True, IntentImpact.INTENT_CHANGING, False),
        ("INPUT_OR_REFERENCE_ERROR", False, IntentImpact.INTENT_CHANGING, False),
    ),
)
def test_diagnosis_respects_intent_context(
    evidence_bundle_factory,
    approved_diagnostic_intent,
    failure_class,
    overlaps_roi,
    expected_impact,
    automatic,
) -> None:
    bundle = evidence_bundle_factory(
        failure_class=failure_class,
        overlaps_roi=overlaps_roi,
    )
    diagnosis = diagnose_failure(bundle, approved_diagnostic_intent)
    assert diagnosis.proposals[0].intent_impact is expected_impact
    assert diagnosis.proposals[0].automatic_execution_eligible is automatic


def test_retry_ledger_rejects_same_fingerprint_and_patch_twice(retry_ledger, proposal):
    retry_ledger.reserve(proposal)
    with pytest.raises(ValueError, match="RETRY_ALREADY_ATTEMPTED"):
        retry_ledger.reserve(proposal)
```

- [ ] **Step 2: Run RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_diagnosis.py apps/febio_cae_harness/tests/unit/test_retry_ledger.py -q
```

Expected: imports fail for `febio_cae_harness.diagnosis` and `retry_ledger`.

- [ ] **Step 3: Implement the exact impact enum and conservative decision function**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json


class IntentImpact(StrEnum):
    INTENT_PRESERVING = "INTENT_PRESERVING"
    INTENT_SENSITIVE = "INTENT_SENSITIVE"
    INTENT_CHANGING = "INTENT_CHANGING"


@dataclass(frozen=True)
class ChangeProposal:
    hypothesis: str
    evidence: tuple[str, ...]
    selector_id: str | None
    step_id: str | None
    before: object
    after: object
    affected_entities: tuple[str, ...]
    roi_impact: str
    load_path_impact: str
    expected_improvement: str
    side_effects: tuple[str, ...]
    verification: tuple[str, ...]
    intent_impact: IntentImpact
    automatic_execution_eligible: bool
    rollback: str
    failure_fingerprint: str
    retry_budget: int
    purpose: str = "diagnostic"
    eligible_for_promotion: bool = False

    def to_payload(self) -> dict[str, object]:
        value = asdict(self)
        value["intent_impact"] = self.intent_impact.value
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
            )
        )

    def retry_key(self) -> str:
        payload = {
            "failure_fingerprint": self.failure_fingerprint,
            "selector_id": self.selector_id,
            "step_id": self.step_id,
            "before": self.before,
            "after": self.after,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(canonical).hexdigest().upper()

    def to_patch_mapping(self) -> dict[str, object]:
        if self.selector_id is None or self.step_id is None:
            raise ValueError("REVIEW_ONLY_PROPOSAL_CANNOT_PATCH")
        return {
            "selector_id": self.selector_id,
            "step_id": self.step_id,
            "before": self.before,
            "after": self.after,
            "purpose": self.purpose,
            "eligible_for_promotion": self.eligible_for_promotion,
        }


@dataclass(frozen=True)
class Diagnosis:
    proposals: tuple[ChangeProposal, ...]
    required_action: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "proposals": [proposal.to_payload() for proposal in self.proposals],
            "required_action": self.required_action,
        }


def _review_only(bundle, impact: IntentImpact, required_action: str) -> Diagnosis:
    return Diagnosis(
        proposals=(
            ChangeProposal(
                hypothesis=f"{bundle.failure.failure_class} may change analysis meaning",
                evidence=(bundle.failure.fingerprint,),
                selector_id=None,
                step_id=None,
                before=None,
                after=None,
                affected_entities=tuple(bundle.failure.affected_entities),
                roi_impact="review required",
                load_path_impact="review required",
                expected_improvement="none without a reviewed model change",
                side_effects=("unknown until human review",),
                verification=("revise and reapprove Analysis Intent",),
                intent_impact=impact,
                automatic_execution_eligible=False,
                rollback="no mutation was performed",
                failure_fingerprint=bundle.failure.fingerprint,
                retry_budget=0,
            ),
        ),
        required_action=required_action,
    )


def diagnose_failure(bundle, intent):
    failure = str(bundle.failure.failure_class)
    if failure == "NONLINEAR_CONVERGENCE_ERROR":
        matches = [
            change
            for change in intent.contract["allowed_numerical_changes"]
            if change["selector_id"] == "solid.max_refs"
        ]
        if len(matches) != 1:
            return Diagnosis(proposals=(), required_action="review")
        selector = matches[0]
        before = int(bundle.control_values["solid.max_refs"])
        candidates = tuple(
            value for value in selector["allowed_target_values"]
            if int(value) > before
        )
        if not candidates:
            return Diagnosis(proposals=(), required_action="review")
        proposal = ChangeProposal(
            hypothesis="more nonlinear reformations may complete the unchanged equilibrium path",
            evidence=tuple(bundle.failure.evidence_ids),
            selector_id="solid.max_refs",
            step_id=str(bundle.failure.step_id),
            before=before,
            after=int(candidates[0]),
            affected_entities=(),
            roi_impact="no geometry or result population changes",
            load_path_impact="no load, constraint, material, or contact changes",
            expected_improvement="permit additional nonlinear reformations",
            side_effects=("longer solve time",),
            verification=(
                "rerun process/LOG/FBS composite completion gate",
                "compare requested intent result fields",
            ),
            intent_impact=IntentImpact.INTENT_PRESERVING,
            automatic_execution_eligible=True,
            rollback="discard the diagnostic attempt; keep the adopted FEB unchanged",
            failure_fingerprint=bundle.failure.fingerprint,
            retry_budget=int(selector["retry_budget"]),
        )
        return Diagnosis(proposals=(proposal,), required_action="retry")
    if failure == "INITIAL_MESH_ERROR":
        return _review_only(
            bundle,
            IntentImpact.INTENT_SENSITIVE,
            "review",
        )
    return _review_only(
        bundle,
        IntentImpact.INTENT_CHANGING,
        "revise-intent",
    )
```

- [ ] **Step 4: Implement immutable retry reservation**

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.events import read_event_log
from febio_cae_harness.diagnosis import ChangeProposal, IntentImpact


@dataclass(frozen=True)
class RetryReservation:
    ordinal: int
    key: str
    event_sha256: str


class RetryLedger:
    def __init__(self, case: CaseStore, maximum_attempts: int) -> None:
        self._case = case
        self._maximum_attempts = maximum_attempts

    def reserve(self, proposal: ChangeProposal) -> RetryReservation:
        if (
            proposal.intent_impact is not IntentImpact.INTENT_PRESERVING
            or not proposal.automatic_execution_eligible
            or proposal.purpose != "diagnostic"
            or proposal.eligible_for_promotion
        ):
            raise ValueError("RETRY_NOT_AUTOMATICALLY_ELIGIBLE")
        key = proposal.retry_key()
        effective_maximum = min(
            self._maximum_attempts,
            proposal.retry_budget,
        )
        with self._case.locked() as transaction:
            events = read_event_log(self._case.event_log)
            prior_keys = tuple(
                str(event.payload["retry_key"])
                for event in events
                if event.event_type == "RETRY_RESERVED"
            )
            if key in prior_keys:
                raise ValueError("RETRY_ALREADY_ATTEMPTED")
            if len(prior_keys) >= effective_maximum:
                raise ValueError("RETRY_BUDGET_EXHAUSTED")
            ordinal = len(prior_keys) + 1
            projection = transaction.replay()
            state = CaseState(projection["harness"]["case_state"])
            event = transaction.append(
                "RETRY_RESERVED",
                state,
                {
                    "retry_key": key,
                    "ordinal": ordinal,
                    "failure_fingerprint": proposal.failure_fingerprint,
                    "selector_id": proposal.selector_id,
                    "before": proposal.before,
                    "after": proposal.after,
                    "purpose": "diagnostic",
                    "eligible_for_promotion": False,
                },
            )
        return RetryReservation(
            ordinal=ordinal,
            key=key,
            event_sha256=event.event_sha256,
        )
```

Add `"RETRY_RESERVED"` to `events.py::SAME_STATE_EVENT_TYPES`. This is the
only new same-state event in Phase 1C: the reservation is immutable evidence,
while `PREFLIGHT_PASSED` remains the state-changing event that binds the new
retry attempt.

- [ ] **Step 5: Add full ChangeProposal schema assertions and run GREEN**

Create a closed `change-proposal.schema.json` with the exact fields emitted by
`to_payload()`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/change-proposal.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "hypothesis",
    "evidence",
    "selector_id",
    "step_id",
    "before",
    "after",
    "affected_entities",
    "roi_impact",
    "load_path_impact",
    "expected_improvement",
    "side_effects",
    "verification",
    "intent_impact",
    "automatic_execution_eligible",
    "rollback",
    "failure_fingerprint",
    "retry_budget",
    "purpose",
    "eligible_for_promotion"
  ],
  "properties": {
    "hypothesis": {"type": "string", "minLength": 1},
    "evidence": {
      "type": "array",
      "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "selector_id": {"type": ["string", "null"], "minLength": 1},
    "step_id": {"type": ["string", "null"], "minLength": 1},
    "before": {},
    "after": {},
    "affected_entities": {
      "type": "array",
      "items": {"type": "string", "minLength": 1}
    },
    "roi_impact": {"type": "string", "minLength": 1},
    "load_path_impact": {"type": "string", "minLength": 1},
    "expected_improvement": {"type": "string", "minLength": 1},
    "side_effects": {
      "type": "array",
      "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "verification": {
      "type": "array",
      "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "intent_impact": {
      "enum": [
        "INTENT_PRESERVING",
        "INTENT_SENSITIVE",
        "INTENT_CHANGING"
      ]
    },
    "automatic_execution_eligible": {"type": "boolean"},
    "rollback": {"type": "string", "minLength": 1},
    "failure_fingerprint": {
      "type": "string",
      "pattern": "^[0-9A-F]{64}$"
    },
    "retry_budget": {"type": "integer", "minimum": 0, "maximum": 3},
    "purpose": {"const": "diagnostic"},
    "eligible_for_promotion": {"const": false}
  }
}
```

Create `diagnosis.schema.json` as a closed object requiring
`schema_version=1`, an array of schema-valid proposal objects, and
`required_action` from `retry`, `review`, or `revise-intent`. Add these tests:

```python
from dataclasses import replace

from febio_cae_harness.schema import validate_schema


def test_change_proposal_contains_complete_audit_fields(proposal) -> None:
    payload = proposal.to_payload()
    validate_schema("change-proposal", payload)
    assert payload["hypothesis"].strip()
    assert payload["evidence"]
    assert payload["selector_id"] == "solid.max_refs"
    assert payload["before"] != payload["after"]
    assert payload["roi_impact"].strip()
    assert payload["load_path_impact"].strip()
    assert payload["verification"]
    assert payload["rollback"].strip()


def test_change_proposal_schema_rejects_blank_and_extra_fields(proposal) -> None:
    blank = proposal.to_payload()
    blank["hypothesis"] = ""
    with pytest.raises(Exception):
        validate_schema("change-proposal", blank)
    extra = proposal.to_payload()
    extra["solver_args"] = ["anything"]
    with pytest.raises(Exception):
        validate_schema("change-proposal", extra)


def test_review_only_proposal_cannot_become_automatic(proposal) -> None:
    unsafe = replace(
        proposal,
        intent_impact=IntentImpact.INTENT_CHANGING,
        automatic_execution_eligible=True,
    )
    with pytest.raises(ValueError, match="RETRY_NOT_AUTOMATICALLY_ELIGIBLE"):
        retry_ledger.reserve(unsafe)
```

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_diagnosis.py apps/febio_cae_harness/tests/unit/test_retry_ledger.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add apps/febio_cae_harness/src/febio_cae_harness/diagnosis.py apps/febio_cae_harness/src/febio_cae_harness/retry_ledger.py apps/febio_cae_harness/src/febio_cae_harness/events.py apps/febio_cae_harness/src/febio_cae_harness/schemas/diagnosis.schema.json apps/febio_cae_harness/src/febio_cae_harness/schemas/change-proposal.schema.json apps/febio_cae_harness/tests/unit/test_diagnosis.py apps/febio_cae_harness/tests/unit/test_retry_ledger.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: diagnose failures against analysis intent"
```

## Task 4: Exact CLI command surface and JSON-only stdout

**Files:**

- Modify: `apps/febio_cae_harness/src/febio_cae_harness/cli.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/__init__.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/case.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/source.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/input.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/inspect.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/intent.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/model.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/solve.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/verify.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/diagnose.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/report.py`
- Create: `apps/febio_cae_harness/tests/unit/test_cli.py`

**Interfaces:**

- Consumes: Phase 1A/B service objects and `CommandResult`.
- Produces: `build_parser() -> argparse.ArgumentParser`, `dispatch(namespace: argparse.Namespace) -> CommandResult`, and console command `febio-cae`.
- Every `CommandResult.evidence` item is the Phase 1A closed
  `EvidenceRecord` JSON shape `{"kind": "...", "data": {...}}`. A command
  that emits a required kind emits it exactly once. Phase 1E
  consumes `installed-identity`, `case-status`, `source-resolution`,
  `feb-inspection`, `inheritance-inspection`, `intent-revision`,
  `analysis-intent-approval-request`, `model-adoption`, `preflight`,
  `process-evidence`, `log-verification`, `fbs-verification`,
  `result-validation`, `completion-decision`, and `report-promotion`.
- Every artifact record has a stable `role`; solver outputs use exactly
  `attempt-solver-log` and `attempt-solver-xplt`, while reports use
  `audit-report-json` and `audit-report-html`.

- [ ] **Step 1: Write the failing parser inventory test**

```python
import json
import subprocess

import pytest

from febio_cae_harness.cli import build_parser


COMMANDS = (
    ("case", "init"),
    ("case", "adopt"),
    ("case", "status"),
    ("source", "resolve"),
    ("source", "approve"),
    ("input", "ingest"),
    ("inspect", "feb"),
    ("inspect", "step"),
    ("inspect", "inheritance"),
    ("intent", "draft"),
    ("intent", "request-approval"),
    ("intent", "approve"),
    ("intent", "show"),
    ("model", "adopt-existing"),
    ("preflight",),
    ("solve",),
    ("verify",),
    ("diagnose",),
    ("retry",),
    ("report",),
    ("cancel",),
    ("workflow", "run"),
)


@pytest.mark.parametrize("argv", COMMANDS)
def test_exact_command_surface(argv: tuple[str, ...], tmp_path) -> None:
    case_dir = tmp_path / "case"
    request = tmp_path / "request.json"
    base = list(argv)
    if argv == ("case", "init"):
        base += ["--case-dir", str(case_dir), "--analysis-id", "case-1"]
    elif argv == ("case", "adopt"):
        base += [
            "--case-dir", str(case_dir),
            "--analysis-id", "case-1",
            "--expected-legacy-manifest-sha256", "A" * 64,
            "--preexisting-inventory-json", str(request),
            "--expected-preexisting-inventory-sha256", "B" * 64,
        ]
    else:
        base += ["--case-dir", str(case_dir)]
    if argv == ("source", "resolve"):
        base += ["--request-json", str(request)]
    elif argv == ("source", "approve"):
        base += ["--approval-json", str(request)]
    elif argv == ("input", "ingest"):
        base += ["--role", "authoritative-feb"]
    elif argv == ("intent", "draft"):
        base += ["--contract-json", str(request)]
    elif argv == ("intent", "approve"):
        base += ["--approval-json", str(request)]
    elif argv == ("preflight",):
        base += ["--config-json", str(request)]
    elif argv == ("cancel",):
        base += ["--attempt-id", "attempt-1", "--owner-token", "B" * 64]
    elif argv == ("workflow", "run"):
        base += ["--through", "REPORTED"]
    namespace = build_parser().parse_args(base)
    assert namespace.command == argv[0]


def test_unknown_command_emits_one_json_object(installed_cli: str) -> None:
    result = subprocess.run(
        [installed_cli, "unknown"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"
    assert result.stdout.count("\n") <= 1
    assert result.returncode == 20


def test_version_contract_survives_parser_expansion(installed_cli: str) -> None:
    result = subprocess.run(
        [installed_cli, "--version"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert result.returncode == 0
    assert len(parsed["evidence"]) == 1
```

- [ ] **Step 2: Run RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_cli.py -q
```

Expected: `ImportError: cannot import name 'build_parser'`.

- [ ] **Step 3: Implement the exact parser without arbitrary solver arguments**

```python
import argparse
from pathlib import Path


def _case_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--case-dir", type=Path, required=True)


def _json_file(
    parser: argparse.ArgumentParser,
    option: str,
    destination: str,
) -> None:
    parser.add_argument(option, dest=destination, type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="febio-cae", exit_on_error=False)
    root = parser.add_subparsers(dest="command", required=True)

    case = root.add_parser("case")
    case_actions = case.add_subparsers(dest="action", required=True)
    case_init = case_actions.add_parser("init")
    _case_dir(case_init)
    case_init.add_argument("--analysis-id", required=True)
    case_adopt = case_actions.add_parser("adopt")
    _case_dir(case_adopt)
    case_adopt.add_argument("--analysis-id", required=True)
    case_adopt.add_argument(
        "--expected-legacy-manifest-sha256",
        required=True,
    )
    _json_file(
        case_adopt,
        "--preexisting-inventory-json",
        "preexisting_inventory_json",
    )
    case_adopt.add_argument(
        "--expected-preexisting-inventory-sha256",
        required=True,
    )
    case_status = case_actions.add_parser("status")
    _case_dir(case_status)

    source = root.add_parser("source")
    source_actions = source.add_subparsers(dest="action", required=True)
    source_resolve = source_actions.add_parser("resolve")
    _case_dir(source_resolve)
    _json_file(source_resolve, "--request-json", "request_json")
    source_approve = source_actions.add_parser("approve")
    _case_dir(source_approve)
    _json_file(source_approve, "--approval-json", "approval_json")

    input_parser = root.add_parser("input")
    input_actions = input_parser.add_subparsers(dest="action", required=True)
    ingest = input_actions.add_parser("ingest")
    _case_dir(ingest)
    ingest.add_argument(
        "--role",
        choices=("authoritative-feb",),
        required=True,
    )

    inspect = root.add_parser("inspect")
    inspect_actions = inspect.add_subparsers(dest="action", required=True)
    inspect_feb = inspect_actions.add_parser("feb")
    _case_dir(inspect_feb)
    inspect_feb.add_argument(
        "--exclude-domain",
        dest="excluded_domains",
        action="append",
        default=[],
    )
    inspect_step = inspect_actions.add_parser("step")
    _case_dir(inspect_step)
    inspect_inheritance = inspect_actions.add_parser("inheritance")
    _case_dir(inspect_inheritance)

    intent = root.add_parser("intent")
    intent_actions = intent.add_subparsers(dest="action", required=True)
    intent_draft = intent_actions.add_parser("draft")
    _case_dir(intent_draft)
    _json_file(intent_draft, "--contract-json", "contract_json")
    intent_request = intent_actions.add_parser("request-approval")
    _case_dir(intent_request)
    intent_approve = intent_actions.add_parser("approve")
    _case_dir(intent_approve)
    _json_file(intent_approve, "--approval-json", "approval_json")
    intent_show = intent_actions.add_parser("show")
    _case_dir(intent_show)

    model = root.add_parser("model")
    model_actions = model.add_subparsers(dest="action", required=True)
    adopt_existing = model_actions.add_parser("adopt-existing")
    _case_dir(adopt_existing)

    preflight = root.add_parser("preflight")
    _case_dir(preflight)
    _json_file(preflight, "--config-json", "config_json")
    for command in ("solve", "verify", "diagnose", "retry", "report"):
        child = root.add_parser(command)
        _case_dir(child)

    cancel = root.add_parser("cancel")
    _case_dir(cancel)
    cancel.add_argument("--attempt-id", required=True)
    cancel.add_argument("--owner-token", required=True)

    workflow = root.add_parser("workflow")
    workflow_actions = workflow.add_subparsers(dest="action", required=True)
    run = workflow_actions.add_parser("run")
    _case_dir(run)
    run.add_argument(
        "--through",
        choices=("PREFLIGHT_PASSED", "RESULT_VERIFIED", "REPORTED"),
        required=True,
    )
    return parser
```

- [ ] **Step 4: Route parse errors through `CommandResult`**

Use a parser that never writes usage to stdout, and wrap parse/dispatch exactly:

```python
def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    try:
        if args == ["--version"]:
            result = CommandResult(
                exit_code=ExitCode.SUCCESS,
                status=Status.SUCCESS,
                case_state=None,
                evidence=(
                    EvidenceRecord(
                        kind="package-version",
                        data={"version": __version__},
                    ),
                ),
            )
        else:
            namespace = build_parser().parse_args(args)
            result = dispatch(namespace)
    except (argparse.ArgumentError, SystemExit, UnicodeError) as error:
        print(f"invalid command: {error}", file=sys.stderr)
        result = CommandResult(
            exit_code=ExitCode.INVALID_INPUT_OR_CONTRACT,
            status=Status.ERROR,
            case_state=None,
            error={
                "code": "INVALID_INPUT_OR_CONTRACT",
                "message": "command arguments did not match the audited CLI contract",
            },
        )
    return int(emit_result(result, sys.stdout))
```

Add a test that `--extra-args`, `--solver-args`, and shell
metacharacter-bearing unknown options are rejected before any service is
called. Add per-command tests that omit each required argument and prove exit
`20`, lowercase `status="error"`, one JSON object, and zero service calls.

- [ ] **Step 5: Run GREEN and base CLI regression**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_cli.py apps/febio_cae_harness/tests/unit/test_cli_base.py apps/febio_cae_harness/tests/contract/test_command_result_schema.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add apps/febio_cae_harness/src/febio_cae_harness/cli.py apps/febio_cae_harness/src/febio_cae_harness/commands apps/febio_cae_harness/tests/unit/test_cli.py
git commit -m "feat: expose the audited FEBio CAE CLI"
```

## Task 5: State-aware orchestrator, run lease, and cancellation

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/orchestrator.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/cancel.py`
- Create: `apps/febio_cae_harness/tests/integration/test_orchestrator.py`
- Create: `apps/febio_cae_harness/tests/integration/test_cancel_command.py`

**Interfaces:**

- Consumes: `CaseStore`, `AttemptStore`, `RunLease`,
  `request_cancel()` and current-lease projection from Phase 1B,
  `CompletionDecision`, diagnosis/retry APIs, and Phase 1A/B services.
- Produces: `HarnessServices`,
  `Orchestrator.execute(request: WorkflowRequest) -> CommandResult`, and the
  CLI cancel handler. Phase 1C does not create a second lease/cancellation
  implementation.

- [ ] **Step 1: Write the failing success-path transition test**

```python
def test_workflow_runs_only_the_post_approval_success_sequence(
    fake_services,
    approved_case,
):
    result = fake_services.orchestrator.execute(
        {
            "command": "workflow.run",
            "case_dir": str(approved_case),
            "through": "REPORTED",
        }
    )

    assert result.status.value == "success"
    assert result.case_state == "REPORTED"
    assert fake_services.case_store.transition_names() == (
        "INTENT_APPROVED",
        "MODEL_BUILT",
        "PREFLIGHT_PASSED",
        "SOLVED",
        "RESULT_VERIFIED",
        "REPORTED",
    )


def test_workflow_never_crosses_a_human_gate(
    fake_services,
    drafted_case,
):
    result = fake_services.orchestrator.execute({
        "command": "workflow.run",
        "case_dir": str(drafted_case),
        "through": "REPORTED",
    })
    assert result.status.value == "waiting_for_human"
    assert result.case_state == "INTENT_DRAFTED"
    assert result.allowed_next_actions == ("intent approve", "cancel")
    assert fake_services.attempts_created == 0
    assert fake_services.processes_started == 0
```

- [ ] **Step 2: Write the failing active-run/cancel tests**

```python
def test_only_status_and_matching_cancel_are_allowed_during_live_run(
    running_case,
    orchestrator,
):
    blocked = orchestrator.execute(
        {"command": "intent.draft", "case_dir": str(running_case)}
    )
    assert blocked.error["code"] == "CASE_RUNNING"

    wrong = orchestrator.execute(
        {
            "command": "cancel",
            "case_dir": str(running_case),
            "attempt_id": "wrong-attempt",
            "owner_token": "wrong-token",
        }
    )
    assert wrong.error["code"] == "CANCEL_TOKEN_MISMATCH"

    accepted = orchestrator.execute(running_case.matching_cancel_request())
    assert accepted.status.value == "success"
    assert accepted.allowed_next_actions == ("status",)
```

- [ ] **Step 3: Run RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_orchestrator.py apps/febio_cae_harness/tests/integration/test_cancel_command.py -q
```

Expected: collection fails because `orchestrator.py` and `commands/cancel.py` do not exist.

- [ ] **Step 4: Implement dependency-injected dispatch**

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from febio_cae_harness.attempt_store import current_run_lease
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.response import CommandResult


@dataclass(frozen=True)
class HarnessServices:
    cases: object
    sources: object
    inspector: object
    intents: object
    attempts: object
    preflight: object
    runner: object
    logs: object
    fbs: object
    completion: object
    reporter: object
    handlers: Mapping[
        str,
        Callable[[object, Mapping[str, object]], CommandResult],
    ]


class Orchestrator:
    def __init__(self, services: HarnessServices) -> None:
        self._services = services

    def _run_workflow(self, case, request) -> CommandResult:
        target = str(request["through"])
        next_stage = {
            "INTENT_APPROVED": "model.adopt-existing",
            "MODEL_BUILT": "preflight",
            "PREFLIGHT_PASSED": "solve",
            "SOLVED": "verify",
            "RESULT_VERIFIED": "report",
        }
        target_rank = {
            "PREFLIGHT_PASSED": 0,
            "RESULT_VERIFIED": 2,
            "REPORTED": 3,
        }
        state_rank = {
            "PREFLIGHT_PASSED": 0,
            "SOLVED": 1,
            "RESULT_VERIFIED": 2,
            "REPORTED": 3,
        }
        if target not in target_rank:
            raise ValueError("WORKFLOW_TARGET_INVALID")
        while True:
            state = str(case.replay()["harness"]["case_state"])
            if state == target:
                return CommandResult(
                    exit_code=ExitCode.SUCCESS,
                    status=Status.SUCCESS,
                    case_state=state,
                )
            if state in state_rank and state_rank[state] > target_rank[target]:
                return CommandResult(
                    exit_code=ExitCode.INVALID_STATE_OR_POLICY,
                    status=Status.ERROR,
                    case_state=state,
                    error={
                        "code": "WORKFLOW_TARGET_BEHIND_CURRENT_STATE",
                        "message": "requested workflow target is behind the case",
                    },
                )
            if state in {
                "CASE_CREATED",
                "INPUT_INSPECTED",
                "INTENT_DRAFTED",
                "WAITING_FOR_HUMAN",
            }:
                next_human_action = {
                    "CASE_CREATED": "source resolve",
                    "INPUT_INSPECTED": "intent draft",
                    "INTENT_DRAFTED": "intent approve",
                    "WAITING_FOR_HUMAN": "approve-source",
                }[state]
                return CommandResult(
                    exit_code=ExitCode.WAITING_FOR_HUMAN,
                    status=Status.WAITING_FOR_HUMAN,
                    case_state=state,
                    allowed_next_actions=(next_human_action, "cancel"),
                    blockers=({
                        "code": "HUMAN_APPROVAL_REQUIRED",
                        "message": "workflow cannot cross an approval gate",
                    },),
                )
            command = next_stage.get(state)
            if command is None:
                return CommandResult(
                    exit_code=ExitCode.INVALID_STATE_OR_POLICY,
                    status=Status.ERROR,
                    case_state=state,
                    allowed_next_actions=("status", "diagnose", "cancel"),
                    error={
                        "code": "WORKFLOW_STOP_STATE",
                        "message": "no automatic transition is allowed",
                    },
                )
            handler = self._services.handlers.get(command)
            if handler is None:
                return CommandResult(
                    exit_code=ExitCode.INTERNAL_ERROR,
                    status=Status.ERROR,
                    case_state=state,
                    error={
                        "code": "WORKFLOW_HANDLER_MISSING",
                        "message": f"no handler is registered for {command}",
                    },
                )
            stage_request = dict(request)
            stage_request["command"] = command
            result = handler(case, stage_request)
            if result.status is not Status.SUCCESS:
                return result
            observed = str(case.replay()["harness"]["case_state"])
            if observed == state:
                return CommandResult(
                    exit_code=ExitCode.INTERNAL_ERROR,
                    status=Status.ERROR,
                    case_state=state,
                    error={
                        "code": "WORKFLOW_STAGE_DID_NOT_ADVANCE",
                        "message": f"{command} returned success without transition",
                    },
                )

    def execute(self, request):
        handlers = self._services.handlers
        command = str(request["command"])
        if command in {"case.init", "case.adopt"}:
            handler = handlers.get(command)
            if handler is None:
                return CommandResult(
                    exit_code=ExitCode.INTERNAL_ERROR,
                    status=Status.ERROR,
                    case_state=None,
                    error={
                        "code": "CASE_BOOTSTRAP_HANDLER_MISSING",
                        "message": f"no handler is registered for {command}",
                    },
                )
            return handler(None, request)
        case = self._services.cases.open(Path(str(request["case_dir"])))
        projection = case.replay()
        case_state = str(projection["harness"]["case_state"])
        lease = current_run_lease(case)
        if lease is not None and request["command"] not in ("case.status", "cancel"):
            return CommandResult(
                exit_code=ExitCode.INVALID_STATE_OR_POLICY,
                status=Status.ERROR,
                case_state=case_state,
                allowed_next_actions=("status", "cancel"),
                error={
                    "code": "CASE_RUNNING",
                    "message": "a solver attempt owns the current run lease",
                },
            )
        if (
            command == "workflow.run"
            and "workflow.run" not in handlers
        ):
            return self._run_workflow(case, request)
        if command not in handlers:
            return CommandResult(
                exit_code=ExitCode.INVALID_INPUT_OR_CONTRACT,
                status=Status.ERROR,
                case_state=case_state,
                error={
                    "code": "INVALID_COMMAND",
                    "message": "command is not in the audited handler map",
                },
            )
        handler = handlers[command]
        return handler(case, request)
```

- [ ] **Step 5: Route cancellation through the Phase 1B immutable lease API**

```python
from febio_cae_harness.attempt_store import request_cancel
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.hashing import file_evidence
from febio_cae_harness.response import CommandResult, EvidenceRecord


def handle_cancel(case, request) -> CommandResult:
    path = request_cancel(
        case,
        str(request["attempt_id"]),
        str(request["owner_token"]),
    )
    artifact = file_evidence(path)
    artifact["role"] = "cancel-request"
    case_state = str(case.replay()["harness"]["case_state"])
    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        status=Status.SUCCESS,
        case_state=case_state,
        allowed_next_actions=("status",),
        artifacts=(artifact,),
        evidence=(
            EvidenceRecord(
                kind="cancel-request",
                data={
                    "attempt_id": request["attempt_id"],
                    "cancel_request_sha256": artifact["sha256"],
                },
            ),
        ),
    )
```

Translate Phase 1B `NO_ACTIVE_RUN` and exact token mismatch exceptions to stable
`INVALID_STATE_OR_POLICY` envelopes. The handler must never expose the owner
token in stdout, stderr, events, or reports.

- [ ] **Step 6: Add stop-gate and exception-location cases**

Add table tests for unresolved source, missing physics, stale/wrong approval, preflight drift, solve failure, result incomplete, retry budget exhaustion, and cancellation. An exception after attempt creation writes under that attempt; a pre-attempt exception writes create-new under `05_Verification/harness/internal-errors` and does not create an attempt.

- [ ] **Step 7: Run GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_orchestrator.py apps/febio_cae_harness/tests/integration/test_cancel_command.py -q
git add apps/febio_cae_harness/src/febio_cae_harness/orchestrator.py apps/febio_cae_harness/src/febio_cae_harness/commands/cancel.py apps/febio_cae_harness/tests/integration/test_orchestrator.py apps/febio_cae_harness/tests/integration/test_cancel_command.py
git commit -m "feat: orchestrate state-safe CAE workflows"
```

Expected: all selected tests pass before the commit runs.

## Task 6: Deterministic audit report and create-new promotion

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/execution_profile.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/report.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/templates/audit-report.html`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/report-data.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/report-promotion.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_report.py`
- Create: `apps/febio_cae_harness/tests/integration/test_report_promotion.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: authoritative event replay, evidence/artifact hashes, `AttemptStore`, `CaseStore`, and verified promotion API.
- Produces: the shared `execution_profile_sha256(case, revision) -> str`,
  `expected_installed_tool_fingerprints(install_manifest_path,
  execution_config) -> dict`, `ReportBundle`,
  `build_report_data(case: CaseStore, attempt: AttemptStore) ->
  dict[str, object]`, `render_report(data: dict[str, object]) -> ReportBundle`,
  and `promote_report(case: CaseStore, attempt: AttemptStore, bundle:
  ReportBundle) -> tuple[ArtifactRef, ArtifactRef, bool, dict, tuple[dict,
  ...]]`, where the boolean records an identical create-new bundle reuse, the
  dict is the fully prevalidated promotion evidence, and the final tuple is
  the four prevalidated public artifact records.

- [ ] **Step 1: Write the failing deterministic/escaping test**

```python
from febio_cae_harness.report import render_report


def sample_report_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "engineering_question": "Check the approved response.",
        "intent": {"contract_sha256": "A" * 64},
        "provenance": {"input_manifest": {"sha256": "B" * 64}},
        "inspection": {"references_resolved": True},
        "attempts": [{"attempt_id": "attempt-0001"}],
        "log_evidence": {"status": "accepted"},
        "xplt_evidence": {"status": "accepted"},
        "result_validation": {"status": "accepted"},
        "diagnostics": [],
        "limitations": ["Not manufacturing validation."],
        "conclusion": {
            "claim_type": "Calculation",
            "product_acceptance": False,
            "statement": "The requested numerical evidence passed.",
        },
        "artifact_ledger": [],
        "claim_labels": {
            "engineering_question": "Published",
            "intent": "Published",
            "provenance": "Published",
            "inspection": "Calculation",
            "attempts": "Published",
            "log_evidence": "Calculation",
            "xplt_evidence": "Calculation",
            "result_validation": "Calculation",
            "diagnostics": "Inference",
            "limitations": "Assumption",
            "conclusion": "Calculation",
            "artifact_ledger": "Published",
        },
    }


def test_report_is_deterministic_and_escapes_untrusted_text():
    data = sample_report_data()
    data["engineering_question"] = "<script>alert(1)</script>"
    first = render_report(data)
    second = render_report(data)

    assert first.json_bytes == second.json_bytes
    assert first.html_bytes == second.html_bytes
    assert b"<script>alert(1)</script>" not in first.html_bytes
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in first.html_bytes


def test_unavailable_evidence_is_not_zero_or_pass():
    data = sample_report_data()
    data["xplt_evidence"] = None
    bundle = render_report(data)
    assert b"unavailable" in bundle.html_bytes
    assert b'"xplt_evidence":null' in bundle.json_bytes
```

- [ ] **Step 2: Run RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_report.py -q
```

Expected: `ModuleNotFoundError: No module named 'febio_cae_harness.report'`.

- [ ] **Step 3: Implement canonical JSON and standard-library HTML escaping**

```python
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from importlib.resources import files
import json

from febio_cae_harness.schema import validate_schema


@dataclass(frozen=True)
class ReportBundle:
    data_sha256: str
    json_bytes: bytes
    html_bytes: bytes


def _display(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )


def render_report(data):
    validate_schema("report-data", data)
    json_bytes = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    sections = (
        ("Engineering question", data["engineering_question"]),
        ("Intent and approval", data["intent"]),
        ("Input and tools", data["provenance"]),
        ("FEB inspection", data["inspection"]),
        ("Attempts", data["attempts"]),
        ("LOG evidence", data["log_evidence"]),
        ("XPLT/FBS evidence", data.get("xplt_evidence")),
        ("Intent-specific validation", data.get("result_validation")),
        ("Diagnostics and retries", data["diagnostics"]),
        ("Limitations", data["limitations"]),
        ("Conclusion", data["conclusion"]),
        ("Artifact ledger", data["artifact_ledger"]),
        ("Claim labels", data["claim_labels"]),
    )
    body = "".join(
        f"<section><h2>{escape(title)}</h2><pre>{escape(_display(value))}</pre></section>"
        for title, value in sections
    )
    template = files("febio_cae_harness").joinpath(
        "templates/audit-report.html"
    ).read_text(encoding="utf-8")
    if template.count("{{BODY}}") != 1:
        raise ValueError("AUDIT_REPORT_TEMPLATE_INVALID")
    html_bytes = template.replace("{{BODY}}", body).encode("utf-8")
    return ReportBundle(
        data_sha256=sha256(json_bytes).hexdigest().upper(),
        json_bytes=json_bytes,
        html_bytes=html_bytes,
    )
```

Create `templates/audit-report.html` exactly:

```html
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>FEBio CAE audit report</title>
</head>
<body>{{BODY}}</body>
</html>
```

Create `schemas/report-data.schema.json` exactly:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/report-data.schema.json",
  "type": "object",
  "required": [
    "schema_version",
    "engineering_question",
    "intent",
    "provenance",
    "inspection",
    "attempts",
    "log_evidence",
    "xplt_evidence",
    "result_validation",
    "diagnostics",
    "limitations",
    "conclusion",
    "artifact_ledger",
    "claim_labels"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "engineering_question": {"type": "string", "minLength": 1},
    "intent": {"type": "object"},
    "provenance": {"type": "object"},
    "inspection": {"type": ["object", "null"]},
    "attempts": {"type": "array", "minItems": 1},
    "log_evidence": {"type": ["object", "null"]},
    "xplt_evidence": {"type": ["object", "null"]},
    "result_validation": {"type": ["object", "null"]},
    "diagnostics": {"type": "array"},
    "limitations": {
      "type": "array",
      "items": {"type": "string", "minLength": 1}
    },
    "conclusion": {
      "type": "object",
      "required": ["claim_type", "product_acceptance", "statement"],
      "properties": {
        "claim_type": {"const": "Calculation"},
        "product_acceptance": {"const": false},
        "statement": {"type": "string", "minLength": 1}
      },
      "additionalProperties": false
    },
    "artifact_ledger": {"type": "array"},
    "claim_labels": {
      "type": "object",
      "required": [
        "engineering_question",
        "intent",
        "provenance",
        "inspection",
        "attempts",
        "log_evidence",
        "xplt_evidence",
        "result_validation",
        "diagnostics",
        "limitations",
        "conclusion",
        "artifact_ledger"
      ],
      "additionalProperties": false,
      "properties": {
        "engineering_question": {"$ref": "#/$defs/claimLabel"},
        "intent": {"$ref": "#/$defs/claimLabel"},
        "provenance": {"$ref": "#/$defs/claimLabel"},
        "inspection": {"$ref": "#/$defs/claimLabel"},
        "attempts": {"$ref": "#/$defs/claimLabel"},
        "log_evidence": {"$ref": "#/$defs/claimLabel"},
        "xplt_evidence": {"$ref": "#/$defs/claimLabel"},
        "result_validation": {"$ref": "#/$defs/claimLabel"},
        "diagnostics": {"$ref": "#/$defs/claimLabel"},
        "limitations": {"$ref": "#/$defs/claimLabel"},
        "conclusion": {"$ref": "#/$defs/claimLabel"},
        "artifact_ledger": {"$ref": "#/$defs/claimLabel"}
      }
    }
  },
  "$defs": {
    "claimLabel": {
      "enum": ["Published", "Calculation", "Assumption", "Inference"]
    }
  },
  "additionalProperties": false
}
```

Add `"schemas/report-data.schema.json"`,
`"schemas/report-promotion.schema.json"`, and
`"templates/audit-report.html"` to the exact installed-resource inventory.

- [ ] **Step 4: Write the failing promotion gate test**

```python
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

import febio_cae_harness.report as report_module
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.report import render_report
from febio_cae_harness.report import promote_report


class RecordingTransaction:
    def __init__(self, case) -> None:
        self.case = case

    def replay(self) -> dict[str, object]:
        return {"harness": {"case_state": self.case.state}}

    def append(self, event_type, to_state, payload) -> None:
        self.case.events.append((event_type, to_state.value, payload))
        self.case.state = to_state.value


class RecordingCase:
    def __init__(self, case_dir: Path, state: CaseState) -> None:
        self.case_dir = case_dir
        self.state = state.value
        self.events: list[tuple[str, str, dict[str, object]]] = []
        (case_dir / "04_Report").mkdir(parents=True)

    @contextmanager
    def locked(self):
        yield RecordingTransaction(self)


def completed_attempt(case_dir: Path):
    root = case_dir / "90_Temporary" / "attempts" / "attempt-0001"
    (root / "generated").mkdir(parents=True)
    return SimpleNamespace(
        case_dir=case_dir,
        root=root,
        attempt_id="attempt-0001",
    )


def report_bundle():
    return render_report({
        "schema_version": 1,
        "engineering_question": "Check the approved response.",
        "intent": {},
        "provenance": {},
        "inspection": None,
        "attempts": [{"attempt_id": "attempt-0001"}],
        "log_evidence": None,
        "xplt_evidence": None,
        "result_validation": None,
        "diagnostics": [],
        "limitations": ["Evidence not present is unavailable."],
        "conclusion": {
            "claim_type": "Calculation",
            "product_acceptance": False,
            "statement": "Only the requested numerical evidence is reported.",
        },
        "artifact_ledger": [],
        "claim_labels": {
            "engineering_question": "Published",
            "intent": "Published",
            "provenance": "Published",
            "inspection": "Calculation",
            "attempts": "Published",
            "log_evidence": "Calculation",
            "xplt_evidence": "Calculation",
            "result_validation": "Calculation",
            "diagnostics": "Inference",
            "limitations": "Assumption",
            "conclusion": "Calculation",
            "artifact_ledger": "Published",
        },
    })


def test_report_cannot_promote_before_result_verified(tmp_path, monkeypatch) -> None:
    case = RecordingCase(tmp_path / "case", CaseState.MODEL_BUILT)
    attempt = completed_attempt(case.case_dir)
    monkeypatch.setattr(report_module, "promote_verified", lambda value: ())
    with pytest.raises(ValueError, match="RESULT_VERIFIED_REQUIRED"):
        promote_report(case, attempt, report_bundle())
    assert list((case.case_dir / "04_Report").iterdir()) == []


def test_report_promotion_is_create_new_and_hash_bound(
    tmp_path,
    monkeypatch,
) -> None:
    case = RecordingCase(tmp_path / "case", CaseState.RESULT_VERIFIED)
    attempt = completed_attempt(case.case_dir)
    collision = case.case_dir / "04_Report" / "report-attempt-0001"
    collision.mkdir()
    (collision / "audit-report.json").write_bytes(b"different")
    (collision / "audit-report.html").write_bytes(b"different")
    monkeypatch.setattr(report_module, "promote_verified", lambda value: ())
    monkeypatch.setattr(
        report_module,
        "report_promotion_payload",
        lambda unused_attempt, json_ref, html_ref, reused: {
            "attempt_id": unused_attempt.attempt_id,
            "promoted_results": [],
            "reports": [
                {
                    "role": "audit-report-json",
                    "path": str(json_ref.path),
                    "bytes": json_ref.bytes,
                    "sha256": json_ref.sha256,
                },
                {
                    "role": "audit-report-html",
                    "path": str(html_ref.path),
                    "bytes": html_ref.bytes,
                    "sha256": html_ref.sha256,
                },
            ],
        },
    )
    monkeypatch.setattr(
        report_module,
        "_validate_report_promotion_payload",
        lambda *unused: None,
    )
    bundle = report_bundle()
    (
        json_ref,
        html_ref,
        report_reused,
        promotion,
        public_artifacts,
    ) = promote_report(
        case,
        attempt,
        bundle,
    )
    assert json_ref.path.parents[1].name == "04_Report"
    assert html_ref.path.parent == json_ref.path.parent
    assert json_ref.path.parent != collision
    assert json_ref.sha256 == sha256(bundle.json_bytes).hexdigest().upper()
    assert report_reused is False
    assert promotion["attempt_id"] == attempt.attempt_id
    assert len(public_artifacts) == 2
    assert case.state == "REPORTED"
    assert case.events[-1][0] == "REPORT_PROMOTED"
```

- [ ] **Step 5: Implement verified-only promotion and claim labels**

Create `execution_profile.py`. This is the single implementation used by
approval request, approval recording, preflight, and final report generation:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

from .events import read_event_log
from .hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .input_store import input_record_set_digest
from .schema import validate_schema


def resolve_install_pointer(
    install_manifest_path: Path,
    pointer: dict[str, object],
) -> Path:
    manifest_path = install_manifest_path.resolve(strict=True)
    version_root = manifest_path.parent
    relative = Path(str(pointer["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("INSTALL_POINTER_PATH_UNSAFE")
    resolved = (version_root / relative).resolve(strict=True)
    if version_root != resolved and version_root not in resolved.parents:
        raise ValueError("INSTALL_POINTER_ESCAPE")
    if (
        resolved.stat().st_size != int(pointer["bytes"])
        or sha256_file(resolved) != pointer["sha256"]
    ):
        raise ValueError("INSTALL_POINTER_DRIFT")
    return resolved


def expected_installed_tool_fingerprints(
    install_manifest_path: Path,
    execution_config: dict[str, object],
) -> dict[str, object]:
    manifest_path = install_manifest_path.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy_path = resolve_install_pointer(
        manifest_path,
        manifest["release"]["repository_policy"],
    )
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    solver = execution_config["expected_solver"]
    return {
        "harness_version": str(manifest["harness_version"]),
        "build_provenance_sha256": str(
            manifest["build_provenance"]["sha256"]
        ),
        "install_manifest_sha256": sha256_file(manifest_path),
        "wheel_sha256": str(manifest["wheel"]["sha256"]),
        "reviewed_source_commit": str(
            manifest["build_provenance"]["source_commit"]
        ),
        "solver_sha256": str(solver["sha256"]),
        "solver_version": str(solver["log_version"]),
        "fbs_runtime_tree_sha256": str(
            manifest["fbs_runtime"]["sha256"]
        ),
        "fbs_loaded_dll_profile_sha256": str(
            manifest["fbs_runtime"]["loaded_dll_profile_sha256"]
        ),
        "policy_sha256": str(
            manifest["release"]["repository_policy"]["sha256"]
        ),
        "policy_version": str(policy["schema_version"]),
    }


def execution_profile_sha256(case, revision: int) -> str:
    path = Path(
        os.environ["FEBIO_CAE_INSTALL_MANIFEST"]
    ).resolve(strict=True)
    manifest_bytes = path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    release = manifest["release"]
    resolve_install_pointer(path, release["repository_policy"])
    binding_path = (
        case.case_dir
        / "05_Verification/harness/intent"
        / f"intent-inspection-binding-r{revision:04d}.json"
    ).resolve(strict=True)
    binding_bytes = binding_path.read_bytes()
    profile = {
        "release_id": manifest["release_id"],
        "install_manifest_sha256": sha256_bytes(manifest_bytes),
        "build_provenance_sha256": manifest["build_provenance"]["sha256"],
        "wheel_sha256": manifest["wheel"]["sha256"],
        "profile_sha256": manifest["profile"]["sha256"],
        "fbs_runtime_tree_sha256": manifest["fbs_runtime"]["sha256"],
        "fbs_loaded_dll_profile_sha256": (
            manifest["fbs_runtime"]["loaded_dll_profile_sha256"]
        ),
        "repository_policy_sha256": release["repository_policy"]["sha256"],
        "intent_inspection_binding_sha256": sha256_bytes(
            binding_bytes
        ),
    }
    return sha256_bytes(canonical_json_bytes(profile))


def validate_current_intent_authority(
    case,
    intent_revision: dict[str, object],
    approval_record: dict[str, object],
    approval_record_path: Path,
) -> Path | None:
    case.replay(repair_manifest=False)
    contract = intent_revision["contract"]
    validate_schema("analysis-intent", contract)
    if (
        sha256_bytes(canonical_json_bytes(contract))
        != intent_revision["contract_sha256"]
    ):
        raise ValueError("CURRENT_INTENT_CONTRACT_BYTES_DRIFT")
    if contract["analysis_id"] != intent_revision["analysis_id"]:
        raise ValueError("CURRENT_INTENT_ANALYSIS_ID_DRIFT")
    if (
        input_record_set_digest(case.case_dir)
        != intent_revision["input_record_set_digest"]
    ):
        raise ValueError("CURRENT_INPUT_RECORD_SET_DRIFT")
    expected_source = intent_revision[
        "source_selection_approval_record_digest"
    ]
    source_paths = sorted(
        (case.case_dir / "01_Input").glob(
            "source-selection-approval-*.json"
        )
    )
    if expected_source is None:
        if source_paths:
            raise ValueError("UNBOUND_SOURCE_SELECTION_APPROVAL_PRESENT")
        source_path = None
    else:
        if len(source_paths) != 1:
            raise ValueError("CURRENT_SOURCE_SELECTION_APPROVAL_COUNT")
        source_path = source_paths[0].resolve(strict=True)
        if sha256_file(source_path) != expected_source:
            raise ValueError("CURRENT_SOURCE_SELECTION_APPROVAL_DRIFT")
        source_record = json.loads(source_path.read_text(encoding="utf-8"))
        validate_schema("source-selection-approval-record", source_record)
        source_events = [
            event
            for event in read_event_log(case.event_log)
            if event.event_type == "SOURCE_SELECTION_APPROVED"
        ]
        if len(source_events) != 1:
            raise ValueError("CURRENT_SOURCE_SELECTION_APPROVAL_EVENT_COUNT")
        source_event = source_events[0]
        event_source_path = Path(str(
            source_event.payload["record_path"]
        ))
        if not event_source_path.is_absolute():
            event_source_path = case.case_dir / event_source_path
        event_source_path = event_source_path.resolve(strict=True)
        if (
            event_source_path != source_path
            or source_event.payload["request_id"]
            != source_record["request_id"]
            or source_event.payload["approval_record_sha256"]
            != sha256_file(source_path)
            or source_event.payload["candidate_set_digest"]
            != source_record["candidate_set_digest"]
            or source_event.payload["selected_canonical_path"]
            != source_record["selected_canonical_path"]
            or source_event.payload["selected_sha256"]
            != source_record["selected_sha256"]
            or int(source_event.payload["selected_bytes"])
            != int(source_record["selected_bytes"])
        ):
            raise ValueError("CURRENT_SOURCE_SELECTION_APPROVAL_EVENT_DRIFT")
    for key in (
        "analysis_id",
        "revision",
        "contract_sha256",
        "input_record_set_digest",
        "source_selection_approval_record_digest",
    ):
        if approval_record.get(key) != intent_revision.get(key):
            raise ValueError(f"CURRENT_INTENT_APPROVAL_BINDING_DRIFT:{key}")
    approval_path = approval_record_path.resolve(strict=True)
    expected_approval_path = (
        case.case_dir
        / "05_Verification/harness/intent"
        / f"approval-record-{approval_record['request_id']}.json"
    ).resolve(strict=True)
    if approval_path != expected_approval_path:
        raise ValueError("CURRENT_INTENT_APPROVAL_RECORD_PATH_DRIFT")
    approval_events = [
        event
        for event in read_event_log(case.event_log)
        if (
            event.event_type == "INTENT_APPROVED"
            and int(event.payload["revision"])
            == int(intent_revision["revision"])
        )
    ]
    if len(approval_events) != 1:
        raise ValueError("CURRENT_INTENT_APPROVAL_EVENT_COUNT")
    approval_event = approval_events[0]
    if (
        approval_event.payload["request_id"]
        != approval_record["request_id"]
        or approval_event.payload["approval_record_sha256"]
        != sha256_file(approval_path)
    ):
        raise ValueError("CURRENT_INTENT_APPROVAL_EVENT_DRIFT")
    if approval_record.get(
        "execution_profile_sha256"
    ) != execution_profile_sha256(
        case,
        int(intent_revision["revision"]),
    ):
        raise ValueError("CURRENT_INTENT_EXECUTION_PROFILE_DRIFT")
    return source_path
```

Append the following exact code to `report.py`. The report pair becomes visible
through one create-new directory rename. A crash before the rename leaves only
an immutable staging directory below the attempt; a crash after the rename
leaves a complete, hash-verifiable pair that the next invocation can reuse.

```python
import os
from pathlib import Path
import uuid

from febio_cae_harness.case_state import CaseState
from febio_cae_harness.events import read_event_log
from febio_cae_harness.execution_profile import (
    expected_installed_tool_fingerprints,
    validate_current_intent_authority,
)
from febio_cae_harness.hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from febio_cae_harness.jsonio import ArtifactRef, atomic_create_artifact
from febio_cae_harness.promotion import promote_verified


_CLAIM_LABELS = {
    "engineering_question": "Published",
    "intent": "Published",
    "provenance": "Published",
    "inspection": "Calculation",
    "attempts": "Published",
    "log_evidence": "Calculation",
    "xplt_evidence": "Calculation",
    "result_validation": "Calculation",
    "diagnostics": "Inference",
    "limitations": "Assumption",
    "conclusion": "Calculation",
    "artifact_ledger": "Published",
}


def _load_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"REPORT_EVIDENCE_NOT_OBJECT:{path.name}")
    return value


def _load_json_lines(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    records = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(
                f"REPORT_STAGE_EVENT_NOT_OBJECT:{line_number}"
            )
        records.append(value)
    return records


def build_report_data(case, attempt) -> dict[str, object]:
    intent = _load_json(attempt.root / "intent-revision.json")
    if intent is None:
        raise ValueError("REPORT_INTENT_REVISION_MISSING")
    contract_value = intent.get("contract", intent)
    if not isinstance(contract_value, dict):
        raise ValueError("REPORT_INTENT_CONTRACT_INVALID")
    question = contract_value.get("engineering_question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("REPORT_ENGINEERING_QUESTION_MISSING")
    revision = int(intent["revision"])
    intent_dir = case.case_dir / "05_Verification/harness/intent"
    approval_candidates = [
        (path.resolve(strict=True), _load_json(path))
        for path in sorted(intent_dir.glob("approval-record-*.json"))
    ]
    approval_candidates = [
        item
        for item in approval_candidates
        if (
            isinstance(item[1], dict)
            and int(item[1].get("revision", -1)) == revision
        )
    ]
    if len(approval_candidates) != 1:
        raise ValueError("REPORT_CURRENT_APPROVAL_RECORD_COUNT")
    approval_path, approval = approval_candidates[0]
    validate_schema("approval-record", approval)
    validate_current_intent_authority(
        case,
        intent,
        approval,
        approval_path,
    )
    inspection_path = (
        case.case_dir / "05_Verification/harness/feb-inspection.json"
    )
    selection_path = (
        case.case_dir
        / "05_Verification/harness/feb-inspection-selection.json"
    )
    inspection_binding_path = (
        case.case_dir
        / "05_Verification/harness/intent"
        / f"intent-inspection-binding-r{revision:04d}.json"
    )
    inspection = _load_json(inspection_path)
    inspection_binding = _load_json(inspection_binding_path)
    if inspection is None or inspection_binding is None:
        raise ValueError("REPORT_BOUND_INSPECTION_MISSING")
    if (
        inspection_binding.get("revision") != revision
        or inspection_binding.get("contract_sha256")
        != intent.get("contract_sha256")
        or inspection_binding.get("feb_inspection_sha256")
        != sha256_file(inspection_path)
        or inspection_binding.get("feb_inspection_selection_sha256")
        != sha256_file(selection_path)
    ):
        raise ValueError("REPORT_BOUND_INSPECTION_DRIFT")
    validate_schema("feb-inspection", inspection)
    stage_events = _load_json_lines(attempt.root / "stage-events.jsonl")
    log_evidence = _load_json(attempt.root / "log-verification.json")
    xplt_evidence = _load_json(attempt.root / "fbs-verification.json")
    result_validation = _load_json(
        attempt.root / "result-verification.json"
    )
    if (
        log_evidence is None
        or xplt_evidence is None
        or result_validation is None
    ):
        raise ValueError("REPORT_VERIFICATION_EVIDENCE_MISSING")
    validate_schema("log-evidence", log_evidence)
    validate_schema("fbs-evidence", xplt_evidence)
    validate_schema("result-verification", result_validation)
    completion_path = attempt.root / "completion-decision.json"
    completion = _load_json(completion_path)
    if completion is None:
        raise ValueError("REPORT_COMPLETION_DECISION_MISSING")
    validate_schema("completion-decision", completion)
    if (
        completion["status"] != "accepted"
        or completion["case_state"] != "SOLVED"
        or completion["attempt_id"] != attempt.attempt_id
        or result_validation["status"] != "accepted"
        or result_validation["case_state"] != "RESULT_VERIFIED"
        or result_validation["attempt_id"] != attempt.attempt_id
    ):
        raise ValueError("REPORT_ACCEPTED_EVIDENCE_REQUIRED")
    evidence_by_role = {
        item["role"]: item for item in completion["evidence_records"]
    }
    if set(evidence_by_role) != {
        "process-evidence",
        "log-verification",
        "fbs-verification",
    }:
        raise ValueError("REPORT_COMPLETION_EVIDENCE_ROLE_SET")
    expected_evidence_paths = {
        "process-evidence": attempt.root / "process-evidence.json",
        "log-verification": attempt.root / "log-verification.json",
        "fbs-verification": attempt.root / "fbs-verification.json",
    }
    for role, expected_path in expected_evidence_paths.items():
        pointer = evidence_by_role[role]
        raw_pointer_path = Path(str(pointer["path"]))
        pointer_path = (
            raw_pointer_path
            if raw_pointer_path.is_absolute()
            else attempt.root / raw_pointer_path
        ).resolve(strict=True)
        if (
            pointer_path != expected_path.resolve(strict=True)
            or pointer_path.stat().st_size != int(pointer["bytes"])
            or sha256_file(pointer_path) != pointer["sha256"]
        ):
            raise ValueError(f"REPORT_COMPLETION_EVIDENCE_DRIFT:{role}")
    result_path = attempt.root / "result-verification.json"
    if (
        result_validation["completion_decision_sha256"]
        != sha256_file(completion_path)
        or result_validation["fbs_evidence_sha256"]
        != sha256_file(attempt.root / "fbs-verification.json")
    ):
        raise ValueError("REPORT_RESULT_EVIDENCE_BINDING_DRIFT")
    events = read_event_log(case.event_log)
    completion_events = [
        event
        for event in events
        if (
            event.event_type == "COMPLETION_DECIDED"
            and event.payload.get("attempt_id") == attempt.attempt_id
        )
    ]
    result_events = [
        event
        for event in events
        if (
            event.event_type == "RESULT_VALIDATED"
            and event.payload.get("attempt_id") == attempt.attempt_id
        )
    ]
    if len(completion_events) != 1 or len(result_events) != 1:
        raise ValueError("REPORT_AUTHORITATIVE_EVIDENCE_EVENT_COUNT")
    if (
        completion_events[0].payload["completion_decision_sha256"]
        != sha256_file(completion_path)
        or result_events[0].payload["result_verification_sha256"]
        != sha256_file(result_path)
        or result_events[0].payload["completion_decision_sha256"]
        != sha256_file(completion_path)
    ):
        raise ValueError("REPORT_AUTHORITATIVE_EVIDENCE_EVENT_DRIFT")
    result_artifacts = (
        result_validation.get("artifacts", [])
        if result_validation is not None
        else []
    )
    if not isinstance(result_artifacts, list):
        raise ValueError("REPORT_ARTIFACT_LEDGER_INVALID")
    limitations = contract_value.get("prohibited_conclusions", [])
    if not isinstance(limitations, list) or not all(
        isinstance(item, str) and item.strip() for item in limitations
    ):
        raise ValueError("REPORT_LIMITATIONS_INVALID")
    diagnostics = [
        evidence
        for event in stage_events
        for evidence in [event.get("evidence", event)]
        if isinstance(evidence, dict)
        and evidence.get("kind") in {"diagnosis", "change-proposal"}
    ]
    normalized_config = _load_json(
        attempt.root / "normalized-config.json"
    )
    tool_fingerprints = _load_json(
        attempt.root / "tool-fingerprints.json"
    )
    if normalized_config is None or tool_fingerprints is None:
        raise ValueError("REPORT_ATTEMPT_PROVENANCE_MISSING")
    install_manifest_path = Path(
        os.environ["FEBIO_CAE_INSTALL_MANIFEST"]
    ).resolve(strict=True)
    if tool_fingerprints != expected_installed_tool_fingerprints(
        install_manifest_path,
        normalized_config,
    ):
        raise ValueError("REPORT_ATTEMPT_PROVENANCE_DRIFT")
    data = {
        "schema_version": 1,
        "engineering_question": question,
        "intent": intent,
        "provenance": {
            "input_manifest": _load_json(
                attempt.root / "input-manifest.json"
            ),
            "tool_fingerprints": tool_fingerprints,
            "authoritative_case_projection": case.replay(),
        },
        "inspection": inspection,
        "attempts": [{
            "attempt_id": attempt.attempt_id,
            "resume_key": attempt.resume_key,
            "stage_events": stage_events,
        }],
        "log_evidence": log_evidence,
        "xplt_evidence": xplt_evidence,
        "result_validation": result_validation,
        "diagnostics": diagnostics,
        "limitations": limitations,
        "conclusion": {
            "claim_type": "Calculation",
            "product_acceptance": False,
            "statement": (
                "The approved numerical execution and requested result "
                "checks passed; this is not product acceptance."
            ),
        },
        "artifact_ledger": result_artifacts,
        "claim_labels": dict(_CLAIM_LABELS),
    }
    validate_schema("report-data", data)
    return data


def _ref(path: Path, data: bytes) -> ArtifactRef:
    return ArtifactRef(
        path=path.resolve(strict=True),
        bytes=len(data),
        sha256=sha256_bytes(data),
    )


def _matching_bundle(
    directory: Path,
    bundle: ReportBundle,
) -> tuple[ArtifactRef, ArtifactRef, bool] | None:
    json_path = directory / "audit-report.json"
    html_path = directory / "audit-report.html"
    if not json_path.is_file() or not html_path.is_file():
        return None
    if (
        json_path.stat().st_size != len(bundle.json_bytes)
        or html_path.stat().st_size != len(bundle.html_bytes)
        or sha256_file(json_path) != sha256_bytes(bundle.json_bytes)
        or sha256_file(html_path) != sha256_bytes(bundle.html_bytes)
    ):
        return None
    return (
        _ref(json_path, bundle.json_bytes),
        _ref(html_path, bundle.html_bytes),
        True,
    )


def _publish_report_directory(
    case,
    attempt,
    bundle: ReportBundle,
) -> tuple[ArtifactRef, ArtifactRef, bool]:
    staging = (
        attempt.root
        / "generated"
        / f"report-staging-{uuid.uuid4().hex}"
    )
    staging.mkdir(parents=False, exist_ok=False)
    atomic_create_artifact(
        staging / "audit-report.json",
        bundle.json_bytes,
    )
    atomic_create_artifact(
        staging / "audit-report.html",
        bundle.html_bytes,
    )
    report_root = case.case_dir / "04_Report"
    digest = bundle.data_sha256[:12]
    candidates = [
        report_root / f"report-{attempt.attempt_id}",
        *(
            report_root
            / f"report-{attempt.attempt_id}-{digest}-{index:03d}"
            for index in range(1, 1000)
        ),
    ]
    for destination in candidates:
        if destination.exists():
            reused = _matching_bundle(destination, bundle)
            if reused is not None:
                return reused
            continue
        try:
            os.rename(staging, destination)
        except OSError:
            if destination.exists():
                continue
            raise
        return (
            _ref(
                destination / "audit-report.json",
                bundle.json_bytes,
            ),
            _ref(
                destination / "audit-report.html",
                bundle.html_bytes,
            ),
            False,
        )
    raise RuntimeError("REPORT_CREATE_NEW_NAME_EXHAUSTED")


def promote_report(
    case,
    attempt,
    bundle: ReportBundle,
) -> tuple[
    ArtifactRef,
    ArtifactRef,
    bool,
    dict[str, object],
    tuple[dict[str, object], ...],
]:
    with case.locked() as transaction:
        state = str(transaction.replay()["harness"]["case_state"])
        if state != CaseState.RESULT_VERIFIED.value:
            raise ValueError("RESULT_VERIFIED_REQUIRED")
    promoted_results = promote_verified(attempt)
    with case.locked() as transaction:
        state = str(transaction.replay()["harness"]["case_state"])
        if state != CaseState.RESULT_VERIFIED.value:
            raise ValueError("RESULT_VERIFIED_REQUIRED")
        json_ref, html_ref, report_reused = _publish_report_directory(
            case,
            attempt,
            bundle,
        )
        promotion = report_promotion_payload(
            attempt,
            json_ref,
            html_ref,
            report_reused,
        )
        _validate_report_promotion_payload(case, attempt, promotion)
        if tuple(item.sha256 for item in promoted_results) != tuple(
            item["destination_sha256"]
            for item in promotion["promoted_results"]
        ):
            raise ValueError("REPORT_PROMOTED_RESULT_RETURN_DRIFT")
        public_artifacts = tuple([
            {
                "role": str(item["role"]),
                "path": str((
                    case.case_dir / str(item["destination"])
                ).resolve(strict=True)),
                "bytes": int(item["destination_bytes"]),
                "sha256": str(item["destination_sha256"]),
            }
            for item in promotion["promoted_results"]
        ] + [
            {
                "role": str(item["role"]),
                "path": str(Path(item["path"]).resolve(strict=True)),
                "bytes": int(item["bytes"]),
                "sha256": str(item["sha256"]),
            }
            for item in promotion["reports"]
        ])
        transaction.append(
            "REPORT_PROMOTED",
            CaseState.REPORTED,
            {
                "attempt_id": attempt.attempt_id,
                "report_data_sha256": bundle.data_sha256,
                "report_json_sha256": json_ref.sha256,
                "report_html_sha256": html_ref.sha256,
                "promoted_result_sha256": [
                    item.sha256 for item in promoted_results
                ],
                "report_promotion_sha256": sha256_bytes(
                    canonical_json_bytes(promotion)
                ),
            },
        )
        return (
            json_ref,
            html_ref,
            report_reused,
            promotion,
            public_artifacts,
        )


def report_promotion_payload(
    attempt,
    json_ref: ArtifactRef,
    html_ref: ArtifactRef,
    report_reused: bool,
) -> dict[str, object]:
    completion_path = attempt.root / "completion-decision.json"
    result_path = attempt.root / "result-verification.json"
    event_path = attempt.root / "promotion-event.json"
    event = _load_json(event_path)
    if event is None:
        raise ValueError("RESULT_PROMOTION_EVENT_MISSING")
    pointers = {
        "completion_decision": completion_path,
        "result_verification": result_path,
        "promotion_event": event_path,
    }
    payload = {
        "schema_version": 1,
        "attempt_id": attempt.attempt_id,
        "promotions_overwritten_count": 0,
        "report_bundle_reused": report_reused,
        **{
            name: {
                "path": str(path.resolve(strict=True)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for name, path in pointers.items()
        },
        "promoted_results": event["artifacts"],
        "reports": [
            {
                "role": role,
                "path": str(ref.path),
                "bytes": ref.bytes,
                "sha256": ref.sha256,
            }
            for role, ref in (
                ("audit-report-json", json_ref),
                ("audit-report-html", html_ref),
            )
        ],
    }
    if (
        event["completion_decision_sha256"]
        != payload["completion_decision"]["sha256"]
        or event["result_verification_sha256"]
        != payload["result_verification"]["sha256"]
    ):
        raise ValueError("REPORT_PROMOTION_BINDING_DRIFT")
    validate_schema("report-promotion", payload)
    return payload


def _validate_report_promotion_payload(
    case,
    attempt,
    payload: dict[str, object],
) -> None:
    validate_schema("report-promotion", payload)
    if payload["attempt_id"] != attempt.attempt_id:
        raise ValueError("REPORT_PROMOTION_ATTEMPT_DRIFT")
    expected_pointers = {
        "completion_decision": attempt.root / "completion-decision.json",
        "result_verification": attempt.root / "result-verification.json",
        "promotion_event": attempt.root / "promotion-event.json",
    }
    for name, expected_path in expected_pointers.items():
        pointer = payload[name]
        path = Path(pointer["path"]).resolve(strict=True)
        if (
            path != expected_path.resolve(strict=True)
            or path.stat().st_size != int(pointer["bytes"])
            or sha256_file(path) != pointer["sha256"]
        ):
            raise ValueError(f"REPORT_PROMOTION_POINTER_DRIFT:{name}")
    for item in payload["promoted_results"]:
        if item["source_sha256"] != item["destination_sha256"]:
            raise ValueError("REPORT_PROMOTED_SOURCE_DESTINATION_MISMATCH")
        destination = (
            case.case_dir / str(item["destination"])
        ).resolve(strict=True)
        if (
            destination.stat().st_size != int(item["destination_bytes"])
            or sha256_file(destination) != item["destination_sha256"]
        ):
            raise ValueError("REPORT_PROMOTED_DESTINATION_DRIFT")
    for item in payload["reports"]:
        path = Path(item["path"]).resolve(strict=True)
        if (
            path.stat().st_size != int(item["bytes"])
            or sha256_file(path) != item["sha256"]
        ):
            raise ValueError("REPORT_BUNDLE_POINTER_DRIFT")
```

Create `schemas/report-promotion.schema.json` with the complete closed schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/report-promotion.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "attempt_id", "promotions_overwritten_count",
    "report_bundle_reused", "completion_decision", "result_verification",
    "promotion_event", "promoted_results", "reports"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "attempt_id": {"type": "string", "minLength": 1},
    "promotions_overwritten_count": {"const": 0},
    "report_bundle_reused": {"type": "boolean"},
    "completion_decision": {"$ref": "#/$defs/pointer"},
    "result_verification": {"$ref": "#/$defs/pointer"},
    "promotion_event": {"$ref": "#/$defs/pointer"},
    "promoted_results": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "prefixItems": [
        {
          "allOf": [
            {"$ref": "#/$defs/promoted"},
            {"properties": {"role": {"const": "xplt"}}}
          ]
        },
        {
          "allOf": [
            {"$ref": "#/$defs/promoted"},
            {"properties": {"role": {"const": "solver-log"}}}
          ]
        }
      ],
      "items": false
    },
    "reports": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "prefixItems": [
        {
          "allOf": [
            {"$ref": "#/$defs/artifact"},
            {"properties": {"role": {"const": "audit-report-json"}}}
          ]
        },
        {
          "allOf": [
            {"$ref": "#/$defs/artifact"},
            {"properties": {"role": {"const": "audit-report-html"}}}
          ]
        }
      ],
      "items": false
    }
  },
  "$defs": {
    "pointer": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "bytes", "sha256"],
      "properties": {
        "path": {"type": "string", "minLength": 1},
        "bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      }
    },
    "artifact": {
      "type": "object",
      "additionalProperties": false,
      "required": ["role", "path", "bytes", "sha256"],
      "properties": {
        "role": {"type": "string", "minLength": 1},
        "path": {"type": "string", "minLength": 1},
        "bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      }
    },
    "promoted": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "role", "source_sha256", "destination", "destination_bytes",
        "destination_sha256"
      ],
      "properties": {
        "role": {"enum": ["xplt", "solver-log"]},
        "source_sha256": {
          "type": "string", "pattern": "^[0-9A-F]{64}$"
        },
        "destination": {"type": "string", "minLength": 1},
        "destination_bytes": {"type": "integer", "minimum": 1},
        "destination_sha256": {
          "type": "string", "pattern": "^[0-9A-F]{64}$"
        }
      }
    }
  }
}
```

The array order is part of the contract: promoted `xplt` precedes
`solver-log`, and `audit-report-json` precedes `audit-report-html`. The
production handler also requires `source_sha256 == destination_sha256` for
both promoted results and rehashes all seven referenced files before returning.

Extend `test_report_promotion.py` to write bound completion,
result-verification, and promotion-event records, call
`report_promotion_payload`, validate its schema, rehash all five pointers, and
    assert a changed completion hash, result hash, promoted destination, extra
    field, or nonzero overwrite count is rejected. Also exercise
    `build_report_data` with the pre-attempt case-level FEB inspection and its
    revision-specific `intent-inspection-binding` and approval records. Assert
    that the exact inspection, LOG, FBS, and result-verification payloads appear
    in the report, and that mutation of the approval's revision/contract/input
    bindings, nested contract bytes, any current InputRecord, the bound
    source-selection approval, its execution-profile digest, the bound
    inspection or selection, the approval record path/hash versus the immutable
    `INTENT_APPROVED` event, any completion evidence pointer, the
    result-to-completion/FBS hashes, the immutable `COMPLETION_DECIDED` or
    `RESULT_VALIDATED` event, or any of those three attempt evidence records is
    rejected before rendering or promotion. The report test must point
    `FEBIO_CAE_INSTALL_MANIFEST` at the same synthetic installed manifest used
    when the approval was requested.

- [ ] **Step 6: Run GREEN and report regression**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_report.py apps/febio_cae_harness/tests/integration/test_report_promotion.py apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 6**

```powershell
git add apps/febio_cae_harness/src/febio_cae_harness/execution_profile.py apps/febio_cae_harness/src/febio_cae_harness/report.py apps/febio_cae_harness/src/febio_cae_harness/templates/audit-report.html apps/febio_cae_harness/src/febio_cae_harness/schemas/report-data.schema.json apps/febio_cae_harness/src/febio_cae_harness/schemas/report-promotion.schema.json apps/febio_cae_harness/tests/unit/test_report.py apps/febio_cae_harness/tests/integration/test_report_promotion.py apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: generate auditable CAE reports"
```

## Task 7: Full fake-service orchestration regression

**Files:**

- Create: `apps/febio_cae_harness/tests/integration/test_workflow_matrix.py`
- Modify: `apps/febio_cae_harness/README.md`

**Interfaces:**

- Consumes: public `febio-cae` command contract and all Phase 1A–1C service interfaces.
- Produces: a deterministic fake-service matrix proving every public stop/success path without native FEBio/FBS.

- [ ] **Step 1: Write the failing matrix**

```python
from dataclasses import dataclass
import json
from pathlib import Path

import pytest

import febio_cae_harness.orchestrator as orchestrator_module
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.orchestrator import HarnessServices, Orchestrator
from febio_cae_harness.response import CommandResult


@pytest.mark.parametrize(
    ("scenario", "state", "exit_code", "next_actions"),
    (
        ("success", "REPORTED", 0, ("accept",)),
        ("unresolved-source", "WAITING_FOR_HUMAN", 10, ("approve-source", "cancel")),
        ("missing-physics", "WAITING_FOR_HUMAN", 10, ("intent draft", "cancel")),
        ("stale-approval", "INTENT_DRAFTED", 30, ("intent request-approval", "cancel")),
        ("preflight-drift", "MODEL_BUILT", 40, ("preflight", "cancel")),
        ("solve-failure", "SOLVE_FAILED", 50, ("diagnose", "cancel")),
        ("result-incomplete", "RESULT_INCOMPLETE", 60, ("diagnose", "cancel")),
        ("retry-exhausted", "SOLVE_FAILED", 50, ("review", "revise-intent", "cancel")),
        ("cancelled", "CANCELLED", 0, ("status",)),
    ),
)
def test_workflow_matrix(
    workflow_scenario_runner,
    scenario,
    state,
    exit_code,
    next_actions,
) -> None:
    observed = workflow_scenario_runner(scenario)
    assert observed.case_state == state
    assert observed.exit_code == exit_code
    assert observed.allowed_next_actions == next_actions
    assert observed.stdout_json_objects == 1
    assert observed.permanent_partial_promotions == ()
```

- [ ] **Step 2: Run RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/integration/test_workflow_matrix.py -q
```

Expected: fixture error `fixture 'workflow_scenario_runner' not found`.

- [ ] **Step 3: Implement the fake-service scenario factory**

Append this exact factory to `test_workflow_matrix.py`. These command-level
fakes complement, but do not replace, the Task 5 transition-sequence tests.
Each scenario returns one injected service outcome, invokes
`Orchestrator.execute`, serializes exactly once, and scans permanent locations
for an incomplete report pair or a leaked `.partial` file.

```python
@dataclass(frozen=True)
class Scenario:
    state: str
    exit_code: ExitCode
    status: Status
    next_actions: tuple[str, ...]


@dataclass(frozen=True)
class Observed:
    case_state: str
    exit_code: int
    allowed_next_actions: tuple[str, ...]
    stdout_json_objects: int
    permanent_partial_promotions: tuple[str, ...]


SCENARIOS = {
    "success": Scenario(
        "REPORTED", ExitCode.SUCCESS, Status.SUCCESS, ("accept",)
    ),
    "unresolved-source": Scenario(
        "WAITING_FOR_HUMAN",
        ExitCode.WAITING_FOR_HUMAN,
        Status.WAITING_FOR_HUMAN,
        ("approve-source", "cancel"),
    ),
    "missing-physics": Scenario(
        "WAITING_FOR_HUMAN",
        ExitCode.WAITING_FOR_HUMAN,
        Status.WAITING_FOR_HUMAN,
        ("intent draft", "cancel"),
    ),
    "stale-approval": Scenario(
        "INTENT_DRAFTED",
        ExitCode.INVALID_STATE_OR_POLICY,
        Status.ERROR,
        ("intent request-approval", "cancel"),
    ),
    "preflight-drift": Scenario(
        "MODEL_BUILT",
        ExitCode.RESOURCE_OR_TOOL_ERROR,
        Status.ERROR,
        ("preflight", "cancel"),
    ),
    "solve-failure": Scenario(
        "SOLVE_FAILED",
        ExitCode.SOLVE_FAILED,
        Status.ERROR,
        ("diagnose", "cancel"),
    ),
    "result-incomplete": Scenario(
        "RESULT_INCOMPLETE",
        ExitCode.RESULT_INCOMPLETE,
        Status.ERROR,
        ("diagnose", "cancel"),
    ),
    "retry-exhausted": Scenario(
        "SOLVE_FAILED",
        ExitCode.SOLVE_FAILED,
        Status.ERROR,
        ("review", "revise-intent", "cancel"),
    ),
    "cancelled": Scenario(
        "CANCELLED", ExitCode.SUCCESS, Status.SUCCESS, ("status",)
    ),
}


class FakeCase:
    def __init__(self, case_dir: Path) -> None:
        self.case_dir = case_dir

    def replay(self) -> dict[str, object]:
        return {"harness": {"case_state": "CASE_CREATED"}}


class FakeCases:
    def __init__(self, case: FakeCase) -> None:
        self.case = case

    def open(self, case_dir: str) -> FakeCase:
        assert Path(case_dir) == self.case.case_dir
        return self.case


def _partial_promotions(case_dir: Path) -> tuple[str, ...]:
    findings = [
        path.relative_to(case_dir).as_posix()
        for root_name in ("01_Input", "02_Model", "03_Result", "04_Report",
                          "05_Verification")
        for path in (case_dir / root_name).rglob("*.partial")
    ]
    for directory in (case_dir / "04_Report").glob("report-*"):
        if not directory.is_dir():
            continue
        present = {
            path.name for path in directory.iterdir() if path.is_file()
        }
        required = {"audit-report.json", "audit-report.html"}
        if present != required:
            findings.append(directory.relative_to(case_dir).as_posix())
    return tuple(sorted(findings))


@pytest.fixture
def workflow_scenario_runner(tmp_path, monkeypatch):
    case_dir = tmp_path / "case"
    for name in (
        "01_Input",
        "02_Model",
        "03_Result",
        "04_Report",
        "05_Verification",
        "90_Temporary",
    ):
        (case_dir / name).mkdir(parents=True)
    case = FakeCase(case_dir)
    monkeypatch.setattr(
        orchestrator_module,
        "current_run_lease",
        lambda value: None,
    )

    def run(scenario_name: str) -> Observed:
        scenario = SCENARIOS[scenario_name]

        def handler(opened_case, request) -> CommandResult:
            assert opened_case is case
            assert request["scenario"] == scenario_name
            if scenario_name == "success":
                report = (
                    case_dir
                    / "04_Report"
                    / "report-attempt-0001"
                )
                report.mkdir(exist_ok=False)
                (report / "audit-report.json").write_bytes(b"{}\n")
                (report / "audit-report.html").write_bytes(b"<html></html>\n")
            return CommandResult(
                exit_code=scenario.exit_code,
                status=scenario.status,
                case_state=scenario.state,
                allowed_next_actions=scenario.next_actions,
                error=(
                    None
                    if scenario.status is not Status.ERROR
                    else {
                        "code": scenario_name.upper().replace("-", "_"),
                        "message": "injected deterministic service outcome",
                    }
                ),
            )

        inert = object()
        services = HarnessServices(
            cases=FakeCases(case),
            sources=inert,
            inspector=inert,
            intents=inert,
            attempts=inert,
            preflight=inert,
            runner=inert,
            logs=inert,
            fbs=inert,
            completion=inert,
            reporter=inert,
            handlers={"workflow.run": handler},
        )
        result = Orchestrator(services).execute({
            "command": "workflow.run",
            "case_dir": str(case_dir),
            "through": "REPORTED",
            "scenario": scenario_name,
        })
        stdout = json.dumps(
            result.to_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        return Observed(
            case_state=str(result.case_state),
            exit_code=int(result.exit_code),
            allowed_next_actions=result.allowed_next_actions,
            stdout_json_objects=len(
                [line for line in stdout.splitlines() if line.strip()]
            ),
            permanent_partial_promotions=_partial_promotions(case_dir),
        )

    return run
```

- [ ] **Step 4: Run the Phase 1C suite and all earlier regressions**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit apps/febio_cae_harness/tests/contract apps/febio_cae_harness/tests/integration -q
& .\.venv\Scripts\python.exe -m pytest apps/febio_gmsh_launcher/tests -q
git diff --check
git status --short
```

Expected: all tests pass; only Task 7 files are uncommitted.

- [ ] **Step 5: Commit Task 7**

```powershell
git add apps/febio_cae_harness/tests/integration/test_workflow_matrix.py apps/febio_cae_harness/README.md
git commit -m "test: cover the complete CAE workflow matrix"
```

## Task 8: Production command wiring and pre-approval CLI journey

**Files:**

- Modify: `apps/febio_cae_harness/src/febio_cae_harness/cli.py`
- Modify: `apps/febio_cae_harness/src/febio_cae_harness/orchestrator.py`
- Modify: `apps/febio_cae_harness/src/febio_cae_harness/commands/__init__.py`
- Modify: every command module created in Task 4
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/common.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/commands/production.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/execution_request.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/execution-request.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/source-resolution.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/inheritance-inspection.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_command_registry.py`
- Create: `apps/febio_cae_harness/tests/unit/test_execution_request.py`
- Create: `apps/febio_cae_harness/tests/unit/test_production_sources.py`
- Create: `apps/febio_cae_harness/tests/unit/test_production_intent.py`
- Create: `apps/febio_cae_harness/tests/unit/test_production_preflight.py`
- Create: `apps/febio_cae_harness/tests/unit/test_xplt_request_binding.py`
- Create: `apps/febio_cae_harness/tests/unit/test_production_solve_verify.py`
- Create: `apps/febio_cae_harness/tests/unit/test_production_report.py`
- Create: `apps/febio_cae_harness/tests/unit/test_production_control.py`
- Create: `apps/febio_cae_harness/tests/integration/test_cli_preapproval_journey.py`

**Interfaces:**

- Consumes: all public Phase 1A–C APIs, `build_parser()`, `HarnessServices`,
  `Orchestrator`, and the exact evidence/artifact contracts listed in Task 4.
- Produces: `namespace_to_request(namespace) -> dict[str, object]`,
  `build_production_orchestrator() -> Orchestrator`, and a fully wired
  `dispatch(namespace) -> CommandResult`. Every parser leaf is connected to
  exactly one production handler; no handler calls Gmsh, FEBio, FBS, or a shell
  outside the Phase 1 adapters.

- [ ] **Step 1: Write the failing parser-to-request registry test**

Create `tests/unit/test_command_registry.py`:

```python
import pytest

from febio_cae_harness.cli import build_parser, namespace_to_request


@pytest.mark.parametrize(
    ("argv", "expected"),
    (
        (
            ("case", "init", "--case-dir", "C:\\case",
             "--analysis-id", "analysis-001"),
            "case.init",
        ),
        (
            (
                "case", "adopt", "--case-dir", "C:\\case",
                "--analysis-id", "analysis-001",
                "--expected-legacy-manifest-sha256", "A" * 64,
                "--preexisting-inventory-json", "C:\\inventory.json",
                "--expected-preexisting-inventory-sha256", "B" * 64,
            ),
            "case.adopt",
        ),
        (("case", "status", "--case-dir", "C:\\case"), "case.status"),
        (
            (
                "source", "resolve", "--case-dir", "C:\\case",
                "--request-json", "C:\\request.json",
            ),
            "source.resolve",
        ),
        (
            (
                "source", "approve", "--case-dir", "C:\\case",
                "--approval-json", "C:\\approval.json",
            ),
            "source.approve",
        ),
        (
            ("input", "ingest", "--case-dir", "C:\\case",
             "--role", "authoritative-feb"),
            "input.ingest",
        ),
        (
            (
                "inspect", "feb", "--case-dir", "C:\\case",
                "--exclude-domain", "rigid-tool",
            ),
            "inspect.feb",
        ),
        (("inspect", "step", "--case-dir", "C:\\case"), "inspect.step"),
        (
            ("inspect", "inheritance", "--case-dir", "C:\\case"),
            "inspect.inheritance",
        ),
        (
            (
                "intent", "draft", "--case-dir", "C:\\case",
                "--contract-json", "C:\\contract.json",
            ),
            "intent.draft",
        ),
        (
            ("intent", "request-approval", "--case-dir", "C:\\case"),
            "intent.request-approval",
        ),
        (
            (
                "intent", "approve", "--case-dir", "C:\\case",
                "--approval-json", "C:\\approval.json",
            ),
            "intent.approve",
        ),
        (
            ("intent", "show", "--case-dir", "C:\\case"),
            "intent.show",
        ),
        (
            ("model", "adopt-existing", "--case-dir", "C:\\case"),
            "model.adopt-existing",
        ),
        (("preflight", "--case-dir", "C:\\case",
          "--config-json", "C:\\config.json"), "preflight"),
        (("solve", "--case-dir", "C:\\case"), "solve"),
        (("verify", "--case-dir", "C:\\case"), "verify"),
        (("diagnose", "--case-dir", "C:\\case"), "diagnose"),
        (("retry", "--case-dir", "C:\\case"), "retry"),
        (("report", "--case-dir", "C:\\case"), "report"),
        (
            (
                "cancel", "--case-dir", "C:\\case",
                "--attempt-id", "attempt-0001",
                "--owner-token", "C" * 64,
            ),
            "cancel",
        ),
        (
            (
                "workflow", "run", "--case-dir", "C:\\case",
                "--through", "REPORTED",
            ),
            "workflow.run",
        ),
    ),
)
def test_namespace_to_request_has_one_stable_command_key(
    argv,
    expected,
) -> None:
    namespace = build_parser().parse_args(argv)
    request = namespace_to_request(namespace)
    assert request["command"] == expected
    assert "action" not in request
    if expected == "inspect.feb":
        assert request["excluded_domains"] == ["rigid-tool"]
```

The final parametrization contains all 22 parser leaves from Task 4.

- [ ] **Step 2: Run RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests/unit/test_command_registry.py -q
```

Expected: `ImportError: cannot import name 'namespace_to_request'`.

- [ ] **Step 3: Implement exact namespace normalization and dispatch**

Append to `cli.py` and replace the initial `dispatch` stub:

```python
_NESTED_COMMANDS = {"case", "source", "input", "inspect", "intent",
                    "model", "workflow"}


def namespace_to_request(namespace: argparse.Namespace) -> dict[str, object]:
    payload = {
        key: value
        for key, value in vars(namespace).items()
        if value is not None
    }
    command = str(payload.pop("command"))
    action = payload.pop("action", None)
    if command in _NESTED_COMMANDS:
        if not isinstance(action, str) or not action:
            raise ValueError("COMMAND_ACTION_MISSING")
        command = f"{command}.{action}"
    elif action is not None:
        raise ValueError("UNEXPECTED_COMMAND_ACTION")
    return {"command": command, **payload}


def dispatch(namespace: argparse.Namespace) -> CommandResult:
    from febio_cae_harness.commands.production import (
        build_production_orchestrator,
    )

    return build_production_orchestrator().execute(
        namespace_to_request(namespace)
    )
```

Keep the `--version` short-circuit before this path. Do not cache mutable
case/attempt state across CLI processes.

- [ ] **Step 4: Add the external execution-request schema and normalization test**

`preflight --config-json` consumes an external execution request, not Phase
1B's internal `run-config.schema.json`. Create
`schemas/execution-request.schema.json` with this closed schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/execution-request.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "timeout_seconds", "cancel_grace_seconds",
    "maximum_process_tree_working_set_mib", "retry_budget",
    "expected_log_times", "expected_fbs_times", "log_time_abs_tol",
    "fbs_time_abs_tol", "displacement_abs_tol_mm", "allow_zero_state",
    "allow_unrequested_states", "expected_solver",
    "expected_fbs_runtime_tree_sha256"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
    "cancel_grace_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    "maximum_process_tree_working_set_mib": {
      "type": "integer", "minimum": 128
    },
    "retry_budget": {"type": "integer", "minimum": 0, "maximum": 3},
    "expected_log_times": {
      "type": "array", "minItems": 1, "uniqueItems": true,
      "items": {"type": "number", "exclusiveMinimum": 0}
    },
    "expected_fbs_times": {
      "type": "array", "minItems": 1, "uniqueItems": true,
      "items": {"type": "number", "minimum": 0}
    },
    "log_time_abs_tol": {"type": "number", "minimum": 0},
    "fbs_time_abs_tol": {"type": "number", "minimum": 0},
    "displacement_abs_tol_mm": {"type": "number", "exclusiveMinimum": 0},
    "allow_zero_state": {"type": "boolean"},
    "allow_unrequested_states": {"type": "boolean"},
    "expected_solver": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "sha256", "log_version"],
      "properties": {
        "path": {"type": "string", "minLength": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "log_version": {"type": "string", "minLength": 1}
      }
    },
    "expected_fbs_runtime_tree_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    }
  }
}
```

Create `tests/unit/test_execution_request.py`. The test writes a complete
request with 20 strictly increasing positive LOG times and 21 strictly
increasing FBS times beginning at zero. It supplies every
`ExecutionBindings` field explicitly, invokes `build_attempt_config`, validates
the result against Phase 1B `run-config.schema.json`, and asserts:

```python
assert set(config) == {
    "resume_key", "input_manifest", "intent_revision",
    "normalized_config", "commands", "tool_fingerprints",
}
assert config["normalized_config"]["expected_log_times"] == [
    index / 20 for index in range(1, 21)
]
assert config["normalized_config"]["expected_fbs_times"] == [
    index / 20 for index in range(0, 21)
]
assert config["normalized_config"]["retry_budget"] == 0
assert config["commands"]["febio"][1:] == [
    "-i", "input.feb", "-o", "solver.log", "-p", "solver.xplt",
]
assert config["tool_fingerprints"]["build_provenance_sha256"] == (
    bindings.build_provenance_sha256
)
assert config["tool_fingerprints"]["install_manifest_sha256"] == (
    bindings.install_manifest_sha256
)
assert config["tool_fingerprints"]["wheel_sha256"] == bindings.wheel_sha256
assert config["tool_fingerprints"]["solver_sha256"] == "A" * 64
assert len(config["resume_key"]) == 64
```

Also mutate each external field and each `ExecutionBindings` dataclass field in
turn—including the build-provenance, install-manifest, wheel, and reviewed
source-commit identities; every semantic change must change the resume key.
Assert unsorted,
duplicate, non-finite, missing-zero FBS, mismatched final LOG/FBS time,
`allow_zero_state=true` with an expected nonempty state set, and
`retry_budget` greater than the approved intent policy are rejected. Exercise
`validate_execution_request_against_intent` against a closed synthetic intent
and install manifest; bind the exact request's canonical SHA-256 in
`expected_run`, then independently mutate every external execution-request
field, solver version, LOG count/final time, FBS count/final time, the required
FBS count versus `time_steps + 1`, automatic retry budget, selector retry
budget, and installed FBS runtime-tree hash. Each mismatch must be rejected
before an internal run config or attempt exists.

- [ ] **Step 5: Run the execution-request RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_execution_request.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.execution_request'`.

- [ ] **Step 6: Implement the only external-to-internal conversion**

Create `execution_request.py`:

```python
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .attempt_store import ResumeInputs, compute_resume_key
from .hashing import canonical_json_bytes, sha256_bytes
from .schema import validate_schema


@dataclass(frozen=True)
class ExecutionRequest:
    payload: dict[str, object]
    sha256: str


@dataclass(frozen=True)
class ExecutionBindings:
    input_manifest: dict[str, object]
    intent_revision: dict[str, object]
    input_record_set_digest: str
    source_selection_approval_record_digest: str | None
    contract_sha256: str
    analysis_intent_approval_record_sha256: str
    harness_version: str
    build_provenance_sha256: str
    install_manifest_sha256: str
    wheel_sha256: str
    reviewed_source_commit: str
    fbs_loaded_dll_profile_sha256: str
    policy_sha256: str
    policy_version: str


def load_execution_request(path: Path) -> ExecutionRequest:
    canonical = path.resolve(strict=True)
    value = json.loads(canonical.read_text(encoding="utf-8"))
    validate_schema("execution-request", value)
    log_times = tuple(float(item) for item in value["expected_log_times"])
    fbs_times = tuple(float(item) for item in value["expected_fbs_times"])
    for label, times in (("LOG", log_times), ("FBS", fbs_times)):
        if (
            not all(math.isfinite(item) for item in times)
            or any(right <= left for left, right in zip(times, times[1:]))
        ):
            raise ValueError(f"{label}_TIMES_NOT_STRICTLY_INCREASING")
    if fbs_times[0] != 0.0:
        raise ValueError("FBS_INITIAL_ZERO_TARGET_REQUIRED")
    if log_times[-1] != fbs_times[-1]:
        raise ValueError("LOG_FBS_FINAL_TIME_MISMATCH")
    if value["allow_zero_state"]:
        raise ValueError("EMPTY_FBS_STATE_SET_MUST_BE_REJECTED")
    canonical_bytes = canonical_json_bytes(value)
    return ExecutionRequest(value, sha256_bytes(canonical_bytes))


def build_attempt_config(
    request: ExecutionRequest,
    bindings: ExecutionBindings,
    *,
    approved_retry_budget: int,
) -> dict[str, object]:
    value = request.payload
    if int(value["retry_budget"]) > approved_retry_budget:
        raise ValueError("EXECUTION_RETRY_BUDGET_EXCEEDS_INTENT")
    solver = value["expected_solver"]
    normalized = json.loads(canonical_json_bytes(value).decode("utf-8"))
    inputs = ResumeInputs(
        input_record_set_digest=bindings.input_record_set_digest,
        source_selection_approval_record_digest=(
            bindings.source_selection_approval_record_digest
        ),
        contract_sha256=bindings.contract_sha256,
        analysis_intent_approval_record_sha256=(
            bindings.analysis_intent_approval_record_sha256
        ),
        normalized_config_sha256=request.sha256,
        harness_version=bindings.harness_version,
        build_provenance_sha256=bindings.build_provenance_sha256,
        install_manifest_sha256=bindings.install_manifest_sha256,
        wheel_sha256=bindings.wheel_sha256,
        reviewed_source_commit=bindings.reviewed_source_commit,
        solver_sha256=str(solver["sha256"]),
        solver_version=str(solver["log_version"]),
        fbs_runtime_tree_sha256=str(
            value["expected_fbs_runtime_tree_sha256"]
        ),
        loaded_dll_profile_sha256=(
            bindings.fbs_loaded_dll_profile_sha256
        ),
        policy_sha256=bindings.policy_sha256,
        policy_version=bindings.policy_version,
    )
    return {
        "resume_key": compute_resume_key(inputs),
        "input_manifest": bindings.input_manifest,
        "intent_revision": bindings.intent_revision,
        "normalized_config": normalized,
        "commands": {
            "febio": [
                str(solver["path"]),
                "-i", "input.feb",
                "-o", "solver.log",
                "-p", "solver.xplt",
            ]
        },
        "tool_fingerprints": {
            "harness_version": bindings.harness_version,
            "build_provenance_sha256": (
                bindings.build_provenance_sha256
            ),
            "install_manifest_sha256": bindings.install_manifest_sha256,
            "wheel_sha256": bindings.wheel_sha256,
            "reviewed_source_commit": bindings.reviewed_source_commit,
            "solver_sha256": str(solver["sha256"]),
            "solver_version": str(solver["log_version"]),
            "fbs_runtime_tree_sha256": str(
                value["expected_fbs_runtime_tree_sha256"]
            ),
            "fbs_loaded_dll_profile_sha256": (
                bindings.fbs_loaded_dll_profile_sha256
            ),
            "policy_sha256": bindings.policy_sha256,
            "policy_version": bindings.policy_version,
        },
    }


def validate_execution_request_against_intent(
    request: ExecutionRequest,
    intent_revision: dict[str, object],
    install_manifest: dict[str, object],
) -> int:
    expected_run = intent_revision["contract"]["expected_run"]
    if expected_run.get("resolved") is not True:
        raise ValueError("INTENT_EXPECTED_RUN_UNRESOLVED")
    expected = expected_run["value"]
    value = request.payload
    log_times = value["expected_log_times"]
    fbs_times = value["expected_fbs_times"]
    if request.sha256 != expected["execution_request_sha256"]:
        raise ValueError("EXECUTION_REQUEST_NOT_APPROVED")
    if int(expected["required_fbs_state_count"]) != (
        int(expected["time_steps"]) + 1
    ):
        raise ValueError("INTENT_FBS_STATE_COUNT_MUST_INCLUDE_INITIAL_STATE")
    if str(value["expected_solver"]["log_version"]) != str(
        expected["solver_version"]
    ):
        raise ValueError("EXECUTION_SOLVER_VERSION_NOT_APPROVED")
    if (
        len(log_times) != int(expected["time_steps"])
        or float(log_times[-1]) != float(expected["end_time"])
    ):
        raise ValueError("EXECUTION_LOG_TIME_SET_NOT_APPROVED")
    if (
        len(fbs_times) != int(expected["required_fbs_state_count"])
        or float(fbs_times[-1]) != float(expected["end_time"])
    ):
        raise ValueError("EXECUTION_FBS_TIME_SET_NOT_APPROVED")
    approved_automatic_budget = int(expected["automatic_retry_budget"])
    selector_budget = max(
        (
            int(item["retry_budget"])
            for item in intent_revision["contract"]["allowed_numerical_changes"]
        ),
        default=0,
    )
    if approved_automatic_budget > selector_budget:
        raise ValueError("INTENT_RETRY_BUDGET_EXCEEDS_ALLOWED_CHANGES")
    if int(value["retry_budget"]) > approved_automatic_budget:
        raise ValueError("EXECUTION_RETRY_BUDGET_EXCEEDS_EXPECTED_RUN")
    installed_runtime_tree = str(
        install_manifest["fbs_runtime"]["sha256"]
    )
    if (
        str(value["expected_fbs_runtime_tree_sha256"])
        != installed_runtime_tree
    ):
        raise ValueError("EXECUTION_FBS_RUNTIME_TREE_NOT_INSTALLED")
    return approved_automatic_budget
```

Add `"schemas/execution-request.schema.json"` to `EXPECTED_RESOURCES`. The
production `preflight` adapter must load current hashes from the immutable
case/intent/install records, create `ExecutionBindings`, call this function,
validate the returned internal object as `run-config`, and only then call
`AttemptStore.start`. Before conversion it must call
`validate_execution_request_against_intent`; the returned budget is the only
`approved_retry_budget` passed to `build_attempt_config`. No other handler may
construct an internal run config.

- [ ] **Step 7: Write the closed production registry RED**

Append to `tests/unit/test_command_registry.py`:

```python
from febio_cae_harness.commands.production import (
    PRODUCTION_COMMANDS,
    build_production_orchestrator,
    production_handler_map,
)


EXPECTED = {
    "case.init", "case.adopt", "case.status",
    "source.resolve", "source.approve", "input.ingest",
    "inspect.feb", "inspect.step", "inspect.inheritance",
    "intent.draft", "intent.request-approval", "intent.approve", "intent.show",
    "model.adopt-existing", "preflight", "solve", "verify", "diagnose",
    "retry", "report", "cancel",
}


def test_production_registry_is_closed_and_workflow_is_internal() -> None:
    handlers = production_handler_map()
    assert PRODUCTION_COMMANDS == frozenset(EXPECTED)
    assert set(handlers) == EXPECTED
    assert "workflow.run" not in handlers
    assert len({id(handler) for handler in handlers.values()}) == len(EXPECTED)
    orchestrator = build_production_orchestrator()
    assert set(orchestrator._services.handlers) == EXPECTED
```

- [ ] **Step 8: Run the registry RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_command_registry.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.commands.production'`.

- [ ] **Step 9: Add common authoritative-load helpers and the production registry**

Create `commands/common.py`. These are the only production helpers permitted
to reopen an active attempt or turn domain evidence into a `CommandResult`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Mapping

from febio_cae_harness.attempt_store import AttemptPaths, AttemptStore
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.events import read_event_log
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.response import CommandResult, EvidenceRecord


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def state_of(case: CaseStore) -> str:
    return str(case.replay(repair_manifest=False)["harness"]["case_state"])


def evidence(kind: str, value: object) -> EvidenceRecord:
    payload = asdict(value) if is_dataclass(value) else dict(value)
    for key, item in tuple(payload.items()):
        if isinstance(item, Path):
            payload[key] = str(item.resolve(strict=True))
    return EvidenceRecord(kind=kind, data=payload)


def success(
    case: CaseStore,
    *records: EvidenceRecord,
    artifacts: tuple[dict[str, object], ...] = (),
) -> CommandResult:
    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        status=Status.SUCCESS,
        case_state=state_of(case),
        artifacts=artifacts,
        evidence=tuple(records),
    )


def artifact(role: str, path: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    return {
        "role": role,
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def latest_event(case: CaseStore, event_type: str):
    found = [
        item for item in read_event_log(case.event_log)
        if item.event_type == event_type
    ]
    if not found:
        raise RuntimeError(f"AUTHORITATIVE_EVENT_MISSING:{event_type}")
    return found[-1]


def open_bound_attempt(case: CaseStore) -> AttemptStore:
    events = [
        item
        for item in read_event_log(case.event_log)
        if isinstance(item.payload.get("attempt_id"), str)
        and item.event_type in {
            "PREFLIGHT_PASSED", "COMPLETION_DECIDED", "RESULT_VALIDATED",
            "PROMOTION_COMPLETED", "REPORT_PROMOTED",
        }
    ]
    if not events:
        raise RuntimeError("AUTHORITATIVE_ATTEMPT_EVENT_MISSING")
    attempt_id = str(events[-1].payload["attempt_id"])
    root = (
        case.case_dir / "90_Temporary" / "attempts" / attempt_id
    ).resolve(strict=True)
    preflight = load_json(root / "preflight-decision.json")
    if root.name != attempt_id or preflight.get("attempt_id") != attempt_id:
        raise RuntimeError("AUTHORITATIVE_ATTEMPT_BINDING_MISMATCH")
    preflight_events = [
        item for item in events
        if item.event_type == "PREFLIGHT_PASSED"
        and item.payload.get("attempt_id") == attempt_id
    ]
    if len(preflight_events) != 1:
        raise RuntimeError("AUTHORITATIVE_PREFLIGHT_EVENT_COUNT")
    resume_records = [
        item for item in preflight["evidence"]
        if item.get("role") == "resume-key"
    ]
    if len(resume_records) != 1:
        raise RuntimeError("AUTHORITATIVE_RESUME_KEY_COUNT")
    resume_key = str(resume_records[0]["sha256"])
    if preflight_events[0].payload.get("resume_key") != resume_key:
        raise RuntimeError("AUTHORITATIVE_RESUME_KEY_BINDING_MISMATCH")
    return AttemptStore(
        case.case_dir,
        attempt_id,
        root,
        resume_key,
        AttemptPaths(
            root / "solver",
            root / "raw-logs",
            root / "generated",
            root / "stage-events.jsonl",
            root / "attempt-result.json",
        ),
    )
```

Create `commands/production.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Mapping

from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.orchestrator import HarnessServices, Orchestrator
from febio_cae_harness.response import CommandResult

Handler = Callable[[object | None, Mapping[str, object]], CommandResult]

PRODUCTION_COMMANDS = frozenset({
    "case.init", "case.adopt", "case.status",
    "source.resolve", "source.approve", "input.ingest",
    "inspect.feb", "inspect.step", "inspect.inheritance",
    "intent.draft", "intent.request-approval", "intent.approve", "intent.show",
    "model.adopt-existing", "preflight", "solve", "verify", "diagnose",
    "retry", "report", "cancel",
})


def production_handler_map() -> dict[str, Handler]:
    from .case import handle_case_adopt, handle_case_init, handle_case_status
    from .cancel import handle_cancel
    from .diagnose import handle_diagnose, handle_retry
    from .input import handle_input_ingest
    from .inspect import (
        handle_inspect_feb,
        handle_inspect_inheritance,
        handle_inspect_step,
    )
    from .intent import (
        handle_intent_approve,
        handle_intent_draft,
        handle_intent_request_approval,
        handle_intent_show,
    )
    from .model import handle_model_adopt_existing, handle_preflight
    from .report import handle_report
    from .solve import handle_solve
    from .source import handle_source_approve, handle_source_resolve
    from .verify import handle_verify

    handlers = {
        "case.init": handle_case_init,
        "case.adopt": handle_case_adopt,
        "case.status": handle_case_status,
        "source.resolve": handle_source_resolve,
        "source.approve": handle_source_approve,
        "input.ingest": handle_input_ingest,
        "inspect.feb": handle_inspect_feb,
        "inspect.step": handle_inspect_step,
        "inspect.inheritance": handle_inspect_inheritance,
        "intent.draft": handle_intent_draft,
        "intent.request-approval": handle_intent_request_approval,
        "intent.approve": handle_intent_approve,
        "intent.show": handle_intent_show,
        "model.adopt-existing": handle_model_adopt_existing,
        "preflight": handle_preflight,
        "solve": handle_solve,
        "verify": handle_verify,
        "diagnose": handle_diagnose,
        "retry": handle_retry,
        "report": handle_report,
        "cancel": handle_cancel,
    }
    if set(handlers) != PRODUCTION_COMMANDS:
        raise RuntimeError("PRODUCTION_HANDLER_SET_DRIFT")
    if len({id(item) for item in handlers.values()}) != len(handlers):
        raise RuntimeError("PRODUCTION_HANDLER_ALIAS_FORBIDDEN")
    return handlers


def build_production_orchestrator(
    *,
    handlers: Mapping[str, Handler] | None = None,
    cases: object = CaseStore,
) -> Orchestrator:
    selected = dict(production_handler_map() if handlers is None else handlers)
    if set(selected) != PRODUCTION_COMMANDS:
        raise ValueError("PRODUCTION_HANDLER_SET_INVALID")
    return Orchestrator(HarnessServices(
        cases=cases,
        sources=None,
        inspector=None,
        intents=None,
        attempts=None,
        preflight=None,
        runner=None,
        logs=None,
        fbs=None,
        completion=None,
        reporter=None,
        handlers=selected,
    ))
```

- [ ] **Step 10: Run the registry GREEN and commit the wiring skeleton**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_command_registry.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/common.py `
  apps/febio_cae_harness/src/febio_cae_harness/commands/production.py `
  apps/febio_cae_harness/tests/unit/test_command_registry.py
git commit -m "feat: close the production command registry"
```

Expected: the focused test passes and `workflow.run` is absent from the
registry because `Orchestrator._run_workflow` owns it.

- [ ] **Step 11: Write the source, input, and inspection handler RED**

Create `tests/unit/test_production_sources.py`:

```python
import json
from pathlib import Path

from febio_cae_harness.commands.input import handle_input_ingest
from febio_cae_harness.commands.inspect import handle_inspect_feb
from febio_cae_harness.commands.source import handle_source_resolve
from febio_cae_harness.hashing import sha256_file


def test_exact_source_reaches_compact_feb_inspection(
    initialized_case,
    complete_feb,
    source_expectation_file,
) -> None:
    resolved = handle_source_resolve(initialized_case, {
        "request_json": source_expectation_file,
    })
    assert resolved.evidence[0].kind == "source-resolution"
    assert resolved.evidence[0].data["status"] == "EXACT"
    assert resolved.evidence[0].data["source_selection_approval_required"] is False
    assert resolved.evidence[0].data["selected"]["path"] == str(
        complete_feb.resolve()
    )
    ingested = handle_input_ingest(initialized_case, {
        "role": "authoritative-feb",
    })
    assert ingested.evidence[0].kind == "input-record"
    inspected = handle_inspect_feb(initialized_case, {
        "excluded_domains": ["rigid-tool"],
    })
    assert inspected.case_state == "INPUT_INSPECTED"
    payload = inspected.evidence[0].data
    assert payload["source_sha256"] == sha256_file(complete_feb)
    assert "nodes" not in payload
    assert "elements" not in payload
    assert payload["domains"]
    selection = json.loads((
        initialized_case.case_dir
        / "05_Verification/harness/feb-inspection-selection.json"
    ).read_text(encoding="utf-8"))
    assert selection == {
        "schema_version": 1,
        "excluded_domains": ["rigid-tool"],
    }
```

Add separate tests in the same file for an alternate source and for ambiguous
sources. `source.resolve` must return exit `10`, persist exactly one
source-selection approval request, and create no InputRecord. After
`source.approve`, `input.ingest` must rehash the approved candidate before
calling `ingest_create_new`. A changed byte must raise
`SOURCE_SELECTION_APPROVED_FILE_DRIFT` before an InputRecord or state event is
created. A `MISSING` or `PROHIBITED_PREDECESSOR` resolution must return typed
exit `20`, create no source-selection request, and expose only `cancel` as the
next action. Round-trip the persisted resolution through its closed schema and
decoder; mutation of `path`, bytes, hash, mtime, status, candidate membership,
an `EXACT` selection to a discovered-but-nonmatching or non-preferred
candidate, or an extra field must fail before ingest. `inspect.step` must reject a
`.feb`; `inspect.inheritance` must emit every `lineage_evidence` and
`reference_results` item, with every reference result containing
`"reuse": false`, validate the closed inheritance schema, and set
`reference_results_reusable_as_attempt_output=false`. Mutating any referenced
external file after source resolution must fail before emitting success.
For an alternate source, also mutate or rename the approval record after
`SOURCE_SELECTION_APPROVED`; its path, hash, request ID, candidate-set digest,
and selected candidate must all be rechecked against that immutable event
before ingest.

- [ ] **Step 12: Run the source-handler RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_sources.py -q
```

Expected: collection fails because `handle_source_resolve` is not defined.

- [ ] **Step 13: Implement source/input/inspection adapters against Phase 1A**

In `commands/source.py`, implement and persist the exact resolution payload:

```python
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import secrets

from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from febio_cae_harness.jsonio import atomic_create_artifact
from febio_cae_harness.provenance import (
    SourceCandidate,
    SourceExpectation,
    SourceResolution,
    SourceResolutionStatus,
    load_source_expectation,
    resolve_source,
)
from febio_cae_harness.response import CommandResult, EvidenceRecord
from febio_cae_harness.schema import validate_schema
from febio_cae_harness.source_approval import (
    approve_case_source_selection,
    request_case_source_selection,
)


def _candidate_payload(item):
    return None if item is None else {
        "path": str(item.canonical_path),
        "bytes": item.bytes,
        "sha256": item.sha256,
        "modified_ns": item.modified_ns,
    }


def _resolution_payload(value):
    payload = {
        "schema_version": 1,
        "status": value.status.value,
        "selected": _candidate_payload(value.selected),
        "discovered_candidates": [
            _candidate_payload(item) for item in value.discovered_candidates
        ],
        "candidates_matching_expected": [
            _candidate_payload(item)
            for item in value.candidates_matching_expected
        ],
        "candidate_set_digest": value.candidate_set_digest,
        "selection_reason": value.selection_reason,
        "source_selection_approval_required": value.status in {
            SourceResolutionStatus.ALTERNATE_REQUIRES_APPROVAL,
            SourceResolutionStatus.AMBIGUOUS,
        },
        "expectation": value.expectation.to_payload(),
    }
    validate_schema("source-resolution", payload)
    return payload


def _candidate_from_payload(value):
    return SourceCandidate(
        canonical_path=Path(str(value["path"])),
        bytes=int(value["bytes"]),
        sha256=str(value["sha256"]),
        modified_ns=int(value["modified_ns"]),
    )


def load_persisted_resolution(path: Path) -> SourceResolution:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    validate_schema("source-resolution", value)
    expectation = SourceExpectation.from_payload(value["expectation"])
    selected = (
        None
        if value["selected"] is None
        else _candidate_from_payload(value["selected"])
    )
    discovered = tuple(
        _candidate_from_payload(item)
        for item in value["discovered_candidates"]
    )
    matching = tuple(
        _candidate_from_payload(item)
        for item in value["candidates_matching_expected"]
    )
    candidate_set_digest = sha256_bytes(canonical_json_bytes([
        item.digest_value() for item in discovered
    ]))
    if candidate_set_digest != value["candidate_set_digest"]:
        raise ValueError("SOURCE_RESOLUTION_CANDIDATE_SET_DIGEST_DRIFT")
    if selected is not None and selected not in discovered:
        raise ValueError("SOURCE_RESOLUTION_SELECTED_NOT_DISCOVERED")
    if any(item not in discovered for item in matching):
        raise ValueError("SOURCE_RESOLUTION_MATCH_NOT_DISCOVERED")
    if any(
        item.sha256 != expectation.expected_sha256
        or item.bytes != expectation.expected_bytes
        for item in matching
    ):
        raise ValueError("SOURCE_RESOLUTION_FALSE_EXPECTED_MATCH")
    status = SourceResolutionStatus(str(value["status"]))
    if status is SourceResolutionStatus.EXACT:
        if selected is None or selected not in matching:
            raise ValueError("SOURCE_RESOLUTION_EXACT_SELECTION_NOT_MATCHING")
        if (
            expectation.preferred_path is None
            or selected.canonical_path.resolve(strict=True)
            != expectation.preferred_path.resolve(strict=True)
        ):
            raise ValueError("SOURCE_RESOLUTION_EXACT_NOT_PREFERRED_PATH")
    return SourceResolution(
        expectation=expectation,
        status=status,
        selected=selected,
        discovered_candidates=discovered,
        candidates_matching_expected=matching,
        candidate_set_digest=candidate_set_digest,
        selection_reason=str(value["selection_reason"]),
    )


def handle_source_resolve(case, request):
    expectation = load_source_expectation(request["request_json"])
    resolution = resolve_source(expectation)
    payload = _resolution_payload(resolution)
    path = (
        case.case_dir
        / "05_Verification/harness/source-resolution.json"
    )
    atomic_create_artifact(path, canonical_json_bytes(payload) + b"\n")
    record = EvidenceRecord("source-resolution", payload)
    if resolution.status is SourceResolutionStatus.EXACT:
        return CommandResult(
            ExitCode.SUCCESS, Status.SUCCESS,
            case.replay(repair_manifest=False)["harness"]["case_state"],
            evidence=(record,),
        )
    if resolution.status not in {
        SourceResolutionStatus.ALTERNATE_REQUIRES_APPROVAL,
        SourceResolutionStatus.AMBIGUOUS,
    }:
        return CommandResult(
            ExitCode.INVALID_INPUT_OR_CONTRACT,
            Status.ERROR,
            case.replay(repair_manifest=False)["harness"]["case_state"],
            blockers=({
                "code": f"SOURCE_RESOLUTION_{resolution.status.value}",
                "message": resolution.selection_reason,
            },),
            allowed_next_actions=("cancel",),
            evidence=(record,),
        )
    request_record = request_case_source_selection(
        case,
        analysis_id=str(case.replay()["analysis_id"]),
        blocker_digest=sha256_bytes(canonical_json_bytes(payload)),
        resolution=resolution,
        requested_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        nonce=secrets.token_urlsafe(32),
    )
    request_payload = asdict(request_record)
    request_payload.pop("request_path")
    return CommandResult(
        ExitCode.WAITING_FOR_HUMAN,
        Status.WAITING_FOR_HUMAN,
        case.replay(repair_manifest=False)["harness"]["case_state"],
        blockers=({
            "code": "SOURCE_SELECTION_APPROVAL_REQUIRED",
            "message": "source authority is not an exact preferred-path match",
        },),
        allowed_next_actions=("source approve", "cancel"),
        evidence=(
            record,
            EvidenceRecord("source-selection-approval-request", request_payload),
        ),
    )


def handle_source_approve(case, request):
    approval = json.loads(
        Path(request["approval_json"]).resolve(strict=True).read_text(
            encoding="utf-8"
        )
    )
    request_files = sorted(
        (case.case_dir / "01_Input").glob(
            "source-selection-request-*.json"
        )
    )
    if len(request_files) != 1:
        raise RuntimeError("SOURCE_SELECTION_REQUEST_COUNT")
    record = approve_case_source_selection(
        case, request_files[0], **approval
    )
    payload = asdict(record)
    payload.pop("record_path")
    return CommandResult(
        ExitCode.SUCCESS,
        Status.SUCCESS,
        case.replay(repair_manifest=False)["harness"]["case_state"],
        evidence=(EvidenceRecord("source-selection-approval", payload),),
    )
```

Do not invent source approval fields: pass the closed user JSON unchanged to
`approve_case_source_selection`.

Create `schemas/source-resolution.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/source-resolution.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "status", "selected", "discovered_candidates",
    "candidates_matching_expected", "candidate_set_digest",
    "selection_reason", "source_selection_approval_required", "expectation"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "status": {
      "enum": [
        "EXACT", "ALTERNATE_REQUIRES_APPROVAL", "AMBIGUOUS", "MISSING",
        "PROHIBITED_PREDECESSOR"
      ]
    },
    "selected": {
      "oneOf": [
        {"type": "null"},
        {"$ref": "#/$defs/candidate"}
      ]
    },
    "discovered_candidates": {
      "type": "array",
      "uniqueItems": true,
      "items": {"$ref": "#/$defs/candidate"}
    },
    "candidates_matching_expected": {
      "type": "array",
      "uniqueItems": true,
      "items": {"$ref": "#/$defs/candidate"}
    },
    "candidate_set_digest": {
      "type": "string",
      "pattern": "^[0-9A-F]{64}$"
    },
    "selection_reason": {"type": "string", "minLength": 1},
    "source_selection_approval_required": {"type": "boolean"},
    "expectation": {"type": "object"}
  },
  "$defs": {
    "candidate": {
      "type": "object",
      "additionalProperties": false,
      "required": ["path", "bytes", "sha256", "modified_ns"],
      "properties": {
        "path": {"type": "string", "minLength": 1},
        "bytes": {"type": "integer", "minimum": 0},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "modified_ns": {"type": "integer", "minimum": 0}
      }
    }
  },
  "allOf": [
    {
      "if": {"properties": {"status": {"const": "EXACT"}}},
      "then": {
        "properties": {
          "selected": {"$ref": "#/$defs/candidate"},
          "candidates_matching_expected": {"minItems": 1},
          "source_selection_approval_required": {"const": false}
        }
      }
    },
    {
      "if": {
        "properties": {
          "status": {"const": "ALTERNATE_REQUIRES_APPROVAL"}
        }
      },
      "then": {
        "properties": {
          "selected": {"type": "null"},
          "candidates_matching_expected": {"minItems": 1, "maxItems": 1},
          "source_selection_approval_required": {"const": true}
        }
      }
    },
    {
      "if": {"properties": {"status": {"const": "AMBIGUOUS"}}},
      "then": {
        "properties": {
          "selected": {"type": "null"},
          "candidates_matching_expected": {"minItems": 2},
          "source_selection_approval_required": {"const": true}
        }
      }
    },
    {
      "if": {"properties": {"status": {"const": "MISSING"}}},
      "then": {
        "properties": {
          "selected": {"type": "null"},
          "candidates_matching_expected": {"maxItems": 0},
          "source_selection_approval_required": {"const": false}
        }
      }
    },
    {
      "if": {
        "properties": {
          "status": {"const": "PROHIBITED_PREDECESSOR"}
        }
      },
      "then": {
        "properties": {
          "selected": {"type": "null"},
          "source_selection_approval_required": {"const": false}
        }
      }
    }
  ]
}
```

Create `schemas/inheritance-inspection.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/inheritance-inspection.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version", "lineage_evidence", "reference_results",
    "reference_results_reusable_as_attempt_output"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "lineage_evidence": {
      "type": "array",
      "uniqueItems": true,
      "items": {"$ref": "#/$defs/evidencePointer"}
    },
    "reference_results": {
      "type": "array",
      "uniqueItems": true,
      "items": {"$ref": "#/$defs/referenceResult"}
    },
    "reference_results_reusable_as_attempt_output": {"const": false}
  },
  "$defs": {
    "evidencePointer": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "canonical_path", "sha256", "bytes"],
      "properties": {
        "kind": {"type": "string", "minLength": 1},
        "canonical_path": {"type": "string", "minLength": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "bytes": {"type": "integer", "minimum": 1}
      }
    },
    "referenceResult": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "canonical_path", "sha256", "bytes", "reuse"],
      "properties": {
        "kind": {"type": "string", "minLength": 1},
        "canonical_path": {"type": "string", "minLength": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "bytes": {"type": "integer", "minimum": 1},
        "reuse": {"const": false}
      }
    }
  }
}
```

The decoder additionally passes `expectation` through Phase 1A
`SourceExpectation.from_payload`, so the separately installed closed
`source-expectation` schema remains authoritative for that nested object. Add
both `"schemas/source-resolution.schema.json"` and
`"schemas/inheritance-inspection.schema.json"` to `EXPECTED_RESOURCES`.

In `commands/input.py`, load the saved resolution. For an alternate source,
load the single bound source-selection approval record from `01_Input`. Select
only the exact candidate represented by the resolution or the approval record,
then revalidate its path, byte count, SHA-256, and original modified time:

```python
from febio_cae_harness.events import read_event_log


resolution = load_persisted_resolution(
    case.case_dir / "05_Verification/harness/source-resolution.json"
)
if resolution.status is SourceResolutionStatus.EXACT:
    if resolution.selected is None:
        raise RuntimeError("SOURCE_RESOLUTION_EXACT_WITHOUT_SELECTION")
    selected_candidate = resolution.selected
elif resolution.status in {
    SourceResolutionStatus.ALTERNATE_REQUIRES_APPROVAL,
    SourceResolutionStatus.AMBIGUOUS,
}:
    approval_paths = sorted(
        (case.case_dir / "01_Input").glob(
            "source-selection-approval-*.json"
        )
    )
    if len(approval_paths) != 1:
        raise RuntimeError("SOURCE_SELECTION_APPROVAL_COUNT")
    approval_path = approval_paths[0].resolve(strict=True)
    approval = load_json(approval_path)
    case.replay(repair_manifest=False)
    approval_events = [
        event
        for event in read_event_log(case.event_log)
        if event.event_type == "SOURCE_SELECTION_APPROVED"
    ]
    if len(approval_events) != 1:
        raise RuntimeError("SOURCE_SELECTION_APPROVAL_EVENT_COUNT")
    approval_event = approval_events[0]
    event_record_path = Path(str(
        approval_event.payload["record_path"]
    ))
    if not event_record_path.is_absolute():
        event_record_path = case.case_dir / event_record_path
    event_record_path = event_record_path.resolve(strict=True)
    if (
        event_record_path != approval_path
        or approval_event.payload["request_id"] != approval["request_id"]
        or approval_event.payload["approval_record_sha256"]
        != sha256_file(approval_path)
        or approval_event.payload["candidate_set_digest"]
        != approval["candidate_set_digest"]
        or approval_event.payload["selected_canonical_path"]
        != approval["selected_canonical_path"]
        or approval_event.payload["selected_sha256"]
        != approval["selected_sha256"]
        or int(approval_event.payload["selected_bytes"])
        != int(approval["selected_bytes"])
    ):
        raise RuntimeError("SOURCE_SELECTION_APPROVAL_EVENT_DRIFT")
    matches = [
        item
        for item in resolution.candidates_matching_expected
        if (
            str(item.canonical_path)
            == str(approval["selected_canonical_path"])
            and item.bytes == int(approval["selected_bytes"])
            and item.sha256 == str(approval["selected_sha256"])
        )
    ]
    if len(matches) != 1:
        raise RuntimeError("SOURCE_SELECTION_APPROVAL_NOT_IN_RESOLUTION")
    selected_candidate = matches[0]
else:
    raise RuntimeError(f"SOURCE_NOT_INGESTIBLE:{resolution.status.value}")

selected_path = selected_candidate.canonical_path.resolve(strict=True)
before = selected_path.stat()
observed_sha256 = sha256_file(selected_path)
after = selected_path.stat()
if (
    before.st_size != selected_candidate.bytes
    or after.st_size != selected_candidate.bytes
    or before.st_mtime_ns != selected_candidate.modified_ns
    or after.st_mtime_ns != selected_candidate.modified_ns
    or observed_sha256 != selected_candidate.sha256
):
    raise RuntimeError("SOURCE_SELECTION_APPROVED_FILE_DRIFT")
record = ingest_create_new(
    case.case_dir,
    selected_path,
    "authoritative-feb",
    resolution=resolution,
)
```

Return one `EvidenceRecord("input-record", persisted_input_record_json)`.
Never use a candidate path supplied on the `input ingest` command line.

In `commands/inspect.py`, use the only persisted authoritative InputRecord:

```python
inspection = inspect_feb(
    canonical_input_path,
    excluded_domains=tuple(str(item) for item in request["excluded_domains"]),
)
payload = inspection.to_payload()
validate_schema("feb-inspection", payload)
atomic_create_artifact(
    case.case_dir / "05_Verification/harness/feb-inspection.json",
    canonical_json_bytes(payload) + b"\n",
)
atomic_create_artifact(
    case.case_dir / "05_Verification/harness/feb-inspection-selection.json",
    canonical_json_bytes({
        "schema_version": 1,
        "excluded_domains": list(request["excluded_domains"]),
    }) + b"\n",
)
case.append(
    "INPUT_INSPECTED",
    CaseState.INPUT_INSPECTED,
    {
        "input_record_set_digest": input_record_set_digest(case.case_dir),
        "source_sha256": inspection.source_sha256,
        "feb_domain_signature": inspection.domain_signature,
        "invariant_signatures": inspection.invariant_signatures,
        "process_started": False,
    },
)
return success(case, EvidenceRecord("feb-inspection", payload))
```

`handle_inspect_step` calls only `inspect_step` on a saved `.step`/`.stp`
source and emits `step-inspection`. In `commands/inspect.py`, implement the
inheritance handler against the persisted resolution rather than accepting a
new caller path:

```python
from pathlib import Path

from febio_cae_harness.commands.source import load_persisted_resolution
from febio_cae_harness.hashing import sha256_file


def _verified_external_pointer(item) -> dict[str, object]:
    payload = item.to_payload()
    path = Path(payload["canonical_path"]).resolve(strict=True)
    if (
        path.stat().st_size != int(payload["bytes"])
        or sha256_file(path) != payload["sha256"]
    ):
        raise RuntimeError(f"LINEAGE_POINTER_DRIFT:{payload['kind']}")
    payload["canonical_path"] = str(path)
    return payload


def handle_inspect_inheritance(case, request):
    resolution = load_persisted_resolution(
        case.case_dir / "05_Verification/harness/source-resolution.json"
    )
    expectation = resolution.expectation
    lineage = [
        _verified_external_pointer(item)
        for item in expectation.lineage_evidence
    ]
    reference_results = [
        _verified_external_pointer(item)
        for item in expectation.reference_results
    ]
    if any(item["reuse"] is not False for item in reference_results):
        raise RuntimeError("REFERENCE_RESULT_REUSE_ENABLED")
    payload = {
        "schema_version": 1,
        "lineage_evidence": lineage,
        "reference_results": reference_results,
        "reference_results_reusable_as_attempt_output": False,
    }
    validate_schema("inheritance-inspection", payload)
    return success(
        case,
        EvidenceRecord("inheritance-inspection", payload),
    )
```

Add the existing `validate_schema`, `success`, and `EvidenceRecord` imports to
that module. Both inspection handlers are read-only and create no attempt.

- [ ] **Step 14: Run source/input/inspection GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_sources.py `
  apps/febio_cae_harness/tests/integration/test_input_store.py `
  apps/febio_cae_harness/tests/unit/test_feb_inspector.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/source.py `
  apps/febio_cae_harness/src/febio_cae_harness/commands/input.py `
  apps/febio_cae_harness/src/febio_cae_harness/commands/inspect.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/source-resolution.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/inheritance-inspection.schema.json `
  apps/febio_cae_harness/tests/unit/test_production_sources.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: wire authoritative source and inspection commands"
```

Expected: all selected tests pass and no test fixture contains product
geometry.

- [ ] **Step 15: Write the intent and human-approval handler RED**

Create `tests/unit/test_production_intent.py`:

```python
import json

import pytest

from febio_cae_harness.commands.intent import (
    handle_intent_approve,
    handle_intent_draft,
    handle_intent_request_approval,
    handle_intent_show,
)


def test_draft_request_approve_show_never_starts_attempt(
    inspected_case,
    complete_contract_file,
    install_manifest_env,
    tmp_path,
) -> None:
    drafted = handle_intent_draft(inspected_case, {
        "contract_json": complete_contract_file,
    })
    assert drafted.case_state == "INTENT_DRAFTED"
    assert drafted.evidence[0].kind == "intent-revision"
    waiting = handle_intent_request_approval(inspected_case, {})
    assert int(waiting.exit_code) == 10
    assert waiting.status.value == "waiting_for_human"
    packet = waiting.evidence[0].data
    approval = {
        **packet,
        "approval_text": "Approve this exact synthetic analysis intent.",
        "actor_kind": "human",
        "source_channel": "pytest",
        "approved_at": "2026-07-30T00:01:00Z",
    }
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    accepted = handle_intent_approve(inspected_case, {
        "approval_json": approval_path,
    })
    assert accepted.case_state == "INTENT_APPROVED"
    shown = handle_intent_show(inspected_case, {})
    assert shown.evidence[0].data["approval"]["request_id"] == packet["request_id"]
    assert not (
        inspected_case.case_dir / "90_Temporary/attempts"
    ).exists()


def test_approval_reloads_current_install_profile_and_rejects_drift(
    drafted_case,
    approval_json,
    install_manifest_env,
) -> None:
    handle_intent_request_approval(drafted_case, {})
    changed = json.loads(install_manifest_env.read_text(encoding="utf-8"))
    changed["release_id"] = "synthetic-drift"
    install_manifest_env.write_text(
        json.dumps(changed, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="execution profile drift"):
        handle_intent_approve(drafted_case, {
            "approval_json": approval_json,
        })
```

Also mutate nonce, request id, contract hash, input-record-set digest, source
approval digest, approval text, actor kind, and approved timestamp one at a
time. Each mutation must fail before `INTENT_APPROVED` and preserve all
pre-existing bytes. After approving revision 1, draft revision 2 and request
approval; `intent show` must return revision 2's request as outstanding rather
than hiding it because revision 1 has a record. Mutating either the bound FEB
inspection or its selection record after the request must change the
case-specific execution-profile digest and reject approval.

- [ ] **Step 16: Run the intent-handler RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_intent.py -q
```

Expected: collection fails because `handle_intent_draft` is not defined.

- [ ] **Step 17: Implement the four intent handlers**

In `commands/intent.py`, use this exact installed execution-profile digest and
the Phase 1A approval APIs:

```python
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

from febio_cae_harness.approval import (
    create_revision,
    record_approval,
    request_approval,
)
from febio_cae_harness.commands.common import evidence, load_json, success
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.execution_profile import execution_profile_sha256
from febio_cae_harness.hashing import canonical_json_bytes
from febio_cae_harness.response import CommandResult, EvidenceRecord


def _intent_dir(case) -> Path:
    return case.case_dir / "05_Verification/harness/intent"


def _only_or_latest(directory: Path, pattern: str) -> Path:
    found = sorted(directory.glob(pattern))
    if not found:
        raise RuntimeError(f"AUTHORITATIVE_RECORD_MISSING:{pattern}")
    return found[-1].resolve(strict=True)


def _source_approval(case) -> Path | None:
    found = sorted((
        case.case_dir / "01_Input"
    ).glob("source-selection-approval-*.json"))
    if len(found) > 1:
        raise RuntimeError("SOURCE_SELECTION_APPROVAL_COUNT")
    return None if not found else found[0].resolve(strict=True)


def handle_intent_draft(case, request):
    contract = load_json(Path(request["contract_json"]))
    inspection_path = (
        case.case_dir / "05_Verification/harness/feb-inspection.json"
    )
    selection_path = (
        case.case_dir
        / "05_Verification/harness/feb-inspection-selection.json"
    )
    inspection = load_json(inspection_path)
    selection = load_json(selection_path)
    revision = create_revision(
        case,
        contract,
        inspection=inspection,
        source_selection_approval_record=_source_approval(case),
    )
    binding = {
        "schema_version": 1,
        "revision": revision.revision,
        "contract_sha256": revision.contract_sha256,
        "feb_inspection_sha256": sha256_file(inspection_path),
        "feb_inspection_selection_sha256": sha256_file(selection_path),
        "excluded_domains": list(selection["excluded_domains"]),
    }
    atomic_create_artifact(
        _intent_dir(case)
        / f"intent-inspection-binding-r{revision.revision:04d}.json",
        canonical_json_bytes(binding) + b"\n",
    )
    payload = asdict(revision)
    payload["revision_path"] = str(revision.revision_path.resolve(strict=True))
    return success(case, EvidenceRecord("intent-revision", payload))


def handle_intent_request_approval(case, request):
    revision_path = _only_or_latest(
        _intent_dir(case), "analysis-intent-r*.json"
    )
    revision = load_json(revision_path)
    packet = request_approval(
        case,
        int(revision["revision"]),
        execution_profile_sha256=execution_profile_sha256(
            case,
            int(revision["revision"]),
        ),
        requested_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    payload = asdict(packet)
    payload.pop("request_path")
    return CommandResult(
        ExitCode.WAITING_FOR_HUMAN,
        Status.WAITING_FOR_HUMAN,
        case.replay(repair_manifest=False)["harness"]["case_state"],
        blockers=({
            "code": "ANALYSIS_INTENT_APPROVAL_REQUIRED",
            "message": "the exact immutable intent revision requires human approval",
        },),
        allowed_next_actions=("intent approve", "cancel"),
        evidence=(
            EvidenceRecord("analysis-intent-approval-request", payload),
        ),
    )


def handle_intent_approve(case, request):
    supplied = load_json(Path(request["approval_json"]))
    validate_schema("approval-record", supplied)
    request_path = _only_or_latest(
        _intent_dir(case), "approval-request-*.json"
    )
    bound = load_json(request_path)
    for key in (
        "purpose", "request_id", "analysis_id", "revision",
        "contract_sha256", "input_record_set_digest",
        "source_selection_approval_record_digest",
        "execution_profile_sha256", "nonce",
    ):
        if supplied.get(key) != bound.get(key):
            raise ValueError(f"APPROVAL_SUPPLIED_BINDING_MISMATCH:{key}")
    record = record_approval(
        case,
        request_path,
        presented_nonce=str(supplied["nonce"]),
        approval_text=str(supplied["approval_text"]),
        actor_kind=str(supplied["actor_kind"]),
        source_channel=str(supplied["source_channel"]),
        approved_at=str(supplied["approved_at"]),
        current_execution_profile_sha256=execution_profile_sha256(
            case,
            int(bound["revision"]),
        ),
        current_source_selection_approval_record=_source_approval(case),
    )
    payload = asdict(record)
    payload.pop("record_path")
    return success(
        case, EvidenceRecord("analysis-intent-approval", payload)
    )


def handle_intent_show(case, request):
    revision = load_json(_only_or_latest(
        _intent_dir(case), "analysis-intent-r*.json"
    ))
    requests = [
        load_json(path)
        for path in sorted(_intent_dir(case).glob("approval-request-*.json"))
    ]
    records = [
        load_json(path)
        for path in sorted(_intent_dir(case).glob("approval-record-*.json"))
    ]
    approved_request_ids = {
        str(item["request_id"]) for item in records
    }
    unapproved = [
        item
        for item in requests
        if str(item["request_id"]) not in approved_request_ids
    ]
    if len(unapproved) > 1:
        raise RuntimeError("OUTSTANDING_APPROVAL_REQUEST_COUNT")
    outstanding = None if not unapproved else unapproved[0]
    current_records = [
        item
        for item in records
        if int(item["revision"]) == int(revision["revision"])
    ]
    if len(current_records) > 1:
        raise RuntimeError("CURRENT_APPROVAL_RECORD_COUNT")
    current_approval = None if not current_records else current_records[0]
    payload = {
        "revision": revision,
        "outstanding_approval_request": outstanding,
        "approval": current_approval,
    }
    return success(case, EvidenceRecord("analysis-intent", payload))
```

The JSON accepted by `intent approve` is the complete closed
`approval-record` shape. Every request-bound field is compared with the
reloaded immutable request before the five human-supplied fields
(`approval_text`, `actor_kind`, `source_channel`, `approved_at`, and the
presented nonce) are passed to `record_approval`.

- [ ] **Step 18: Run intent GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_intent.py `
  apps/febio_cae_harness/tests/unit/test_approval.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/intent.py `
  apps/febio_cae_harness/tests/unit/test_production_intent.py
git commit -m "feat: wire immutable intent approval commands"
```

Expected: all tests pass; approval request/show/approve create no model,
attempt, process, LOG, XPLT, or permanent result.

- [ ] **Step 19: Write the model-adoption and preflight RED**

Create `tests/unit/test_production_preflight.py`:

```python
from febio_cae_harness.commands.model import (
    handle_model_adopt_existing,
    handle_preflight,
)


def test_preflight_is_only_external_request_to_attempt_conversion(
    approved_case,
    execution_request_file,
    installed_runtime,
) -> None:
    adopted = handle_model_adopt_existing(approved_case, {})
    assert adopted.case_state == "MODEL_BUILT"
    assert adopted.evidence[0].kind == "model-adoption"
    result = handle_preflight(approved_case, {
        "config_json": execution_request_file,
    })
    assert result.case_state == "PREFLIGHT_PASSED"
    assert result.evidence[0].kind == "preflight"
    preflight = result.evidence[0].data
    assert preflight["status"] == "accepted"
    assert preflight["checked_roles"] == [
        "adopted-feb", "staged-feb", "model-evidence",
        "intent-approval", "solver", "policy",
        "fbs-runtime-tree", "fbs-loaded-dll-profile", "resume-key",
    ]
    attempts = list((
        approved_case.case_dir / "90_Temporary/attempts"
    ).iterdir())
    assert len(attempts) == 1
    assert (attempts[0] / "model-evidence.json").is_file()
```

Add a spy around `build_attempt_config` and assert it is called once by
`preflight` and never by any other production handler. Mutate each of the nine
preflight bindings and assert no process launches and the state remains
`MODEL_BUILT`. Add a two-domain synthetic fixture whose saved selection is
`{"schema_version":1,"excluded_domains":["rigid-domain"]}`; assert
`create_model_evidence` receives exactly
`excluded_domains=("rigid-domain",)`. Mutating that selection after intent
approval must fail with `INTENT_INSPECTION_BINDING_DRIFT` before an attempt is
created. Add an older approved revision beside a newer one and prove preflight
selects the approval record by revision rather than filename order. Mutate in
turn the current request/record revision, contract hash, InputRecord-set
digest, source-selection digest, execution-profile hash, and nonce; each must
fail before `AttemptStore.start`. Place source request/approval JSON beside the
three canonical InputRecord patterns and prove they are excluded from
`input_manifest`, while mutation of any canonical InputRecord is detected by
the recomputed set digest. Independently mutate the nested contract bytes
without changing its declared hash and mutate the bound source-selection
approval file after human approval; both must fail before attempt creation.
Replace or rename the approval record while keeping its JSON fields
self-consistent and prove its mismatch with the immutable `INTENT_APPROVED`
event is also rejected.
Finally, alter the intent-expected solver/time/state contract or the installed
FBS runtime-tree hash and prove the external request is rejected before
conversion. Independently mutate every generated `tool-fingerprints.json`
field or its current install-manifest counterpart—including wheel,
build-provenance, install-manifest, and reviewed-source-commit identity—and
prove the exact comparison fails before `AttemptStore.start`.

- [ ] **Step 20: Run the model/preflight RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_preflight.py -q
```

Expected: collection fails because `handle_preflight` is not defined.

- [ ] **Step 21: Implement model adoption and the sole preflight conversion**

In `commands/model.py`, implement `handle_model_adopt_existing` by loading the
single `authoritative-feb-*.json` InputRecord, reconstructing the exact Phase
1A `InputRecord` dataclass, and calling `adopt_existing_feb`. Return:

```python
return success(case, EvidenceRecord("model-adoption", {
    "input_record_id": record.record_id,
    "source_sha256": record.sha256,
    "destination": str(adopted.path.resolve(strict=True)),
    "destination_bytes": adopted.bytes,
    "destination_sha256": adopted.sha256,
}))
```

Implement `handle_preflight` with this exact operation order:

```python
from febio_cae_harness.execution_profile import (
    expected_installed_tool_fingerprints,
    validate_current_intent_authority,
)
from febio_cae_harness.execution_request import (
    validate_execution_request_against_intent,
)
from febio_cae_harness.input_store import input_record_set_digest


external = load_execution_request(Path(request["config_json"]))
revision_path = _only_or_latest(intent_dir, "analysis-intent-r*.json")
revision = load_json(revision_path)
approval_candidates = [
    (path.resolve(strict=True), load_json(path))
    for path in sorted(intent_dir.glob("approval-record-*.json"))
]
approval_candidates = [
    item
    for item in approval_candidates
    if int(item[1]["revision"]) == int(revision["revision"])
]
if len(approval_candidates) != 1:
    raise RuntimeError("CURRENT_INTENT_APPROVAL_RECORD_COUNT")
approval_path, approval = approval_candidates[0]
approval_request_path = (
    intent_dir
    / f"approval-request-{approval['request_id']}.json"
).resolve(strict=True)
approval_request = load_json(approval_request_path)
validate_schema("approval-request", approval_request)
validate_schema("approval-record", approval)
for key in (
    "analysis_id", "revision", "contract_sha256", "input_record_set_digest",
    "source_selection_approval_record_digest",
):
    if (
        approval.get(key) != revision.get(key)
        or approval_request.get(key) != revision.get(key)
    ):
        raise RuntimeError(f"CURRENT_INTENT_APPROVAL_BINDING_DRIFT:{key}")
for key in (
    "purpose", "request_id", "analysis_id", "revision",
    "contract_sha256", "input_record_set_digest",
    "source_selection_approval_record_digest", "execution_profile_sha256",
    "nonce",
):
    if approval.get(key) != approval_request.get(key):
        raise RuntimeError(f"APPROVAL_REQUEST_RECORD_DRIFT:{key}")
install_path = Path(
    os.environ["FEBIO_CAE_INSTALL_MANIFEST"]
).resolve(strict=True)
install = load_json(install_path)
policy_path = resolve_install_pointer(
    install_path,
    install["release"]["repository_policy"],
)
policy = load_json(policy_path)
if int(policy["schema_version"]) != 1:
    raise RuntimeError("INSTALLED_REPOSITORY_POLICY_VERSION_UNSUPPORTED")
input_record_paths = sorted(
    path
    for pattern in (
        "authoritative-feb-*.json",
        "external-source-record-*.json",
        "baseline-result-reference-*.json",
    )
    for path in (case.case_dir / "01_Input").glob(pattern)
)
input_records = []
for path in input_record_paths:
    value = load_json(path)
    validate_schema("input-record", value)
    input_records.append(value)
current_input_record_set_digest = input_record_set_digest(case.case_dir)
if (
    not input_records
    or current_input_record_set_digest
    != revision["input_record_set_digest"]
):
    raise RuntimeError("CURRENT_INPUT_RECORD_SET_DRIFT")
validate_current_intent_authority(
    case,
    revision,
    approval,
    approval_path,
)
approved_retry_budget = validate_execution_request_against_intent(
    external,
    revision,
    install,
)
bindings = ExecutionBindings(
    input_manifest={"records": input_records},
    intent_revision=revision,
    input_record_set_digest=current_input_record_set_digest,
    source_selection_approval_record_digest=(
        revision["source_selection_approval_record_digest"]
    ),
    contract_sha256=str(revision["contract_sha256"]),
    analysis_intent_approval_record_sha256=sha256_file(approval_path),
    harness_version=str(install["harness_version"]),
    build_provenance_sha256=str(install["build_provenance"]["sha256"]),
    install_manifest_sha256=sha256_file(install_path),
    wheel_sha256=str(install["wheel"]["sha256"]),
    reviewed_source_commit=str(install["build_provenance"]["source_commit"]),
    fbs_loaded_dll_profile_sha256=str(
        install["fbs_runtime"]["loaded_dll_profile_sha256"]
    ),
    policy_sha256=str(install["release"]["repository_policy"]["sha256"]),
    policy_version=str(policy["schema_version"]),
)
selection = load_json(
    case.case_dir
    / "05_Verification/harness/feb-inspection-selection.json"
)
intent_binding_path = (
    intent_dir
    / f"intent-inspection-binding-r{int(revision['revision']):04d}.json"
).resolve(strict=True)
intent_binding = load_json(intent_binding_path)
inspection_path = (
    case.case_dir / "05_Verification/harness/feb-inspection.json"
)
if (
    intent_binding["revision"] != revision["revision"]
    or intent_binding["contract_sha256"] != revision["contract_sha256"]
    or intent_binding["feb_inspection_sha256"] != sha256_file(inspection_path)
    or intent_binding["feb_inspection_selection_sha256"]
    != sha256_file(
        case.case_dir
        / "05_Verification/harness/feb-inspection-selection.json"
    )
    or intent_binding["excluded_domains"] != selection["excluded_domains"]
):
    raise RuntimeError("INTENT_INSPECTION_BINDING_DRIFT")
config = build_attempt_config(
    external,
    bindings,
    approved_retry_budget=approved_retry_budget,
)
validate_schema("run-config", config)
if config["tool_fingerprints"] != expected_installed_tool_fingerprints(
    install_path,
    external.payload,
):
    raise RuntimeError("PREFLIGHT_INSTALLED_IDENTITY_DRIFT")
attempt = AttemptStore.start(case, config)
adopted = artifact_ref_from_model_adopted_event(case)
staged = stage_solver_input(attempt, adopted)
model = create_model_evidence(
    attempt,
    staged,
    excluded_domains=tuple(intent_binding["excluded_domains"]),
)
runtime = fbs_runtime_from_install_manifest(install)
decision = run_preflight(case, PreflightInputs(
    attempt=attempt,
    adopted_feb=ExpectedArtifact(adopted.path, adopted.sha256),
    staged_feb=ExpectedArtifact(staged.path, staged.sha256),
    model_evidence=ExpectedArtifact(model.path, model.sha256),
    intent_approval=ExpectedArtifact(
        approval_path, sha256_file(approval_path)
    ),
    solver=ExpectedArtifact(
        Path(external.payload["expected_solver"]["path"]),
        str(external.payload["expected_solver"]["sha256"]),
    ),
    fbs_runtime=runtime,
    policy=ExpectedArtifact(
        policy_path,
        str(install["release"]["repository_policy"]["sha256"]),
    ),
    resume_key=attempt.resume_key,
))
return success(
    case,
    EvidenceRecord("preflight", {
        "status": decision.status,
        "attempt_id": decision.attempt_id,
        "checked_roles": list(decision.checked_roles),
        "evidence": list(decision.evidence),
    }),
)
```

Define both helpers in the same module, with no caller-supplied paths:

```python
def artifact_ref_from_model_adopted_event(case) -> ArtifactRef:
    event = latest_event(case, "MODEL_ADOPTED")
    path = (case.case_dir / str(event.payload["destination"])).resolve(strict=True)
    if (
        path.stat().st_size != int(event.payload["destination_bytes"])
        or sha256_file(path) != event.payload["destination_sha256"]
    ):
        raise RuntimeError("ADOPTED_MODEL_EVENT_BINDING_DRIFT")
    return ArtifactRef(
        path,
        int(event.payload["destination_bytes"]),
        str(event.payload["destination_sha256"]),
    )


def fbs_runtime_from_install_manifest(value) -> FbsRuntime:
    runtime = value["fbs_runtime"]
    return FbsRuntime(
        Path(runtime["python_exe"]).resolve(strict=True),
        Path(runtime["fbs_module"]).resolve(strict=True),
        Path(runtime["febio_bin"]).resolve(strict=True),
        str(runtime["sha256"]),
        str(runtime["loaded_dll_profile_sha256"]),
    )
```

Import every named Phase 1A/B type and function explicitly. This module is the
only code that calls `build_attempt_config`; grep-enforce that invariant in
`test_production_preflight.py`.

- [ ] **Step 22: Run model/preflight GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_preflight.py `
  apps/febio_cae_harness/tests/unit/test_preflight.py `
  apps/febio_cae_harness/tests/unit/test_execution_request.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/model.py `
  apps/febio_cae_harness/tests/unit/test_production_preflight.py
git commit -m "feat: bind model adoption to transitive preflight"
```

Expected: all tests pass, exactly one attempt exists, and no process is
started.

- [ ] **Step 23: Write the exact Analysis Intent to XPLT-request RED**

Create `tests/unit/test_xplt_request_binding.py`:

```python
import json

from febio_cae_harness.commands.verify import (
    build_xplt_request_from_intent,
)
from febio_cae_harness.fbs_worker import (
    _alias_partitions,
    _partition_signature,
    _resolve_operation,
)


def test_synthetic_request_binds_all_intent_semantics(
    synthetic_xplt_attempt,
    synthetic_intent_revision,
    synthetic_execution_config,
) -> None:
    request = build_xplt_request_from_intent(
        synthetic_xplt_attempt,
        synthetic_intent_revision,
        synthetic_execution_config,
    )
    assert request.expected_times == tuple(
        index / 20 for index in range(21)
    )
    assert request.allow_zero_state is False
    assert {item["name"] for item in request.populations} == {
        "solid-domain", "rigid-domain",
    }
    assert {
        item["domain_alias"]: (
            item["material_name"],
            item["element_count"],
            item["referenced_node_count"],
            item["nodes_per_element"],
        )
        for item in request.domain_bindings
    } == {
        "solid-domain": (
            "deformable-material", 2, 14, 10,
        ),
        "rigid-domain": ("rigid-material", 1, 4, 4),
    }
    keys = {
        (
            item["name"], item["association"], item["operation"],
            item["population"],
        )
        for item in request.fields
    }
    assert keys == {
        ("displacement", "nodeData", 0, "rigid-domain"),
        ("displacement", "nodeData", 1, "rigid-domain"),
        ("displacement", "nodeData", 2, "rigid-domain"),
        ("displacement", "nodeData", 6, "rigid-domain"),
        ("stress", "elemData", "MAT3DS.EFFECTIVE", "solid-domain"),
        ("relative volume", "elemData", 0, "solid-domain"),
    }
    assert request.initial_coordinates == ()
    model = json.loads((
        synthetic_xplt_attempt.root / "model-evidence.json"
    ).read_text(encoding="utf-8"))
    assert (
        request.expected_initial_coordinate_signature_sha256
        == model["initial_coordinate_signature"]["sha256"]
    )
    check = request.kinematic_checks[0]
    assert len(check["expected_by_time"]) == 21
    assert check["expected_by_time"][0] == {
        "time": 0.0, "vector": [-0.0, 0.0, 0.0], "magnitude": 0.0,
    }
    assert check["expected_by_time"][-1] == {
        "time": 1.0,
        "vector": [0.0, 0.0, -2.0],
        "magnitude": 2.0,
    }
    assert check["abs_tol"] == 1.0e-6


def test_official_effective_enum_resolves_to_six_not_p2() -> None:
    class Mat3ds:
        EFFECTIVE = 6
        P2 = 8

    class Post:
        MAT3DS = Mat3ds

    class Fbs:
        post = Post

    assert _resolve_operation(Fbs(), "MAT3DS.EFFECTIVE") == 6
    assert _resolve_operation(Fbs(), "MAT3DS.EFFECTIVE") != Fbs.post.MAT3DS.P2


def test_empty_partition_names_bind_only_by_feb_authority() -> None:
    def partition(index, material, node_count):
        value = {
            "partition_index": index,
            "partition_name": "",
            "material_name": material,
            "element_indices": [index],
            "element_ids": [100 + index],
            "node_indices": list(range(node_count)),
            "node_ids": list(range(1, node_count + 1)),
            "elements": [{
                "element_index": index,
                "element_id": 100 + index,
                "node_count": node_count,
                "connectivity_node_indices": list(range(node_count)),
                "connectivity_node_ids": list(range(1, node_count + 1)),
            }],
        }
        return value

    partitions = [
        partition(0, "deformable-material", 10),
        partition(1, "rigid-material", 4),
    ]
    bindings = [
        {
            "domain_alias": alias,
            "material_name": item["material_name"],
            "element_count": 1,
            "referenced_node_count": len(item["node_ids"]),
            "element_type": element_type,
            "nodes_per_element": len(item["node_ids"]),
            "connectivity_signature_version":
                "feb-domain-connectivity-signature-v1",
            "connectivity_signature_sha256": _partition_signature(item),
        }
        for alias, element_type, item in (
            ("solid-domain", "TET10", partitions[0]),
            ("rigid-domain", "TET4", partitions[1]),
        )
    ]
    bound = _alias_partitions(partitions, bindings)
    assert [item["partition_name"] for item in bound] == ["", ""]
    assert [item["domain_alias"] for item in bound] == [
        "solid-domain", "rigid-domain",
    ]
    assert [item["elements"][0]["element_type"] for item in bound] == [
        "TET10", "TET4",
    ]
```

The synthetic fixture contains only fabricated coordinates, connectivity,
small aggregate counts, synthetic signatures, neutral material/domain names,
and a fabricated displacement vector. Implement the three named local pytest
fixtures in this test module with those exact values; they must not read
Phase 1E or any file below `02_CAE`.
Add rejection tests for an unknown alias, ambiguous alias prefix, material
qualifier mismatch, missing zero state, a request not stating all 21 states,
an operation other than integers or `fbs.post.MAT3DS.EFFECTIVE`, duplicate
field keys, nonfinite coordinates, and a rigid-body material that does not map
to exactly one FEB domain binding.

- [ ] **Step 24: Run the XPLT-request RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_xplt_request_binding.py -q
```

Expected: collection fails because
`build_xplt_request_from_intent` is not defined.

- [ ] **Step 25: Implement the deterministic XPLT request converter**

Add to `commands/verify.py`:

```python
from __future__ import annotations

import math
from pathlib import Path

from febio_cae_harness.attempt_store import AttemptStore
from febio_cae_harness.commands.common import load_json
from febio_cae_harness.fbs_protocol import XpltRequest
from febio_cae_harness.hashing import sha256_file


def _resolved(contract: dict[str, object], key: str):
    wrapper = contract[key]
    if not isinstance(wrapper, dict) or wrapper.get("resolved") is not True:
        raise ValueError(f"INTENT_VALUE_UNRESOLVED:{key}")
    return wrapper["value"]


def _population_binding(
    text: object,
    bindings: tuple[dict[str, object], ...],
) -> tuple[str, str]:
    value = str(text)
    candidates = sorted(
        (
            str(item["domain_alias"])
            for item in bindings
            if value.startswith(f"{item['domain_alias']} ")
        ),
        key=len,
        reverse=True,
    )
    if not candidates:
        raise ValueError(f"RESULT_POPULATION_ALIAS_MISSING:{value}")
    longest = len(candidates[0])
    aliases = [item for item in candidates if len(item) == longest]
    if len(aliases) != 1:
        raise ValueError(f"RESULT_POPULATION_ALIAS_AMBIGUOUS:{value}")
    alias = aliases[0]
    suffix = value[len(alias) + 1:]
    if suffix == "referenced node_index":
        association_kind = "node"
        qualifier = ""
    elif suffix.endswith(" element_index"):
        association_kind = "element"
        qualifier = suffix[:-len(" element_index")].strip()
    else:
        raise ValueError(f"RESULT_POPULATION_GRAMMAR_INVALID:{value}")
    binding = next(
        item for item in bindings if item["domain_alias"] == alias
    )
    if qualifier and qualifier.casefold() not in str(
        binding["material_name"]
    ).casefold():
        raise ValueError(f"RESULT_POPULATION_MATERIAL_MISMATCH:{value}")
    return alias, association_kind


def _operation(value: object) -> int | str:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if value == "fbs.post.MAT3DS.EFFECTIVE":
        return "MAT3DS.EFFECTIVE"
    raise ValueError(f"RESULT_OPERATION_NOT_AUDITED:{value}")


def build_xplt_request_from_intent(
    attempt: AttemptStore,
    intent_revision: dict[str, object],
    execution: dict[str, object],
) -> XpltRequest:
    contract = intent_revision["contract"]
    model_path = (attempt.root / "model-evidence.json").resolve(strict=True)
    model = load_json(model_path)
    source = Path(str(model["source"]["path"])).resolve(strict=True)
    if (
        source.stat().st_size != int(model["source"]["bytes"])
        or sha256_file(source) != model["source"]["sha256"]
    ):
        raise RuntimeError("MODEL_SOURCE_DRIFT_BEFORE_FBS_REQUEST")
    bindings = tuple(dict(item) for item in model["domain_bindings"])
    request_items = _resolved(contract, "result_requests")
    expected_times = tuple(
        float(item) for item in execution["expected_fbs_times"]
    )
    if (
        len(expected_times) < 2
        or expected_times[0] != 0.0
        or any(
            right <= left
            for left, right in zip(expected_times, expected_times[1:])
        )
    ):
        raise ValueError("FBS_EXPECTED_TIMES_INVALID")
    populations: dict[str, dict[str, object]] = {}
    fields: list[dict[str, object]] = []
    field_keys: set[tuple[str, str, int | str, str]] = set()
    association_by_alias: dict[str, set[str]] = {}
    for item in request_items:
        alias, kind = _population_binding(item["population"], bindings)
        association = str(item["accessor"])
        required_association = "nodeData" if kind == "node" else "elemData"
        if association != required_association:
            raise ValueError("RESULT_POPULATION_ASSOCIATION_MISMATCH")
        if item["states"] != f"all {len(expected_times)} FBS states":
            raise ValueError("RESULT_STATE_SCOPE_MISMATCH")
        operations = (
            tuple(item["components"])
            if "components" in item
            else (item["component"],)
        )
        populations[alias] = {
            "name": alias,
            "domain_aliases": [alias],
            "require_disjoint": True,
        }
        association_by_alias.setdefault(alias, set()).add(association)
        for requested in operations:
            operation = _operation(requested)
            key = (
                str(item["field"]), association, operation, alias
            )
            if key in field_keys:
                raise ValueError("RESULT_FIELD_REQUEST_DUPLICATE")
            field_keys.add(key)
            fields.append({
                "name": key[0],
                "association": association,
                "operation": operation,
                "population": alias,
            })
    loads = _resolved(contract, "loads")
    requested_checks = _resolved(contract, "kinematic_checks")
    kinematics: tuple[dict[str, object], ...] = ()
    if requested_checks:
        rigid_matches = [
            item for item in bindings
            if item["material_name"] == loads["rigid_body"]
        ]
        if len(rigid_matches) != 1:
            raise ValueError("RIGID_BODY_DOMAIN_BINDING_COUNT")
        rigid_alias = str(rigid_matches[0]["domain_alias"])
        displacement_operations = {
            item["operation"] for item in fields
            if item["name"] == "displacement"
            and item["association"] == "nodeData"
            and item["population"] == rigid_alias
        }
        if displacement_operations != {0, 1, 2, 6}:
            raise ValueError("RIGID_FULL_VECTOR_REQUEST_REQUIRED")
        end_time = float(_resolved(contract, "expected_run")["end_time"])
        final_vector = tuple(float(item) for item in loads["final_vector_mm"])
        final_magnitude = float(loads["final_magnitude_mm"])
        if (
            end_time <= 0.0
            or len(final_vector) != 3
            or not math.isclose(
                math.sqrt(sum(item * item for item in final_vector)),
                final_magnitude,
                rel_tol=0.0,
                abs_tol=float(execution["displacement_abs_tol_mm"]),
            )
        ):
            raise ValueError("RIGID_FINAL_VECTOR_INTENT_INVALID")
        kinematics = ({
            "field": "displacement",
            "population": rigid_alias,
            "expected_by_time": [
                {
                    "time": time,
                    "vector": [
                        component * time / end_time
                        for component in final_vector
                    ],
                    "magnitude": final_magnitude * time / end_time,
                }
                for time in expected_times
            ],
            "abs_tol": float(execution["displacement_abs_tol_mm"]),
        },)
    return XpltRequest.from_model_evidence(
        attempt.paths.solver / "solver.xplt",
        model_path,
        expected_times=expected_times,
        allow_zero_state=bool(execution["allow_zero_state"]),
        allow_unrequested_states=bool(
            execution["allow_unrequested_states"]
        ),
        time_abs_tol=float(execution["fbs_time_abs_tol"]),
        populations=tuple(populations[key] for key in sorted(populations)),
        fields=tuple(fields),
        initial_coordinates=(),
        kinematic_checks=kinematics,
    )
```

No raw FBS partition name enters this converter. `XpltRequest` carries the
model-evidence `domain_bindings`; Phase 1B `_alias_partitions` binds each
runtime partition one-to-one by material name, element count, referenced-node
count, and `feb-domain-connectivity-signature-v1`, then injects the
authoritative FEB element type and validates node cardinality. The symbolic
`MAT3DS.EFFECTIVE` is deliberately resolved inside the locked FBS worker,
where the official runtime returns integer `6`; integer `8` is `P2` and is
never substituted. `initial_coordinates=()` deliberately requests no
unbounded coordinate list: all-node identity is the
`feb-node-coordinate-f32-v1` SHA loaded by
`XpltRequest.from_model_evidence`; optional coordinate spot checks remain
bounded.

- [ ] **Step 26: Run XPLT-request GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_xplt_request_binding.py `
  apps/febio_cae_harness/tests/unit/test_fbs_worker.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/verify.py `
  apps/febio_cae_harness/tests/unit/test_xplt_request_binding.py
git commit -m "feat: bind approved intent to FBS result requests"
```

Expected: both suites pass, including empty partition names and official
operation `6`.

- [ ] **Step 27: Write the solve and verify production RED**

Create `tests/unit/test_production_solve_verify.py`:

```python
import json

from febio_cae_harness.commands.solve import handle_solve
from febio_cae_harness.commands.verify import handle_verify


def test_solve_calls_owned_runner_log_fbs_then_completion_once(
    preflight_case,
    monkeypatch,
    accepted_process,
    accepted_log,
    accepted_fbs,
    accepted_completion,
) -> None:
    calls = []
    completion_calls = []
    monkeypatch.setattr(
        "febio_cae_harness.commands.solve.run_febio",
        lambda request: calls.append("run_febio") or accepted_process,
    )
    monkeypatch.setattr(
        "febio_cae_harness.commands.solve.parse_febio_log",
        lambda path, expected: calls.append("parse_log") or accepted_log,
    )
    monkeypatch.setattr(
        "febio_cae_harness.commands.solve.inspect_xplt",
        lambda request, runtime: calls.append("inspect_xplt") or accepted_fbs,
    )
    monkeypatch.setattr(
        "febio_cae_harness.commands.solve.decide_completion",
        lambda *args: (
            calls.append("decide_completion")
            or completion_calls.append(args)
            or accepted_completion
        ),
    )
    result = handle_solve(preflight_case, {})
    assert calls == [
        "run_febio", "parse_log", "inspect_xplt", "decide_completion",
    ]
    assert len(completion_calls) == 1
    assert completion_calls[0][-1] is not None
    assert result.case_state == "SOLVED"
    assert [item.kind for item in result.evidence] == [
        "process-evidence", "log-verification",
        "fbs-verification", "completion-decision",
    ]
    assert {item["role"] for item in result.artifacts} == {
        "attempt-solver-log", "attempt-solver-xplt",
    }


def test_verify_rebuilds_same_request_and_runs_result_gate_once(
    solved_case,
    monkeypatch,
    accepted_result,
) -> None:
    calls = []
    monkeypatch.setattr(
        "febio_cae_harness.commands.verify.validate_result",
        lambda case, inputs: calls.append(inputs) or accepted_result,
    )
    result = handle_verify(solved_case, {})
    assert len(calls) == 1
    inputs = calls[0]
    saved_fbs = json.loads((
        solved_case.case_dir
        / "90_Temporary/attempts"
        / inputs.attempt.attempt_id
        / "fbs-verification.json"
    ).read_text(encoding="utf-8"))
    assert inputs.fbs_request.request_sha256 == saved_fbs["request_id"]
    assert inputs.require_initial_coordinate_bit_equality is True
    assert inputs.require_rigid_full_vector is True
    assert inputs.rigid_abs_tol == 1.0e-6
    assert result.case_state == "RESULT_VERIFIED"
    assert [item.kind for item in result.evidence] == [
        "fbs-verification", "result-validation",
    ]
    assert result.artifacts[0]["role"] == "attempt-solver-xplt"
```

Add tests that a rejected LOG still calls completion with a typed
`FailureEvidence`, a rejected/missing FBS result reaches
`RESULT_INCOMPLETE`, a request-id mismatch fails before `validate_result`, and
neither handler accepts a path/hash/state from the prior CLI stdout. Mutate
one requested field, population alias, expected-state index/time, or
kinematic-check coverage item in saved FBS evidence; each mutation must be
rejected inside `validate_result` through the passed exact `XpltRequest`.
For the missing-LOG case, make `parse_febio_log` raise `FileNotFoundError` and
assert the handler still calls completion once with rejected typed
`LogEvidence`, `FailureClass.RESOURCE_OR_TOOL_ERROR`, and no synthesized LOG
file. For XPLT-request construction failure, assert completion receives
`fbs_request=None`, emits `FBS_REQUEST_UNAVAILABLE`, and cannot return
`SOLVED`.

- [ ] **Step 28: Run the solve/verify RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_solve_verify.py -q
```

Expected: collection fails because `handle_solve` and `handle_verify` are not
defined.

- [ ] **Step 29: Implement the owned solve, LOG, FBS, and completion pipeline**

In `commands/solve.py`:

```python
from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
from pathlib import Path

from febio_cae_harness.commands.common import (
    load_json,
    open_bound_attempt,
)
from febio_cae_harness.commands.model import fbs_runtime_from_install_manifest
from febio_cae_harness.commands.verify import build_xplt_request_from_intent
from febio_cae_harness.completion import decide_completion
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.failure import (
    FailureClass,
    FailureEvidence,
    classify_log_failure,
)
from febio_cae_harness.fbs_client import FbsInspectionError, inspect_xplt
from febio_cae_harness.fbs_protocol import XpltEvidence
from febio_cae_harness.febio_command import FebioRunRequest
from febio_cae_harness.febio_runner import (
    run_febio,
    solver_artifact_records,
)
from febio_cae_harness.hashing import canonical_json_bytes, sha256_file
from febio_cae_harness.jsonio import atomic_create_artifact
from febio_cae_harness.log_parser import (
    LogEvidence,
    LogExpectation,
    parse_febio_log,
)
from febio_cae_harness.response import CommandResult


def _record_payload(record) -> dict[str, object]:
    value = record.to_record()
    return {"kind": value.kind, **value.data}


def _rejected_log_and_failure(
    log_path: Path,
    process,
    error: BaseException,
    input_sha256: str,
    contract_sha256: str,
) -> tuple[LogEvidence, FailureEvidence]:
    code = f"LOG_UNAVAILABLE:{type(error).__name__}"
    observed = process.artifacts_after.get("log", {})
    observed_sha256 = str(
        observed.get("sha256")
        or hashlib.sha256(b"").hexdigest().upper()
    )
    observed_bytes = int(observed.get("bytes") or 0)
    log = LogEvidence(
        kind="log-verification",
        status="rejected",
        path=str(log_path.resolve()),
        bytes=observed_bytes,
        sha256=observed_sha256,
        solver_version=None,
        files_used={},
        beginning_steps=(),
        converged_times=(),
        converged_line_indices=(),
        completed_steps=None,
        normal_termination=False,
        normal_termination_line_index=None,
        warning_lines=(),
        error_lines=(code,),
        recovered_warnings=(),
        unresolved_warnings=(),
        elapsed_seconds=None,
        peak_memory_mib=None,
        blocking_reasons=(code,),
    )
    core = {
        "failure_class": FailureClass.RESOURCE_OR_TOOL_ERROR.value,
        "phase": "EVIDENCE",
        "normalized_fatal_lines": (code,),
        "last_converged_time": None,
        "failing_step": None,
        "failing_time": None,
        "element_ids": (),
        "node_ids": (),
        "input_sha256": input_sha256,
        "contract_sha256": contract_sha256,
        "automatic_retry_allowed": False,
    }
    failure = FailureEvidence(
        failure_class=FailureClass.RESOURCE_OR_TOOL_ERROR,
        phase="EVIDENCE",
        normalized_fatal_lines=(code,),
        last_converged_time=None,
        failing_step=None,
        failing_time=None,
        element_ids=(),
        node_ids=(),
        input_sha256=input_sha256,
        contract_sha256=contract_sha256,
        automatic_retry_allowed=False,
        fingerprint_sha256=hashlib.sha256(
            canonical_json_bytes(core)
        ).hexdigest().upper(),
    )
    return log, failure


def handle_solve(case, request):
    attempt = open_bound_attempt(case)
    execution = load_json(attempt.root / "normalized-config.json")
    intent_revision = load_json(attempt.root / "intent-revision.json")
    model = load_json(attempt.root / "model-evidence.json")
    install = load_json(
        Path(os.environ["FEBIO_CAE_INSTALL_MANIFEST"])
    )
    solver = execution["expected_solver"]
    run_request = FebioRunRequest(
        executable=Path(solver["path"]).resolve(strict=True),
        expected_solver_sha256=str(solver["sha256"]),
        input_feb=(attempt.paths.solver / "input.feb").resolve(strict=True),
        expected_input_sha256=str(model["source"]["sha256"]),
        working_directory=attempt.paths.solver.resolve(strict=True),
        output_log=(attempt.paths.solver / "solver.log").resolve(),
        output_xplt=(attempt.paths.solver / "solver.xplt").resolve(),
        timeout_seconds=float(execution["timeout_seconds"]),
        cancel_grace_seconds=float(execution["cancel_grace_seconds"]),
        process_tree_working_set_limit_mib=int(
            execution["maximum_process_tree_working_set_mib"]
        ),
        inherited_environment_names=(
            "PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
        ),
    )
    process = run_febio(run_request)
    log_path = attempt.paths.solver / "solver.log"
    input_sha256 = str(model["source"]["sha256"])
    contract_sha256 = str(intent_revision["contract_sha256"])
    try:
        log = parse_febio_log(
            log_path,
            LogExpectation(
                expected_input_basename="input.feb",
                expected_log_basename="solver.log",
                expected_plot_basename="solver.xplt",
                expected_solver_version=str(solver["log_version"]),
                expected_times=tuple(
                    float(item)
                    for item in execution["expected_log_times"]
                ),
                time_abs_tol=float(execution["log_time_abs_tol"]),
            ),
        )
        log_failure = classify_log_failure(
            log, input_sha256, contract_sha256
        )
    except (OSError, UnicodeError, ValueError, OverflowError) as error:
        log, log_failure = _rejected_log_and_failure(
            log_path,
            process,
            error,
            input_sha256,
            contract_sha256,
        )
    xplt_request = None
    try:
        xplt_request = build_xplt_request_from_intent(
            attempt, intent_revision, execution
        )
        fbs = inspect_xplt(
            xplt_request,
            fbs_runtime_from_install_manifest(install),
        )
    except (FbsInspectionError, FileNotFoundError, ValueError) as error:
        fbs = XpltEvidence({
            "kind": "fbs-verification",
            "status": "rejected",
            "error_code": type(error).__name__,
            "detail": str(error),
        })
    process_payload = _record_payload(process)
    log_payload = log.to_dict()
    fbs_payload = fbs.to_dict()
    failure_payload = None
    if log_failure is not None:
        failure_payload = asdict(log_failure)
        failure_payload["failure_class"] = log_failure.failure_class.value
        atomic_create_artifact(
            attempt.root / "failure-evidence.json",
            canonical_json_bytes(failure_payload) + b"\n",
        )
    completion = decide_completion(
        case,
        attempt,
        process_payload,
        log_payload,
        fbs_payload,
        failure_payload,
        xplt_request,
    )
    accepted = completion.case_state == "SOLVED"
    attempt.finalize({
        "status": "COMPLETED",
        "completed_stages": ["solve"] if accepted else [],
        "completion_decision_sha256": (
            sha256_file(attempt.root / "completion-decision.json")
        ),
    })
    artifacts = (
        solver_artifact_records(process)
        if (
            process.artifacts_after["log"].get("exists") is True
            and process.artifacts_after["xplt"].get("exists") is True
        )
        else ()
    )
    if accepted:
        exit_code = ExitCode.SUCCESS
        status = Status.SUCCESS
    elif completion.case_state == "SOLVE_FAILED":
        exit_code = ExitCode.SOLVE_FAILED
        status = Status.ERROR
    else:
        exit_code = ExitCode.RESULT_INCOMPLETE
        status = Status.ERROR
    return CommandResult(
        exit_code=exit_code,
        status=status,
        case_state=completion.case_state,
        allowed_next_actions=(
            ("verify", "report", "cancel")
            if accepted
            else ("diagnose", "retry", "cancel")
        ),
        artifacts=tuple(artifacts),
        evidence=(
            process.to_record(),
            log.to_record(),
            fbs.to_record(),
            completion.to_record(),
        ),
    )
```

The local conversion above turns `FailureClass` into its stable string value
before `decide_completion`; the solve handler performs no permanent promotion.

- [ ] **Step 30: Implement authoritative verify and result validation**

Complete `commands/verify.py`:

```python
from febio_cae_harness.commands.common import (
    artifact,
    load_json,
    open_bound_attempt,
)
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.response import CommandResult, EvidenceRecord
from febio_cae_harness.result_validation import (
    ResultValidationInputs,
    validate_result,
)


def handle_verify(case, request):
    attempt = open_bound_attempt(case)
    execution = load_json(attempt.root / "normalized-config.json")
    intent_revision = load_json(attempt.root / "intent-revision.json")
    rebuilt = build_xplt_request_from_intent(
        attempt, intent_revision, execution
    )
    fbs_path = (attempt.root / "fbs-verification.json").resolve(strict=True)
    fbs = load_json(fbs_path)
    if (
        fbs.get("status") != "accepted"
        or fbs.get("request_id") != rebuilt.request_sha256
        or fbs.get("model_evidence_sha256") != rebuilt.model_evidence_sha256
        or fbs.get("xplt", {}).get("sha256") != rebuilt.xplt_sha256
    ):
        raise RuntimeError("FBS_REQUEST_BINDING_DRIFT")
    result = validate_result(case, ResultValidationInputs(
        attempt=attempt,
        model_evidence_path=attempt.root / "model-evidence.json",
        fbs_request=rebuilt,
        require_initial_coordinate_bit_equality=True,
        require_rigid_full_vector=bool(rebuilt.kinematic_checks),
        rigid_abs_tol=float(execution["displacement_abs_tol_mm"]),
    ))
    accepted = result.status == "accepted"
    return CommandResult(
        exit_code=(
            ExitCode.SUCCESS if accepted else ExitCode.RESULT_INCOMPLETE
        ),
        status=Status.SUCCESS if accepted else Status.ERROR,
        case_state=result.case_state,
        allowed_next_actions=(
            ("report", "cancel")
            if accepted
            else ("diagnose", "retry", "cancel")
        ),
        artifacts=(
            artifact(
                "attempt-solver-xplt",
                attempt.paths.solver / "solver.xplt",
            ),
        ),
        evidence=(
            EvidenceRecord("fbs-verification", fbs),
            result.to_record(),
        ),
    )
```

Phase 1B `ResultValidationInputs` already requires
`fbs_request: XpltRequest`. Its `_request_coverage_blockers` compares
`request_id`, the exact requested field/association/operation-source/population
set, the exact population/domain-alias mapping, every expected state index,
and the exact kinematic field/population set before it can accept a result.
Keep the mutation tests above in both
`test_production_solve_verify.py` and `test_result_validation.py`; this makes
the result gate, not only the CLI adapter, own the intent-to-FBS binding.

The handler rebuilds the semantic request from immutable attempt intent/model
records, compares all three request/model/XPLT bindings, and only then calls
`validate_result`. It does not evaluate all nonempty FBS associations:
Phase 1B validates only the intent-selected `nodeData` or `elemData`
collection, because the official runtime may populate the other collections
too.

- [ ] **Step 31: Run solve/verify GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_solve_verify.py `
  apps/febio_cae_harness/tests/unit/test_completion.py `
  apps/febio_cae_harness/tests/unit/test_result_validation.py `
  apps/febio_cae_harness/tests/unit/test_log_parser.py `
  apps/febio_cae_harness/tests/integration/test_fbs_client.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/solve.py `
  apps/febio_cae_harness/src/febio_cae_harness/commands/verify.py `
  apps/febio_cae_harness/tests/unit/test_production_solve_verify.py
git commit -m "feat: wire owned solve and intent-specific verification"
```

Expected: all selected tests pass; accepted solve returns exactly the four
evidence kinds and two attempt artifact roles, and verify reaches
`RESULT_VERIFIED`.

- [ ] **Step 32: Write the report/promotion production RED**

Create `tests/unit/test_production_report.py`:

```python
from febio_cae_harness.commands.report import handle_report
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.schema import validate_schema


def test_report_returns_bound_promoted_results_and_two_reports(
    result_verified_case,
) -> None:
    result = handle_report(result_verified_case, {})
    assert result.case_state == "REPORTED"
    assert [item.kind for item in result.evidence] == [
        "completion-decision", "report-promotion",
    ]
    promotion = result.evidence[1].data
    validate_schema("report-promotion", promotion)
    assert promotion["promotions_overwritten_count"] == 0
    assert [item["role"] for item in promotion["promoted_results"]] == [
        "xplt", "solver-log",
    ]
    assert [item["role"] for item in promotion["reports"]] == [
        "audit-report-json", "audit-report-html",
    ]
    assert [item["role"] for item in result.artifacts] == [
        "xplt", "solver-log", "audit-report-json", "audit-report-html",
    ]
    for item in result.artifacts:
        assert sha256_file(Path(item["path"])) == item["sha256"]
```

Import `Path`. Add mutation tests for each of the three decision/event pointer
hashes, each promoted source/destination hash, each report hash, artifact
order, an extra schema field, and `promotions_overwritten_count=1`. Also mutate
each stored attempt tool fingerprint, the bound normalized solver identity,
and each corresponding current install-manifest identity; the report must
reject any stored/current mismatch. Each case must fail before
`REPORT_PROMOTED` and preserve existing permanent artifacts.

- [ ] **Step 33: Run the report-handler RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_report.py -q
```

Expected: collection fails because `handle_report` is not defined.

- [ ] **Step 34: Implement report generation, result promotion, and artifact normalization**

Complete `commands/report.py`:

```python
from __future__ import annotations

from febio_cae_harness.commands.common import (
    load_json,
    open_bound_attempt,
)
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.report import (
    build_report_data,
    promote_report,
    render_report,
)
from febio_cae_harness.response import CommandResult, EvidenceRecord


def handle_report(case, request):
    attempt = open_bound_attempt(case)
    completion_path = attempt.root / "completion-decision.json"
    completion = load_json(completion_path)
    if (
        completion.get("kind") != "completion-decision"
        or completion.get("status") != "accepted"
        or completion.get("case_state") != "SOLVED"
        or completion.get("attempt_id") != attempt.attempt_id
    ):
        raise RuntimeError("REPORT_ACCEPTED_COMPLETION_REQUIRED")
    data = build_report_data(case, attempt)
    bundle = render_report(data)
    (
        json_ref,
        html_ref,
        reused,
        promotion,
        public_artifacts,
    ) = promote_report(
        case,
        attempt,
        bundle,
    )
    return CommandResult(
        exit_code=ExitCode.SUCCESS,
        status=Status.SUCCESS,
        case_state="REPORTED",
        allowed_next_actions=("status",),
        artifacts=public_artifacts,
        evidence=(
            EvidenceRecord("completion-decision", completion),
            EvidenceRecord("report-promotion", promotion),
        ),
    )
```

This returns four standard `CommandResult.artifacts` records. The first two
are the create-new promoted `xplt` and `solver-log`; the last two are exactly
`audit-report-json` and `audit-report-html`. The `report-promotion` evidence
retains the richer source/destination audit records. `promote_report` validates
the closed promotion payload and rehashes all upstream, promoted-result, and
report pointers before it appends `REPORT_PROMOTED`; the command handler does
no fallible validation after that state transition.

- [ ] **Step 35: Run report GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_report.py `
  apps/febio_cae_harness/tests/unit/test_report.py `
  apps/febio_cae_harness/tests/integration/test_report_promotion.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/report.py `
  apps/febio_cae_harness/tests/unit/test_production_report.py
git commit -m "feat: wire verified result and report promotion"
```

Expected: all tests pass, the promotion count is zero, and all seven pointer
or artifact files rehash exactly.

- [ ] **Step 36: Write case bootstrap, status, and cancel RED tests**

Create the case/control portion of `tests/unit/test_production_control.py`:

```python
from febio_cae_harness.commands.case import (
    handle_case_adopt,
    handle_case_init,
    handle_case_status,
)
from febio_cae_harness.commands.cancel import handle_cancel


def test_case_init_and_status_have_replayable_closed_evidence(tmp_path) -> None:
    case_dir = tmp_path / "case"
    created = handle_case_init(None, {
        "case_dir": case_dir,
        "analysis_id": "analysis-001",
    })
    assert created.case_state == "CASE_CREATED"
    assert created.evidence[0].kind == "case-initialized"
    case = CaseStore.open(case_dir)
    status = handle_case_status(case, {})
    payload = status.evidence[0].data
    assert set(payload) == {
        "event_chain_valid", "event_count", "last_event_sha256",
        "manifest_projection_current", "active_run_lease",
        "active_process_count",
    }
    assert payload["event_chain_valid"] is True
    assert payload["manifest_projection_current"] is True
    assert payload["active_run_lease"] is None
    assert payload["active_process_count"] == 0


def test_cancel_never_discloses_owner_token(running_case, owner_token) -> None:
    result = handle_cancel(running_case, {
        "attempt_id": running_case.attempt_id,
        "owner_token": owner_token,
    })
    assert result.evidence[0].kind == "cancel-request"
    assert owner_token not in json.dumps(result.to_payload())
```

Import `json` and `CaseStore`. Add an adoption test that supplies a wrong
`analysis_id` and proves the legacy manifest and full inventory are unchanged.
On the success path, assert `case-adoption` contains the absolute persisted
`preexisting_inventory_artifact_path` and its rehashed
`preexisting_inventory_artifact_sha256`, matching the Phase 1A
`LEGACY_ADOPTED` event rather than merely echoing the caller's digest.
Add status tests for a flipped event byte, torn final JSONL line, and stale
manifest; record all bytes before the call and assert exact byte equality
after exit `70`.

- [ ] **Step 37: Run the case/control RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_control.py -q
```

Expected: collection fails because `handle_case_status` is not defined.

- [ ] **Step 38: Implement case init/adopt/status and retain token-bound cancel**

In `commands/case.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import psutil

from febio_cae_harness.attempt_store import current_run_lease
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.events import read_event_log
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.response import CommandResult, EvidenceRecord


def handle_case_init(unused, request):
    case = CaseStore.initialize(
        Path(request["case_dir"]), str(request["analysis_id"])
    )
    return CommandResult(
        ExitCode.SUCCESS,
        Status.SUCCESS,
        "CASE_CREATED",
        evidence=(EvidenceRecord("case-initialized", {
            "analysis_id": str(request["analysis_id"]),
            "case_dir": str(case.case_dir),
        }),),
    )


def handle_case_adopt(unused, request):
    case_dir = Path(request["case_dir"]).resolve(strict=True)
    legacy_path = case_dir / "CASE_MANIFEST.json"
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    if legacy.get("analysis_id") != request["analysis_id"]:
        raise ValueError("LEGACY_ANALYSIS_ID_MISMATCH")
    case = CaseStore.adopt_existing(
        case_dir,
        expected_manifest_sha256=str(
            request["expected_legacy_manifest_sha256"]
        ),
        preexisting_inventory_json=Path(
            request["preexisting_inventory_json"]
        ),
        expected_preexisting_inventory_sha256=str(
            request["expected_preexisting_inventory_sha256"]
        ),
    )
    inventory_sha256 = str(
        request["expected_preexisting_inventory_sha256"]
    )
    inventory_artifact = (
        case.case_dir
        / "05_Verification/harness/preexisting-inventories"
        / f"preexisting-inventory-{inventory_sha256}.json"
    ).resolve(strict=True)
    if sha256_file(inventory_artifact) != inventory_sha256:
        raise RuntimeError("PERSISTED_PREEXISTING_INVENTORY_DRIFT")
    return CommandResult(
        ExitCode.SUCCESS,
        Status.SUCCESS,
        str(case.replay(repair_manifest=False)["harness"]["case_state"]),
        evidence=(EvidenceRecord("case-adoption", {
            "analysis_id": str(request["analysis_id"]),
            "case_dir": str(case.case_dir),
            "legacy_manifest_sha256": str(
                request["expected_legacy_manifest_sha256"]
            ),
            "preexisting_inventory_artifact_path": str(inventory_artifact),
            "preexisting_inventory_artifact_sha256": sha256_file(
                inventory_artifact
            ),
        }),),
    )


def _active_process_count(lease) -> int:
    if lease is None or not lease.binding_path.exists():
        return 0
    binding = json.loads(lease.binding_path.read_text(encoding="utf-8"))
    pid = int(binding["pid"])
    try:
        process = psutil.Process(pid)
        return 1 + len(process.children(recursive=True))
    except psutil.Error:
        return 0


def handle_case_status(case, request):
    try:
        events = read_event_log(case.event_log)
        projection = case.replay(repair_manifest=False)
        manifest = json.loads(
            case.manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return CommandResult(
            ExitCode.INTERNAL_ERROR,
            Status.ERROR,
            None,
            error={
                "code": "CASE_EVENT_OR_MANIFEST_CORRUPT",
                "message": "authoritative case replay failed closed",
            },
        )
    if manifest != projection:
        return CommandResult(
            ExitCode.INTERNAL_ERROR,
            Status.ERROR,
            str(projection["harness"]["case_state"]),
            error={
                "code": "CASE_MANIFEST_PROJECTION_STALE",
                "message": "status never repairs a stale manifest",
            },
        )
    lease = current_run_lease(case)
    active = None
    if lease is not None:
        binding = (
            None
            if not lease.binding_path.exists()
            else json.loads(lease.binding_path.read_text(encoding="utf-8"))
        )
        active = {
            "attempt_id": lease.attempt_id,
            "started_at": lease.started_at,
            "pid": None if binding is None else int(binding["pid"]),
            "job_name": (
                None if binding is None else str(binding["job_name"])
            ),
            "cancel_requested": lease.cancel_request_path.exists(),
        }
    payload = {
        "event_chain_valid": True,
        "event_count": len(events),
        "last_event_sha256": events[-1].event_sha256,
        "manifest_projection_current": True,
        "active_run_lease": active,
        "active_process_count": _active_process_count(lease),
    }
    return CommandResult(
        ExitCode.SUCCESS,
        Status.SUCCESS,
        str(projection["harness"]["case_state"]),
        evidence=(EvidenceRecord("case-status", payload),),
    )
```

Keep `commands/cancel.py::handle_cancel` from Task 5. Its evidence contains
only `role`, `path`, `bytes`, and `sha256`; neither stdout nor stderr may
contain `owner_token`.

- [ ] **Step 39: Run case/control GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_control.py `
  apps/febio_cae_harness/tests/integration/test_case_store.py `
  apps/febio_cae_harness/tests/integration/test_cancel_command.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/case.py `
  apps/febio_cae_harness/src/febio_cae_harness/commands/cancel.py `
  apps/febio_cae_harness/tests/unit/test_production_control.py
git commit -m "feat: wire case status and owned cancellation"
```

Expected: all tests pass; corrupt status cases preserve all bytes and the
cancellation owner token never appears in a response.

- [ ] **Step 40: Write diagnose/retry RED tests**

Append to `tests/unit/test_production_control.py`:

```python
from febio_cae_harness.commands.diagnose import (
    handle_diagnose,
    handle_retry,
)


def test_bottomframe_zero_budget_retry_is_a_no_write_policy_stop(
    failed_bottomframe_case,
) -> None:
    diagnosed = handle_diagnose(failed_bottomframe_case, {})
    assert diagnosed.evidence[0].kind == "diagnosis"
    before = inventory(failed_bottomframe_case.case_dir)
    rejected = handle_retry(failed_bottomframe_case, {})
    assert int(rejected.exit_code) == 30
    assert rejected.error["code"] == "RETRY_NOT_APPROVED_BY_INTENT"
    assert inventory(failed_bottomframe_case.case_dir) == before


def test_approved_diagnostic_retry_creates_new_preflight_attempt(
    failed_diagnostic_case,
) -> None:
    handle_diagnose(failed_diagnostic_case, {})
    result = handle_retry(failed_diagnostic_case, {})
    assert result.case_state == "PREFLIGHT_PASSED"
    assert [item.kind for item in result.evidence] == [
        "change-proposal", "retry-reservation", "preflight",
    ]
    attempts = sorted((
        failed_diagnostic_case.case_dir / "90_Temporary/attempts"
    ).iterdir())
    assert len(attempts) == 2
    assert attempts[0].joinpath("solver/input.feb").read_bytes() != (
        attempts[1].joinpath("solver/input.feb").read_bytes()
    )
    assert (
        attempts[0].joinpath("model-evidence.json").exists()
        and attempts[1].joinpath("model-evidence.json").exists()
    )
```

Define `inventory` in the test as a relative-path to `(bytes, sha256)` mapping.
Also prove duplicate fingerprint/change pairs, exhausted budgets,
intent-sensitive mesh changes, ROI/load-path changes, and
`eligible_for_promotion=true` create no retry attempt. For an otherwise valid
retry fixture, independently drift the current Analysis Intent authority and
each exact installed fingerprint (harness version, build provenance, install
manifest, wheel, reviewed source commit, solver/version, FBS tree/profile, and
policy/version); every case must fail before `PREFLIGHT_PASSED`.

- [ ] **Step 41: Run diagnose/retry RED**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_control.py -q
```

Expected: collection fails because `handle_diagnose` and `handle_retry` are
not defined.

- [ ] **Step 42: Implement authoritative diagnosis loading**

In `commands/diagnose.py`, define the exact adapter types and handler:

```python
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from febio_cae_harness.commands.common import (
    load_json,
    open_bound_attempt,
    success,
)
from febio_cae_harness.diagnosis import (
    ChangeProposal,
    IntentImpact,
    diagnose_failure,
)
from febio_cae_harness.hashing import canonical_json_bytes, sha256_file
from febio_cae_harness.jsonio import atomic_create_artifact
from febio_cae_harness.response import EvidenceRecord


@dataclass(frozen=True)
class FailureView:
    failure_class: str
    fingerprint: str
    affected_entities: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    step_id: str | None


@dataclass(frozen=True)
class EvidenceBundle:
    failure: FailureView
    control_values: dict[str, object]


def _max_refs(path):
    root = ET.parse(path).getroot()
    values = [
        item for item in (
            root.find("./Control/solver/max_refs"),
            *root.findall("./Step/step/Control/solver/max_refs"),
        )
        if item is not None and item.text is not None
    ]
    if len(values) != 1:
        raise RuntimeError("DIAGNOSIS_MAX_REFS_COUNT")
    return int(values[0].text.strip())


def _approved_intent(attempt):
    revision = load_json(attempt.root / "intent-revision.json")
    return SimpleNamespace(contract=revision["contract"])


def handle_diagnose(case, request):
    attempt = open_bound_attempt(case)
    failure_path = attempt.root / "failure-evidence.json"
    if not failure_path.exists():
        completion_path = attempt.root / "completion-decision.json"
        completion = load_json(completion_path)
        view = FailureView(
            failure_class=str(completion["failure_class"]),
            fingerprint=sha256_file(completion_path),
            affected_entities=(),
            evidence_ids=(sha256_file(completion_path),),
            step_id=None,
        )
        if not view.failure_class:
            raise RuntimeError("COMPLETION_FAILURE_CLASS_MISSING")
        payload = {
            "schema_version": 1,
            "proposals": [],
            "required_action": "review",
        }
        atomic_create_artifact(
            attempt.root / "diagnosis.json",
            canonical_json_bytes(payload) + b"\n",
        )
        return success(case, EvidenceRecord("diagnosis", payload))
    failure = load_json(failure_path)
    log_path = attempt.root / "log-verification.json"
    view = FailureView(
        failure_class=str(failure["failure_class"]),
        fingerprint=str(failure["fingerprint_sha256"]),
        affected_entities=tuple(
            f"element:{item}" for item in failure["element_ids"]
        ) + tuple(f"node:{item}" for item in failure["node_ids"]),
        evidence_ids=(sha256_file(log_path),),
        step_id=(
            None if failure["failing_step"] is None
            else str(failure["failing_step"])
        ),
    )
    model = load_json(attempt.root / "model-evidence.json")
    bundle = EvidenceBundle(
        failure=view,
        control_values={
            "solid.max_refs": _max_refs(Path(model["source"]["path"])),
        },
    )
    diagnosis = diagnose_failure(bundle, _approved_intent(attempt))
    payload = diagnosis.to_payload()
    atomic_create_artifact(
        attempt.root / "diagnosis.json",
        canonical_json_bytes(payload) + b"\n",
    )
    return success(case, EvidenceRecord("diagnosis", payload))
```

If `failure-evidence.json` is absent because the failure was only
process/resource or FBS evidence, the branch above persists a review-only
diagnosis from `completion-decision.failure_class` and its completion hash;
it never fabricates an automatic proposal.

- [ ] **Step 43: Implement policy-limited retry reservation and new-attempt preflight**

Add these exact reconstruction and retry entry points to
`commands/diagnose.py`:

```python
from datetime import UTC, datetime
import os
from uuid import uuid4

from febio_cae_harness.attempt_store import AttemptPaths, AttemptStore
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.commands.common import state_of
from febio_cae_harness.commands.model import (
    fbs_runtime_from_install_manifest,
)
from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.events import read_event_log
from febio_cae_harness.execution_profile import (
    expected_installed_tool_fingerprints,
    resolve_install_pointer,
    validate_current_intent_authority,
)
from febio_cae_harness.fbs_client import verify_runtime
from febio_cae_harness.hashing import sha256_bytes
from febio_cae_harness.jsonio import ArtifactRef
from febio_cae_harness.model_adoption import stage_solver_input
from febio_cae_harness.model_evidence import create_model_evidence
from febio_cae_harness.numerical_patch import (
    apply_numerical_patch,
    load_diagnostic_policy,
)
from febio_cae_harness.preflight import (
    ExpectedArtifact,
    PreflightDecision,
)
from febio_cae_harness.response import CommandResult
from febio_cae_harness.retry_ledger import (
    RetryLedger,
    RetryReservation,
)


def _proposal(value) -> ChangeProposal:
    return ChangeProposal(
        hypothesis=str(value["hypothesis"]),
        evidence=tuple(value["evidence"]),
        selector_id=value["selector_id"],
        step_id=value["step_id"],
        before=value["before"],
        after=value["after"],
        affected_entities=tuple(value["affected_entities"]),
        roi_impact=str(value["roi_impact"]),
        load_path_impact=str(value["load_path_impact"]),
        expected_improvement=str(value["expected_improvement"]),
        side_effects=tuple(value["side_effects"]),
        verification=tuple(value["verification"]),
        intent_impact=IntentImpact(value["intent_impact"]),
        automatic_execution_eligible=bool(
            value["automatic_execution_eligible"]
        ),
        rollback=str(value["rollback"]),
        failure_fingerprint=str(value["failure_fingerprint"]),
        retry_budget=int(value["retry_budget"]),
        purpose=str(value["purpose"]),
        eligible_for_promotion=bool(value["eligible_for_promotion"]),
    )


def handle_retry(case, request):
    prior = open_bound_attempt(case)
    diagnosis = load_json(prior.root / "diagnosis.json")
    automatic = [
        _proposal(item)
        for item in diagnosis["proposals"]
        if item["automatic_execution_eligible"] is True
    ]
    execution = load_json(prior.root / "normalized-config.json")
    if (
        int(execution["retry_budget"]) == 0
        or len(automatic) != 1
        or automatic[0].retry_budget == 0
    ):
        return CommandResult(
            ExitCode.INVALID_STATE_OR_POLICY,
            Status.ERROR,
            state_of(case),
            allowed_next_actions=("intent draft", "status", "cancel"),
            error={
                "code": "RETRY_NOT_APPROVED_BY_INTENT",
                "message": "no single automatic diagnostic retry is approved",
            },
        )
    proposal = automatic[0]
    reservation = RetryLedger(
        case,
        maximum_attempts=int(execution["retry_budget"]),
    ).reserve(proposal)
    policy = load_diagnostic_policy()
    intent = _approved_intent(prior)
    patch = apply_numerical_patch(
        prior,
        prior.paths.solver / "input.feb",
        proposal,
        intent,
        policy,
    )
    retry = create_retry_attempt(
        case,
        prior,
        reservation,
        patch.artifact,
    )
    decision = preflight_retry_attempt(case, retry, prior, reservation)
    return success(
        case,
        EvidenceRecord("change-proposal", proposal.to_payload()),
        EvidenceRecord("retry-reservation", {
            "ordinal": reservation.ordinal,
            "key": reservation.key,
            "event_sha256": reservation.event_sha256,
            "attempt_id": retry.attempt_id,
        }),
        EvidenceRecord("preflight", {
            "status": decision.status,
            "attempt_id": decision.attempt_id,
            "checked_roles": list(decision.checked_roles),
            "evidence": list(decision.evidence),
        }),
    )
```

Implement the two private failure-state adapters in the same module. They
copy the literal create-new layout and preflight checks because the public
Phase 1B entry points intentionally accept only `MODEL_BUILT`:

```python
def _reservation_event(
    case: CaseStore,
    reservation: RetryReservation,
):
    matches = [
        event
        for event in read_event_log(case.event_log)
        if (
            event.event_type == "RETRY_RESERVED"
            and event.payload.get("retry_key") == reservation.key
            and event.payload.get("ordinal") == reservation.ordinal
        )
    ]
    if len(matches) != 1:
        raise RuntimeError("RETRY_RESERVATION_EVENT_COUNT")
    event = matches[0]
    if event.event_sha256 != reservation.event_sha256:
        raise RuntimeError("RETRY_RESERVATION_EVENT_HASH_DRIFT")
    return event


def _retry_snapshot(path: Path) -> dict[str, object]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise RuntimeError(f"RETRY_SNAPSHOT_NOT_OBJECT:{path.name}")
    return value


def create_retry_attempt(
    case: CaseStore,
    prior: AttemptStore,
    reservation: RetryReservation,
    patched: ArtifactRef,
) -> AttemptStore:
    with case.locked() as transaction:
        projection = transaction.replay()
        if projection["harness"]["case_state"] not in {
            CaseState.SOLVE_FAILED.value,
            CaseState.RESULT_INCOMPLETE.value,
        }:
            raise RuntimeError("RETRY_ATTEMPT_REQUIRES_FAILED_STATE")
        if prior.case_dir != case.case_dir:
            raise RuntimeError("RETRY_PRIOR_ATTEMPT_CASE_MISMATCH")
        _reservation_event(case, reservation)
        if (
            patched.path.stat().st_size != patched.bytes
            or sha256_file(patched.path) != patched.sha256
        ):
            raise RuntimeError("RETRY_PATCH_ARTIFACT_DRIFT")

        input_manifest = _retry_snapshot(
            prior.root / "input-manifest.json"
        )
        if "diagnostic_retry" in input_manifest:
            raise RuntimeError("RETRY_INPUT_MANIFEST_ALREADY_DERIVED")
        input_manifest["diagnostic_retry"] = {
            "prior_attempt_id": prior.attempt_id,
            "reservation_key": reservation.key,
            "reservation_ordinal": reservation.ordinal,
            "reservation_event_sha256": reservation.event_sha256,
            "patched_path": str(patched.path.resolve(strict=True)),
            "patched_bytes": patched.bytes,
            "patched_sha256": patched.sha256,
            "purpose": "diagnostic",
            "eligible_for_promotion": False,
        }
        resume_key = sha256_bytes(canonical_json_bytes({
            "prior_resume_key": prior.resume_key,
            "reservation_key": reservation.key,
            "patched_sha256": patched.sha256,
        }))
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        attempt_id = f"{timestamp}-{uuid4().hex[:8]}"
        root = (
            case.case_dir / "90_Temporary" / "attempts" / attempt_id
        )
        root.mkdir(parents=True, exist_ok=False)
        solver = root / "solver"
        raw_logs = root / "raw-logs"
        generated = root / "generated"
        solver.mkdir()
        raw_logs.mkdir()
        generated.mkdir()
        snapshots = (
            ("input-manifest.json", input_manifest),
            (
                "intent-revision.json",
                _retry_snapshot(prior.root / "intent-revision.json"),
            ),
            (
                "normalized-config.json",
                _retry_snapshot(prior.root / "normalized-config.json"),
            ),
            (
                "commands.json",
                _retry_snapshot(prior.root / "commands.json"),
            ),
            (
                "tool-fingerprints.json",
                _retry_snapshot(prior.root / "tool-fingerprints.json"),
            ),
        )
        for filename, value in snapshots:
            atomic_create_artifact(
                root / filename,
                canonical_json_bytes(value) + b"\n",
            )
        atomic_create_artifact(root / "stage-events.jsonl", b"")
        retry = AttemptStore(
            case.case_dir,
            attempt_id,
            root,
            resume_key,
            AttemptPaths(
                solver,
                raw_logs,
                generated,
                root / "stage-events.jsonl",
                root / "attempt-result.json",
            ),
        )
        staged = stage_solver_input(retry, patched)
        if staged.sha256 != patched.sha256 or staged.bytes != patched.bytes:
            raise RuntimeError("RETRY_STAGED_PATCH_MISMATCH")
        return retry


def _preflight_role(
    preflight: dict[str, object],
    role: str,
) -> ExpectedArtifact:
    found = [
        item for item in preflight["evidence"]
        if item.get("role") == role
    ]
    if len(found) != 1:
        raise RuntimeError(f"RETRY_PRIOR_PREFLIGHT_ROLE_COUNT:{role}")
    item = found[0]
    path = Path(str(item["path"])).resolve(strict=True)
    if (
        path.stat().st_size != int(item["bytes"])
        or sha256_file(path) != item["sha256"]
    ):
        raise RuntimeError(f"RETRY_PRIOR_PREFLIGHT_ROLE_DRIFT:{role}")
    return ExpectedArtifact(path, str(item["sha256"]))


def preflight_retry_attempt(
    case: CaseStore,
    retry: AttemptStore,
    prior: AttemptStore,
    reservation: RetryReservation,
) -> PreflightDecision:
    with case.locked() as transaction:
        projection = transaction.replay()
        if projection["harness"]["case_state"] not in {
            CaseState.SOLVE_FAILED.value,
            CaseState.RESULT_INCOMPLETE.value,
        }:
            raise RuntimeError("RETRY_PREFLIGHT_REQUIRES_FAILED_STATE")
        if (
            prior.case_dir != case.case_dir
            or retry.case_dir != case.case_dir
            or prior.attempt_id == retry.attempt_id
        ):
            raise RuntimeError("RETRY_PREFLIGHT_ATTEMPT_BINDING_INVALID")
        _reservation_event(case, reservation)

        input_manifest = _retry_snapshot(
            retry.root / "input-manifest.json"
        )
        lineage = input_manifest.get("diagnostic_retry")
        if not isinstance(lineage, dict) or lineage != {
            "prior_attempt_id": prior.attempt_id,
            "reservation_key": reservation.key,
            "reservation_ordinal": reservation.ordinal,
            "reservation_event_sha256": reservation.event_sha256,
            "patched_path": lineage.get("patched_path"),
            "patched_bytes": lineage.get("patched_bytes"),
            "patched_sha256": lineage.get("patched_sha256"),
            "purpose": "diagnostic",
            "eligible_for_promotion": False,
        }:
            raise RuntimeError("RETRY_INPUT_MANIFEST_LINEAGE_DRIFT")
        staged_path = (retry.paths.solver / "input.feb").resolve(strict=True)
        if (
            staged_path.stat().st_size != int(lineage["patched_bytes"])
            or sha256_file(staged_path) != lineage["patched_sha256"]
            or (
                Path(str(lineage["patched_path"])).resolve(strict=True)
                .stat().st_size
                != int(lineage["patched_bytes"])
            )
            or sha256_file(
                Path(str(lineage["patched_path"])).resolve(strict=True)
            ) != lineage["patched_sha256"]
        ):
            raise RuntimeError("RETRY_STAGED_PATCH_LINEAGE_DRIFT")
        expected_resume_key = sha256_bytes(canonical_json_bytes({
            "prior_resume_key": prior.resume_key,
            "reservation_key": reservation.key,
            "patched_sha256": lineage["patched_sha256"],
        }))
        if retry.resume_key != expected_resume_key:
            raise RuntimeError("RETRY_RESUME_KEY_DRIFT")

        revision = _retry_snapshot(retry.root / "intent-revision.json")
        binding_path = (
            case.case_dir
            / "05_Verification/harness/intent"
            / f"intent-inspection-binding-r{int(revision['revision']):04d}.json"
        ).resolve(strict=True)
        binding = load_json(binding_path)
        selection_path = (
            case.case_dir
            / "05_Verification/harness/feb-inspection-selection.json"
        ).resolve(strict=True)
        inspection_path = (
            case.case_dir
            / "05_Verification/harness/feb-inspection.json"
        ).resolve(strict=True)
        selection = load_json(selection_path)
        if (
            binding["revision"] != revision["revision"]
            or binding["contract_sha256"] != revision["contract_sha256"]
            or binding["feb_inspection_sha256"]
            != sha256_file(inspection_path)
            or binding["feb_inspection_selection_sha256"]
            != sha256_file(selection_path)
            or binding["excluded_domains"] != selection["excluded_domains"]
        ):
            raise RuntimeError("RETRY_INTENT_INSPECTION_BINDING_DRIFT")

        staged = ArtifactRef(
            staged_path,
            int(lineage["patched_bytes"]),
            str(lineage["patched_sha256"]),
        )
        model = create_model_evidence(
            retry,
            staged,
            excluded_domains=tuple(binding["excluded_domains"]),
        )
        prior_preflight = load_json(
            prior.root / "preflight-decision.json"
        )
        adopted = _preflight_role(prior_preflight, "adopted-feb")
        intent_approval = _preflight_role(
            prior_preflight, "intent-approval"
        )
        validate_current_intent_authority(
            case,
            revision,
            load_json(intent_approval.path),
            intent_approval.path,
        )
        execution = _retry_snapshot(
            retry.root / "normalized-config.json"
        )
        tool_fingerprints = _retry_snapshot(
            retry.root / "tool-fingerprints.json"
        )
        solver = ExpectedArtifact(
            Path(str(execution["expected_solver"]["path"])).resolve(
                strict=True
            ),
            str(execution["expected_solver"]["sha256"]),
        )
        prior_solver = _preflight_role(prior_preflight, "solver")
        if solver != prior_solver:
            raise RuntimeError("RETRY_SOLVER_BINDING_DRIFT")
        install_path = Path(
            os.environ["FEBIO_CAE_INSTALL_MANIFEST"]
        ).resolve(strict=True)
        install = load_json(install_path)
        runtime = fbs_runtime_from_install_manifest(install)
        if (
            runtime.expected_runtime_tree_sha256
            != execution["expected_fbs_runtime_tree_sha256"]
        ):
            raise RuntimeError("RETRY_FBS_RUNTIME_BINDING_DRIFT")
        policy_path = resolve_install_pointer(
            install_path,
            install["release"]["repository_policy"],
        )
        policy = ExpectedArtifact(
            policy_path,
            str(install["release"]["repository_policy"]["sha256"]),
        )
        prior_policy = _preflight_role(prior_preflight, "policy")
        if policy != prior_policy:
            raise RuntimeError("RETRY_POLICY_BINDING_DRIFT")
        expected_tool_fingerprints = (
            expected_installed_tool_fingerprints(
                install_path,
                execution,
            )
        )
        if tool_fingerprints != expected_tool_fingerprints:
            raise RuntimeError("RETRY_INSTALLED_IDENTITY_DRIFT")

        evidence: list[dict[str, object]] = []
        for role, expected in (
            ("adopted-feb", adopted),
            ("staged-feb", ExpectedArtifact(staged.path, staged.sha256)),
            ("model-evidence", ExpectedArtifact(model.path, model.sha256)),
            ("intent-approval", intent_approval),
            ("solver", solver),
            ("policy", policy),
        ):
            actual = sha256_file(expected.path)
            if actual != expected.sha256:
                raise RuntimeError(f"RETRY_PREFLIGHT_HASH_DRIFT:{role}")
            evidence.append({
                "role": role,
                "path": str(expected.path.resolve(strict=True)),
                "bytes": expected.path.stat().st_size,
                "sha256": actual,
            })
        verify_runtime(runtime)
        evidence.extend((
            {
                "role": "fbs-runtime-tree",
                "sha256": runtime.expected_runtime_tree_sha256,
            },
            {
                "role": "fbs-loaded-dll-profile",
                "sha256": (
                    runtime.expected_loaded_dll_manifest_sha256
                ),
            },
            {"role": "resume-key", "sha256": retry.resume_key},
        ))
        decision = PreflightDecision(
            status="accepted",
            attempt_id=retry.attempt_id,
            checked_roles=tuple(
                str(item["role"]) for item in evidence
            ),
            evidence=tuple(evidence),
        )
        decision_path = retry.root / "preflight-decision.json"
        atomic_create_artifact(
            decision_path,
            canonical_json_bytes({
                "schema_version": 1,
                "status": decision.status,
                "attempt_id": decision.attempt_id,
                "checked_roles": list(decision.checked_roles),
                "evidence": list(decision.evidence),
            }) + b"\n",
        )
        transaction.append(
            "PREFLIGHT_PASSED",
            CaseState.PREFLIGHT_PASSED,
            {
                "attempt_id": retry.attempt_id,
                "preflight_decision_sha256": sha256_file(decision_path),
                "resume_key": retry.resume_key,
                "prior_attempt_id": prior.attempt_id,
                "retry_reservation_event_sha256": (
                    reservation.event_sha256
                ),
            },
        )
        return decision
```

The focused test must inspect both finished functions and reject `pass`,
`NotImplementedError`, or an ellipsis. This is the one mechanical
duplication allowed in Phase 1: it preserves the closed
failure-state-to-`PREFLIGHT_PASSED` transition while keeping both the adopted
FEB and the prior attempt immutable.

- [ ] **Step 44: Run diagnose/retry GREEN and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_production_control.py `
  apps/febio_cae_harness/tests/unit/test_diagnosis.py `
  apps/febio_cae_harness/tests/unit/test_retry_ledger.py `
  apps/febio_cae_harness/tests/unit/test_numerical_patch.py -q
git add `
  apps/febio_cae_harness/src/febio_cae_harness/commands/diagnose.py `
  apps/febio_cae_harness/tests/unit/test_production_control.py
git commit -m "feat: wire intent-aware diagnosis and bounded retry"
```

Expected: all tests pass. An approved zero retry budget writes
nothing; a synthetic approved numerical diagnostic produces a distinct,
preflight-passed attempt without changing the adopted FEB or prior attempt,
and no retry can cross an Analysis Intent or installed-release identity change.

- [ ] **Step 45: Write and run the actual pre-approval CLI journey**

`test_cli_preapproval_journey.py` must invoke `main(argv)` separately for:

```text
case init
source resolve
input ingest
inspect feb
inspect inheritance
intent draft
intent request-approval
workflow run --through REPORTED
```

Use a complete small synthetic FEB and a test workspace policy rooted below
pytest `tmp_path`. Capture each stdout independently and assert exactly one
schema-valid JSON object. The final workflow call must return exit `10`,
lowercase `waiting_for_human`, remain `INTENT_DRAFTED`, create zero attempt
directories, and start zero processes. Then submit an explicit synthetic-test
approval record through `intent approve` and prove a stale nonce, wrong
contract hash, and wrong execution-profile hash each return exit `30` without
creating a model or attempt.

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_command_registry.py `
  apps/febio_cae_harness/tests/integration/test_cli_preapproval_journey.py -q
```

Expected: all selected tests pass.

- [ ] **Step 46: Run the full Phase 1A-C regression and commit**

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit `
  apps/febio_cae_harness/tests/contract `
  apps/febio_cae_harness/tests/integration -q
& .\.venv\Scripts\python.exe -m pytest apps/febio_gmsh_launcher/tests -q
git diff --check
git status --short
git add `
  apps/febio_cae_harness/src/febio_cae_harness/cli.py `
  apps/febio_cae_harness/src/febio_cae_harness/orchestrator.py `
  apps/febio_cae_harness/src/febio_cae_harness/execution_request.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/execution-request.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/commands `
  apps/febio_cae_harness/tests/unit/test_command_registry.py `
  apps/febio_cae_harness/tests/unit/test_execution_request.py `
  apps/febio_cae_harness/tests/integration/test_cli_preapproval_journey.py
git diff --cached --check
git commit -m "feat: wire the production CAE command surface"
```

Expected: all tests pass, the commit contains only the listed tool files, and
`git status --short` is empty after commit.
