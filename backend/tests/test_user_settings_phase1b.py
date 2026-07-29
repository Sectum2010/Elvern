from __future__ import annotations

from backend.app.db import get_connection, init_db, utcnow_iso


FLOATING_POSITION_RETIREMENT_MIGRATION = "floating_controls_position_retired_v1"


def _login_admin(client, admin_credentials: dict[str, str]) -> None:
    response = client.post(
        "/api/auth/login",
        json={
            "username": admin_credentials["username"],
            "password": admin_credentials["password"],
        },
    )
    assert response.status_code == 200


def test_user_settings_no_longer_expose_position_and_ignore_legacy_top_patch(
    client,
    admin_credentials,
) -> None:
    _login_admin(client, admin_credentials)

    initial = client.get("/api/user-settings")
    assert initial.status_code == 200
    assert "floating_controls_position" not in initial.json()

    updated = client.patch(
        "/api/user-settings",
        json={
            "floating_controls_position": "top",
            "hide_recently_added": True,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["hide_recently_added"] is True
    assert "floating_controls_position" not in updated.json()


def test_floating_position_retirement_migration_deletes_legacy_rows_once(
    initialized_settings,
) -> None:
    with get_connection(initialized_settings) as connection:
        user_id = int(connection.execute("SELECT id FROM users LIMIT 1").fetchone()["id"])
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = ?",
            (FLOATING_POSITION_RETIREMENT_MIGRATION,),
        )
        connection.execute(
            """
            INSERT INTO user_settings (user_id, key, value, updated_at)
            VALUES (?, 'floating_controls_position', 'top', ?)
            ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
            """,
            (user_id, utcnow_iso()),
        )
        connection.commit()

    init_db(initialized_settings)
    init_db(initialized_settings)

    with get_connection(initialized_settings) as connection:
        legacy_count = connection.execute(
            "SELECT COUNT(*) AS count FROM user_settings WHERE key = 'floating_controls_position'"
        ).fetchone()["count"]
        migration_count = connection.execute(
            "SELECT COUNT(*) AS count FROM schema_migrations WHERE name = ?",
            (FLOATING_POSITION_RETIREMENT_MIGRATION,),
        ).fetchone()["count"]

    assert legacy_count == 0
    assert migration_count == 1


def test_desktop_floating_island_position_defaults_saves_and_normalizes(
    client,
    admin_credentials,
) -> None:
    _login_admin(client, admin_credentials)

    initial = client.get("/api/user-settings")
    assert initial.status_code == 200
    assert initial.json()["desktop_floating_island_position"] == "top"

    bottom = client.patch(
        "/api/user-settings",
        json={"desktop_floating_island_position": "bottom"},
    )
    assert bottom.status_code == 200
    assert bottom.json()["desktop_floating_island_position"] == "bottom"

    invalid = client.patch(
        "/api/user-settings",
        json={"desktop_floating_island_position": "sideways"},
    )
    assert invalid.status_code == 200
    assert invalid.json()["desktop_floating_island_position"] == "top"
