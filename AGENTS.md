# Tool-development rules

- This is the canonical Git repository in the FEBio workspace; its linked
  worktrees must remain under `01_Tools/_worktrees`.
- Use Git for every reusable tool change and publish approved branches to a
  tool-only GitHub repository.
- Never attach or push the mixed legacy repository/bundle as a GitHub remote.
- Never copy a real CAE model, result, input STEP, FSM, FEB, XPLT, MSH, INP,
  VTU, or VOL into this repository.
- Tests may use only small synthetic fixtures with no product geometry.
- Tool outputs for a real analysis must resolve under that case's
  `90_Temporary`.
- Run focused tests and inspect Git status before every commit.
