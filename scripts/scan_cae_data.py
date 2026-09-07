"""Repository entry point for the CAE data boundary scanner."""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from febio_cae.cli.scan_cae_data import main

if __name__ == "__main__":
    raise SystemExit(main())
