"""Bounded FEBio 4.12 printed observations, not native convergence qualification."""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from febio_cae.domain import (
    AttemptRecord,
    CaseRevision,
    CompatibilityProfile,
    ExecutionBundle,
    MeshArtifact,
    Quantity,
    ResultManifest,
)
from febio_cae.domain.artifacts import ResolvedFileContent
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.ports import PortError, PortErrorCategory

_POLICY = {
    "id": "reported-final-cycle-4.12-v1",
    "comparison": "exact printed decimal strict less/greater; equality unverified",
    "qualification": "UNVERIFIED",
}
_EVIDENCE = {
    "manual": "4e10844e1d47bbdae0d7b7792d6a01a8b3b02bf13591530d782b6da5e89795ec",
    "grammar": "86bd41c31aa9b22fa7ccb15c09502cdbe882e9b0c6133692b13dc474c331e898",
    "review": "a7089c2c1e643d38384ebef23557c561f024cb21af1a5b730037ef51454a6a0f",
}
_NORM = re.compile(r"[0-9]\.[0-9]{6}e[+-][0-9]{2}")
_BEGIN = re.compile(r"=+ beginning time step ([1-9][0-9]*) : (0\.[1-9]|1) =+")
_STATUS = re.compile(r"Nonlinear solution status: time= (0\.[1-9]|1)")
_AUGMENT = re.compile(r"\.+ augmentation # ([1-9][0-9]*)")
_ACCEPT = re.compile(r"-+ converged at time : (0\.[1-9]|1)")
_COUNTERS = ("stiffness updates", "right hand side evaluations", "stiffness matrix reformations")
_NUMERIC = (
    "dtol",
    "etol",
    "rtol",
    "min_residual",
    "max_refs",
    "tolerance",
    "gaptol",
    "minaug",
    "maxaug",
)
_CONTROLS = (*_NUMERIC, "reform_augment", "laugon", "two_pass", "max_ups")


@dataclass(frozen=True, slots=True)
class ReportedNorms:
    """Canonical immutable response bytes, constructed only from resolved evidence."""

    payload: bytes

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.payload))


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite configured value")
    return number


