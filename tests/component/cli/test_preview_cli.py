"""Finite pipe and CLI protocol checks; no native application is opened."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from febio_cae.application.service import RegisteredCaseService
from febio_cae.cli.main import main


@pytest.mark.parametrize("duplicate", [False, True])
def test_finite_observation_pipe_reads_one_strict_json_object(duplicate: bool) -> None:
    from febio_cae.cli.preview import read_observation

    read_fd, write_fd = os.pipe()
    try:
        os.write(
            write_fd, b'{"nonce":"one","nonce":"two"}\n' if duplicate else b'{"nonce":"one"}\n'
        )
        if duplicate:
            with pytest.raises(ValueError):
                read_observation(read_fd, 1.0)
        else:
            assert read_observation(read_fd, 1.0) == {"nonce": "one"}
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_observation_pipe_times_out_without_a_background_reader() -> None:
    from febio_cae.cli.preview import read_observation

    read_fd, write_fd = os.pipe()
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            read_observation(read_fd, 0.05)
        assert time.monotonic() - started < 1.0
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_cli_announces_request_before_reading_observation(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    from febio_cae.cli import preview

    events: list[str] = []

    def read(fd: int, timeout: float) -> dict[str, Any]:
        emitted = json.loads(capsys.readouterr().out)
        assert emitted["status"] == "PREVIEW_REQUESTED"
        assert emitted["request"]["binding"]["nonce"] == "fresh"
        events.append("read")
        return {"synthetic": "fresh observation"}

    def observe(self: Any, case_id: str, manifest_id: str, **kwargs: Any) -> dict[str, object]:
        assert (case_id, manifest_id) == ("case-synthetic", "manifest-synthetic")
        assert kwargs["window_id"] == 321 and kwargs["timeout_seconds"] == 30
        assert kwargs["capture"]({"binding": {"nonce": "fresh"}}, 29) == {
            "synthetic": "fresh observation"
        }
        return {"task_status": "COMPLETE", "preview_status": "CONFIRMED", "quality_status": "PASS"}

    monkeypatch.setattr(preview, "read_observation", read)
    monkeypatch.setattr(preview, "_stdin_fd", lambda: 0)
    monkeypatch.setattr(RegisteredCaseService, "observe_preview", observe, raising=False)
    code = main(
        [
            "case",
            "--state-dir",
            str(tmp_path / "state"),
            "preview",
            "case-synthetic",
            "--manifest-id",
            "manifest-synthetic",
            "--window-id",
            "321",
            "--studio",
            "FEBioStudio.exe",
            "--timeout",
            "30",
            "--json",
        ]
    )
    assert code == 0
    assert events == ["read"]
    assert json.loads(capsys.readouterr().out)["preview_status"] == "CONFIRMED"


def test_cli_reads_preview_status_without_a_new_observation(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    def status(self: Any, case_id: str, preview_id: str) -> dict[str, object]:
        assert (case_id, preview_id) == ("case-synthetic", "preview-synthetic")
        return {"preview_status": "CONFIRMED", "task_status": "COMPLETE"}

    monkeypatch.setattr(RegisteredCaseService, "preview_status", status, raising=False)
    assert (
        main(
            [
                "case",
                "--state-dir",
                str(tmp_path / "state"),
                "preview-status",
                "case-synthetic",
                "--preview-id",
                "preview-synthetic",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["task_status"] == "COMPLETE"
