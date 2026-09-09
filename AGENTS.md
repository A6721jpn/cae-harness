# FEBio CAE Harness Development Contract

テストは最低限に。初期計画にないテストや機能追加は最小限に。最短で最速で計画通りの開発を遂行せよ。

Effective: 2026-09-09. This contract supersedes earlier development role and dispatch instructions.

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
- The PM also serves as PdM and uses `gpt-6-astra` / `xhigh`. Exactly one implementation
  task uses `gpt-6-astra` / `low`. A separate, independent code-review task uses
  `gpt-6-astra` / `medium`. Do not dispatch additional or parallel coding workers.
- Continue the existing designated tasks through the Codex task tools, explicitly
  setting the destination role's model and effort on work dispatch. Do not use the
  Luna Spawn route. Reports preserve destination settings; a PM report must not
  switch the PM to the sender's effort. Verify effective settings from runtime
  metadata; a title or requested setting alone is not execution evidence.
- The PM/PdM decides product priorities, scope, task order, technical tradeoffs,
  acceptance criteria and finite development/execution budgets within user-authorized
  requirements. Record consequential decisions and update the two product authorities
  before implementation when behavior or acceptance changes. Routine engineering
  decisions do not require another permission request. This authority does not
  invent physical conditions, authorize unapproved real-data/native operations,
  waive required real E2E, or describe unverified behavior as accepted.
- The PM/PdM owns scope and integration. The sole implementer owns assigned product
  files; the PM owns governance documents and does not concurrently edit those product files. The
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
- Keep common contracts single-owner. The sole implementer works sequentially across
  input/model, solver/FBS, autonomy and build/launch, using explicit file boundaries
  and reviewed base commits. Logical module boundaries do not authorize more workers.
- Worker tasks must state the base commit, allowed files, forbidden changes, RED and GREEN
  commands, completion criteria, unverified items, and required clean commit handoff.
- Accept only small, reviewed, clean commits. Do not hand off uncommitted changes or large
  snapshots.
- Use the smallest meaningful RED/GREEN checks for the planned behavior and observed
  defects. Do not add speculative features or redundant test matrices. Run required
  local gates once on the final implementation candidate; repeat only checks justified
  by a changed candidate, environment, failure or unresolved risk. A documentation-only
  change needs content/link/diff checks and independent review, not product RED/GREEN,
  pytest, build or a new installed environment. Retain valid existing tests.
- Coordinate on completion, failure or a decision request. Do not create acknowledgement
  loops, repeated unchanged polling, or additional tasks solely to wait.

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
