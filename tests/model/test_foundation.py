from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from febio_cae_harness.model import (
    ASK_AND_BLOCK,
    ConditionEvidence,
    ContactIntent,
    DerivedModelPlan,
    EvaluationIntent,
    EvidenceProvenance,
    ModelChange,
    OriginalModel,
    PreflightSeverity,
    ROIIntent,
    StepUnitFact,
    assess_completeness,
    inspect_feb_xml,
    inspect_step,
    run_preflight,
)

FEB_FIXTURE = b"""<?xml version='1.0' encoding='UTF-8'?>
<febio_spec version='4.0'>
  <Module type='solid'/>
  <Material>
    <material id='1' name='synthetic-material' type='neo-Hookean'/>
  </Material>
  <Mesh>
    <Nodes name='all'>
      <node id='1'>0,0,0</node>
      <node id='2'>1,0,0</node>
      <node id='3'>0,1,0</node>
      <node id='4'>0,0,1</node>
    </Nodes>
    <Elements type='tet4' name='body' mat='1'>
      <elem id='1'>1,2,3,4</elem>
    </Elements>
    <NodeSet name='fixed'>1,2</NodeSet>
  </Mesh>
  <Boundary>
    <fix bc='x,y,z' node_set='fixed'/>
  </Boundary>
  <Loads>
    <nodal_load bc='z' node_set='not-authoritatively-defined'>1</nodal_load>
  </Loads>
</febio_spec>
"""


STEP_FIXTURE = b"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('synthetic fixture'),'2;1');
FILE_NAME('fixture.step','2026-08-27',('synthetic'),('synthetic'),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN_CC2'));
ENDSEC;
DATA;
#10 = SI_UNIT(.MILLI., .METRE.);
#11 = ( NAMED_UNIT(*) SI_UNIT(.RADIAN.) PLANE_ANGLE_UNIT() );
#12 = ( NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) LENGTH_UNIT() );
ENDSEC;
END-ISO-10303-21;
"""


def test_feb_inventory_and_reference_closure_are_structural_and_read_only() -> None:
    inspection = inspect_feb_xml(FEB_FIXTURE, source_name="synthetic.feb")

    assert inspection.root_tag == "febio_spec"
    assert inspection.root_attributes["version"] == "4.0"
    assert inspection.tag_counts["node"] == 4
    assert inspection.tag_counts["elem"] == 1
    assert inspection.reference_closure.is_closed is False
    assert any(
        reference.value == "not-authoritatively-defined"
        for reference in inspection.reference_closure.unresolved
    )
    assert inspection.source_name == "synthetic.feb"
    assert isinstance(inspection.root_attributes, MappingProxyType)

    with pytest.raises(TypeError):
        inspection.root_attributes["version"] = "tampered"  # type: ignore[index]


def test_completeness_reports_only_explicit_authoritative_conditions() -> None:
    authoritative = EvidenceProvenance(
        source="synthetic-intent",
        location="intent.json",
        authoritative=True,
    )
    result = assess_completeness(
        required_conditions=("units", "material", "loads"),
        evidence={
            "units": ConditionEvidence("units", {"length": "mm"}, (authoritative,)),
            "material": ConditionEvidence("material", "neo-Hookean", (authoritative,)),
            # A geometry string is intentionally not physical-condition evidence.
            "geometry": ConditionEvidence("geometry", "tetrahedron", (authoritative,)),
        },
    )

    assert result.complete is False
    assert result.state == ASK_AND_BLOCK
    assert [fact.condition for fact in result.missing] == ["loads"]
    assert result.missing[0].action == ASK_AND_BLOCK
    assert result.missing[0].authoritative is True
    assert result.unresolved[0].field_name == "loads"
    assert result.unresolved[0].value is None


def test_original_model_and_derived_plan_are_immutable_and_digest_bound(tmp_path: Path) -> None:
    source = tmp_path / "original.feb"
    source.write_bytes(FEB_FIXTURE)
    original = OriginalModel.from_path(source)
    plan = DerivedModelPlan(
        original=original,
        destination=Path("90_Temporary/attempt-1/derived.feb"),
        changes=(
            ModelChange(
                target="Control/time_steps",
                value=20,
                reason="synthetic structural change",
                evidence=(
                    EvidenceProvenance(
                        source="synthetic-intent",
                        location="intent.json",
                        authoritative=True,
                    ),
                ),
            ),
        ),
    )

    assert plan.original.sha256 == original.sha256
    assert plan.original.verify() is True
    assert plan.destination is not None
    assert plan.destination.as_posix() == "90_Temporary/attempt-1/derived.feb"
    assert plan.is_intent_preserving is True
    assert source.read_bytes() == FEB_FIXTURE
    with pytest.raises(FrozenInstanceError):
        plan.destination = Path("mutated.feb")  # type: ignore[misc]


def test_step_inspection_preserves_explicit_unit_provenance_without_guessing() -> None:
    inspection = inspect_step(STEP_FIXTURE, source_name="synthetic.step")

    assert inspection.schema_identifiers == ("AUTOMOTIVE_DESIGN_CC2",)
    assert inspection.entity_count == 3
    assert inspection.units
    assert any(
        isinstance(unit, StepUnitFact) and unit.prefix == "milli" and unit.symbol == "metre"
        for unit in inspection.units
    )
    assert all(unit.provenance.authoritative for unit in inspection.units)
    assert inspection.unit_provenance == tuple(unit.provenance for unit in inspection.units)

    no_units = inspect_step(
        b"ISO-10303-21;HEADER;ENDSEC;DATA;"
        b"#1 = CARTESIAN_POINT('',(0.,0.,0.));ENDSEC;"
        b"END-ISO-10303-21;"
    )
    assert no_units.units == ()
    assert no_units.units_explicit is False


def test_intent_descriptors_and_preflight_diagnostics_remain_typed() -> None:
    evidence = EvidenceProvenance("synthetic-intent", "intent.json", authoritative=True)
    contact = ContactIntent(
        name="fixture-contact",
        master="master-surface",
        slave="slave-surface",
        evidence=(evidence,),
    )
    roi = ROIIntent(name="tip", selector="explicit-node-set", fields=("displacement",))
    evaluation = EvaluationIntent(
        name="tip-displacement",
        quantities=("displacement",),
        location="tip",
    )

    assert contact.master == "master-surface"
    assert roi.fields == ("displacement",)
    assert evaluation.quantities == ("displacement",)
    assert contact.evidence[0].authoritative is True

    incomplete = assess_completeness(
        required_conditions=("contact",),
        evidence={"contact": ConditionEvidence("contact", contact, (evidence,))},
    )
    preflight = run_preflight(
        feb=inspect_feb_xml(FEB_FIXTURE),
        completeness=incomplete,
    )
    assert preflight.ready is False
    assert preflight.blocking_diagnostics
    assert any(d.severity is PreflightSeverity.BLOCKING for d in preflight.diagnostics)
    assert any(d.code == "MISSING_REFERENCE" for d in preflight.diagnostics)
