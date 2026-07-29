# FEBio Studio 3.1 integration status

## Verified

The exact FEBio Studio v3.1 source contract was checked:

- A Local Launch Configuration starts its configured executable in the job FEB directory.
- The default command is `-i $(Filename)`.
- The job is complete only when that process exits.
- Exit code 0 allows the job report to open the original job basename `.xplt`.
- Nonzero exit is reported as error termination.

The packaged and installed launcher was invoked with this argument contract. Its real Gmsh-to-FEBio small model completed and promoted a valid same-basename XPLT. Bottom Frame also passed FEBio model initialization.

Installed executable:

`C:\Users\backo\AppData\Local\FEBioGmshLauncher\current\FEBioGmshLauncher.exe`

## GUI smoke still requiring an unlocked desktop

Computer Use detected the running `FEBio Studio 3.1.0` window, but window activation timed out twice. Per the Computer Use recovery rules, no further GUI input was attempted. The likely condition is a locked/non-interactive Windows session.

When the desktop is unlocked:

1. Tools → Launch Configurations → Add → `local`.
2. Name: `Gmsh curved Tet10`.
3. FEBio executable: the installed executable above.
4. Keep Run command as `-i $(Filename)`.
5. Run the small model or Bottom Frame.
6. Accept the completed-job dialog and confirm the XPLT opens in the same Studio process.

This final visual GUI observation is the only unverified item; the executable argument, output basename, exit-code, packaged solve, and Studio source contracts are verified.