def _admission(
    content: bytes,
    text: str,
    revision: CaseRevision,
    profile: CompatibilityProfile,
    bundle: ExecutionBundle,
) -> dict[str, float]:
    if profile.solver.version != "4.12.0" or bundle.tool != profile.solver:
        raise ValueError("unsupported solver identity/version")
    if b"<!" in content:
        raise ValueError("unsupported input declarations")
    root = ET.fromstring(content)
    if (
        root.tag != "febio_spec"
        or root.get("version") != "4.0"
        or len(root.findall("Module")) != 1
        or root.findall("Module")[0].get("type") != "solid"
    ):
        raise ValueError("unsupported input module/dialect")
    if len(root.findall("Control")) != 1 or root.findall("Step") or root.findall("MeshAdaptor"):
        raise ValueError("unsupported input step/adaptor structure")
    control = root.find("Control")
    assert control is not None

    def element(parent: ET.Element, path: str) -> ET.Element:
        found = parent.findall(path)
        if len(found) != 1:
            raise ValueError(f"missing/duplicate input {path}")
        return found[0]

    def value(parent: ET.Element, path: str) -> float:
        return _number((element(parent, path).text or "").strip())

    analysis = element(control, "analysis")
    if (analysis.get("type") or (analysis.text or "").strip()) != "static" or control.find(
        "time_stepper"
    ) is not None:
        raise ValueError("unsupported analysis/adaptive input")
    step = value(control, "step_size")
    count = value(control, "time_steps")
    spec = revision.spec
    increments = spec.solver_policy.increments
    if (
        (step, count) != (0.1, 10)
        or increments.adaptive
        or increments.initial_step.to_si().value != step
        or increments.max_steps < count
        or increments.max_step_retries != 0
        or spec.motion.samples[0].time.to_si().value != 0
        or spec.motion.samples[-1].time.to_si().value != 1
    ):
        raise ValueError("unsupported or inconsistent fixed increment coverage")
    solver = element(control, "solver")
    contact = element(root, "Contact/contact")
    if solver.get("type") != "solid" or contact.get("type") != "sliding-elastic":
        raise ValueError("unsupported solver/interface")
    qn = element(solver, "qn_method")
    if qn.get("type") != "BFGS" or solver.find("max_ups") is not None:
        raise ValueError("unsupported quasi-Newton controls")
    values = {c.name: c.value for c in spec.solver_policy.controls}
    configured: dict[str, float] = {}
    for name in _CONTROLS:
        selected = values.get(name)
        if selected is None:
            raise ValueError(f"missing explicit control {name}")
        if name in {"reform_augment", "two_pass"} and type(selected) is not bool:
            raise ValueError(f"invalid explicit boolean {name}")
        if (
            name in {"laugon", "max_refs", "max_ups", "minaug", "maxaug"}
            and type(selected) is not int
        ):
            raise ValueError(f"invalid explicit integer {name}")
        number = selected.to_si().value if isinstance(selected, Quantity) else float(selected)
        parent = (
            contact
            if name in {"laugon", "tolerance", "gaptol", "minaug", "maxaug", "two_pass"}
            else qn
            if name == "max_ups"
            else solver
        )
        # XML uses the compiler's round-trip binary64 serialization. Norm tokens below use Decimal.
        if value(parent, name) != number:
            raise ValueError(f"input/frozen control disagreement: {name}")
        configured[name] = number
    if (
        any(
            configured[k] <= 0
            for k in (
                "dtol",
                "etol",
                "rtol",
                "max_refs",
                "max_ups",
                "tolerance",
                "gaptol",
                "maxaug",
            )
        )
        or configured["min_residual"] != 0
        or configured["minaug"] < 0
        or configured["minaug"] > configured["maxaug"]
        or configured["reform_augment"] != 1
        or configured["laugon"] != 1
        or configured["two_pass"] != 0
    ):
        raise ValueError("disabled, shortcut or unsupported explicit controls")
    preamble = text.split("===== beginning time step", 1)[0]
    if re.findall(r"(?m)^\s*version\s+(\S+)\s*$", text) != ["4.12.0"] or re.findall(
        r"(?m)^contact interface [^\r\n]*", preamble
    ) != ["contact interface 1 - Type: sliding-elastic"]:
        raise ValueError("missing/duplicate/wrong echoed version or interface")
    echoes: dict[str, list[str]] = {}
    for line in preamble.splitlines():
        match = re.fullmatch(r"\s*(.*?)\s+\.{2,}\s*:\s*(.*?)\s*", line)
        if match:
            echoes.setdefault(match[1], []).append(match[2])
    literals = {
        "Module type": "solid",
        "analysis": "STATIC (0)",
        "Auto time stepper activated": "no",
        "reform_augment": "yes (1)",
        "laugon": "AUGLAG (1)",
        "two_pass": "no (0)",
    }
    for name, expected in literals.items():
        if echoes.get(name) != [expected]:
            raise ValueError(f"unsupported/missing echoed {name}")
    for name, expected_number in {
        "time_steps": count,
        "step_size": step,
        **{n: configured[n] for n in _NUMERIC},
    }.items():
        tokens = echoes.get(name, [])
        if len(tokens) != 1 or _number(tokens[0]) != expected_number:
            raise ValueError(f"input/echo disagreement: {name}")
    return configured


def _row(line: str, name: str, offset: int, nonlinear: bool) -> dict[str, Any]:
    separator = r"\s+" if nonlinear else r"\s*:\s*"
    pattern = (
        r"\s*"
        + re.escape(name)
        + separator
        + (r"(\S+)\s+" if nonlinear else "")
        + r"(\S+)\s+(\S+)\s*"
    )
    match = re.fullmatch(pattern, line)
    if match is None:
        raise ValueError(f"unsupported {name} row layout")
    index = 2 if nonlinear else 1
    tokens = match.groups()
    status, reason = (
        "UNVERIFIED",
        "unsupported/nonfinite/negative token, zero requirement or printed equality",
    )
    if all(_NORM.fullmatch(token) for token in tokens):
        current, required = Decimal(match[index]), Decimal(match[index + 1])
        if required > 0 and current >= 0 and current != required:
            status = "PASS" if current < required else "FAIL"
            reason = (
                "exact printed CURRENT strictly "
                + ("below" if status == "PASS" else "above")
                + " its own REQUIRED"
            )
    result: dict[str, Any] = {
        "name": name,
        "current": match[index],
        "required": match[index + 1],
        "current_span": [offset + match.start(index), offset + match.end(index)],
        "required_span": [offset + match.start(index + 1), offset + match.end(index + 1)],
        "status": status,
        "reason": reason,
    }
    if nonlinear:
        result.update(
            initial=match[1], initial_span=[offset + match.start(1), offset + match.end(1)]
        )
    return result


