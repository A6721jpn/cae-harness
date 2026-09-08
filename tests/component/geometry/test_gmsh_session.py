from threading import Event, Thread
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
    with (
        pytest.raises(BackendError, match="initialized.*unowned"),
        _GmshSession(fake, "OpenCASCADE"),
    ):
        pass
    assert fake.models == ["caller-model"]
    assert fake.initialized and fake.events == []


def test_owned_session_finalized_on_failure() -> None:
    fake = SessionDouble(False)
    with pytest.raises(RuntimeError, match="operation failed"), _GmshSession(fake, "OpenCASCADE"):
        raise RuntimeError("operation failed")
    assert fake.events.count("finalize") == 1
    assert not fake.initialized


def test_initialization_failure_preserves_owned_cleanup(monkeypatch: Any) -> None:
    from febio_cae.adapters.geometry.backend import BackendErrorCategory

    fake = SessionDouble(False)

    def fail(*args: Any) -> None:
        raise BackendError(BackendErrorCategory.ENVIRONMENT, "option failed")

    monkeypatch.setattr(fake, "setNumber", fail)
    with pytest.raises(BackendError, match="option failed"), _GmshSession(fake, "OpenCASCADE"):
        pass
    assert fake.events.count("finalize") == 1
    assert not fake.initialized


def test_two_sessions_cannot_both_pass_uninitialized_query() -> None:
    fake = SessionDouble(False)
    entered_query, release_query = Event(), Event()
    first_outcomes: list[object] = []
    original = fake.isInitialized

    def held_query() -> bool:
        observed = original()
        if not entered_query.is_set():
            entered_query.set()
            assert release_query.wait(3), "bounded query release expired"
        return observed

    fake.isInitialized = held_query  # type: ignore[method-assign]

    def first() -> None:
        try:
            with _GmshSession(fake, "OpenCASCADE"):
                first_outcomes.append("entered")
        except BaseException as error:
            first_outcomes.append(error)

    thread = Thread(target=first, daemon=True)
    thread.start()
    try:
        assert entered_query.wait(3)
        # First caller owns admission but is paused before initialization. The
        # second observes False without a process-wide guard and enters unsafely.
        with pytest.raises(BackendError, match="busy"), _GmshSession(fake, "OpenCASCADE"):
            pass
        assert fake.events == []
    finally:
        release_query.set()
        thread.join(3)
    assert not thread.is_alive()
    assert first_outcomes == ["entered"]
    assert fake.events.count("initialize") == 1
    with _GmshSession(fake, "OpenCASCADE"):
        pass
    assert fake.events.count("initialize") == 2


def test_reentry_and_different_wrapper_are_busy_without_mutation() -> None:
    fake = SessionDouble(False)
    session = _GmshSession(fake, "OpenCASCADE")
    with session:
        before = list(fake.events)
        with pytest.raises(BackendError, match="busy"), session:
            pass
        with (
            pytest.raises(BackendError, match="busy"),
            _GmshSession(SessionDouble(False), "OpenCASCADE"),
        ):
            pass
        assert fake.events == before
        assert fake.initialized


@pytest.mark.parametrize("stage", ["initialize", "clear", "finalize"])
def test_failed_lifetime_releases_admission(stage: str, monkeypatch: Any) -> None:
    fake = SessionDouble(False)
    original = getattr(fake, stage)

    def fail() -> None:
        original()
        raise RuntimeError("injected lifetime failure")

    monkeypatch.setattr(fake, stage, fail)
    with (
        pytest.raises(RuntimeError, match="injected lifetime failure"),
        _GmshSession(fake, "OpenCASCADE"),
    ):
        pass
    assert not fake.initialized
    assert fake.events.count("finalize") == 1
    with _GmshSession(SessionDouble(False), "OpenCASCADE"):
        pass
