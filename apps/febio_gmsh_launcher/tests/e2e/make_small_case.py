from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gmsh


def make_small_case(folder: Path, febio_exe: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    step = folder / "small.step"
    gmsh.initialize()
    try:
        gmsh.model.add("small")
        gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.synchronize()
        gmsh.write(str(step))
    finally:
        gmsh.finalize()
    feb = folder / "small.feb"
    feb.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0">
  <Module type="solid"/>
  <Material>
    <material id="1" name="mat" type="neo-Hookean">
      <E>1000</E><v>0.3</v>
    </material>
  </Material>
  <Mesh>
    <Nodes name="Part1">
      <node id="1">0,0,0</node><node id="2">1,0,0</node>
      <node id="3">0,1,0</node><node id="4">1,1,0</node>
      <node id="5">0,0,1</node><node id="6">1,0,1</node>
      <node id="7">0,1,1</node><node id="8">1,1,1</node>
    </Nodes>
    <Elements type="tet4" name="Part1"><elem id="1">1,2,3,5</elem></Elements>
    <Surface name="Bottom">
      <tri3 id="1">1,3,2</tri3><tri3 id="2">2,3,4</tri3>
    </Surface>
    <Surface name="Top">
      <tri3 id="1">5,6,7</tri3><tri3 id="2">6,8,7</tri3>
    </Surface>
  </Mesh>
  <MeshDomains><SolidDomain name="Part1" mat="mat"/></MeshDomains>
  <Boundary>
    <bc name="fix" node_set="@surface:Bottom" type="zero displacement">
      <x_dof>1</x_dof><y_dof>1</y_dof><z_dof>1</z_dof>
    </bc>
  </Boundary>
  <Step>
    <step id="1" name="Step1">
      <Control>
        <analysis>STATIC</analysis><time_steps>1</time_steps><step_size>1</step_size>
        <solver type="solid"/>
      </Control>
      <Boundary>
        <bc name="push" surface="Top" type="normal displacement">
          <scale>-0.01</scale><surface_hint>0</surface_hint><relative>0</relative>
        </bc>
      </Boundary>
    </step>
  </Step>
  <Output><plotfile type="febio"><var type="displacement"/></plotfile></Output>
</febio_spec>
""",
        encoding="utf-8",
    )
    config = folder / "small.gmsh-run.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_stem": "small",
                "step_path": str(step),
                "step_sha256": hashlib.sha256(step.read_bytes()).hexdigest(),
                "gmsh": {
                    "target_size_mm": 0.45,
                    "min_size_mm": 0.2,
                    "algorithm3d": 10,
                    "curvature_elements_per_2pi": 20,
                },
                "quality": {
                    "min_det_j": 0,
                    "min_alpha": 0,
                    "max_corrected_fraction": 1,
                    "max_displacement_mm": 1,
                },
                "febio_exe": str(febio_exe),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return feb
