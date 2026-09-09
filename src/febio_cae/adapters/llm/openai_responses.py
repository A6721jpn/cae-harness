"""Bounded Responses proposal transport; never an authority for physics."""

from __future__ import annotations

import ctypes
import http.client
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from febio_cae.application.intent_contracts import MAX_BYTES, effective_settings, strict_json
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.ports import PortError, PortErrorCategory


def _resolve_key(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise PortError(
            PortErrorCategory.ENVIRONMENT, "configured key environment variable is unavailable"
        )
    return value


def _single_cpu() -> None:
    if os.name != "nt":
        raise OSError("owned Responses transport requires Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    api.GetProcessAffinityMask.restype = ctypes.c_int
    api.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    api.SetProcessAffinityMask.restype = ctypes.c_int
    process, system = ctypes.c_size_t(), ctypes.c_size_t()
    if not api.GetProcessAffinityMask(
        ctypes.c_void_p(-1), ctypes.byref(process), ctypes.byref(system)
    ):
        raise OSError("cannot determine child CPU affinity")
    if not process.value or not api.SetProcessAffinityMask(
        ctypes.c_void_p(-1), process.value & -process.value
    ):
        raise OSError("cannot constrain child CPU affinity")


def _child(directory: Path) -> int:
    # Only environment NAME enters argv/files. Child stdout/stderr are intentionally silent.
    try:
        _single_cpu()
        config = strict_json((directory / "request.json").read_bytes())
        key = _resolve_key(config["key_env"])
        path = config["path"]
        if path not in {"/v1/responses", "/v1/responses/input_tokens"}:
            return 2
        payload = canonical_bytes(config["body"])
        if len(payload) > MAX_BYTES:
            return 2
        connection = http.client.HTTPSConnection("api.openai.com", timeout=config["socket_seconds"])
        try:
            connection.request(
                "POST",
                path,
                body=payload,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                return 2  # No redirects, retries or body-derived error text.
            content = response.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                return 2
            strict_json(content)
            (directory / "response.json").write_bytes(content)
            return 0
        finally:
            connection.close()
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        http.client.HTTPException,
        PortError,
    ):
        return 2


def _http(
    path: str, body: dict[str, Any], settings: dict[str, Any], deadline: float
) -> dict[str, Any]:
    from febio_cae.adapters.geometry.preparation import _run_owned

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Responses operation deadline exceeded")
    if len(canonical_bytes(body)) > MAX_BYTES:
        raise ValueError("request exceeds 256 KiB")
    with tempfile.TemporaryDirectory(prefix="febio-llm-") as temporary:
        directory = Path(temporary)
        (directory / "request.json").write_bytes(
            canonical_bytes(
                {
                    "path": path,
                    "body": body,
                    "key_env": settings["key_env"],
                    "socket_seconds": min(settings["socket_seconds"], remaining),
                }
            )
        )
        try:
            _run_owned(
                (sys.executable, "-m", __name__, str(directory)),
                directory,
                timeout_seconds=remaining,
                memory_bytes=256 * 1024 * 1024,
            )
        except TimeoutError:
            raise TimeoutError(
                "Responses owned-process deadline exceeded; remote outcome unknown"
            ) from None
        except OSError:
            raise OSError("Responses transport failed; remote outcome unknown") from None
        if time.monotonic() >= deadline:
            raise TimeoutError("late Responses result rejected; remote outcome unknown")
        result = strict_json((directory / "response.json").read_bytes())
        if not isinstance(result, dict):
            raise TypeError("response must be an object")
        return result


def _usage(raw: Any, input_limit: int, output_limit: int) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    fields = ("input_tokens", "output_tokens", "total_tokens")
    if any(type(raw.get(key)) is not int or raw[key] < 0 for key in fields):
        return None
    if raw["input_tokens"] + raw["output_tokens"] != raw["total_tokens"]:
        return None
    return {key: raw[key] for key in fields}


def exchange(
    common: dict[str, Any],
    settings: dict[str, Any],
    *,
    before_generation: Callable[[], None],
    on_event: Callable[[dict[str, Any]], None],
    deadline: float | None = None,
) -> dict[str, Any]:
    settings = effective_settings(settings, None)
    _resolve_key(settings["key_env"])
    if (
        set(common) != {"model", "instructions", "input", "text"}
        or common["model"] != settings["model"]
    ):
        raise ValueError("noncanonical Responses common request")
    if len(canonical_bytes(common)) > MAX_BYTES:
        raise ValueError("common request exceeds 256 KiB")
    deadline = (
        min(deadline, time.monotonic() + settings["budget"]["max_elapsed"]["value"])
        if deadline is not None
        else time.monotonic() + settings["budget"]["max_elapsed"]["value"]
    )
    on_event({"state": "COUNTING", "attempted_requests": 1})
    count = _http("/v1/responses/input_tokens", common, settings, deadline)
    if len(canonical_bytes(count)) > MAX_BYTES:
        raise ValueError("count response exceeds 256 KiB")
    amount = count.get("input_tokens")
    if type(amount) is not int or amount < 0:
        raise ValueError("invalid input token count")
    on_event({"counted_input_tokens": amount})
    if time.monotonic() >= deadline:
        raise TimeoutError("late count result rejected")
    if amount > settings["input_tokens"]:
        raise ValueError("input token limit exceeded; generation not sent")
    before_generation()
    generate = {
        **common,
        "store": False,
        "background": False,
        "stream": False,
        "tools": [],
        "max_output_tokens": settings["output_tokens"],
    }
    if len(canonical_bytes(generate)) > MAX_BYTES:
        raise ValueError("generation request exceeds 256 KiB")
    if time.monotonic() >= deadline:
        raise TimeoutError("operation deadline exceeded before generation")
    on_event({"state": "GENERATING", "attempted_requests": 2})
    result = _http("/v1/responses", generate, settings, deadline)
    if len(canonical_bytes(result)) > MAX_BYTES:
        raise ValueError("generation response exceeds 256 KiB")
    usage = _usage(result.get("usage"), settings["input_tokens"], settings["output_tokens"])
    event: dict[str, Any] = {"state": "RECEIVED"}
    if isinstance(result.get("model"), str) and len(result["model"]) <= 128:
        event["actual_model"] = result["model"]
    if usage is not None:
        event["usage"] = usage
        event["reserved_tokens"] = settings["input_tokens"] + usage["total_tokens"]
    on_event(event)  # Reconcile even if status, proposal or final CAS is rejected.
    if usage is not None and (
        usage["input_tokens"] > settings["input_tokens"]
        or usage["output_tokens"] > settings["output_tokens"]
    ):
        raise ValueError("measured generation usage exceeded the configured caps")
    if time.monotonic() >= deadline:
        raise TimeoutError("late generation result rejected")
    if result.get("status") != "completed" or "actual_model" not in event:
        raise ValueError("response incomplete or missing model identity")
    output = result.get("output")
    if not isinstance(output, list):
        raise TypeError("missing typed response output")
    messages = []
    for item in output:
        if not isinstance(item, dict):
            raise TypeError("invalid output item")
        if item.get("type") == "reasoning":
            if not set(item) <= {"type", "id", "summary", "content", "encrypted_content", "status"}:
                raise ValueError("unexpected reasoning output")
            continue
        if (
            item.get("type") != "message"
            or item.get("role") != "assistant"
            or item.get("status") != "completed"
        ):
            raise ValueError("unexpected output type or message status")
        messages.append(item)
    if len(messages) != 1:
        raise ValueError("exactly one completed assistant proposal is required")
    content = messages[0].get("content")
    if (
        not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "output_text"
        or not isinstance(content[0].get("text"), str)
    ):
        raise ValueError("refusal or unexpected assistant content")
    proposal = strict_json(content[0]["text"])
    if (
        not isinstance(proposal, dict)
        or set(proposal) != {"assignments"}
        or not isinstance(proposal["assignments"], list)
        or len(proposal["assignments"]) > 64
    ):
        raise ValueError("invalid closed proposal")
    return proposal


if __name__ == "__main__":
    raise SystemExit(_child(Path(sys.argv[1])))
