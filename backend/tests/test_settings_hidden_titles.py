from __future__ import annotations

from contextlib import contextmanager

from backend.app.db import get_connection, utcnow_iso
from backend.app.services import settings_hidden_titles_service


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _insert_media_item(settings, *, index: int) -> int:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title, original_filename, file_path, file_size, file_mtime,
                year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, 1, 1, 2026, ?, ?, ?)
            """,
            (
                f"Hidden Contract Movie {index}",
                f"hidden-contract-{index}.mkv",
                f"/safe/hidden-contract-{index}.mkv",
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _insert_media_items(settings, *, count: int) -> None:
    now = utcnow_iso()
    with get_connection(settings) as connection:
        connection.executemany(
            """
            INSERT INTO media_items (
                title, original_filename, file_path, file_size, file_mtime,
                year, created_at, updated_at, last_scanned_at
            ) VALUES (?, ?, ?, 1, 1, 2026, ?, ?, ?)
            """,
            [
                (
                    f"Hidden Contract Movie {index}",
                    f"hidden-contract-{index}.mkv",
                    f"/safe/hidden-contract-{index}.mkv",
                    now,
                    now,
                    now,
                )
                for index in range(count)
            ],
        )
        connection.commit()


def test_empty_settings_hidden_titles_does_not_scan_media_or_build_presentations(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
) -> None:
    _insert_media_items(initialized_settings, count=5_000)

    traced_sql: list[str] = []
    real_get_connection = get_connection

    @contextmanager
    def traced_connection(settings):
        with real_get_connection(settings) as connection:
            connection.set_trace_callback(traced_sql.append)
            yield connection

    monkeypatch.setattr(settings_hidden_titles_service, "get_connection", traced_connection)
    monkeypatch.setattr(
        "backend.app.services.library_service.list_hidden_media_items",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full library path used")),
    )
    monkeypatch.setattr(
        settings_hidden_titles_service,
        "_edition_label",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unrelated title parsed")),
    )

    _login(client, **admin_credentials)
    response = client.get("/api/settings/hidden-titles")

    assert response.status_code == 200
    assert response.json()["personal"] == {"count": 0, "items": []}
    assert response.json()["global"] == {"count": 0, "items": []}
    assert not any(" from media_items" in statement.lower() for statement in traced_sql)


def test_settings_hidden_titles_etag_changes_after_hidden_mutation(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    item_id = _insert_media_item(initialized_settings, index=1)
    _login(client, **admin_credentials)

    initial = client.get("/api/settings/hidden-titles")
    assert initial.status_code == 200
    initial_etag = initial.headers["etag"]
    unchanged = client.get(
        "/api/settings/hidden-titles",
        headers={"If-None-Match": initial_etag},
    )
    assert unchanged.status_code == 304
    assert unchanged.content == b""

    hidden = client.post(f"/api/user-hidden-items/{item_id}")
    assert hidden.status_code == 200
    changed = client.get(
        "/api/settings/hidden-titles",
        headers={"If-None-Match": initial_etag},
    )
    assert changed.status_code == 200
    assert changed.headers["etag"] != initial_etag
    assert changed.json()["personal"]["count"] == 1
    assert changed.json()["personal"]["items"][0]["id"] == item_id


def test_standard_user_never_receives_global_hidden_titles(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    item_id = _insert_media_item(initialized_settings, index=2)
    _login(client, **admin_credentials)
    created = client.post(
        "/api/admin/users",
        json={
            "username": "hidden-standard",
            "password": "hidden-standard-password",
            "role": "standard_user",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    assert client.post(f"/api/admin/global-hidden-items/{item_id}").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200

    _login(client, username="hidden-standard", password="hidden-standard-password")
    response = client.get("/api/settings/hidden-titles")

    assert response.status_code == 200
    assert response.json()["global"] is None
    assert response.json()["personal"] == {"count": 0, "items": []}
