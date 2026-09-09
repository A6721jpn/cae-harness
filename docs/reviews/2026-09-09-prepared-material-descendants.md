# Prepared material descendants: code stage

Status: CODE_REVIEW_PENDING; mandatory final acceptance gates PENDING.

Base: `9c7095c4899f6b83c4c68fa38ae73688160e3e5c` (accepted/pushed V2).
Authority update: `11e14c6d21e6ce61dd904eb0506605095b2ce265`.
Test-first commit: `14feceb09570f7a92bccd386fdfce1ae5d645d9c`.

The public case patch, validate/freeze and run-demo preflight path now reuses an
immutable producer-owned PREPARED root for registered explicit Young modulus
changes. Each ancestor's registered bytes, parent spec digest, mesh profile and
source evidence are verified. Restoring only E and its evidence must reproduce
the parent's entire spec; other top-level evidence must remain unchanged.
Original root adoption is checked before existing adoption binds the child.
Root receipt/mesh publication and current draft admission remain unchanged.

Changed product files: storage/preparation.py, application/service.py (prepared
inspection lookup), application/_demo.py (prepared inspection lookup).
Coverage: tests/component/application/test_planar_preparation.py; both authority
documents describe this bounded behavior. No native execution or remesh occurs.

## Focused evidence

- RED: `python -m pytest tests/component/application/test_planar_preparation.py -k prepared_material_child --basetemp .local/v/pc1r`: 1 failed, 8 deselected, exit 1; registered child preflight required the root-only receipt.
- GREEN: `python -m pytest tests/component/application/test_planar_preparation.py tests/component/application/test_planar_edit_validation.py --basetemp .local/v/pc1g`: 10 passed, exit 0.
- Final GREEN after extending the storage snapshot lease: same command with `--basetemp .local/v/pc1g2`; 10 passed, exit 0.
- `python -m ruff format --check .`: 220 files, exit 0.
- `python -m ruff check .`: exit 0 after preserving ValueError for a valid but unsupported material value (initial TRY004, exit 1).
- `python -m mypy src tests`: 172 source files, exit 0.
- `python scripts/scan_cae_data.py --root .`: initial 222 tracked files, exit 0; final staged report scan recorded in the local handoff.

Exact raw logs, argv, Python 3.12.10 executable, SHA and dirty state are in ignored
`.local/verification/pc1-*.json` and `.log`; final clean SHA and applicability are
in `.local/verification/prepared-material-descendants-code-handoff.md`.

## Remaining required work

Independent exact-commit Medium CODE review is next. Full `python -m pytest`,
`python -m build`, and a fresh virtual environment wheel installation followed
by `febio-cae --version` are PENDING for the subsequent acceptance stage after
review fixes settle. Native Gmsh/OCCT/FEBio/Studio, live LLM, required real E2Es
and final BottomFrame E2E remain unverified by this synthetic code stage.
REMOTE_CONFIGURED: https://github.com/A6721jpn/cae-harness.git; PM integration
branch V2; worker has not merged or pushed. This is not project completion.
