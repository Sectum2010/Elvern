from __future__ import annotations

from pathlib import Path

from backend.app.db import get_connection, utcnow_iso
from backend.app.media_scan import scan_media_library
from backend.app.services.desktop_playback_service import resolve_same_host_request


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _logout(client) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _create_standard_user_via_admin(client, *, username: str, password: str) -> None:
    response = client.post(
        "/api/admin/users",
        json={
            "username": username,
            "password": password,
            "role": "standard_user",
            "enabled": True,
        },
    )
    assert response.status_code == 200


def test_health_endpoint_smoke(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "Elvern"}


def test_auth_login_me_logout_smoke(client, admin_credentials) -> None:
    unauthenticated = client.get("/api/auth/me")
    assert unauthenticated.status_code == 401

    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200
    assert login_response.cookies
    assert login_response.json()["user"]["username"] == admin_credentials["username"]
    assert login_response.json()["user"]["session_id"] is None

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["username"] == admin_credentials["username"]
    assert me_response.json()["user"]["role"] == "admin"
    assert me_response.json()["user"]["session_id"] is not None

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"message": "Logged out"}

    after_logout = client.get("/api/auth/me")
    assert after_logout.status_code == 401


def test_admin_media_library_reference_smoke(client, admin_credentials) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    initial_response = client.get("/api/admin/media-library-reference")
    assert initial_response.status_code == 200
    initial_path = Path(initial_response.json()["effective_value"])
    assert initial_response.json() == {
        "configured_value": str(initial_path),
        "effective_value": str(initial_path),
        "default_value": str(initial_path),
        "validation_rules": [
            f"Leave blank to reset to the bootstrap shared local path: {initial_path}",
            "This is the real shared local library path currently used by Elvern for the shared local library.",
            "Use an absolute Linux directory path that already exists on this host.",
        ],
    }

    replacement_path = initial_path.parent / "shared-library-alt"
    replacement_path.mkdir()

    update_response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(replacement_path)},
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        "configured_value": str(replacement_path),
        "effective_value": str(replacement_path),
        "default_value": str(initial_path),
        "validation_rules": [
            f"Leave blank to reset to the bootstrap shared local path: {initial_path}",
            "This is the real shared local library path currently used by Elvern for the shared local library.",
            "Use an absolute Linux directory path that already exists on this host.",
        ],
    }


