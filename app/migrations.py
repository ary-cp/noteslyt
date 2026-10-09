"""Safe application of ordered SQL migrations during API startup."""

from __future__ import annotations

import psycopg

from .config import Settings, get_settings
from .migration_runner import apply_pending_migrations as _apply_pending


def apply_pending_migrations(settings: Settings | None = None) -> list[str]:
    """Apply pending migrations; this startup path never performs a reset."""
    return _apply_pending(settings or get_settings())

