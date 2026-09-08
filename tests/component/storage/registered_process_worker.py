"""Bounded synthetic process driver for registered publication tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from febio_cae.domain.case_revision import CaseRevision
from febio_cae.domain.codec import decode_record
from febio_cae.storage import registry
from febio_cae.storage.registry import CaseStorage, StorageConflictError


def main() -> None:
    root, action, argument, boundary = sys.argv[1:]

    def pause(point: str) -> None:
        if point == boundary:
            print(json.dumps({"event": point, "pid": os.getpid()}), flush=True)
            if sys.stdin.readline().strip() == "crash":
                os._exit(73)

    storage = CaseStorage(root, failure_injector=pause)
    if action == "source":
        original_write = registry._write_atomic

        def paused_write(*args: Any, **kwargs: Any) -> Path:
            result = original_write(*args, **kwargs)
            pause("after_source_write")
            return result

        registry._write_atomic = paused_write
    print(json.dumps({"event": "opened", "pid": os.getpid()}), flush=True)
    try:
        if action == "revision":
            revision = decode_record(Path(argument).read_bytes(), CaseRevision)
            storage.register_revision_if_current(revision, expected_generation=1)
        elif action == "source":
            storage.ingest_source(
                asset_id="contended",
                source_kind="user_instruction",
                media_type="text/plain",
                content=argument.encode(),
            )
        elif action != "open":
            raise ValueError(action)
    except StorageConflictError:
        result = "conflict"
    else:
        result = "ok"
    print(json.dumps({"result": result, "pid": os.getpid()}), flush=True)


if __name__ == "__main__":
    main()
