from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .errors import ExitCode, LauncherError


@dataclass(frozen=True)
class LaunchRequest:
    input_feb: Path
    config_path: Path | None
    non_interactive: bool
    febio_args: tuple[str, ...]


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise LauncherError(message, ExitCode.CONFIG_ERROR)


def parse_febio_args(argv: Sequence[str]) -> LaunchRequest:
    parser = _ArgumentParser(add_help=True, allow_abbrev=False)
    parser.add_argument("-i", dest="input_feb", required=True)
    parser.add_argument("--gmsh-config", dest="config_path")
    parser.add_argument("--non-interactive", action="store_true")
    known, passthrough = parser.parse_known_args(list(argv))
    return LaunchRequest(
        input_feb=Path(known.input_feb),
        config_path=Path(known.config_path) if known.config_path else None,
        non_interactive=known.non_interactive,
        febio_args=tuple(passthrough),
    )


def run_pipeline(request: LaunchRequest) -> int:
    from .orchestrator import run_pipeline as orchestrate

    return int(orchestrate(request))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        request = parse_febio_args(sys.argv[1:] if argv is None else argv)
        return run_pipeline(request)
    except LauncherError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except Exception as exc:
        print(f"INTERNAL ERROR: {exc}", file=sys.stderr)
        return int(ExitCode.INTERNAL_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
