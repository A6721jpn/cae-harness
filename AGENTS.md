# FEBio CAE Harness Development Contract

## Authority and scope

- The only product authorities are:
  - `docs/specs/2026-08-27-febio-llm-cae-harness-design-v2.md`
  - `docs/plans/2026-08-27-febio-cae-harness-greenfield-plan.md`
- Work only inside this repository. Do not inspect, copy, merge, or cherry-pick code,
  tests, schemas, skills, releases, branches, or worktrees from another repository.
- This is a greenfield Python 3.12 project. Do not introduce Codex Desktop or Orca as a
  product runtime dependency. The initial product is a headless CLI; do not add a GUI.
- The authorized remote is `https://github.com/A6721jpn/cae-harness.git` and the
  integration branch is `V2`. Report this state as `REMOTE_CONFIGURED`.
- V2 has an independent history containing only the new V2 design and implementation.
  Do not reuse or inspect legacy CAE Harness code, tests, schemas, releases, branches,
  worktrees, or case assets. Limit fetches and integration to V2 and its development branches.
- The development PM is `gpt-6-astra` / `medium`, implementation workers are independent
  `gpt-5.6-luna` / `max` tasks created and continued through the Luna Spawn skill, and
  the code reviewer is an independent `gpt-6-astra` / `medium` task.
- Message model settings belong to the destination task. Use Luna/max only when
  creating or continuing a worker; reports to the PM preserve Astra/medium.
- The PM owns scope and integration. Workers own assigned implementation files. The
  reviewer inspects exact commits read-only and reports findings to the PM. Only the PM
  integrates reviewed, clean, passing changes into V2 and pushes V2 without force.
- Keep task IDs, runtime metadata, launcher prompts, and local coordination state outside
  Git, under an ignored local coordination directory. These tools are development tools,
  not product runtime dependencies.

## Data and authority boundaries

- Never add real CAE models, results, credentials, or desktop state to Git.
- Treat every `02_CAE` directory as outside the tool repository. Do not modify real
  `02_CAE` data before the final authorized E2E phase.
- Never infer material, load, constraint, contact, ROI, or other physical meaning from
  geometry or convention. Use `ASK_AND_BLOCK` only when authoritative evidence cannot
  determine a required physical condition.
- Synthetic tests are synthetic evidence. Never describe them as real FEBio, official
  FBS, FEBio Studio, Computer Use, or real-model success.

## Implementation discipline

- Use test-first development: record a failing RED command and exit code, implement the
  smallest change, then record the GREEN command, count, and exit code.
- Do not count interrupted runs, collection errors, environment errors, skipped required
  checks, or unexecuted tests as passing evidence.
- Keep common contracts single-owner until frozen. After that boundary is fixed, isolate
  solver/FBS, input/model, autonomy, and build/launch work in separate worktrees.
- Worker tasks must state the base commit, allowed files, forbidden changes, RED and GREEN
  commands, completion criteria, unverified items, and required clean commit handoff.
- Accept only small, reviewed, clean commits. Do not hand off uncommitted changes or large
  snapshots.

## Required local gates

- Tests: `python -m pytest`
- Format: `python -m ruff format --check .`
- Lint: `python -m ruff check .`
- Types: `python -m mypy src tests`
- CAE boundary: `python scripts/scan_cae_data.py --root .`
- Build: `python -m build`
- Installed smoke: create a clean virtual environment, install the built wheel, and run
  `febio-cae --version` from that environment.

## Phase reporting

At every phase boundary report the commit SHA, changed files, exact test commands, test
counts, exit codes, unverified items, and next task. Do not declare the project complete
until every required real E2E and the final BottomFrame real-model E2E have fresh passing
evidence.
