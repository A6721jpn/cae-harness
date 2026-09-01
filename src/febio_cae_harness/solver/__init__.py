"""Headless FEBio solver and synthetic FBS authority boundary."""

from .execution import record_execution_output_artifacts
from .fbs import (
    FbsAdapterAuthority,
    FbsAdapterManager,
    FbsAdapterProtocol,
    FbsValidation,
    validate_requested_fields,
)
from .headless import (
    HeadlessConfigurationError,
    HeadlessRunDiagnostic,
    headless_exit_code,
    reconnect_headless_febio,
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
    validate_runtime_diagnostic,
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
    SolverLaunchCapability,
    SolverLaunchError,
    SolverLaunchSpec,
    SolverOwnershipError,
    SolverRunResult,
    SolverState,
)

__all__ = [
    "FailureClassification",
    "HeadlessConfigurationError",
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
    "SolverLaunchCapability",
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
    "record_execution_output_artifacts",
    "reconnect_headless_febio",
    "run_headless_febio",
    "validate_log",
    "validate_requested_fields",
    "validate_runtime_diagnostic",
    "validate_solver_log",
]
