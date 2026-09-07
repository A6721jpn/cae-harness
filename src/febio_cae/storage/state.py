"""Product state-directory selection with an explicit test override."""

from __future__ import annotations

import os
from pathlib import Path


class ProductState:
    """Resolve the durable catalog location without consulting submitted case data."""

    environment_variable = "FEBIO_CAE_STATE_DIR"

    def __init__(self, path: Path | str | None = None) -> None:
        if path is not None:
            selected = Path(path).expanduser()
            self.source = "explicit"
        else:
            configured = os.environ.get(self.environment_variable, "").strip()
            if configured:
                selected = Path(configured).expanduser()
                self.source = self.environment_variable
            else:
                local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
                if local_app_data:
                    selected = Path(local_app_data) / "febio-cae"
                else:
                    selected = Path.home() / ".febio-cae"
                self.source = "deterministic-user-default"
        self.path = selected.absolute()
        self.path.mkdir(parents=True, exist_ok=True)

    @property
    def catalog_path(self) -> Path:
        return self.path / "catalog.sqlite3"

    @property
    def compatibility_path(self) -> Path:
        return self.path / "compatibility.sqlite3"


__all__ = ["ProductState"]
