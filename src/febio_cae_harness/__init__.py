"""FEBio CAE Harness package."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .intent_lifecycle import IntentLifecycle, IntentLifecycleResult, IntentQuestion

__all__ = [
    "IntentLifecycle",
    "IntentLifecycleResult",
    "IntentQuestion",
    "__version__",
]

__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    if name in {"IntentLifecycle", "IntentLifecycleResult", "IntentQuestion"}:
        from .intent_lifecycle import IntentLifecycle, IntentLifecycleResult, IntentQuestion

        exports = {
            "IntentLifecycle": IntentLifecycle,
            "IntentLifecycleResult": IntentLifecycleResult,
            "IntentQuestion": IntentQuestion,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
