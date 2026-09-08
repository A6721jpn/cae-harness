# P2 concrete placed-selection bridge

Base: `0f2bade8eeafc63f3c4bbad7bb2d54d85805a376`.
Production: `fea85fd`, import/type-only follow-up: `7a395cc`.
REMOTE_CONFIGURED: authorized origin remains `https://github.com/A6721jpn/cae-harness.git`; integration is PM-owned V2.

Concrete adapter API, without changing the public GeometryPort or records:
`resolve_placed_selection(source: SourceAssetContent, geometry: GeometryIntent, rigid_tool: RigidToolIntent, selection: SelectionRef) -> ResolutionSnapshot`.
It validates configured source bytes/identity, geometry source/inspection/digest/unit and solid body, rejects overlapping/foreign body IDs, and routes to the existing placed-part or generated-box report and resolver. Poses are supplied explicitly; this bridge does not perform gap adjustment. Curved tools remain unsupported here.

Changed implementation/test files: `src/febio_cae/adapters/geometry/adapter.py`, `tests/component/geometry/test_placed_selection.py`.

## Fresh local evidence

- RED at `83d000e`: `python -m pytest tests/component/geometry/test_placed_selection.py --basetemp=.local/verification/P2-placed-red-03 -q`: 9 failed, exit 1. Failures are missing bridge calls, not collection/environment failures. Earlier malformed overlap setup was corrected before this recorded RED.
- Focused GREEN: `python -m pytest tests/component/geometry/test_placed_selection.py --basetemp=.local/verification/P2-placed-green-01 -q`: 9 passed, exit 0.
- Full at `7a395cc`: `python -m pytest --basetemp=.local/verification/P2-placed-full-03 -q`: 1129 passed in 22.00 seconds, exit 0.
- `python -m ruff format --check .`: 125 files formatted, exit 0.
- `python -m ruff check .`: exit 0.
- `python -m mypy src tests`: 86 files, exit 0.
- `python -m build --outdir .local/verification/P2-placed-dist-02`: wheel and sdist built, exit 0.
- Fresh `python -m venv .local/verification/P2-placed-installed-01`, offline `python -m pip install --no-index --no-deps .local/verification/P2-placed-dist-02/febio_cae-0.1.0-py3-none-any.whl`, installed `febio-cae.exe --version`: version 0.1.0. No editable install.
- `python scripts/scan_cae_data.py --root .`: no CAE diagnostics in the source candidate; repeat after this evidence-only report.

## Native demonstration boundary

Ignored generated inputs and process records are not product tests or checked-in assets. Actual Gmsh backend attempt 01 generated 800 nodes and 383 Tet10 elements from a newly generated STEP cube; SI import volume approximately 1e-6 m3, native process exit 0 and reaped, single-CPU affinity and 90-second timeout. This was backend-only, not canonical-adapter E2E.

Authorized whole-adapter attempt 02 exited 1 before mesh generation: the demonstration script attempted to encode SelectionRef with the top-level codec, which does not register that record. The failure was preserved; it is a harness serialization mistake, not a product codec defect. No failed or unexecuted canonical/native stage is reported as passing. A further native run requires PM's finite-attempt decision.

Unverified: canonical native mesh handoff, real FEBio output, official FBS, Studio, analytical/contact accuracy, real-model and BottomFrame E2E. Next: PM review/integration of the clean bridge and finite decision on the stopped demonstration. Synthetic fixture profile identifiers in the geometry carrier are not registered solver/output/quality authority; A/B must supply their supported registered mappings before solving.
