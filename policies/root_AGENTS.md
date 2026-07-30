# FEBio workspace rules

Before any work, classify the task.

- Reusable software, tests, installers, and tool documentation belong under
  `01_Tools/febio-tools` and use Git.
- Real-model preprocessing, meshing, solving, post-processing, and reporting
  belong under `02_CAE` and must never use Git or GitHub.
- Do not place new files at the workspace root.
- Read the nearest nested `AGENTS.md` before modifying either area.
- Never delete an existing CAE file unless it is listed in an approved
  `DELETE_CANDIDATES.csv`.

