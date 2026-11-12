"""Data sources: files on this computer and tables in the cloud, behind one interface. See ``base``."""

from __future__ import annotations

from .access import (
    RIGHT_LABELS,
    RIGHTS,
    Access,
    allows,
    current,
    defuse,
    hidden_columns,
    require,
    require_any,
    restrict,
)
from .base import Backend, DataError, Page, TableInfo
from .registry import (
    LOCAL_SUFFIXES,
    ONLINE_KINDS,
    Source,
    backend_for,
    create_local,
    ensure_shop_starter,
    get_registry,
    import_local,
    open_source,
    safe_filename,
)

__all__ = [
    "Access", "Backend", "DataError", "LOCAL_SUFFIXES", "ONLINE_KINDS", "Page", "RIGHTS", "RIGHT_LABELS", "Source",
    "TableInfo", "backend_for", "create_local", "current", "defuse", "ensure_shop_starter", "get_registry", "hidden_columns",
    "import_local", "open_source", "require", "require_any", "restrict", "safe_filename", "allows",
]
