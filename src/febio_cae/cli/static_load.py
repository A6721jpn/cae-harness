"""Public explicit static-load operations; never launches a GUI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from febio_cae.application.static_load import prepare, run, status
from febio_cae.domain import PortError, PortErrorCategory


def run_static_load(arguments: argparse.Namespace) -> int:
    try:
        root = Path(arguments.root)
        if arguments.static_action == "prepare":
            result = prepare(root, Path(arguments.cad), Path(arguments.request))
        elif arguments.static_action == "run":
            result = run(root, Path(arguments.solver))
        else:
            result = status(root)
        print(
            json.dumps(
                {"schema_version": "1", **result},
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
        return 0
    except (OSError, ValueError, TypeError, LookupError, RuntimeError) as error:
        code = (
            {
                PortErrorCategory.INVALID_INPUT: 2,
                PortErrorCategory.NEEDS_PHYSICAL_INPUT: 3,
                PortErrorCategory.UNSUPPORTED_CAPABILITY: 4,
                PortErrorCategory.ENVIRONMENT: 4,
                PortErrorCategory.EXECUTION: 5,
                PortErrorCategory.INTEGRITY: 6,
                PortErrorCategory.QUALITY: 6,
                PortErrorCategory.CANCELLED: 7,
                PortErrorCategory.CONFLICT: 8,
            }[error.category]
            if isinstance(error, PortError)
            else (2 if arguments.static_action == "prepare" else 6)
            if isinstance(error, (ValueError, TypeError, LookupError))
            else 4
        )
        print(
            json.dumps(
                {"schema_version": "1", "status": "FAILED", "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return code
