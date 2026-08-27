"""Headless FEBio solver and synthetic FBS authority boundary."""

from .fbs import (
    FbsAdapterAuthority,
    FbsAdapterManager,
    FbsAdapterProtocol,
    FbsValidation,
    validate_requested_fields,
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
    "FbsAdapterAuthority",
    "FbsAdapterManager",
    "FbsAdapterProtocol",
    "FbsValidation",
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
    "validate_log",
    "validate_requested_fields",
    "validate_solver_log",
]
