"""Relay installed preview's real request and one operator record; never observe UI."""

from __future__ import annotations

import argparse
import json
import math
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


def bridge(command: list[str], exchange: Path, timeout: float) -> int:
    """Use a new external directory; product CLI retains all observation validation."""
    exchange.mkdir(parents=True, exist_ok=False)
    response = exchange / "response.json"
    lines: queue.Queue[bytes | None] = queue.Queue()
    # The public command requires --window-id: this child cannot launch Studio.
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as child:
        assert child.stdin is not None and child.stdout is not None
        output = child.stdout

        def read_output() -> None:
            try:
                for line in output:
                    lines.put(line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        requested = False
        sent = False
        # Startup/exit allowance is not extra observation time; CLI owns that deadline.
        deadline = time.monotonic() + timeout + 30
        try:
            with (exchange / "stdout.jsonl").open("xb") as transcript:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("preview child did not exit within its finite allowance")
                    try:
                        line = lines.get(timeout=0.02)
                    except queue.Empty:
                        line = b""
                    if line is None:
                        return child.wait(timeout=max(0.01, deadline - time.monotonic()))
                    if line:
                        transcript.write(line)
                        transcript.flush()
                        sys.stdout.buffer.write(line)
                        sys.stdout.buffer.flush()
                        event = json.loads(line)
                        if event.get("status") == "PREVIEW_REQUESTED":
                            if requested or response.exists():
                                raise ValueError("duplicate request or response predating request")
                            temporary = exchange / "request.tmp"
                            temporary.write_bytes(line)
                            temporary.rename(exchange / "request.json")
                            requested = True
                    if response.exists() and not sent:
                        if not requested:
                            raise ValueError("response predates the preview request")
                        with response.open("rb") as stream:
                            record = stream.read(65537)
                        if len(record) > 65536:
                            raise ValueError("observation exceeds the 64KiB protocol limit")
                        # No value synthesis or JSON normalization: duplicate keys, nonce,
                        # display values and PNG freshness are checked by the installed CLI.
                        child.stdin.write(record if record.endswith(b"\n") else record + b"\n")
                        child.stdin.flush()
                        child.stdin.close()
                        sent = True
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            reader.join(timeout=1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, type=Path, help="installed febio-cae executable")
    parser.add_argument(
        "--exchange-dir", required=True, type=Path, help="NEW directory outside case"
    )
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--manifest-id", required=True)
    parser.add_argument("--studio", required=True, type=Path)
    parser.add_argument(
        "--window-id", required=True, type=int, help="existing Studio HWND, not PID"
    )
    parser.add_argument(
        "--timeout", type=float, default=120, help="observation seconds, 0 < t <= 120"
    )
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120:
        parser.error("timeout must be finite and in (0, 120]")
    if args.window_id <= 0:
        parser.error("window-id must be positive")
    command = [
        str(args.cli.resolve()),
        "case",
        "--state-dir",
        str(args.state_dir.resolve()),
        "preview",
        args.case_id,
        "--manifest-id",
        args.manifest_id,
        "--studio",
        str(args.studio.resolve()),
        "--window-id",
        str(args.window_id),
        "--timeout",
        str(args.timeout),
        "--json",
    ]
    try:
        return bridge(command, args.exchange_dir.resolve(), args.timeout)
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as error:
        print(f"preview bridge failed: {error}", file=sys.stderr)
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
