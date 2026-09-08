"""Ordinary registered comparison CLI, with explicit input/quality refusal."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from febio_cae.application._comparison import FIXED_CONDITIONS
from febio_cae.application.service import RegisteredCaseService, ServiceConflictError
from febio_cae.domain.codec import decode_record
from febio_cae.domain.comparison import ComparisonSpec
from febio_cae.domain.ports import PortError, PortErrorCategory
from febio_cae.storage.registry import StorageConflictError, StorageIntegrityError

from .case import _error_payload, _print, _read_json

COMPARISON_HELP = """ComparisonSpec schema_version '1'; registered direct material-edit child only.
intended_changes: ['material.youngs_modulus']
Both axes use unit 'm', the same compression interval, and interpolation 'linear':
  axis_id: tool_compression.force_z
    measure_id: contact_force.world_z; aggregation_id: identity; roi_id: tool body ID
  axis_id: tool_compression.part_peak_abs_displacement_z
    measure_id: displacement.world_z; aggregation_id: peak_abs; roi_id: part body ID
The axis is minus World-z tool-position change, not contact-onset depth.
Result units: force N, displacement m. Aggregate saved states, then interpolate
on their union within the shared observed interval. No extrapolation or ranking.
Only whole-tool force and all-part-node displacement are supported.
fixed_conditions must contain all of: """ + ", ".join(FIXED_CONDITIONS)


def run_compare(arguments: Namespace) -> int:
    try:
        request = decode_record(json.dumps(_read_json(Path(arguments.spec))), ComparisonSpec)
        result = RegisteredCaseService(state_dir=arguments.state_dir).compare_case(
            arguments.case_id,
            request,
            baseline_run_id=arguments.baseline_run,
            candidate_run_id=arguments.candidate_run,
        )
        _print(result) if arguments.json else print(result["status"], result["record_path"])
        return 0
    except (
        OSError,
        ValueError,
        PortError,
        ServiceConflictError,
        StorageConflictError,
        StorageIntegrityError,
    ) as error:
        payload, code = _error_payload(error)
        if isinstance(error, StorageIntegrityError) or (
            isinstance(error, PortError)
            and error.category in {PortErrorCategory.INTEGRITY, PortErrorCategory.QUALITY}
        ):
            code = 6
            payload["status"] = "INELIGIBLE"
        _print(payload) if arguments.json else print(str(error))
        return code
