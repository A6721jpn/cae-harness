"""Private injected protocol evidence; never a live model success claim."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.llm import openai_responses as adapter
from febio_cae.domain.budget import Budget
from febio_cae.domain.units import Quantity


def settings() -> dict[str, Any]:
    return {
        "provider": "openai_responses",
        "model": "explicit-test-model",
        "key_env": "P4_PRIVATE_TEST_KEY",
        "budget": Budget(Quantity(15, "s"), 1, 1, 2, 2200).to_dict(),
        "input_tokens": 1000,
        "output_tokens": 200,
        "socket_seconds": 3,
    }


def response(
    assignments: list[dict[str, str]] | None = None, status: str = "completed"
) -> dict[str, Any]:
    import json

    return {
        "status": status,
        "model": "returned-test-model",
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": json.dumps({"assignments": assignments or []})}
                ],
            },
        ],
    }


def common() -> dict[str, Any]:
    return {
        "model": "explicit-test-model",
        "instructions": "bounded proposal",
        "input": "explicit source",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "intent_proposal",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"assignments": {"type": "array", "items": {"type": "string"}}},
                    "required": ["assignments"],
                    "additionalProperties": False,
                },
            }
        },
    }


def test_count_generation_identity_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    events: list[dict[str, Any]] = []

    def http(path: str, body: dict[str, Any], *_: Any) -> dict[str, Any]:
        calls.append((path, body))
        return {"input_tokens": 100} if path.endswith("input_tokens") else response()

    monkeypatch.setattr(adapter, "_http", http)
    monkeypatch.setattr(adapter, "_resolve_key", lambda _: "private-injected")
    output = adapter.exchange(
        common(), settings(), before_generation=lambda: None, on_event=events.append
    )
    assert output["assignments"] == []
    assert len(calls) == 2 and calls[0][0] == "/v1/responses/input_tokens"
    assert calls[0][1] == common()
    for key, value in common().items():
        assert calls[1][1][key] == value
    assert {k: calls[1][1][k] for k in ("store", "background", "stream")} == dict.fromkeys(
        ("store", "background", "stream"), False
    )
    assert calls[1][1]["tools"] == [] and calls[1][1]["max_output_tokens"] == 200
    assert any(e.get("usage", {}).get("total_tokens") == 120 for e in events)


def test_overcap_refusal_incomplete_and_timeout_do_not_propose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter, "_resolve_key", lambda _: "private-injected")
    for outcome in ("overcap", "refusal", "incomplete", "timeout"):
        calls: list[str] = []
        events: list[dict[str, Any]] = []

        def http(
            path: str, *_: Any, calls: list[str] = calls, outcome: str = outcome
        ) -> dict[str, Any]:
            calls.append(path)
            if outcome == "timeout":
                raise TimeoutError("private transport timeout")
            if path.endswith("input_tokens"):
                return {"input_tokens": 1001 if outcome == "overcap" else 100}
            result = response(status="incomplete" if outcome == "incomplete" else "completed")
            if outcome == "refusal":
                result["output"][1]["content"] = [{"type": "refusal", "refusal": "no"}]
            return result

        monkeypatch.setattr(adapter, "_http", http)
        with pytest.raises((ValueError, TimeoutError)):
            adapter.exchange(
                common(), settings(), before_generation=lambda: None, on_event=events.append
            )
        assert len(calls) == (1 if outcome in {"overcap", "timeout"} else 2)
        if outcome in {"refusal", "incomplete"}:
            assert any(e.get("usage", {}).get("total_tokens") == 120 for e in events)


def test_python_only_owned_transport_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from febio_cae.adapters.geometry import preparation

    run_owned = preparation._run_owned
    launched: list[tuple[str, ...]] = []

    def hanging(argv: tuple[str, ...], directory: Path, **limits: Any) -> dict[str, int]:
        launched.append(argv)
        return run_owned((sys.executable, "-c", "import time; time.sleep(30)"), directory, **limits)

    monkeypatch.setattr(preparation, "_run_owned", hanging)
    monkeypatch.setattr(adapter, "_resolve_key", lambda _: "private-injected")
    monkeypatch.setattr(adapter.tempfile, "tempdir", str(tmp_path))
    bounded = settings()
    bounded["budget"]["max_elapsed"]["value"] = 0.4
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        adapter.exchange(common(), bounded, before_generation=lambda: None, on_event=lambda _: None)
    assert len(launched) == 1 and "febio_cae.adapters.llm.openai_responses" in launched[0]
    assert time.monotonic() - started < 5
