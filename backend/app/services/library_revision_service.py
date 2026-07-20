from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import HTTPException

from ..auth import AuthenticatedUser
from ..config import Settings
from ..db import get_connection
from .library_hidden_service import (
    _load_globally_hidden_movie_keys,
    _load_hidden_movie_keys,
)
from .library_movie_identity_service import _row_hidden_movie_key
from .local_library_source_service import ensure_current_shared_local_source_binding
from .media_age_access_service import assert_user_can_access_media_by_age


LIBRARY_REVISION_SCHEMA = "library-revision-v1"
LIBRARY_PROGRESS_STATE_SCHEMA = "library-progress-state-v1"
LIBRARY_REVISION_LAYERS = ("catalog", "presentation", "permission", "user_overlay", "progress")


def _load_counters(connection, *, user_id: int) -> dict[str, tuple[int, int]]:
    rows = connection.execute(
        """
        SELECT scope_kind, scope_id, layer, counter
        FROM library_revision_counters
        WHERE (scope_kind = 'global' AND scope_id = 0)
           OR (scope_kind = 'user' AND scope_id = ?)
        """,
        (user_id,),
    ).fetchall()
    counters = {layer: [0, 0] for layer in LIBRARY_REVISION_LAYERS}
    for row in rows:
        layer = str(row["layer"])
        if layer not in counters:
            continue
        index = 0 if str(row["scope_kind"]) == "global" else 1
        counters[layer][index] = int(row["counter"])
    return {layer: (values[0], values[1]) for layer, values in counters.items()}


def _opaque_token(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    layer: str,
    counters: Any,
) -> str:
    message = json.dumps(
        {
            "schema": LIBRARY_REVISION_SCHEMA,
            "user_id": int(user.id),
            "role": str(user.role or "standard_user"),
            "age_credential": int(getattr(user, "age_credential", 18)),
            "layer": layer,
            "counters": counters,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(settings.session_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def get_library_revision(settings: Settings, *, user: AuthenticatedUser) -> dict[str, str]:
    with get_connection(settings) as connection:
        counters = _load_counters(connection, user_id=int(user.id))
    tokens = {
        layer: _opaque_token(settings, user=user, layer=layer, counters=counters[layer])
        for layer in LIBRARY_REVISION_LAYERS
    }
    combined_counters = [counters[layer] for layer in ("catalog", "presentation", "permission", "user_overlay")]
    return {
        "schema_version": LIBRARY_REVISION_SCHEMA,
        **tokens,
        "combined_library": _opaque_token(
            settings,
            user=user,
            layer="combined_library",
            counters=combined_counters,
        ),
    }


def get_library_progress_state(settings: Settings, *, user: AuthenticatedUser) -> dict[str, object]:
    with get_connection(settings) as connection:
        shared_local_source_id = ensure_current_shared_local_source_binding(
            settings,
            connection=connection,
        )
        globally_hidden_movie_keys = set(_load_globally_hidden_movie_keys(connection))
        hidden_movie_keys = set(_load_hidden_movie_keys(connection, user_id=int(user.id)))
        rows = connection.execute(
            """
            SELECT
                p.media_item_id,
                p.position_seconds,
                p.duration_seconds,
                p.completed,
                m.title,
                m.year,
                m.original_filename
            FROM playback_progress p
            JOIN media_items m ON m.id = p.media_item_id
            LEFT JOIN library_sources s ON s.id = m.library_source_id
            WHERE p.user_id = ?
              AND (p.position_seconds > 0 OR p.completed = 1)
              AND (
                  (
                      COALESCE(m.source_kind, 'local') = 'local'
                      AND m.library_source_id = ?
                  )
                  OR (
                      s.id IS NOT NULL
                      AND (s.owner_user_id = ? OR s.is_shared = 1)
                  )
              )
              AND NOT EXISTS (
                  SELECT 1 FROM global_hidden_media_items gh
                  WHERE gh.media_item_id = p.media_item_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM user_hidden_media_items uh
                  WHERE uh.user_id = ? AND uh.media_item_id = p.media_item_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM user_hidden_library_sources hs
                  WHERE hs.user_id = ? AND hs.library_source_id = m.library_source_id
              )
            ORDER BY p.media_item_id
            """,
            (
                int(user.id),
                shared_local_source_id,
                int(user.id),
                int(user.id),
                int(user.id),
            ),
        ).fetchall()
        counters = _load_counters(connection, user_id=int(user.id))

    items: list[dict[str, object]] = []
    for row in rows:
        item_id = int(row["media_item_id"])
        movie_key = _row_hidden_movie_key(row)
        if movie_key and (movie_key in globally_hidden_movie_keys or movie_key in hidden_movie_keys):
            continue
        try:
            assert_user_can_access_media_by_age(settings, user=user, item_id=item_id, purpose="library-progress")
        except HTTPException as exc:
            if exc.status_code == 403:
                continue
            raise
        items.append({
            "id": item_id,
            "progress_seconds": float(row["position_seconds"] or 0.0),
            "progress_duration_seconds": (
                float(row["duration_seconds"])
                if row["duration_seconds"] is not None
                else None
            ),
            "completed": bool(row["completed"]),
        })
    return {
        "schema_version": LIBRARY_PROGRESS_STATE_SCHEMA,
        "progress_revision": _opaque_token(
            settings,
            user=user,
            layer="progress",
            counters=counters["progress"],
        ),
        "items": items,
    }
