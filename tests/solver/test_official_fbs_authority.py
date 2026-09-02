from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from febio_cae_harness.solver import FbsAdapterManager, validate_requested_fields


class _FiniteFixtureAdapter:
    def read_fields(self, path: Path, fields: Sequence[str]) -> dict[str, object]:
        assert path.is_file()
        return {field: [0.0, 1.0] for field in fields}


def test_direct_manager_cannot_claim_official_provenance(tmp_path: Path) -> None:
    xplt = tmp_path / "result.xplt"
    xplt.write_bytes(b"synthetic-xplt")

    manager = FbsAdapterManager(
        _FiniteFixtureAdapter(),
        "synthetic-fixture",
        tmp_path,
    )
    with manager:
        authority = manager.issue_authority()
        validation = validate_requested_fields(authority, xplt, ("stress",))

        assert validation.valid
        assert validation.official is False
        assert validation.provenance == "synthetic-unverified"


def test_runtime_labels_and_adapter_markers_cannot_issue_official_validation(
    tmp_path: Path,
) -> None:
    class _MarkerAdapter(_FiniteFixtureAdapter):
        official = True
        provenance = "official"

    xplt = tmp_path / "result.xplt"
    xplt.write_bytes(b"synthetic-xplt")
    manager = FbsAdapterManager(
        _MarkerAdapter(),
        "official-fbs-3.1-cp313:forged",
        tmp_path,
    )
    with manager:
        authority = manager.issue_authority()
        validation = validate_requested_fields(authority, xplt, ("stress",))

        assert validation.valid
        assert validation.official is False
        assert validation.provenance == "synthetic-unverified"