def _scan(
    text: str, controls: dict[str, float]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], bool]:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)  # latin1 preserves one character per original byte
    blocks: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    reasons: list[str] = []
    trusted = True
    active: dict[str, Any] | None = None
    seen: set[int] = set()
    interface_seen = False

    def add(kind: str, start: int, end: int, **data: Any) -> int:
        identifier = len(blocks)
        blocks.append(
            {
                "id": identifier,
                "kind": kind,
                "line": start + 1,
                "span": [offsets[start], offsets[end] if end < len(lines) else len(text)],
                "raw": "".join(lines[start:end]),
                **data,
            }
        )
        return identifier

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        begin = _BEGIN.fullmatch(line)
        try:
            if line.lower().startswith(("contact interface", "sliding interface")):
                if (
                    line != "contact interface 1 - Type: sliding-elastic"
                    or interface_seen
                    or seen
                    or active is not None
                ):
                    raise ValueError("unexpected interface declaration outside augmentation")
                interface_seen = True
                i += 1
                continue
            if begin:
                n, t = int(begin[1]), begin[2]
                if (
                    n not in range(1, 11)
                    or n in seen
                    or (seen and n <= max(seen))
                    or t != (str(n / 10) if n < 10 else "1")
                ):
                    raise ValueError("duplicate/inconsistent increment opener")
                if active is not None:
                    reasons.append("previous increment has no accepted closure")
                seen.add(n)
                active = {
                    "step": n,
                    "time": t,
                    "iteration": 0,
                    "augmentation": 0,
                    "last": "begin",
                    "summary": False,
                }
                active["begin_block"] = add("begin", i, i + 1, step=n, time=t)
                i += 1
                continue
            if active is None:
                if any(
                    word in line.lower()
                    for word in (
                        "beginning time step",
                        "nonlinear solution status",
                        "augmentation #",
                        "converged at time",
                        "retry",
                        "cutback",
                    )
                ):
                    raise ValueError("unexpected/retried/outside-increment record")
                i += 1
                continue
            if re.fullmatch(r"[1-9][0-9]*", line):
                iteration = int(line)
                if iteration != active["iteration"] + 1 or active["summary"]:
                    raise ValueError("inconsistent nonlinear iteration/time/order")
                if i + 1 < len(lines):
                    status = _STATUS.fullmatch(lines[i + 1].strip())
                    if status is None or status[1] != active["time"]:
                        raise ValueError("inconsistent nonlinear iteration/time/order")
                for j, counter in enumerate(_COUNTERS, 2):
                    if i + j >= len(lines):
                        break
                    match = re.fullmatch(
                        re.escape(counter) + r"\s*=\s*([0-9]+)", lines[i + j].strip()
                    )
                    if match is None or (
                        counter == _COUNTERS[2]
                        and int(match[1]) >= controls.get("max_refs", math.inf)
                    ):
                        raise ValueError("unsupported counter/reformation-cap record")
                for j, pattern in (
                    (5, r"step from line search\s*=\s*[0-9]+\.[0-9]{6}"),
                    (6, r"convergence norms\s*:\s*INITIAL\s+CURRENT\s+REQUIRED"),
                ):
                    if i + j < len(lines) and not re.fullmatch(pattern, lines[i + j].strip()):
                        raise ValueError("unsupported nonlinear header/counters")
                rows = [
                    _row(lines[i + j], name, offsets[i + j], True)
                    for j, name in enumerate(("residual", "energy", "displacement"), 7)
                    if i + j < len(lines)
                ]
                if i + 9 >= len(lines):
                    # A valid prefix at EOF leaves this increment unresolved; it does not
                    # contradict earlier accepted increments. Malformed present lines above
                    # still invalidate the stream, including retry/interface contradictions.
                    for row in rows:
                        row.update(status="UNVERIFIED", reason="incomplete nonlinear association")
                    add(
                        "incomplete_nonlinear",
                        i,
                        len(lines),
                        step=active["step"],
                        iteration=iteration,
                        rows=rows,
                    )
                    reasons.append(f"line {i + 1}: truncated nonlinear block")
                    i = len(lines)
                    continue
                active["nonlinear_block"] = add(
                    "nonlinear", i, i + 10, step=active["step"], iteration=iteration, rows=rows
                )
                active.update(iteration=iteration, last="nonlinear")
                i += 10
                continue
            augmentation = _AUGMENT.fullmatch(line)
            if augmentation:
                if (
                    i + 4 >= len(lines)
                    or active["last"] != "nonlinear"
                    or int(augmentation[1]) != active["augmentation"] + 1
                    or active["summary"]
                ):
                    raise ValueError("inconsistent/truncated augmentation order")
                if lines[i + 1].strip() != "sliding interface # 1" or not re.fullmatch(
                    r"CURRENT\s+REQUIRED", lines[i + 2].strip()
                ):
                    raise ValueError("unsupported augmentation interface/header")
                rows = [
                    _row(lines[i + j], name, offsets[i + j], False)
                    for j, name in enumerate(("D multiplier", "maximum gap"), 3)
                ]
                active["augmentation_block"] = add(
                    "augmentation",
                    i,
                    i + 5,
                    step=active["step"],
                    augmentation=int(augmentation[1]),
                    interface=1,
                    rows=rows,
                )
                active.update(augmentation=int(augmentation[1]), last="augmentation")
                if active["augmentation"] >= controls.get("maxaug", math.inf):
                    raise ValueError("unsupported augmentation-cap advance")
                i += 5
                continue
            if line == "convergence summary":
                if active["last"] != "augmentation" or active["summary"] or i + 2 >= len(lines):
                    raise ValueError("unexpected/truncated convergence summary")
                iterations = re.fullmatch(
                    r"number of iterations\s*:\s*([0-9]+)", lines[i + 1].strip()
                )
                reformations = re.fullmatch(
                    r"number of reformations\s*:\s*[0-9]+", lines[i + 2].strip()
                )
                if (
                    iterations is None
                    or int(iterations[1]) != active["iteration"]
                    or reformations is None
                ):
                    raise ValueError("inconsistent summary counts")
                add("summary", i, i + 3, step=active["step"])
                active["summary"] = True
                i += 3
                continue
            marker = _ACCEPT.fullmatch(line)
            if marker:
                if (
                    marker[1] != active["time"]
                    or not active["summary"]
                    or active["last"] != "augmentation"
                    or active["augmentation"] < controls.get("minaug", 0)
                ):
                    raise ValueError("unresolved final-cycle acceptance")
                end = add("accepted", i, i + 1, step=active["step"])
                accepted.append(
                    {
                        "step": active["step"],
                        "time": active["time"],
                        "begin_block": active["begin_block"],
                        "nonlinear_block": active["nonlinear_block"],
                        "augmentation_block": active["augmentation_block"],
                        "accepted_block": end,
                    }
                )
                active = None
                i += 1
                continue
            warning = line.strip(" *")
            if warning == "Max nr of iterations reached.":
                if (
                    i + 1 >= len(lines)
                    or lines[i + 1].strip().strip(" *") != "Stiffness matrix will now be reformed."
                    or active["summary"]
                ):
                    raise ValueError("unsupported max-iteration branch")
                add("reformation", i, i + 2, step=active["step"])
                i += 2
                continue
            if not line or re.fullmatch(r"\*+", line) or warning == "WARNING":
                i += 1
                continue
            if re.fullmatch(
                r"Reforming stiffness matrix: reformation #[1-9][0-9]*|=+ reforming stiffness matrix:|Nr of (equations|nonzeroes in stiffness matrix)\s+\.+\s*:\s*[0-9]+",
                line,
            ):
                if active["summary"]:
                    raise ValueError("reformation after final summary")
                add("reformation", i, i + 1, step=active["step"])
                i += 1
                continue
            raise ValueError("unsupported record in an open increment")
        except (ValueError, InvalidOperation) as error:
            trusted = False
            reasons.append(f"line {i + 1}: {error}")
            add("unsupported", i, i + 1)
            i += 1
    if active is not None:
        reasons.append("final increment lacks accepted closure")
    if [step["step"] for step in accepted] != list(range(1, 11)):
        reasons.append("required input increments 1..10 are not completely accepted")
    return blocks, accepted, list(dict.fromkeys(reasons)), trusted


