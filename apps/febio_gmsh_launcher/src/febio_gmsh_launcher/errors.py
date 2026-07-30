from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    CONFIG_ERROR = 20
    PREFLIGHT_CANCEL = 21
    TRANSFER_ERROR = 30
    QUALITY_ERROR = 40
    SOLVER_ERROR = 50
    INTERNAL_ERROR = 70


class LauncherError(RuntimeError):
    def __init__(self, message: str, exit_code: ExitCode) -> None:
        super().__init__(message)
        self.exit_code = exit_code
