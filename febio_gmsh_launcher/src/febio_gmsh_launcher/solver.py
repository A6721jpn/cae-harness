from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class SolverResult:
    exit_code: int
    command: tuple[str, ...]


def build_solver_command(
    executable: Path,
    input_feb: Path,
    extra_args: tuple[str, ...],
    *,
    argument_order: str = "suffix",
) -> list[str]:
    input_arguments = ["-i", input_feb.name]
    if argument_order == "prefix":
        return [str(executable), *extra_args, *input_arguments]
    return [str(executable), *input_arguments, *extra_args]


def run_solver(
    executable: Path,
    input_feb: Path,
    extra_args: tuple[str, ...],
    on_line: Callable[[str], None],
    cancel_event: threading.Event | None = None,
    *,
    argument_order: str = "suffix",
) -> SolverResult:
    command = build_solver_command(
        executable, input_feb, extra_args, argument_order=argument_order
    )
    process = subprocess.Popen(
        command,
        cwd=input_feb.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    assert process.stdout is not None
    for line in process.stdout:
        on_line(line.rstrip())
        if cancel_event is not None and cancel_event.is_set():
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            break
    return SolverResult(process.wait(), tuple(command))
