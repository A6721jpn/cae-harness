"""Durable registered-case storage owned by the application layer."""

from .catalog import CaseCatalog, CaseCatalogError
from .profiles import SQLiteCompatibilityRegistry
from .registry import (
    CaseStorage,
    InjectedStorageFailure,
    RegisteredSource,
    StorageConflictError,
    StorageIntegrityError,
)
from .state import ProductState

__all__ = [
    "CaseCatalog",
    "CaseCatalogError",
    "CaseStorage",
    "InjectedStorageFailure",
    "ProductState",
    "RegisteredSource",
    "SQLiteCompatibilityRegistry",
    "StorageConflictError",
    "StorageIntegrityError",
]
