"""OpenAI proposal boundary (test-first callable scaffold)."""
from typing import Any


def _resolve_key(name: str) -> str:
    raise NotImplementedError("credential boundary not implemented")


def _http(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("transport boundary not implemented")


def exchange(common: dict[str, Any], settings: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("bounded proposal exchange not implemented")
