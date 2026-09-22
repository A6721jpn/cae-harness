"""Real pipe bridge fixtures; no Studio, screenshot, or confirmation is manufactured."""

from __future__ import annotations

import json
import os
import runpy
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
bridge = runpy.run_path(str(ROOT / "scripts" / "observe_preview.py"))["bridge"]
CHILD = """
import json, sys, uuid
from febio_cae.cli.preview import capture_observation
try:
    observed = capture_observation({'binding': {'nonce': uuid.uuid4().hex}}, float(sys.argv[1]))
    print(json.dumps({'received': observed}), flush=True)
except TimeoutError:
    print(json.dumps({'status': 'NEEDS_PREVIEW'}), flush=True)
    sys.exit(7)
"""


def test_bridge_publishes_real_request_then_relays_one_operator_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    exchange = tmp_path / "exchange"

    def respond() -> dict[str, Any]:
        deadline = time.monotonic() + 5
        while not (exchange / "request.json").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("fixture did not receive a request")
            time.sleep(0.01)
        event = json.loads((exchange / "request.json").read_bytes())
        observed = {"request_nonce": event["request"]["binding"]["nonce"], "observer": "独立"}
        temporary = exchange / "response.tmp"
        temporary.write_bytes(json.dumps(observed, ensure_ascii=False).encode("utf-8"))
        temporary.rename(exchange / "response.json")
        return observed

    with ThreadPoolExecutor(max_workers=1) as executor:
        operator = executor.submit(respond)
        assert bridge([sys.executable, "-c", CHILD, "5"], exchange, 5) == 0
        observed = operator.result(timeout=1)
    emitted = (exchange / "stdout.jsonl").read_bytes().splitlines(keepends=True)
    assert (exchange / "request.json").read_bytes() == emitted[0]
    assert json.loads(emitted[1]) == {"received": observed}
    assert not (exchange / "request.tmp").exists()


def test_bridge_rejects_reused_exchange_without_starting_child(tmp_path: Path) -> None:
    exchange = tmp_path / "used"
    exchange.mkdir()
    response = exchange / "response.json"
    response.write_bytes(b'{"request_nonce":"stale"}')
    with pytest.raises(FileExistsError):
        bridge(["must-not-be-started"], exchange, 1)
    assert response.read_bytes() == b'{"request_nonce":"stale"}'


@pytest.mark.skipif(os.name != "nt", reason="product observation PIPE is Windows-only")
def test_bridge_preserves_child_deadline_failure_without_operator_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    exchange = tmp_path / "timeout"
    started = time.monotonic()
    assert bridge([sys.executable, "-c", CHILD, "0.1"], exchange, 0.1) == 7
    assert time.monotonic() - started < 5
    assert json.loads((exchange / "stdout.jsonl").read_bytes().splitlines()[-1]) == {
        "status": "NEEDS_PREVIEW"
    }
    assert not (exchange / "response.json").exists()
