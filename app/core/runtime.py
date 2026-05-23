"""Simple in-memory singleton for storing runtime settings.

Values are set during app initialisation and can be read or updated at any
time by long-running background agents or other components.

"""

from __future__ import annotations

from typing import Any

_store: dict[str, Any] = {}


def set(key: str, value: Any) -> None:
    """Store a runtime variable."""
    _store[key] = value


def get(key: str, default: Any = None) -> Any:
    """Retrieve a runtime variable, returning *default* if not found."""
    return _store.get(key, default)


def all_vars() -> dict[str, Any]:
    """Return a shallow copy of all runtime variables."""
    return dict(_store)