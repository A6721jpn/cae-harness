# Tool-development rules

- This is the only Git repository in the FEBio workspace.
- Use Git for every reusable tool change.
- Never copy a real CAE model, result, input STEP, FSM, FEB, XPLT, MSH, INP,
  VTU, or VOL into this repository.
- Tests may use only small synthetic fixtures with no product geometry.
- Tool outputs for a real analysis must resolve under that case's
  `90_Temporary`.
- Run focused tests and inspect Git status before every commit.

