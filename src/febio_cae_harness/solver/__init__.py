"""Headless FEBio solver and synthetic FBS authority boundary."""

from .fbs import (
    FbsAdapterAuthority,
    FbsAdapterManager,
    FbsAdapterProtocol,
    FbsValidation,
    validate_requested_fields,
)
from .headless import (
    HeadlessRunDiagnostic,
    headless_exit_code,
    run_headless_febio,
)
from .log import LogValidation, LogValidator, validate_log, validate_solver_log
from .runtime import (
    FebioRuntimeDiagnostic,
    RuntimeDiagnostic,
    RuntimeIdentity,
    RuntimeProbeDiagnostic,
    RuntimeProbeError,
    probe_febio,
    probe_runtime,
)
from .supervisor import SolverSupervisor
from .types import (
    FailureClassification,
    OutputExpectation,
    OutputFreshnessError,
    ProcessState,
    RunState,
    SolverClassification,
    SolverConfigurationError,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
    SolverState,
)

__all__ = [
    "FailureClassification",
    "HeadlessRunDiagnostic",
    "FbsAdapterAuthority",
    "FbsAdapterManager",
    "FbsAdapterProtocol",
    "FbsValidation",
    "headless_exit_code",
    "LogValidation",
    "LogValidator",
    "FebioRuntimeDiagnostic",
    "OutputExpectation",
    "OutputFreshnessError",
    "ProcessState",
    "RunState",
    "SolverClassification",
    "SolverConfigurationError",
    "SolverLaunchError",
    "SolverLaunchSpec",
    "SolverOwnershipError",
    "SolverRunResult",
    "SolverState",
    "SolverSupervisor",
    "RuntimeDiagnostic",
    "RuntimeIdentity",
    "RuntimeProbeDiagnostic",
    "RuntimeProbeError",
    "probe_febio",
    "probe_runtime",
    "run_headless_febio",
    "validate_log",
    "validate_requested_fields",
    "validate_solver_log",
]
