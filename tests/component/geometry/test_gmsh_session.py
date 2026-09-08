from typing import Any

import pytest

from febio_cae.adapters.geometry.backend import BackendError
from febio_cae.adapters.geometry.gmsh_occ import _GmshSession


class SessionDouble:
    def __init__(self, initialized: bool) -> None:
        self.initialized = initialized
        self.events: list[str] = []
        self.models = ["caller-model"] if initialized else []
        self.option = self

    def isInitialized(self) -> bool:
        return self.initialized

    def initialize(self) -> None:
        self.events.append("initialize")
        self.initialized = True

    def setNumber(self, *args: Any) -> None:
        self.events.append("option")

    def clear(self) -> None:
        self.events.append("clear")
        self.models.clear()

    def finalize(self) -> None:
        self.events.append("finalize")
        self.initialized = False


def test_existing_session_is_refused_without_any_mutation() -> None:
    fake = SessionDouble(True)
    with pytest.raises(BackendError, match="initialized.*unowned"):
        with _GmshSession(fake, "OpenCASCADE"):
            pass
    assert fake.models == ["caller-model"]
    assert fake.initialized and fake.events == []


def test_owned_session_finalized_on_failure() -> None:
    fake = SessionDouble(False)
    with pytest.raises(RuntimeError, match="operation failed"):
        with _GmshSession(fake, "OpenCASCADE"):
            raise RuntimeError("operation failed")
    assert fake.events.count("finalize") == 1
    assert not fake.initialized