def assess_reported_norms(
    manifest: ResultManifest,
    revision: CaseRevision,
    mesh: MeshArtifact,
    profile: CompatibilityProfile,
    context: tuple[
        AttemptRecord, ExecutionBundle, ResolvedFileContent | None, ResolvedFileContent | None
    ],
) -> ReportedNorms:
    attempt, bundle, source, log = context
    if (
        attempt.attempt_id,
        bundle.bundle_digest,
        bundle.spec_digest,
        bundle.mesh_digest,
        bundle.profile_id,
    ) != (
        manifest.attempt_id,
        manifest.bundle_digest,
        revision.spec_digest,
        mesh.artifact_digest,
        profile.profile_id,
    ):
        raise PortError(PortErrorCategory.INTEGRITY, "reported context identity differs")
    report: dict[str, Any] = {
        "schema_version": "1",
        "claim_id": _POLICY["id"],
        "parser_revision": "1",
        "comparison_policy_digest": hashlib.sha256(canonical_bytes(_POLICY)).hexdigest(),
        "implementation_evidence": _EVIDENCE,
        "native_qualification": "UNVERIFIED",
        "final_status": "UNVERIFIED",
        "reasons": [],
        "blocks": [],
        "accepted_steps": [],
        "bindings": {
            "case_id": revision.case_id,
            "revision_id": revision.revision_id,
            "spec_digest": revision.spec_digest,
            "mesh_digest": mesh.artifact_digest,
            "profile_id": profile.profile_id,
            "profile_digest": hashlib.sha256(profile.to_bytes()).hexdigest(),
            "solver": profile.solver.to_dict(),
            "solver_policy_digest": hashlib.sha256(
                revision.spec.solver_policy.to_bytes()
            ).hexdigest(),
            "solver_profile": revision.spec.solver_policy.profile.to_dict(),
            "run_id": attempt.run_id,
            "attempt_id": attempt.attempt_id,
            "owner_generation": attempt.owner_generation,
            "process": None if attempt.process is None else attempt.process.to_dict(),
            "bundle_digest": bundle.bundle_digest,
            "manifest_id": manifest.manifest_id,
            "manifest_digest": hashlib.sha256(manifest.to_bytes()).hexdigest(),
            "input": None if source is None else source.entry.to_dict(),
            "log": None if log is None else log.entry.to_dict(),
        },
    }
    reasons: list[str] = report["reasons"]
    admitted = False
    controls: dict[str, float] = {}
    text = "" if log is None else log.content.decode("latin1")
    try:
        if source is None or log is None:
            raise ValueError("required registered input or solver log is absent")
        if (
            attempt.process is None
            or attempt.process.executable_digest != profile.solver.executable_digest
            or tuple(attempt.process.argv) != tuple(bundle.argv)
        ):
            raise ValueError("owned supported solver process evidence is absent/inconsistent")
        if tuple(bundle.argv[1:]) != (
            "-i",
            "input/case.feb",
            "-o",
            "output/solver.log",
            "-p",
            "output/results.xplt",
            "-noappend",
            "-noconfig",
        ):
            raise ValueError("unsupported bound compiler invocation")
        if len(log.content) > 8 * 1024 * 1024 or not text.isascii():
            raise ValueError("unsupported log size/encoding")
        controls = _admission(source.content, text, revision, profile, bundle)
        admitted = True
    except (ValueError, ET.ParseError, InvalidOperation) as error:
        reasons.append(str(error))
    blocks, accepted, coverage_reasons, trusted = _scan(text, controls)
    report.update(
        blocks=blocks,
        accepted_steps=accepted,
        expected_steps=list(range(1, 11)) if admitted else [],
        admission="SUPPORTED" if admitted else "UNVERIFIED",
    )
    reasons.extend(coverage_reasons)
    if not admitted or not trusted:
        for block in blocks:
            for row in block.get("rows", []):
                row.update(
                    status="UNVERIFIED", reason="untrusted input/stream admission or association"
                )
    else:
        statuses = [
            row["status"]
            for step in accepted
            for key in ("nonlinear_block", "augmentation_block")
            for row in blocks[step[key]]["rows"]
        ]
        if "FAIL" in statuses:
            report["final_status"] = "FAIL"
        elif "UNVERIFIED" in statuses:
            reasons.append("a final row is zero/equal/nonfinite or has unsupported tokens")
        elif not reasons and len(accepted) == 10:
            report["final_status"] = "PASS"
    report["report_digest"] = hashlib.sha256(canonical_bytes(report)).hexdigest()
    return ReportedNorms(canonical_bytes(report))
