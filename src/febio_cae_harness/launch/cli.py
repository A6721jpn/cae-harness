"""Headless launcher command line entry point.

The command only plans or executes the canonical ``latest-development``
launcher.  It never performs startup updates, network checks, or version-folder
selection.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from febio_cae_harness import __version__

from .deployment import DeploymentError, DeploymentLayout
from .planner import LaunchError, launch_cli, plan_cli_launch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="febio-cae-launch")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    plan = subparsers.add_parser("plan", help="print the fixed headless launch plan")
    plan.add_argument("--local-app-data", type=Path)
    plan.add_argument("--json", action="store_true", dest="as_json")
    plan.add_argument("arguments", nargs=argparse.REMAINDER)

    launch = subparsers.add_parser("launch", help="execute the fixed headless launcher")
    launch.add_argument("--local-app-data", type=Path)
    launch.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        if arguments.command == "plan":
            layout = (
                DeploymentLayout.from_environment()
                if arguments.local_app_data is None
                else DeploymentLayout.from_local_app_data(arguments.local_app_data)
            )
            plan = plan_cli_launch(layout, arguments.arguments)
            payload = plan.to_dict()
            if arguments.as_json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(" ".join(plan.argv))
            return 0
        if arguments.command == "launch":
            if arguments.local_app_data is None:
                completed = launch_cli(arguments.arguments)
            else:
                completed = launch_cli(arguments.arguments, local_app_data=arguments.local_app_data)
            return completed.returncode
        build_parser().print_help()
        return 0
    except (DeploymentError, LaunchError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


__all__ = ["build_parser", "main"]
