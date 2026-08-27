from pathlib import Path

import pytest

from febio_cae_harness.model import EvidenceProvenance, IntentImpact, OriginalModel
from febio_cae_harness.model.derived import FebPatch, write_derived_feb

FEB = b"""<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0">
  <Control><time_steps>4</time_steps></Control>
  <Module type="solid"/>
  <Material><material id="1" name="synthetic"/></Material>
</febio_spec>
"""
TIME = "/febio_spec/Control[1]/time_steps[1]"
MODULE = "/febio_spec/Module[1]"
EVIDENCE = EvidenceProvenance("synthetic-intent", "intent.json", authoritative=True)


def patch(
    target: str,
    mode: str,
    value: str | int | float | bool,
    *,
    attribute_name: str | None = None,
    provenance: tuple[EvidenceProvenance, ...] = (EVIDENCE,),
    intent_impact: IntentImpact = IntentImpact.INTENT_PRESERVING,
) -> FebPatch:
    return FebPatch(
        target=target,
        mode=mode,
        attribute_name=attribute_name,
        value=value,
        reason="synthetic explicit patch",
        provenance=provenance,
        intent_impact=intent_impact,
    )


def test_writes_detached_text_derived_and_receipt(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    attempt = tmp_path / "attempt"
    destination = attempt / "derived.feb"
    source.write_bytes(FEB)
    attempt.mkdir()
    original = OriginalModel.from_path(source)

    receipt = write_derived_feb(original, (patch(TIME, "TEXT", 8),), destination, attempt)

    assert receipt.original_sha256 == original.sha256
    assert receipt.destination == destination
    assert receipt.applied_patch_targets == (TIME,)
    assert receipt.derived_sha256 != receipt.original_sha256
    assert b"<time_steps>8</time_steps>" in destination.read_bytes()
    assert source.read_bytes() == FEB
    assert original.verify()


def test_writes_attribute_and_preserves_non_authoritative_evidence(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    attempt = tmp_path / "attempt"
    source.write_bytes(FEB)
    attempt.mkdir()
    patch_record = patch(
        MODULE,
        "ATTRIBUTE",
        "hex8",
        attribute_name="type",
        provenance=(EvidenceProvenance("synthetic-note"),),
    )

    write_derived_feb(OriginalModel.from_path(source), (patch_record,), attempt / "x.feb", attempt)

    assert b'type="hex8"' in (attempt / "x.feb").read_bytes()


@pytest.mark.parametrize(
    ("record", "payload"),
    [
        (patch(TIME, "TEXT", float("nan")), FEB),
        (patch(TIME, "TEXT", 1, intent_impact=IntentImpact.INTENT_CHANGING), FEB),
        (patch(TIME, "TEXT", 1, provenance=()), FEB),
        (
            patch(
                TIME,
                "TEXT",
                1,
                intent_impact=IntentImpact.INTENT_SENSITIVE,
                provenance=(EvidenceProvenance("synthetic-note"),),
            ),
            FEB,
        ),
        (patch("/febio_spec/Missing[1]", "TEXT", 1), FEB),
        (patch(MODULE, "ATTRIBUTE", "x", attribute_name="missing"), FEB),
        (patch(TIME, "ATTRIBUTE", "x"), FEB),
    ],
)
def test_rejects_invalid_patch_without_destination(
    tmp_path: Path, record: FebPatch, payload: bytes
) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    with pytest.raises(ValueError):
        write_derived_feb(
            OriginalModel.from_bytes(payload), (record,), attempt / "derived.feb", attempt
        )
    assert not (attempt / "derived.feb").exists()


def test_rejects_unsafe_destination_and_dtd(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    destination = attempt / "derived.feb"
    destination.write_bytes(b"existing")
    original = OriginalModel.from_bytes(FEB)

    with pytest.raises(FileExistsError):
        write_derived_feb(original, (patch(TIME, "TEXT", 2),), destination, attempt)
    with pytest.raises(ValueError):
        write_derived_feb(original, (patch(TIME, "TEXT", 2),), tmp_path / "out.feb", attempt)

    dtd = b'<!DOCTYPE febio_spec [<!ENTITY x "bad">]><febio_spec>&x;</febio_spec>'
    with pytest.raises(ValueError):
        write_derived_feb(
            original.from_bytes(dtd), (patch(TIME, "TEXT", 2),), attempt / "d.feb", attempt
        )
    assert not (attempt / "d.feb").exists()