def test_admin_local_directory_browse_lists_host_directories_and_stays_admin_only(
    client,
    admin_credentials,
    tmp_path,
) -> None:
    browse_root = tmp_path / "browse-root"
    browse_root.mkdir()
    alpha_dir = browse_root / "alpha"
    beta_dir = browse_root / "beta"
    alpha_dir.mkdir()
    beta_dir.mkdir()
    (browse_root / "ignore-file.txt").write_text("not a directory", encoding="utf-8")

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    response = client.get(
        "/api/admin/local-directories",
        params={"path": str(browse_root)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "current_path": str(browse_root.resolve()),
        "parent_path": str(browse_root.resolve().parent),
        "directories": [
            {"name": "alpha", "path": str(alpha_dir.resolve())},
            {"name": "beta", "path": str(beta_dir.resolve())},
        ],
    }

    _create_standard_user_via_admin(client, username="browse-user", password="browse-user-password")
    _logout(client)
    _login(client, username="browse-user", password="browse-user-password")

    forbidden = client.get(
        "/api/admin/local-directories",
        params={"path": str(browse_root)},
    )
    assert forbidden.status_code == 403


def test_admin_local_directory_picker_returns_selected_host_directory_and_stays_admin_only(
    client,
    admin_credentials,
    monkeypatch,
    tmp_path,
) -> None:
    selected_dir = tmp_path / "picked-host-directory"
    selected_dir.mkdir()

    monkeypatch.setattr(
        "backend.app.routes.admin.resolve_same_host_request",
        lambda settings, *, platform, client_ip, request_host, explicit_same_host: {
            "same_host": True,
            "detection_source": "test",
            "reason": "test",
        },
    )
    monkeypatch.setattr(
        "backend.app.routes.admin.try_pick_local_directory",
        lambda settings, *, path, title: {
            "status": "selected",
            "selected_path": str(selected_dir.resolve()),
            "reason": None,
            "picker_backend": "zenity",
        },
    )

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    response = client.post(
        "/api/admin/local-directory-picker",
        json={
            "path": str(tmp_path),
            "title": "Select poster directory",
            "platform": "linux",
            "same_host_hint": True,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "selected",
        "selected_path": str(selected_dir.resolve()),
        "reason": None,
        "picker_backend": "zenity",
    }

    _create_standard_user_via_admin(client, username="picker-user", password="picker-password")
    _logout(client)
    _login(client, username="picker-user", password="picker-password")

    forbidden = client.post(
        "/api/admin/local-directory-picker",
        json={
            "path": str(tmp_path),
            "title": "Select poster directory",
            "platform": "linux",
            "same_host_hint": True,
        },
    )
    assert forbidden.status_code == 403


def test_admin_local_directory_picker_capability_is_admin_only_and_reports_same_host_linux_support(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routes.admin.resolve_same_host_request",
        lambda settings, *, platform, client_ip, request_host, explicit_same_host: {
            "same_host": platform == "linux",
            "detection_source": "local_server_ip_match",
            "reason": "Client IP matched a resolved local server address.",
        },
    )
    monkeypatch.setattr(
        "backend.app.routes.admin.get_native_local_directory_picker_capability",
        lambda: {
            "native_picker_supported": True,
            "picker_backend": "zenity",
            "gui_session_available": True,
            "display_available": True,
            "wayland_available": False,
            "dbus_session_available": True,
            "missing_dependency": None,
            "reason": None,
        },
    )

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    response = client.get(
        "/api/admin/local-directory-picker/capability",
        params={"platform": "linux"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "native_picker_supported": True,
        "same_host_linux": True,
        "same_host_detection_source": "local_server_ip_match",
        "same_host_reason": "Client IP matched a resolved local server address.",
        "picker_backend": "zenity",
        "gui_session_available": True,
        "display_available": True,
        "wayland_available": False,
        "dbus_session_available": True,
        "missing_dependency": None,
        "reason": None,
    }

    _create_standard_user_via_admin(client, username="picker-cap-user", password="picker-cap-password")
    _logout(client)
    _login(client, username="picker-cap-user", password="picker-cap-password")

    forbidden = client.get(
        "/api/admin/local-directory-picker/capability",
        params={"platform": "linux"},
    )
    assert forbidden.status_code == 403


def test_resolve_same_host_request_uses_request_host_candidates(initialized_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.desktop_playback_service._resolve_host_ips",
        lambda host: {"100.64.0.9"} if host == "spark.test.ts.net" else set(),
    )

    result = resolve_same_host_request(
        initialized_settings,
        platform="linux",
        client_ip="100.64.0.9",
        request_host="spark.test.ts.net",
    )

    assert result == {
        "same_host": True,
        "detection_source": "local_server_ip_match",
        "reason": "Client IP matched a resolved local server address.",
    }


def test_standard_user_private_media_library_reference_uses_shared_default_and_stays_private(
    client,
    admin_credentials,
) -> None:
    alice_password = "alice-family-password"
    bob_password = "bob-family-password"

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )
    _create_standard_user_via_admin(client, username="alice-reference", password=alice_password)
    _create_standard_user_via_admin(client, username="bob-reference", password=bob_password)

    initial_path = Path(client.get("/api/admin/media-library-reference").json()["effective_value"])
    replacement_path = initial_path.parent / "shared-basement-library"
    replacement_path.mkdir()

    shared_update = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(replacement_path)},
    )
    assert shared_update.status_code == 200
    assert shared_update.json()["effective_value"] == str(replacement_path)

    admin_private_attempt = client.patch(
        "/api/user-settings",
        json={"media_library_reference_private_value": "Admin Private Reference"},
    )
    assert admin_private_attempt.status_code == 403
    assert admin_private_attempt.json()["detail"] == "Only standard users can set a private media library reference"

    _logout(client)

    _login(client, username="alice-reference", password=alice_password)
    alice_initial = client.get("/api/user-settings")
    assert alice_initial.status_code == 200
    assert alice_initial.json()["media_library_reference_private_value"] is None
    assert alice_initial.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert alice_initial.json()["media_library_reference_effective_value"] == str(replacement_path)

    alice_update = client.patch(
        "/api/user-settings",
        json={"media_library_reference_private_value": "Alice Shelf A"},
    )
    assert alice_update.status_code == 200
    assert alice_update.json()["media_library_reference_private_value"] == "Alice Shelf A"
    assert alice_update.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert alice_update.json()["media_library_reference_effective_value"] == "Alice Shelf A"
    _logout(client)

    _login(client, username="bob-reference", password=bob_password)
    bob_initial = client.get("/api/user-settings")
    assert bob_initial.status_code == 200
    assert bob_initial.json()["media_library_reference_private_value"] is None
    assert bob_initial.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert bob_initial.json()["media_library_reference_effective_value"] == str(replacement_path)
    _logout(client)

    _login(client, username="alice-reference", password=alice_password)
    alice_repeat = client.get("/api/user-settings")
    assert alice_repeat.status_code == 200
    assert alice_repeat.json()["media_library_reference_private_value"] == "Alice Shelf A"
    assert alice_repeat.json()["media_library_reference_effective_value"] == "Alice Shelf A"

    alice_clear = client.patch(
        "/api/user-settings",
        json={"media_library_reference_private_value": ""},
    )
    assert alice_clear.status_code == 200
    assert alice_clear.json()["media_library_reference_private_value"] is None
    assert alice_clear.json()["media_library_reference_shared_default_value"] == str(replacement_path)
    assert alice_clear.json()["media_library_reference_effective_value"] == str(replacement_path)
    _logout(client)

    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )
    admin_global = client.get("/api/admin/media-library-reference")
    assert admin_global.status_code == 200
    assert admin_global.json()["configured_value"] == str(replacement_path)
    assert admin_global.json()["effective_value"] == str(replacement_path)


