"""Headless FEBio solver and synthetic FBS authority boundary."""

from .fbs import (
    FbsAdapterAuthority,
    FbsAdapterManager,
    FbsAdapterProtocol,
    FbsValidation,
    validate_requested_fields,
)
from .log import LogValidation, LogValidator, validate_log, validate_solver_log
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
    "validate_log",
    "validate_requested_fields",
    "validate_solver_log",
]
