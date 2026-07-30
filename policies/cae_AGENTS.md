# CAE rules

- Git and GitHub are prohibited everywhere below `02_CAE`.
- Stop immediately if `git rev-parse --is-inside-work-tree` succeeds or a
  `.git` marker exists in this tree.
- Every analysis belongs to one case under `01_Active`.
- Store source input in `01_Input`, FSM and FEB in `02_Model`, XPLT in
  `03_Result`, final reports in `04_Report`, and hashes, solver evidence, and
  validation in `05_Verification`.
- Store MSH, INP, VTU, VOL, candidates, trial logs, caches, and temporary
  scripts only in `90_Temporary` or `99_Temporary`.
- Move a case to `02_Archive` only after the completion gate in the workspace
  README passes.
- Never delete a file directly. Move reviewed candidates to
  `98_Delete_Review` and wait for explicit user approval.

