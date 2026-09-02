"""Conservative FEBio LOG validation.

This module looks only for explicit solver markers.  It never infers physical
meaning from a model, and a normal process exit alone is not considered a
valid analysis result.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .types import SolverClassification

__all__ = ["LogValidation", "LogValidator", "validate_log", "validate_solver_log"]


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_STEP_PATTERNS = (
    re.compile(r"\btime\s+step\s*(?:number\s*)?(?:[=:])?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bstep\s*(?:number\s*)?(?:[=:])\s*(\d+)\b", re.IGNORECASE),
)
_TIME_PATTERN = re.compile(
    rf"\b(?:current\s+|final\s+|at\s+)?time\s*(?:[=:])\s*({_NUMBER})\b",
    re.IGNORECASE,
)
_ELAPSED_TIME_PATTERN = re.compile(r"\belapsed\s+time\b", re.IGNORECASE)
_NEGATIVE_JACOBIAN_PATTERNS = (
    re.compile(r"negative\s+jacobian", re.IGNORECASE),
    re.compile(r"jacobian(?:\s+determinant)?[^\n]*\bnegative\b", re.IGNORECASE),
    re.compile(r"jacobian(?:\s+determinant)?[^\n]*<\s*0", re.IGNORECASE),
)
_NONLINEAR_CONVERGENCE_PATTERNS = (
    re.compile(r"\bnonlinear(?:\s+solver)?\b[^\n]*\bfailed\s+to\s+converge\b", re.IGNORECASE),
    re.compile(r"\bfailed\s+to\s+converge\b[^\n]*\bnonlinear\b", re.IGNORECASE),
    re.compile(r"\bmaximum\s+number\s+of\s+nonlinear\s+iterations\s+reached\b", re.IGNORECASE),
)
_FATAL_PATTERNS = (
    re.compile(r"\bfatal(?:\s+error)?\b", re.IGNORECASE),
    re.compile(r"missing\s+(?:a\s+)?(?:reference|attribute|node|element)", re.IGNORECASE),
    re.compile(r"undefined\s+(?:reference|attribute|node|element)", re.IGNORECASE),
    re.compile(r"cannot\s+(?:find|open|read|load)", re.IGNORECASE),
    re.compile(r"failed\s+to\s+(?:open|load|initialize|read)", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class LogValidation:
    """Evidence extracted from one solver LOG file."""

    path: Path
    exists: bool
    normal_termination: bool
    observed_steps: int | None
    observed_final_time: float | None
    expected_steps: int | None
    expected_final_time: float | None
    classification: SolverClassification | None
    issues: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.exists and self.classification is None

    @property
    def normal_exit(self) -> bool:
        return self.normal_termination

    @property
    def step_count(self) -> int | None:
        return self.observed_steps

    @property
    def final_time(self) -> float | None:
        return self.observed_final_time

    @property
    def failure_classification(self) -> SolverClassification | None:
        return self.classification


def _normalise_fields(
    path: Path,
    *,
    expected_steps: int | None,
    expected_final_time: float | None,
) -> tuple[Path, int | None, float | None]:
    if expected_steps is not None:
        if isinstance(expected_steps, bool) or not isinstance(expected_steps, int):
            raise ValueError("expected_steps must be a non-negative integer or None")
        if expected_steps < 0:
            raise ValueError("expected_steps must be a non-negative integer or None")
    if expected_final_time is not None:
        if isinstance(expected_final_time, bool) or not isinstance(
            expected_final_time, (int, float)
        ):
            raise ValueError("expected_final_time must be a finite number or None")
        if not math.isfinite(float(expected_final_time)):
            raise ValueError("expected_final_time must be a finite number or None")
    return path, expected_steps, expected_final_time


def _missing(
    path: Path, *, expected_steps: int | None, expected_final_time: float | None
) -> LogValidation:
    return LogValidation(
        path=path,
        exists=False,
        normal_termination=False,
        observed_steps=None,
        observed_final_time=None,
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
        classification=SolverClassification.MISSING_OUTPUT,
        issues=(f"LOG file is missing: {path}",),
    )


def _read_log(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _observed_steps(text: str) -> int | None:
    values: list[int] = []
    for pattern in _STEP_PATTERNS:
        values.extend(int(match.group(1)) for match in pattern.finditer(text))
    return max(values) if values else None


def _observed_final_time(text: str) -> float | None:
    values: list[float] = []
    for line in text.splitlines():
        if _ELAPSED_TIME_PATTERN.search(line) is not None:
            continue
        for match in _TIME_PATTERN.finditer(line):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if math.isfinite(value):
                values.append(value)
    return values[-1] if values else None


def _has_normal_termination(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    normalised = " ".join(text.split()).casefold()
    return "normaltermination" in compact or "normal termination" in normalised


def _has_negative_jacobian(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in _NEGATIVE_JACOBIAN_PATTERNS)


def _has_nonlinear_convergence_failure(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in _NONLINEAR_CONVERGENCE_PATTERNS)


def _has_fatal(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in _FATAL_PATTERNS)


def _is_init_only(text: str, observed_steps: int | None, normal_termination: bool) -> bool:
    if observed_steps is not None or normal_termination:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", text.casefold())
    normalised = " ".join(text.split()).casefold()
    return (
        "initializationonly" in compact
        or "initonly" in compact
        or "init only" in normalised
        or ("initialization" in normalised and "complete" in normalised)
    )


def validate_log(
    path: str | Path,
    *,
    expected_steps: int | None = None,
    expected_final_time: float | None = None,
    final_time_tolerance: float = 1e-9,
) -> LogValidation:
    """Validate explicit normal-termination, step, and final-time markers."""

    log_path, expected_steps, expected_final_time = _normalise_fields(
        Path(path),
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
    )
    if isinstance(final_time_tolerance, bool) or not isinstance(final_time_tolerance, (int, float)):
        raise ValueError("final_time_tolerance must be a non-negative finite number")
    if final_time_tolerance < 0 or not math.isfinite(float(final_time_tolerance)):
        raise ValueError("final_time_tolerance must be a non-negative finite number")
    if not log_path.is_file():
        return _missing(
            log_path,
            expected_steps=expected_steps,
            expected_final_time=expected_final_time,
        )

    text = _read_log(log_path)
    if text is None:
        return LogValidation(
            path=log_path,
            exists=True,
            normal_termination=False,
            observed_steps=None,
            observed_final_time=None,
            expected_steps=expected_steps,
            expected_final_time=expected_final_time,
            classification=SolverClassification.INVALID_LOG,
            issues=(f"LOG file is unreadable: {log_path}",),
        )

    observed_steps = _observed_steps(text)
    observed_final_time = _observed_final_time(text)
    normal_termination = _has_normal_termination(text)
    issues: list[str] = []
    classification: SolverClassification | None = None

    if _has_negative_jacobian(text):
        classification = SolverClassification.NEGATIVE_JACOBIAN
        issues.append("LOG reports a negative Jacobian")
    elif _has_nonlinear_convergence_failure(text):
        classification = SolverClassification.NONLINEAR_CONVERGENCE
        issues.append("LOG explicitly reports nonlinear convergence failure")
    elif _has_fatal(text):
        classification = SolverClassification.FATAL
        issues.append("LOG reports a fatal or missing-reference error")
    elif _is_init_only(text, observed_steps, normal_termination):
        classification = SolverClassification.INIT_ONLY
        issues.append("LOG contains initialization-only markers")
    elif not normal_termination:
        classification = SolverClassification.INVALID_LOG
        issues.append("LOG has no normal-termination marker")

    if expected_steps is not None and observed_steps != expected_steps:
        classification = classification or SolverClassification.INVALID_LOG
        issues.append(f"LOG final step {observed_steps!r} does not match expected {expected_steps}")

    if expected_final_time is not None and (
        observed_final_time is None
        or not math.isclose(
            observed_final_time,
            float(expected_final_time),
            rel_tol=final_time_tolerance,
            abs_tol=final_time_tolerance,
        )
    ):
        classification = classification or SolverClassification.INVALID_LOG
        issues.append(
            f"LOG final time {observed_final_time!r} does not match expected {expected_final_time}"
        )

    return LogValidation(
        path=log_path,
        exists=True,
        normal_termination=normal_termination,
        observed_steps=observed_steps,
        observed_final_time=observed_final_time,
        expected_steps=expected_steps,
        expected_final_time=expected_final_time,
        classification=classification,
        issues=tuple(issues),
    )


class LogValidator:
    """Reusable validator configured for one expected analysis contract."""

    def __init__(
        self,
        *,
        expected_steps: int | None = None,
        expected_final_time: float | None = None,
        final_time_tolerance: float = 1e-9,
    ) -> None:
        self.expected_steps = expected_steps
        self.expected_final_time = expected_final_time
        self.final_time_tolerance = final_time_tolerance

    def validate(self, path: str | Path) -> LogValidation:
        return validate_log(
            path,
            expected_steps=self.expected_steps,
            expected_final_time=self.expected_final_time,
            final_time_tolerance=self.final_time_tolerance,
        )

    __call__ = validate


validate_solver_log = validate_log
