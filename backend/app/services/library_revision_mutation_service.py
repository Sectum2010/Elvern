from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping

from ..config import Settings
from ..db import utcnow_iso


LIBRARY_REVISION_MUTABLE_LAYERS = frozenset({
    "catalog",
    "presentation",
    "permission",
    "user_overlay",
    "progress",
})


def _bump_one(
    connection: sqlite3.Connection,
    *,
    scope_kind: str,
    scope_id: int,
    layer: str,
) -> bool:
    if layer not in LIBRARY_REVISION_MUTABLE_LAYERS:
        raise ValueError(f"Unsupported library revision layer: {layer}")
    claim = getattr(connection, "claim_library_revision", None)
    if callable(claim) and not claim(scope_kind, scope_id, layer):
        return False
    connection.execute(
        """
        INSERT INTO library_revision_counters (
            scope_kind, scope_id, layer, counter, updated_at
        ) VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(scope_kind, scope_id, layer) DO UPDATE SET
            counter = counter + 1,
            updated_at = excluded.updated_at
        """,
        (scope_kind, int(scope_id), layer, utcnow_iso()),
    )
    return True


def bump_library_revision_layers(
    settings: Settings,
    connection: sqlite3.Connection,
    *,
    global_layers: Iterable[str] = (),
    user_layers: Mapping[int, Iterable[str]] | None = None,
) -> int:
    """Bump selected revision layers in the caller's existing transaction."""

    if not settings.library_revision_enabled:
        return 0
    writes = 0
    for layer in dict.fromkeys(global_layers):
        writes += int(_bump_one(
            connection,
            scope_kind="global",
            scope_id=0,
            layer=str(layer),
        ))
    for user_id, layers in (user_layers or {}).items():
        for layer in dict.fromkeys(layers):
            writes += int(_bump_one(
                connection,
                scope_kind="user",
                scope_id=int(user_id),
                layer=str(layer),
            ))
    return writes
