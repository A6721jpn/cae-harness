from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.model import (
    ASK_AND_BLOCK,
    CompletenessResult,
    ConditionEvidence,
    ContactIntent,
    DerivedModelPlan,
    EvaluationIntent,
    EvidenceProvenance,
    MissingConditionFact,
    ModelChange,
    OriginalModel,
    PreflightSeverity,
    ROIIntent,
    StepUnitFact,
    assess_completeness,
    inspect_feb_xml,
    inspect_step,
    issue_completeness_authority,
    run_preflight,
)
from febio_cae_harness.workspace import ValidatedCaseWorkspace

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


def test_feb_duplicate_ids_are_scoped_to_definition_kind() -> None:
    distinct_kinds = b"""
    <febio_spec version='4.0'>
      <Material><material id='1' name='synthetic-material'/></Material>
      <Mesh>
        <Nodes><node id='1'>0,0,0</node></Nodes>
        <Elements><elem id='1'>1</elem></Elements>
      </Mesh>
    </febio_spec>
    """
    distinct_inspection = inspect_feb_xml(distinct_kinds)
    assert distinct_inspection.duplicate_identifiers == ()
    assert distinct_inspection.reference_closure.duplicate_keys == ()

    duplicate_material = b"""
    <febio_spec version='4.0'>
      <Material>
        <material id='1' name='first'/>
        <material id='1' name='second'/>
      </Material>
    </febio_spec>
    """
    duplicate_inspection = inspect_feb_xml(duplicate_material)
    assert duplicate_inspection.duplicate_identifiers == ("1",)
    assert duplicate_inspection.reference_closure.duplicate_keys == (("material", "1"),)


def test_feb_domain_namespaces_and_named_material_references_are_structural() -> None:
    domain_fixture = b"""<febio_spec version='4.0'>
<Material><material id='1' name='synthetic-material'/></Material>
<Mesh><Elements name='body'/></Mesh>
<MeshDomains><SolidDomain name='body' mat='synthetic-material' elem_set='body'/></MeshDomains>
</febio_spec>"""
    inspection = inspect_feb_xml(domain_fixture)

    assert inspection.duplicate_keys == ()
    material_reference = next(
        reference for reference in inspection.references if reference.attribute == "mat"
    )
    assert material_reference.value == "synthetic-material"
    assert material_reference.resolved is True
    assert inspection.unresolved_references == ()

    duplicate_domain = domain_fixture.replace(
        b"<SolidDomain name='body' mat='synthetic-material' elem_set='body'/>",
        b"<SolidDomain name='body' mat='synthetic-material' elem_set='body'/>"
        b"<SolidDomain name='body' mat='synthetic-material' elem_set='body'/>",
    )
    duplicate_inspection = inspect_feb_xml(duplicate_domain)
    assert duplicate_inspection.duplicate_keys == (("domain", "body"),)


def test_completeness_reports_only_explicit_authoritative_conditions(tmp_path: Path) -> None:
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("case")
    store = EvidenceStore(
        case,
        IntentContract(
            units={"length": "mm"},
            material="neo-Hookean",
            condition_sources={
                "units": {"source": "synthetic-intent", "location": "intent.json"},
                "material": {"source": "synthetic-intent", "location": "intent.json"},
            },
        ),
    )
    authority = issue_completeness_authority(
        store.issue_intent_snapshot(),
        ("units", "material", "loads"),
    )
    result = assess_completeness(authority)

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


def test_preflight_rejects_forged_completeness_before_units_resolution() -> None:
    forged = CompletenessResult(
        required=("units",),
        resolved=("units",),
        missing=(),
        unresolved=(),
        state="BOUND",
    )
    no_units = inspect_step(
        b"ISO-10303-21;HEADER;ENDSEC;DATA;"
        b"#1 = CARTESIAN_POINT('',(0.,0.,0.));ENDSEC;"
        b"END-ISO-10303-21;"
    )

    preflight = run_preflight(step=no_units, completeness=forged)
    codes = {diagnostic.code for diagnostic in preflight.diagnostics}

    assert preflight.ready is False
    assert "INVALID_COMPLETENESS_AUTHORITY" in codes
    assert "UNRESOLVED_UNITS" in codes
    assert "MISSING_PHYSICAL_CONDITION" not in codes
    assert "UNRESOLVED_PHYSICAL_CONDITION" not in codes


def test_preflight_does_not_project_forged_completeness_questions() -> None:
    forged = CompletenessResult(
        required=("loads",),
        resolved=(),
        missing=(MissingConditionFact("loads", "caller supplied a fact"),),
        unresolved=(),
        state=ASK_AND_BLOCK,
    )

    preflight = run_preflight(completeness=forged)
    codes = {diagnostic.code for diagnostic in preflight.diagnostics}

    assert preflight.ready is False
    assert "INVALID_COMPLETENESS_AUTHORITY" in codes
    assert "MISSING_PHYSICAL_CONDITION" not in codes
    assert "INCOMPLETE_CONDITION_RESULT" not in codes
    assert "UNRESOLVED_PHYSICAL_CONDITION" not in codes
    assert preflight.to_dict()["completeness"] is None


def test_preflight_preserves_live_completeness_ready_and_missing_behavior(
    tmp_path: Path,
) -> None:
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("case")
    store = EvidenceStore(
        case,
        IntentContract(
            units={"length": "mm"},
            material="neo-Hookean",
            condition_sources={
                "units": {"source": "synthetic-intent", "location": "intent.json"},
                "material": {"source": "synthetic-intent", "location": "intent.json"},
            },
        ),
    )

    complete = assess_completeness(
        issue_completeness_authority(store.issue_intent_snapshot(), ("units", "material"))
    )
    ready = run_preflight(completeness=complete)
    assert ready.ready is True
    assert ready.diagnostics == ()

    incomplete = assess_completeness(
        issue_completeness_authority(store.issue_intent_snapshot(), ("units", "loads"))
    )
    blocked = run_preflight(completeness=incomplete)
    assert blocked.ready is False
    assert any(item.code == "MISSING_PHYSICAL_CONDITION" for item in blocked.diagnostics)
    assert not any(item.code == "INVALID_COMPLETENESS_AUTHORITY" for item in blocked.diagnostics)