def test_admin_shared_local_library_path_switch_rebuilds_visible_library(client, admin_credentials, tmp_path) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    original_root = Path(client.app.state.settings.media_root)
    original_movie = original_root / "Who.Am.I.2014.mp4"
    original_movie.write_bytes(b"original shared local movie")
    scan_media_library(client.app.state.settings, reason="manual")

    before_switch = client.get("/api/library")
    assert before_switch.status_code == 200
    assert before_switch.json()["total_items"] == 1
    original_item_id = int(before_switch.json()["items"][0]["id"])

    empty_root = tmp_path / "empty-shared-library"
    empty_root.mkdir()

    update_response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(empty_root)},
    )
    assert update_response.status_code == 200
    assert update_response.json()["effective_value"] == str(empty_root.resolve())

    after_switch = client.get("/api/library")
    assert after_switch.status_code == 200
    assert after_switch.json()["total_items"] == 0
    assert after_switch.json()["items"] == []
    assert after_switch.json()["recently_added"] == []
    assert after_switch.json()["continue_watching"] == []
    assert after_switch.json()["series_rails"] == []

    search_after_switch = client.get("/api/library/search", params={"q": "whoami"})
    assert search_after_switch.status_code == 200
    assert search_after_switch.json()["total_items"] == 0
    assert search_after_switch.json()["items"] == []

    detail_after_switch = client.get(f"/api/library/item/{original_item_id}")
    assert detail_after_switch.status_code == 404
    assert detail_after_switch.json()["detail"] == "Media item not found"

    stream_after_switch = client.get(f"/api/stream/{original_item_id}")
    assert stream_after_switch.status_code == 404
    assert stream_after_switch.json()["detail"] == "Media item not found"

    with get_connection(client.app.state.settings) as connection:
        local_row_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM media_items
            WHERE COALESCE(source_kind, 'local') = 'local'
            """
        ).fetchone()

    assert int(local_row_count["count"]) == 0


def test_legacy_unbound_local_rows_do_not_leak_through_shared_library_visibility(
    client,
    admin_credentials,
    tmp_path,
) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    empty_root = tmp_path / "empty-shared-library"
    empty_root.mkdir()
    update_response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(empty_root)},
    )
    assert update_response.status_code == 200

    legacy_file = tmp_path / "legacy-leak.mp4"
    legacy_file.write_bytes(b"legacy local row")
    now = utcnow_iso()

    with get_connection(client.app.state.settings) as connection:
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                library_source_id,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', NULL, ?, ?, NULL, NULL, NULL, NULL, NULL, 'mp4', ?, ?, ?, ?)
            """,
            (
                "Legacy Leak",
                legacy_file.name,
                str(legacy_file.resolve()),
                int(legacy_file.stat().st_size),
                float(legacy_file.stat().st_mtime),
                2020,
                now,
                now,
                now,
            ),
        )
        legacy_item_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.commit()

    library_response = client.get("/api/library")
    assert library_response.status_code == 200
    assert library_response.json()["total_items"] == 0
    assert library_response.json()["items"] == []

    search_response = client.get("/api/library/search", params={"q": "legacyleak"})
    assert search_response.status_code == 200
    assert search_response.json()["total_items"] == 0
    assert search_response.json()["items"] == []

    detail_response = client.get(f"/api/library/item/{legacy_item_id}")
    assert detail_response.status_code == 404
    assert detail_response.json()["detail"] == "Media item not found"

    stream_response = client.get(f"/api/stream/{legacy_item_id}")
    assert stream_response.status_code == 404
    assert stream_response.json()["detail"] == "Media item not found"


def test_admin_shared_local_library_path_rejects_missing_directory(client, admin_credentials, tmp_path) -> None:
    _login(
        client,
        username=admin_credentials["username"],
        password=admin_credentials["password"],
    )

    missing_path = tmp_path / "missing-shared-library"
    response = client.put(
        "/api/admin/media-library-reference",
        json={"value": str(missing_path)},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Shared local library path does not exist on this host."
