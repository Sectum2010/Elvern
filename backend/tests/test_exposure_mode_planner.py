from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pyotp
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.db import get_connection, utcnow_iso
from backend.app.models import AuthenticatedUser
from backend.app.services import exposure_mode_service as service
from backend.app.services import exposure_maintenance_service as maintenance_service
from backend.app.services.account_access_service import generate_invite_code
from backend.app.services.admin_service import create_user, update_user
from backend.app.services.app_settings_service import set_global_app_setting
from backend.app.services.native_playback_service import (
    create_native_playback_session,
    get_native_playback_session_payload,
    inspect_native_playback_access,
)


MAINTENANCE_MESSAGE = "The server is currently under construction, please try again later"


def _request_with_origin(
    settings,
    *,
    origin: str = "http://testserver",
    peer_host: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
    url_prefix: str | None = "abcdfghj",
) -> Request:
    parsed = service.normalize_origin(origin)
    host = parsed.get("origin", origin).split("://", 1)[1]
    scheme = str(parsed.get("scheme") or origin.split("://", 1)[0])
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/admin/exposure/validate",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {"Host": host}).items()
        ],
        "client": (peer_host, 50000),
        "server": (host, 80),
        "scheme": scheme,
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings, url_prefix=url_prefix)),
    }
    return Request(scope)


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _admin_actor(settings) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1,
        username=settings.admin_username,
        role="admin",
        enabled=True,
        assistant_beta_enabled=False,
        age_credential=18,
    )


def _create_user(
    settings,
    *,
    username: str,
    password: str = "standard-user-password",
    role: str = "standard_user",
    enabled: bool = True,
) -> dict[str, object]:
    return create_user(
        settings,
        username=username,
        password=password,
        role=role,
        enabled=enabled,
        age_credential=18,
        actor=_admin_actor(settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


def _session_row_for_username(settings, username: str):
    with get_connection(settings) as connection:
        return connection.execute(
            """
            SELECT s.id, s.user_id, s.revoked_at, s.revoked_reason
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (username,),
        ).fetchone()


def _create_media_item(settings, *, relative_name: str = "movie.mp4") -> dict[str, object]:
    media_file = Path(settings.media_root) / relative_name
    media_file.write_bytes(b"not a real media file")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
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
            ) VALUES (?, ?, ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Maintenance Test Movie",
                media_file.name,
                str(media_file),
                media_file.stat().st_size,
                media_file.stat().st_mtime,
                120.0,
                None,
                None,
                "h264",
                "aac",
                "mp4",
                2024,
                now,
                now,
                now,
            ),
        )
        connection.commit()
        item_id = int(cursor.lastrowid)
    return {
        "id": item_id,
        "title": "Maintenance Test Movie",
        "original_filename": media_file.name,
        "file_path": str(media_file),
        "source_kind": "local",
        "duration_seconds": 120.0,
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "resume_position_seconds": 0,
        "subtitles": [],
    }


def _prepare_request_payload(admin_credentials) -> dict[str, object]:
    return {
        "current_admin_password": admin_credentials["password"],
        "acknowledgement": True,
    }


def _save_private_pending_draft(client, admin_credentials, *, private_origin: str = "http://192.168.1.10:4173"):
    response = client.post(
        "/api/admin/exposure/drafts",
        json={
            "desired_mode": "private",
            "private_origin": private_origin,
            "acknowledgement": True,
            "current_admin_password": admin_credentials["password"],
        },
    )
    assert response.status_code == 200
    return response


def _save_public_custom_pending_draft(client, admin_credentials, *, public_origin: str = "https://media.example.com"):
    response = client.post(
        "/api/admin/exposure/drafts",
        json={
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": public_origin,
            "reverse_proxy_provider": "caddy",
            "acknowledgement": True,
            "current_admin_password": admin_credentials["password"],
        },
    )
    assert response.status_code == 200
    return response


def _save_public_direct_ip_pending_draft(client, admin_credentials, *, public_origin: str = "http://203.0.113.10:4173"):
    response = client.post(
        "/api/admin/exposure/drafts",
        json={
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": public_origin,
            "reverse_proxy_provider": "manual_other",
            "acknowledgement": True,
            "direct_ip_not_recommended_acknowledgement": True,
            "current_admin_password": admin_credentials["password"],
        },
    )
    assert response.status_code == 200
    return response


def _enable_maintenance_lock(settings) -> None:
    maintenance_service.set_exposure_maintenance_lock(
        settings,
        _admin_actor(settings),
        enabled=True,
    )


def _store_prepared_switch(
    settings,
    desired: dict[str, object],
    *,
    status: str = "prepared_for_manual_apply",
    verification: dict[str, object] | None = None,
) -> dict:
    prepared_switch = {
        "status": status,
        "prepared_at": utcnow_iso(),
        "prepared_by_user_id": 1,
        "prepared_by_username": settings.admin_username,
        "takes_effect": False,
        "desired": desired,
        "validation": {"status": "warnings", "errors": [], "warnings": [], "checks": []},
        "plan": {},
        "env_block": "",
        "manual_steps": [],
        "restart_required": True,
        "maintenance_mode_enabled": True,
        "maintenance_mode_auto_enabled": True,
        "verification_required": True,
        "current_origin_match_required_in_phase": "phase_4_verification",
        "url_prefix_rotation": "manual_only",
        "env_writing": "manual_only",
        "activation_not_implemented": True,
    }
    if verification is not None:
        prepared_switch["verification"] = verification
    if status == service.PREPARED_SWITCH_STATUS_VERIFIED:
        prepared_switch["verified_at"] = utcnow_iso()
        prepared_switch["verified_by_user_id"] = 1
        prepared_switch["verified_by_username"] = settings.admin_username
        prepared_switch["maintenance_mode_release"] = "manual_only"
    set_global_app_setting(
        settings,
        key=service.EXPOSURE_MODE_PREPARED_SWITCH_KEY,
        value=json.dumps(prepared_switch, sort_keys=True),
    )
    return prepared_switch


def _public_custom_runtime_settings(settings, **overrides):
    values = {
        "private_network_only": False,
        "public_app_origin": "https://media.example.com",
        "backend_origin": "",
        "cookie_secure": True,
        "trusted_proxy_cidrs": ("127.0.0.1/8",),
    }
    values.update(overrides)
    return replace(settings, **values)


def _public_direct_ip_runtime_settings(settings, **overrides):
    values = {
        "private_network_only": False,
        "public_app_origin": "http://203.0.113.10:4173",
        "backend_origin": "",
        "cookie_secure": False,
        "trusted_proxy_cidrs": ("127.0.0.1/8",),
    }
    values.update(overrides)
    return replace(settings, **values)


def _private_runtime_settings(settings, **overrides):
    values = {
        "private_network_only": True,
        "public_app_origin": "",
        "backend_origin": "",
        "cookie_secure": False,
        "trusted_proxy_cidrs": ("127.0.0.1/8",),
    }
    values.update(overrides)
    return replace(settings, **values)


def _check_statuses(verification: dict[str, object]) -> dict[str, str]:
    checks = verification.get("checks", [])
    assert isinstance(checks, list)
    return {
        str(check["name"]): str(check["status"])
        for check in checks
        if isinstance(check, dict) and "name" in check and "status" in check
    }


def test_origin_normalization_accepts_origin_and_rejects_path_query_fragment() -> None:
    valid = service.normalize_origin("https://media.example.com")
    assert valid["ok"] is True
    assert valid["origin"] == "https://media.example.com"

    for value in (
        "https://media.example.com/path",
        "https://media.example.com?debug=1",
        "https://media.example.com#fragment",
    ):
        invalid = service.normalize_origin(value)
        assert invalid["ok"] is False
        assert "Origin must contain only scheme, host, and optional port." in invalid["errors"]


def test_public_custom_domain_validation_blocks_localhost_http_and_raw_ip(test_settings) -> None:
    request = _request_with_origin(test_settings, origin="https://media.example.com")

    localhost = service.validate_exposure_plan(
        test_settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://localhost",
            "reverse_proxy_provider": "caddy",
        },
    )
    assert localhost["validation"]["status"] == "blocked"
    assert any("localhost" in error for error in localhost["validation"]["errors"])

    http_domain = service.validate_exposure_plan(
        test_settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "http://media.example.com",
            "reverse_proxy_provider": "nginx",
        },
    )
    assert http_domain["validation"]["status"] == "blocked"
    assert "Public custom domains must use HTTPS to be considered ready." in http_domain["validation"]["errors"]

    raw_ip = service.validate_exposure_plan(
        test_settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://203.0.113.10",
            "reverse_proxy_provider": "caddy",
        },
    )
    assert raw_ip["validation"]["status"] == "blocked"
    assert any("DNS name" in error for error in raw_ip["validation"]["errors"])


def test_https_public_custom_domain_validates_with_current_origin_match(test_settings) -> None:
    request = _request_with_origin(
        test_settings,
        origin="http://127.0.0.1",
        headers={
            "Host": "127.0.0.1",
            "X-Forwarded-Host": "media.example.com",
            "X-Forwarded-Proto": "https",
        },
    )

    result = service.validate_exposure_plan(
        test_settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
            "reverse_proxy_provider": "cloudflare_tunnel",
        },
    )

    assert result["validation"]["errors"] == []
    assert any(
        check["name"] == "current_origin_match" and check["status"] == "pass"
        for check in result["validation"]["checks"]
    )


def test_current_request_origin_ignores_forwarded_headers_from_untrusted_peer(test_settings) -> None:
    settings = replace(test_settings, trusted_proxy_cidrs=())
    request = _request_with_origin(
        settings,
        origin="http://192.0.2.55:4173",
        peer_host="192.0.2.55",
        headers={
            "Host": "192.0.2.55:4173",
            "X-Forwarded-Host": "media.example.com",
            "X-Forwarded-Proto": "https",
        },
    )

    assert service.resolve_current_request_origin(settings, request) == "http://192.0.2.55:4173"


def test_public_direct_ip_accepts_documentation_ip_with_not_recommended_warning(test_settings) -> None:
    request = _request_with_origin(test_settings, origin="http://203.0.113.10:4173")

    result = service.validate_exposure_plan(
        test_settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": "http://203.0.113.10:4173",
            "reverse_proxy_provider": "manual_other",
        },
    )

    assert result["validation"]["errors"] == []
    assert service.DIRECT_PUBLIC_IP_WARNING in result["validation"]["warnings"]
    assert result["desired"]["public_origin"] == "http://203.0.113.10:4173"
    assert "ELVERN_COOKIE_SECURE=false" in service.build_copyable_env_block(result["plan"])


def test_public_direct_ip_https_keeps_secure_cookie_suggestion(test_settings) -> None:
    request = _request_with_origin(test_settings, origin="https://203.0.113.10")

    result = service.validate_exposure_plan(
        test_settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": "https://203.0.113.10",
            "reverse_proxy_provider": "manual_other",
        },
    )

    assert result["validation"]["errors"] == []
    assert service.DIRECT_PUBLIC_IP_WARNING in result["validation"]["warnings"]
    assert "ELVERN_COOKIE_SECURE=true" in service.build_copyable_env_block(result["plan"])


def test_public_direct_ip_rejects_private_loopback_and_link_local(test_settings) -> None:
    request = _request_with_origin(test_settings, origin="http://testserver")
    for origin in ("http://10.0.0.4:4173", "http://127.0.0.1:4173", "http://169.254.10.1:4173"):
        result = service.validate_exposure_plan(
            test_settings,
            request,
            {
                "desired_mode": "public",
                "public_entry_kind": "direct_ip",
                "public_origin": origin,
                "reverse_proxy_provider": "manual_other",
            },
        )
        assert result["validation"]["status"] == "blocked"
        assert any("Direct public IP mode requires" in error for error in result["validation"]["errors"])


def test_provider_choices_do_not_include_tailscale_funnel(client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.post(
        "/api/admin/exposure/validate",
        json={
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
            "reverse_proxy_provider": "tailscale_funnel",
        },
    )

    assert response.status_code == 422
    status = client.get("/api/admin/exposure/status")
    assert status.status_code == 200
    assert "tailscale_funnel" not in status.json()["provider_choices"]


def test_current_origin_mismatch_tells_admin_to_open_proposed_address(test_settings) -> None:
    request = _request_with_origin(test_settings, origin="http://testserver")

    result = service.validate_exposure_plan(
        test_settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
            "reverse_proxy_provider": "caddy",
        },
    )

    assert service.CURRENT_ORIGIN_REVALIDATION_MESSAGE in result["validation"]["warnings"]
    assert any(check["name"] == "current_origin_match" and check["status"] == "warn" for check in result["validation"]["checks"])


def test_validation_service_has_no_backend_origin_probe() -> None:
    source = Path("backend/app/services/exposure_mode_service.py").read_text(encoding="utf-8")

    assert "requests." not in source
    assert "httpx." not in source
    assert "urllib.request" not in source
    assert "urlopen" not in source
    assert "fetch(" not in source


def test_draft_save_requires_admin_password_and_acknowledgement(client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    payload = {
        "desired_mode": "private",
        "private_origin": "http://192.168.1.10:4173",
        "acknowledgement": False,
        "current_admin_password": admin_credentials["password"],
    }

    missing_ack = client.post("/api/admin/exposure/drafts", json=payload)
    assert missing_ack.status_code == 400

    payload["acknowledgement"] = True
    payload["current_admin_password"] = ""
    missing_password = client.post("/api/admin/exposure/drafts", json=payload)
    assert missing_password.status_code == 400


def test_draft_save_stores_inert_app_setting_without_runtime_mutation(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    original_private_network_only = initialized_settings.private_network_only
    original_public_app_origin = initialized_settings.public_app_origin
    original_trusted_proxy_cidrs = initialized_settings.trusted_proxy_cidrs

    response = client.post(
        "/api/admin/exposure/drafts",
        json={
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": "http://203.0.113.10:4173",
            "reverse_proxy_provider": "manual_other",
            "acknowledgement": True,
            "direct_ip_not_recommended_acknowledgement": True,
            "current_admin_password": admin_credentials["password"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["takes_effect"] is False
    assert body["pending_draft"]["takes_effect"] is False
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (service.EXPOSURE_MODE_PENDING_DRAFT_KEY,),
        ).fetchone()
    assert row is not None
    assert initialized_settings.private_network_only == original_private_network_only
    assert initialized_settings.public_app_origin == original_public_app_origin
    assert initialized_settings.trusted_proxy_cidrs == original_trusted_proxy_cidrs


def test_direct_ip_draft_requires_not_recommended_acknowledgement(client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.post(
        "/api/admin/exposure/drafts",
        json={
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": "http://203.0.113.10:4173",
            "reverse_proxy_provider": "manual_other",
            "acknowledgement": True,
            "direct_ip_not_recommended_acknowledgement": False,
            "current_admin_password": admin_credentials["password"],
        },
    )

    assert response.status_code == 400
    assert service.DIRECT_PUBLIC_IP_WARNING in response.json()["detail"]


def test_draft_clear_removes_only_pending_draft(client, initialized_settings, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    service.save_pending_exposure_draft(
        initialized_settings,
        SimpleNamespace(id=1, username="admin"),
        {},
        validation_snapshot={
            "desired": {"desired_mode": "private"},
            "validation": {"status": "ready", "errors": [], "warnings": [], "checks": []},
            "plan": {"env_suggestions": [], "manual_steps": [], "reverse_proxy_notes": [], "activation_notes": []},
            "takes_effect": False,
        },
    )

    response = client.delete("/api/admin/exposure/drafts")

    assert response.status_code == 200
    assert response.json()["pending_draft"] is None
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (service.EXPOSURE_MODE_PENDING_DRAFT_KEY,),
        ).fetchone()
    assert row is None


def test_security_exposure_maintenance_lock_defaults_and_storage(initialized_settings) -> None:
    default_state = maintenance_service.get_exposure_maintenance_lock(initialized_settings)
    assert default_state["enabled"] is False
    assert default_state["message"] == MAINTENANCE_MESSAGE

    set_global_app_setting(
        initialized_settings,
        key=maintenance_service.EXPOSURE_MODE_MAINTENANCE_LOCK_KEY,
        value="{not-json",
    )
    invalid_state = maintenance_service.get_exposure_maintenance_lock(initialized_settings)
    assert invalid_state["enabled"] is False

    enabled_state = maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=True,
    )
    assert enabled_state["enabled"] is True
    assert enabled_state["message"] == MAINTENANCE_MESSAGE
    assert enabled_state["created_by_username"] == initialized_settings.admin_username
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (maintenance_service.EXPOSURE_MODE_MAINTENANCE_LOCK_KEY,),
        ).fetchone()
    assert row is not None

    disabled_state = maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=False,
    )
    assert disabled_state["enabled"] is False
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (maintenance_service.EXPOSURE_MODE_MAINTENANCE_LOCK_KEY,),
        ).fetchone()
    assert row is None


def test_security_maintenance_mode_routes_require_admin_password_and_ack(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    assert client.get("/api/admin/maintenance-mode").status_code == 401

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    missing_ack = client.post(
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert missing_ack.status_code == 400

    wrong_password = client.post(
        "/api/admin/maintenance-mode",
        json={"current_admin_password": "wrong-password", "acknowledgement": True},
    )
    assert wrong_password.status_code == 401

    response = client.post(
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["revoked_non_admin_sessions"] == 0
    assert response.json()["affected_non_admin_users"] == 0

    status = client.get("/api/admin/exposure/status")
    assert status.status_code == 200
    assert status.json()["active"]["maintenance_lock"]["enabled"] is True
    assert status.json()["active"]["maintenance_mode"]["enabled"] is True

    wrong_disable = client.request(
        "DELETE",
        "/api/admin/maintenance-mode",
        json={"current_admin_password": "wrong-password", "acknowledgement": False},
    )
    assert wrong_disable.status_code == 401

    disabled = client.request(
        "DELETE",
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert maintenance_service.get_exposure_maintenance_lock(initialized_settings)["enabled"] is False

    legacy = client.post(
        "/api/admin/exposure/maintenance-lock",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert legacy.status_code == 200
    assert legacy.json()["enabled"] is True


def test_security_exposure_maintenance_lock_does_not_mutate_pending_draft_or_users(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_user(initialized_settings, username="draft-standard-user")
    service.save_pending_exposure_draft(
        initialized_settings,
        _admin_actor(initialized_settings),
        {},
        validation_snapshot={
            "desired": {"desired_mode": "private"},
            "validation": {"status": "ready", "errors": [], "warnings": [], "checks": []},
            "plan": {"env_suggestions": [], "manual_steps": [], "reverse_proxy_notes": [], "activation_notes": []},
            "takes_effect": False,
        },
    )
    with get_connection(initialized_settings) as connection:
        before_users = [
            (row["id"], row["enabled"])
            for row in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]

    response = client.post(
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )

    assert response.status_code == 200
    assert response.json()["revoked_non_admin_sessions"] == 0
    with get_connection(initialized_settings) as connection:
        after_users = [
            (row["id"], row["enabled"])
            for row in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]
        draft_row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (service.EXPOSURE_MODE_PENDING_DRAFT_KEY,),
        ).fetchone()
    assert after_users == before_users
    assert draft_row is not None


def test_security_exposure_maintenance_lock_blocks_standard_login_without_session(
    client,
    initialized_settings,
) -> None:
    _create_user(initialized_settings, username="blocked-standard")
    maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=True,
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "blocked-standard", "password": "standard-user-password"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == MAINTENANCE_MESSAGE
    assert _session_row_for_username(initialized_settings, "blocked-standard") is None


def test_security_maintenance_mode_blocks_standard_totp_completion_without_session(
    client,
    initialized_settings,
) -> None:
    _create_user(initialized_settings, username="totp-standard")
    _login(client, username="totp-standard", password="standard-user-password")
    setup = client.post("/api/auth/totp/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    verify = client.post("/api/auth/totp/setup/verify", json={"code": pyotp.TOTP(secret).now()})
    assert verify.status_code == 200
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE users SET totp_last_used_window = NULL WHERE username = ?",
            ("totp-standard",),
        )
        connection.commit()
    assert client.post("/api/auth/logout").status_code == 200

    challenge = client.post(
        "/api/auth/login",
        json={"username": "totp-standard", "password": "standard-user-password"},
    )
    assert challenge.status_code == 200
    assert challenge.json()["session"] == "pending_totp"
    maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=True,
    )

    response = client.post(
        "/api/auth/login/totp",
        json={"challenge_token": challenge.json()["challenge_token"], "code": pyotp.TOTP(secret).now()},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == MAINTENANCE_MESSAGE
    assert _session_row_for_username(initialized_settings, "totp-standard")["revoked_reason"] == "logout"


def test_security_exposure_maintenance_lock_keeps_disabled_login_behavior(
    client,
    initialized_settings,
) -> None:
    _create_user(initialized_settings, username="disabled-standard", enabled=False)
    maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=True,
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "disabled-standard", "password": "standard-user-password"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "This account has been disabled"


def test_security_exposure_maintenance_lock_allows_admin_login_and_admin_routes(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=True,
    )

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    response = client.get("/api/admin/exposure/status")

    assert response.status_code == 200
    assert response.json()["active"]["maintenance_lock"]["enabled"] is True


def test_security_maintenance_mode_revokes_existing_standard_sessions_without_disabling_users(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _create_user(initialized_settings, username="session-standard")
    _login(client, username="session-standard", password="standard-user-password")
    standard_token = client.cookies.get(initialized_settings.session_cookie_name)
    session_row = _session_row_for_username(initialized_settings, "session-standard")
    assert session_row is not None

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    admin_token = client.cookies.get(initialized_settings.session_cookie_name)
    admin_session_row = _session_row_for_username(initialized_settings, admin_credentials["username"])
    response = client.post(
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert response.status_code == 200
    assert response.json()["revoked_non_admin_sessions"] == 1
    assert response.json()["affected_non_admin_users"] == 1

    client.cookies.set(initialized_settings.session_cookie_name, standard_token)
    response = client.get("/api/auth/me")

    assert response.status_code == 503
    assert response.json()["detail"] == MAINTENANCE_MESSAGE
    with get_connection(initialized_settings) as connection:
        fresh_session = connection.execute(
            "SELECT revoked_at, revoked_reason FROM sessions WHERE id = ?",
            (session_row["id"],),
        ).fetchone()
        fresh_admin_session = connection.execute(
            "SELECT revoked_at FROM sessions WHERE id = ?",
            (admin_session_row["id"],),
        ).fetchone()
        user_row = connection.execute(
            "SELECT enabled FROM users WHERE username = ?",
            ("session-standard",),
        ).fetchone()
    assert fresh_session["revoked_at"] is not None
    assert fresh_session["revoked_reason"] == "maintenance_mode"
    assert fresh_admin_session["revoked_at"] is None
    assert user_row["enabled"] == 1

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "Logged out"

    client.cookies.set(initialized_settings.session_cookie_name, admin_token)
    disable_response = client.request(
        "DELETE",
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert disable_response.status_code == 200
    with get_connection(initialized_settings) as connection:
        still_revoked = connection.execute(
            "SELECT revoked_at, revoked_reason FROM sessions WHERE id = ?",
            (session_row["id"],),
        ).fetchone()
    assert still_revoked["revoked_at"] is not None
    assert still_revoked["revoked_reason"] == "maintenance_mode"


def test_security_maintenance_mode_preserves_decoupled_external_native_playback(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
) -> None:
    created_user = _create_user(initialized_settings, username="maintenance-native-external")
    _login(client, username="maintenance-native-external", password="standard-user-password")
    standard_session = _session_row_for_username(initialized_settings, "maintenance-native-external")
    assert standard_session is not None
    item = _create_media_item(initialized_settings, relative_name="maintenance-native-external.mp4")
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )
    native_session = create_native_playback_session(
        initialized_settings,
        user_id=int(created_user["id"]),
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS VLC Handoff",
        created_from_auth_session_id=int(standard_session["id"]),
    )

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    response = client.post(
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )

    assert response.status_code == 200
    assert response.json()["revoked_non_admin_sessions"] == 1
    with get_connection(initialized_settings) as connection:
        auth_row = connection.execute(
            "SELECT revoked_at, revoked_reason FROM sessions WHERE id = ?",
            (standard_session["id"],),
        ).fetchone()
        native_row = connection.execute(
            """
            SELECT auth_session_id, created_from_auth_session_id, revoked_at
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (str(native_session["session_id"]),),
        ).fetchone()

    assert auth_row["revoked_at"] is not None
    assert auth_row["revoked_reason"] == "maintenance_mode"
    assert native_row["auth_session_id"] is None
    assert native_row["created_from_auth_session_id"] == standard_session["id"]
    assert native_row["revoked_at"] is None

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is True
    assert access_state["reason"] == "allowed"
    payload = get_native_playback_session_payload(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert payload["session_id"] == native_session["session_id"]


def test_security_maintenance_mode_revokes_auth_session_coupled_native_playback(
    client,
    initialized_settings,
    admin_credentials,
    monkeypatch,
) -> None:
    created_user = _create_user(initialized_settings, username="maintenance-native-coupled")
    _login(client, username="maintenance-native-coupled", password="standard-user-password")
    standard_session = _session_row_for_username(initialized_settings, "maintenance-native-coupled")
    assert standard_session is not None
    item = _create_media_item(initialized_settings, relative_name="maintenance-native-coupled.mp4")
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )
    native_session = create_native_playback_session(
        initialized_settings,
        user_id=int(created_user["id"]),
        item=item,
        auth_session_id=int(standard_session["id"]),
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Linux Same-Host VLC",
        created_from_auth_session_id=int(standard_session["id"]),
    )

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    response = client.post(
        "/api/admin/maintenance-mode",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )

    assert response.status_code == 200
    with get_connection(initialized_settings) as connection:
        native_row = connection.execute(
            """
            SELECT auth_session_id, created_from_auth_session_id, revoked_at
            FROM native_playback_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (str(native_session["session_id"]),),
        ).fetchone()

    assert native_row["auth_session_id"] == standard_session["id"]
    assert native_row["created_from_auth_session_id"] == standard_session["id"]
    assert native_row["revoked_at"] is not None
    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is False
    assert access_state["reason"] == "native_session_revoked"


def test_security_user_disable_still_revokes_decoupled_external_native_playback(
    client,
    initialized_settings,
    monkeypatch,
) -> None:
    created_user = _create_user(initialized_settings, username="maintenance-native-disable")
    user_id = int(created_user["id"])
    _login(client, username="maintenance-native-disable", password="standard-user-password")
    standard_session = _session_row_for_username(initialized_settings, "maintenance-native-disable")
    assert standard_session is not None
    item = _create_media_item(initialized_settings, relative_name="maintenance-native-disable.mp4")
    monkeypatch.setattr(
        "backend.app.services.native_playback_service._probe_tracks",
        lambda file_path, settings, **kwargs: ([], []),
    )
    native_session = create_native_playback_session(
        initialized_settings,
        user_id=user_id,
        item=item,
        auth_session_id=None,
        user_agent="pytest",
        source_ip="127.0.0.1",
        client_name="Elvern iOS Infuse Handoff",
        created_from_auth_session_id=int(standard_session["id"]),
    )

    update_user(
        initialized_settings,
        user_id=user_id,
        enabled=False,
        role=None,
        current_admin_password=None,
        actor=_admin_actor(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    access_state = inspect_native_playback_access(
        initialized_settings,
        session_id=str(native_session["session_id"]),
        access_token=str(native_session["access_token"]),
    )
    assert access_state["allowed"] is False
    assert access_state["reason"] == "native_session_revoked"
    with pytest.raises(HTTPException) as exc_info:
        get_native_playback_session_payload(
            initialized_settings,
            session_id=str(native_session["session_id"]),
            access_token=str(native_session["access_token"]),
        )
    assert exc_info.value.status_code == 401


def test_security_exposure_maintenance_lock_blocks_signup_but_not_admin_user_create(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    invite_payload = generate_invite_code(
        initialized_settings,
        actor=_admin_actor(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=True,
    )

    signup_response = client.post(
        "/api/auth/signup",
        json={
            "username": "signup-standard",
            "password": "standard-user-password",
            "confirm_password": "standard-user-password",
            "invite_code": invite_payload["code"],
        },
    )

    assert signup_response.status_code == 503
    assert signup_response.json()["detail"] == MAINTENANCE_MESSAGE

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    create_response = client.post(
        "/api/admin/users",
        json={
            "username": "created-by-admin",
            "password": "standard-user-password",
            "role": "standard_user",
            "enabled": True,
            "age_credential": 18,
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["username"] == "created-by-admin"


def test_prepared_switch_default_and_routes_require_admin(client, admin_credentials) -> None:
    get_response = client.get("/api/admin/exposure/prepared-switch")
    assert get_response.status_code == 401

    verification_get_response = client.get("/api/admin/exposure/verification")
    assert verification_get_response.status_code == 401

    finalized_get_response = client.get("/api/admin/exposure/finalized-profile")
    assert finalized_get_response.status_code == 401

    post_response = client.post(
        "/api/admin/exposure/prepare-switch",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert post_response.status_code == 401

    verification_post_response = client.post(
        "/api/admin/exposure/verify-prepared-switch",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert verification_post_response.status_code == 401

    finalized_post_response = client.post(
        "/api/admin/exposure/finalize-profile",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert finalized_post_response.status_code == 401

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    response = client.get("/api/admin/exposure/prepared-switch")
    assert response.status_code == 200
    assert response.json() == {"prepared_switch": None, "takes_effect": False}

    verification_response = client.get("/api/admin/exposure/verification")
    assert verification_response.status_code == 200
    assert verification_response.json() == {
        "prepared_switch": None,
        "verification": {
            "status": "not_ready",
            "errors": [],
            "warnings": ["No prepared manual switch is available to verify."],
            "checks": [],
            "takes_effect": False,
        },
        "takes_effect": False,
    }

    finalized_response = client.get("/api/admin/exposure/finalized-profile")
    assert finalized_response.status_code == 200
    assert finalized_response.json() == {"finalized_profile": None, "takes_effect": False}

    status_response = client.get("/api/admin/exposure/status")
    assert status_response.status_code == 200
    assert status_response.json()["prepared_switch"] is None
    assert status_response.json()["finalized_profile"] is None
    assert status_response.json()["takes_effect"] is False


def test_verify_prepared_switch_route_requires_password_ack_prepared_switch_and_maintenance_mode(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    missing_password = client.post(
        "/api/admin/exposure/verify-prepared-switch",
        json={"acknowledgement": True},
    )
    assert missing_password.status_code == 422

    wrong_password = client.post(
        "/api/admin/exposure/verify-prepared-switch",
        json={"current_admin_password": "wrong-password", "acknowledgement": True},
    )
    assert wrong_password.status_code == 401

    missing_ack = client.post(
        "/api/admin/exposure/verify-prepared-switch",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert missing_ack.status_code == 400
    assert missing_ack.json()["detail"] == service.EXPOSURE_VERIFY_PREPARED_SWITCH_ACKNOWLEDGEMENT

    no_prepared_switch = client.post(
        "/api/admin/exposure/verify-prepared-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert no_prepared_switch.status_code == 400
    assert no_prepared_switch.json()["detail"]["verification"]["status"] == "blocked"

    _store_prepared_switch(
        initialized_settings,
        {"desired_mode": "private", "private_origin": ""},
    )
    maintenance_off = client.post(
        "/api/admin/exposure/verify-prepared-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert maintenance_off.status_code == 400
    assert maintenance_off.json()["detail"]["verification"]["status"] == "blocked"
    assert "Maintenance Mode must remain enabled" in maintenance_off.json()["detail"]["verification"]["errors"][0]

    _enable_maintenance_lock(initialized_settings)
    verified = client.post(
        "/api/admin/exposure/verify-prepared-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert verified.status_code == 200
    prepared_switch = verified.json()["prepared_switch"]
    assert prepared_switch["status"] == service.PREPARED_SWITCH_STATUS_VERIFIED
    assert prepared_switch["takes_effect"] is False
    assert prepared_switch["activation_not_implemented"] is True
    assert prepared_switch["maintenance_mode_release"] == "manual_only"
    assert prepared_switch["verified_by_username"] == admin_credentials["username"]
    assert verified.json()["verification"]["status"] == "passed"
    assert maintenance_service.get_exposure_maintenance_lock(initialized_settings)["enabled"] is True

    status_response = client.get("/api/admin/exposure/status")
    assert status_response.status_code == 200
    assert status_response.json()["prepared_switch"]["status"] == service.PREPARED_SWITCH_STATUS_VERIFIED

    verification_response = client.get("/api/admin/exposure/verification")
    assert verification_response.status_code == 200
    assert verification_response.json()["prepared_switch"]["status"] == service.PREPARED_SWITCH_STATUS_VERIFIED
    assert verification_response.json()["verification"]["status"] == "passed"
    assert verification_response.json()["takes_effect"] is False


def test_verify_public_custom_prepared_switch_passes_and_uses_runtime_allowlist(initialized_settings) -> None:
    settings = _public_custom_runtime_settings(initialized_settings)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
            "reverse_proxy_provider": "caddy",
        },
    )
    request = _request_with_origin(settings, origin="https://media.example.com")

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    verification = result["verification"]
    checks = _check_statuses(verification)
    assert verification["status"] == "passed"
    assert verification["errors"] == []
    assert result["takes_effect"] is False
    assert result["prepared_switch"]["status"] == service.PREPARED_SWITCH_STATUS_VERIFIED
    assert result["prepared_switch"]["verification"] == verification
    assert checks["current_origin_match"] == "pass"
    assert checks["private_network_only"] == "pass"
    assert checks["public_app_origin"] == "pass"
    assert checks["cookie_secure"] == "pass"


def test_verify_public_custom_prepared_switch_blocks_current_origin_mismatch(initialized_settings) -> None:
    settings = _public_custom_runtime_settings(initialized_settings)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
        },
    )
    request = _request_with_origin(settings, origin="https://admin.example.net")

    with pytest.raises(HTTPException) as exc_info:
        service.verify_prepared_exposure_switch(
            settings,
            request,
            _admin_actor(settings),
            acknowledgement=True,
        )

    assert exc_info.value.status_code == 400
    verification = exc_info.value.detail["verification"]
    assert verification["status"] == "blocked"
    assert _check_statuses(verification)["current_origin_match"] == "block"
    assert service.get_prepared_exposure_switch(settings)["status"] == service.PREPARED_SWITCH_STATUS_PREPARED


def test_verify_public_custom_blocks_public_app_origin_mismatch(initialized_settings) -> None:
    settings = _public_custom_runtime_settings(
        initialized_settings,
        public_app_origin="https://wrong.example.com",
    )
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
        },
    )
    request = _request_with_origin(settings, origin="https://media.example.com")

    with pytest.raises(HTTPException) as exc_info:
        service.verify_prepared_exposure_switch(
            settings,
            request,
            _admin_actor(settings),
            acknowledgement=True,
        )

    verification = exc_info.value.detail["verification"]
    assert verification["status"] == "blocked"
    assert _check_statuses(verification)["public_app_origin"] == "block"


def test_verify_public_custom_backend_origin_mismatch_warns_only(initialized_settings) -> None:
    settings = _public_custom_runtime_settings(
        initialized_settings,
        backend_origin="https://backend.example.net",
    )
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
        },
    )
    request = _request_with_origin(settings, origin="https://media.example.com")

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    verification = result["verification"]
    assert verification["status"] == "warnings"
    assert verification["errors"] == []
    assert _check_statuses(verification)["backend_origin"] == "warn"


def test_verify_public_direct_ip_http_allows_cookie_secure_false_and_warns(initialized_settings) -> None:
    settings = _public_direct_ip_runtime_settings(initialized_settings)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": "http://203.0.113.10:4173",
            "reverse_proxy_provider": "manual_other",
        },
    )
    request = _request_with_origin(settings, origin="http://203.0.113.10:4173")

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    verification = result["verification"]
    checks = _check_statuses(verification)
    assert verification["status"] == "warnings"
    assert verification["errors"] == []
    assert checks["cookie_secure"] == "pass"
    assert checks["direct_ip_not_recommended"] == "warn"


def test_verify_public_direct_ip_http_blocks_cookie_secure_true(initialized_settings) -> None:
    settings = _public_direct_ip_runtime_settings(initialized_settings, cookie_secure=True)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": "http://203.0.113.10:4173",
        },
    )
    request = _request_with_origin(settings, origin="http://203.0.113.10:4173")

    with pytest.raises(HTTPException) as exc_info:
        service.verify_prepared_exposure_switch(
            settings,
            request,
            _admin_actor(settings),
            acknowledgement=True,
        )

    verification = exc_info.value.detail["verification"]
    assert verification["status"] == "blocked"
    assert _check_statuses(verification)["cookie_secure"] == "block"


def test_verify_public_prepared_switch_blocks_broad_proxy_cidrs(initialized_settings) -> None:
    settings = _public_custom_runtime_settings(initialized_settings, trusted_proxy_cidrs=("0.0.0.0/0",))
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
        },
    )
    request = _request_with_origin(settings, origin="https://media.example.com")

    with pytest.raises(HTTPException) as exc_info:
        service.verify_prepared_exposure_switch(
            settings,
            request,
            _admin_actor(settings),
            acknowledgement=True,
        )

    verification = exc_info.value.detail["verification"]
    assert verification["status"] == "blocked"
    assert _check_statuses(verification)["trusted_proxy_cidrs"] == "block"


def test_verify_prepared_switch_url_prefix_missing_warns_only(initialized_settings) -> None:
    settings = _public_custom_runtime_settings(initialized_settings, url_prefix=None)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
        },
    )
    request = _request_with_origin(settings, origin="https://media.example.com", url_prefix="")

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    verification = result["verification"]
    assert verification["status"] == "warnings"
    assert verification["errors"] == []
    assert _check_statuses(verification)["url_prefix_present"] == "warn"


def test_verify_private_prepared_switch_skips_origin_match_when_private_origin_missing(
    initialized_settings,
) -> None:
    settings = _private_runtime_settings(initialized_settings)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(settings, {"desired_mode": "private", "private_origin": ""})
    request = _request_with_origin(settings, origin="http://127.0.0.1")

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    verification = result["verification"]
    assert verification["status"] == "passed"
    assert _check_statuses(verification)["current_origin_match"] == "info"


def test_verify_private_prepared_switch_passes_with_matching_private_origin(initialized_settings) -> None:
    settings = _private_runtime_settings(initialized_settings)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {"desired_mode": "private", "private_origin": "http://192.168.1.10:4173"},
    )
    request = _request_with_origin(settings, origin="http://192.168.1.10:4173")

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    verification = result["verification"]
    assert verification["status"] == "passed"
    assert _check_statuses(verification)["current_origin_match"] == "pass"


def test_verify_private_prepared_switch_blocks_private_origin_mismatch(initialized_settings) -> None:
    settings = _private_runtime_settings(initialized_settings)
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {"desired_mode": "private", "private_origin": "http://192.168.1.10:4173"},
    )
    request = _request_with_origin(settings, origin="http://192.168.1.20:4173")

    with pytest.raises(HTTPException) as exc_info:
        service.verify_prepared_exposure_switch(
            settings,
            request,
            _admin_actor(settings),
            acknowledgement=True,
        )

    verification = exc_info.value.detail["verification"]
    assert verification["status"] == "blocked"
    assert _check_statuses(verification)["current_origin_match"] == "block"


def test_verify_private_prepared_switch_warnings_do_not_block(initialized_settings) -> None:
    settings = _private_runtime_settings(
        initialized_settings,
        cookie_secure=True,
        public_app_origin="http://private.example.test",
        trusted_proxy_cidrs=("::/0",),
    )
    _enable_maintenance_lock(settings)
    _store_prepared_switch(
        settings,
        {"desired_mode": "private", "private_origin": "http://192.168.1.10:4173"},
    )
    request = _request_with_origin(settings, origin="http://192.168.1.10:4173")

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    verification = result["verification"]
    checks = _check_statuses(verification)
    assert verification["status"] == "warnings"
    assert verification["errors"] == []
    assert checks["cookie_secure"] == "warn"
    assert checks["public_app_origin"] == "warn"
    assert checks["trusted_proxy_cidrs"] == "warn"


def test_verify_prepared_switch_has_no_activation_side_effects(initialized_settings) -> None:
    settings = _public_custom_runtime_settings(initialized_settings)
    _enable_maintenance_lock(settings)
    _create_user(settings, username="verify-standard-user")
    _store_prepared_switch(
        settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
        },
    )
    request = _request_with_origin(settings, origin="https://media.example.com")
    before_runtime = (
        settings.private_network_only,
        settings.public_app_origin,
        settings.backend_origin,
        settings.cookie_secure,
        settings.session_ttl_hours,
    )
    with get_connection(settings) as connection:
        before_users = [
            (row["id"], row["enabled"])
            for row in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]
        before_revoked_sessions = connection.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE revoked_at IS NOT NULL"
        ).fetchone()["count"]

    result = service.verify_prepared_exposure_switch(
        settings,
        request,
        _admin_actor(settings),
        acknowledgement=True,
    )

    with get_connection(settings) as connection:
        after_users = [
            (row["id"], row["enabled"])
            for row in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]
        after_revoked_sessions = connection.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE revoked_at IS NOT NULL"
        ).fetchone()["count"]
    assert result["prepared_switch"]["status"] == service.PREPARED_SWITCH_STATUS_VERIFIED
    assert result["prepared_switch"]["takes_effect"] is False
    assert (
        settings.private_network_only,
        settings.public_app_origin,
        settings.backend_origin,
        settings.cookie_secure,
        settings.session_ttl_hours,
    ) == before_runtime
    assert after_users == before_users
    assert after_revoked_sessions == before_revoked_sessions


def test_finalize_profile_route_requires_password_ack_and_verified_prepared_switch(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    missing_password = client.post(
        "/api/admin/exposure/finalize-profile",
        json={"acknowledgement": True},
    )
    assert missing_password.status_code == 422

    wrong_password = client.post(
        "/api/admin/exposure/finalize-profile",
        json={"current_admin_password": "wrong-password", "acknowledgement": True},
    )
    assert wrong_password.status_code == 401

    missing_ack = client.post(
        "/api/admin/exposure/finalize-profile",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert missing_ack.status_code == 400
    assert missing_ack.json()["detail"] == service.EXPOSURE_FINALIZE_PROFILE_ACKNOWLEDGEMENT

    no_prepared_switch = client.post(
        "/api/admin/exposure/finalize-profile",
        json=_prepare_request_payload(admin_credentials),
    )
    assert no_prepared_switch.status_code == 400
    assert no_prepared_switch.json()["detail"] == "Verify a prepared switch before finalizing the exposure profile."

    _store_prepared_switch(
        initialized_settings,
        {"desired_mode": "private", "private_origin": ""},
    )
    unverified = client.post(
        "/api/admin/exposure/finalize-profile",
        json=_prepare_request_payload(admin_credentials),
    )
    assert unverified.status_code == 400
    assert unverified.json()["detail"] == "Prepared switch must be verified after restart before finalization."


def test_finalize_verified_profile_stores_official_record_and_clears_working_state(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_public_custom_pending_draft(client, admin_credentials)
    _enable_maintenance_lock(initialized_settings)
    verification = {
        "status": "passed",
        "errors": [],
        "warnings": [],
        "checks": [{"name": "current_origin_match", "status": "pass", "detail": "matched"}],
        "takes_effect": False,
    }
    prepared_switch = _store_prepared_switch(
        initialized_settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
            "reverse_proxy_provider": "caddy",
        },
        status=service.PREPARED_SWITCH_STATUS_VERIFIED,
        verification=verification,
    )

    response = client.post(
        "/api/admin/exposure/finalize-profile",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 200
    payload = response.json()
    finalized_profile = payload["finalized_profile"]
    assert payload["takes_effect"] is False
    assert finalized_profile["status"] == "finalized"
    assert finalized_profile["mode"] == "public"
    assert finalized_profile["public_entry_kind"] == "custom_domain"
    assert finalized_profile["public_origin"] == "https://media.example.com"
    assert finalized_profile["private_origin"] == ""
    assert finalized_profile["reverse_proxy_provider"] == "caddy"
    assert finalized_profile["verification"] == verification
    assert finalized_profile["prepared_at"] == prepared_switch["prepared_at"]
    assert finalized_profile["verified_at"] == prepared_switch["verified_at"]
    assert finalized_profile["finalized_by_username"] == admin_credentials["username"]
    assert finalized_profile["finalized_at"]
    assert finalized_profile["takes_effect"] is False
    assert finalized_profile["maintenance_mode_release"] == "manual_only"
    assert finalized_profile["url_prefix_rotation"] == "manual_only"
    assert finalized_profile["env_writing"] == "manual_only"

    finalized_response = client.get("/api/admin/exposure/finalized-profile")
    assert finalized_response.status_code == 200
    assert finalized_response.json()["finalized_profile"] == finalized_profile

    status_response = client.get("/api/admin/exposure/status")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["finalized_profile"] == finalized_profile
    assert status_payload["pending_draft"] is None
    assert status_payload["prepared_switch"] is None
    assert status_payload["takes_effect"] is False
    assert maintenance_service.get_exposure_maintenance_lock(initialized_settings)["enabled"] is True


def test_finalize_verified_profile_allows_warning_verification(initialized_settings) -> None:
    verification = {
        "status": "warnings",
        "errors": [],
        "warnings": ["Runtime backend_origin differs from expected origin."],
        "checks": [{"name": "backend_origin", "status": "warn", "detail": "mismatch"}],
        "takes_effect": False,
    }
    _store_prepared_switch(
        initialized_settings,
        {
            "desired_mode": "public",
            "public_entry_kind": "direct_ip",
            "public_origin": "http://203.0.113.10:4173",
            "reverse_proxy_provider": "manual_other",
        },
        status=service.PREPARED_SWITCH_STATUS_VERIFIED,
        verification=verification,
    )

    result = service.finalize_verified_exposure_profile(
        initialized_settings,
        _admin_actor(initialized_settings),
        acknowledgement=True,
    )

    finalized_profile = result["finalized_profile"]
    assert finalized_profile["status"] == "finalized"
    assert finalized_profile["public_entry_kind"] == "direct_ip"
    assert finalized_profile["verification"]["status"] == "warnings"
    assert result["takes_effect"] is False


def test_finalize_verified_profile_has_no_runtime_user_session_or_maintenance_side_effects(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _create_user(initialized_settings, username="finalize-standard-user")
    _login(client, username="finalize-standard-user", password="standard-user-password")
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_private_pending_draft(client, admin_credentials, private_origin="http://192.168.1.10:4173")
    _enable_maintenance_lock(initialized_settings)
    _store_prepared_switch(
        initialized_settings,
        {"desired_mode": "private", "private_origin": "http://192.168.1.10:4173"},
        status=service.PREPARED_SWITCH_STATUS_VERIFIED,
        verification={"status": "passed", "errors": [], "warnings": [], "checks": [], "takes_effect": False},
    )
    before_runtime = (
        initialized_settings.private_network_only,
        initialized_settings.public_app_origin,
        initialized_settings.backend_origin,
        initialized_settings.cookie_secure,
        initialized_settings.session_ttl_hours,
    )
    with get_connection(initialized_settings) as connection:
        before_users = [
            (row["id"], row["enabled"])
            for row in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]
        before_revoked_sessions = connection.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE revoked_at IS NOT NULL"
        ).fetchone()["count"]

    response = client.post(
        "/api/admin/exposure/finalize-profile",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 200
    with get_connection(initialized_settings) as connection:
        after_users = [
            (row["id"], row["enabled"])
            for row in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]
        after_revoked_sessions = connection.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE revoked_at IS NOT NULL"
        ).fetchone()["count"]
    assert (
        initialized_settings.private_network_only,
        initialized_settings.public_app_origin,
        initialized_settings.backend_origin,
        initialized_settings.cookie_secure,
        initialized_settings.session_ttl_hours,
    ) == before_runtime
    assert after_users == before_users
    assert after_revoked_sessions == before_revoked_sessions
    assert maintenance_service.get_exposure_maintenance_lock(initialized_settings)["enabled"] is True


def test_prepare_switch_requires_password_ack_and_pending_draft_then_auto_enables_maintenance_mode(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    missing_password = client.post(
        "/api/admin/exposure/prepare-switch",
        json={"acknowledgement": True},
    )
    assert missing_password.status_code == 422

    wrong_password = client.post(
        "/api/admin/exposure/prepare-switch",
        json={"current_admin_password": "wrong-password", "acknowledgement": True},
    )
    assert wrong_password.status_code == 401

    missing_ack = client.post(
        "/api/admin/exposure/prepare-switch",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert missing_ack.status_code == 400
    assert "only prepares a manual switch plan" in missing_ack.json()["detail"]

    no_draft = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert no_draft.status_code == 400
    assert no_draft.json()["detail"] == "Save a pending exposure draft before preparing a manual switch."

    _save_private_pending_draft(client, admin_credentials)
    prepared = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert prepared.status_code == 200
    assert prepared.json()["takes_effect"] is False
    prepared_switch = prepared.json()["prepared_switch"]
    assert prepared_switch["status"] == "prepared_for_manual_apply"
    assert prepared_switch["maintenance_mode_auto_enabled"] is True
    assert prepared_switch["verification_required"] is True
    assert prepared_switch["current_origin_match_required_in_phase"] == "phase_4_verification"
    assert maintenance_service.get_exposure_maintenance_lock(initialized_settings)["enabled"] is True


def test_prepare_switch_revalidates_pending_draft_and_blocks_invalid_origin(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _enable_maintenance_lock(initialized_settings)
    set_global_app_setting(
        initialized_settings,
        key=service.EXPOSURE_MODE_PENDING_DRAFT_KEY,
        value=json.dumps(
            {
                "desired": {
                    "desired_mode": "public",
                    "public_entry_kind": "custom_domain",
                    "public_origin": "https://localhost",
                    "reverse_proxy_provider": "caddy",
                },
                "validation": {"status": "ready", "errors": [], "warnings": [], "checks": []},
                "plan": {},
                "takes_effect": False,
            }
        ),
    )

    response = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Prepared switch cannot be created while validation has blocking errors."


def test_prepare_switch_public_custom_domain_defers_current_origin_match_to_phase_4(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_public_custom_pending_draft(client, admin_credentials)

    response = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 200
    prepared = response.json()["prepared_switch"]
    assert prepared["status"] == "prepared_for_manual_apply"
    assert prepared["takes_effect"] is False
    assert prepared["verification_required"] is True
    assert prepared["current_origin_match_required_in_phase"] == "phase_4_verification"
    assert prepared["maintenance_mode_auto_enabled"] is True
    assert prepared["desired"]["public_origin"] == "https://media.example.com"
    assert any(
        check["name"] == "current_origin_match" and check["status"] == "warn"
        for check in prepared["validation"]["checks"]
    )
    assert service.CURRENT_ORIGIN_REVALIDATION_MESSAGE in prepared["validation"]["warnings"]
    assert "ELVERN_PRIVATE_NETWORK_ONLY=false" in prepared["env_block"]
    assert "ELVERN_PUBLIC_APP_ORIGIN=https://media.example.com" in prepared["env_block"]
    assert "ELVERN_BACKEND_ORIGIN=https://media.example.com" in prepared["env_block"]
    assert "ELVERN_COOKIE_SECURE=true" in prepared["env_block"]


def test_prepare_switch_public_direct_ip_defers_origin_match_and_preserves_warnings(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_public_direct_ip_pending_draft(client, admin_credentials)

    response = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 200
    prepared = response.json()["prepared_switch"]
    assert prepared["desired"]["public_entry_kind"] == "direct_ip"
    assert prepared["verification_required"] is True
    assert prepared["current_origin_match_required_in_phase"] == "phase_4_verification"
    assert service.DIRECT_PUBLIC_IP_WARNING in prepared["validation"]["warnings"]
    assert any(
        check["name"] == "current_origin_match" and check["status"] == "warn"
        for check in prepared["validation"]["checks"]
    )
    assert "ELVERN_COOKIE_SECURE=false" in prepared["env_block"]


def test_prepare_switch_requires_stored_direct_ip_acknowledgement(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _enable_maintenance_lock(initialized_settings)
    set_global_app_setting(
        initialized_settings,
        key=service.EXPOSURE_MODE_PENDING_DRAFT_KEY,
        value=json.dumps(
            {
                "desired": {
                    "desired_mode": "public",
                    "public_entry_kind": "direct_ip",
                    "public_origin": "http://203.0.113.10:4173",
                    "reverse_proxy_provider": "manual_other",
                },
                "validation": {"status": "ready", "errors": [], "warnings": [], "checks": []},
                "plan": {},
                "takes_effect": False,
                "direct_ip_not_recommended_acknowledgement": False,
            }
        ),
    )

    response = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
        headers={
            "Host": "127.0.0.1",
            "X-Forwarded-Host": "203.0.113.10:4173",
            "X-Forwarded-Proto": "http",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == service.DIRECT_PUBLIC_IP_WARNING


def test_prepare_switch_private_draft_succeeds_without_public_origin_match(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_private_pending_draft(client, admin_credentials)
    _enable_maintenance_lock(initialized_settings)

    response = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 200
    prepared = response.json()["prepared_switch"]
    assert prepared["status"] == "prepared_for_manual_apply"
    assert prepared["takes_effect"] is False
    assert prepared["activation_not_implemented"] is True
    assert prepared["url_prefix_rotation"] == "manual_only"
    assert prepared["env_writing"] == "manual_only"
    assert "ELVERN_PRIVATE_NETWORK_ONLY=true" in prepared["env_block"]
    assert "SECRET" not in prepared["env_block"]
    assert "PASSWORD" not in prepared["env_block"]
    assert "SESSION" not in prepared["env_block"]


def test_prepare_switch_auto_enables_maintenance_mode_and_revokes_non_admin_sessions(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _create_user(initialized_settings, username="prepare-standard")
    _login(client, username="prepare-standard", password="standard-user-password")
    standard_session = _session_row_for_username(initialized_settings, "prepare-standard")
    assert standard_session is not None

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_private_pending_draft(client, admin_credentials)

    response = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 200
    prepared = response.json()["prepared_switch"]
    assert prepared["maintenance_mode_auto_enabled"] is True
    assert prepared["maintenance_mode_revoked_non_admin_sessions"] == 1
    assert maintenance_service.get_exposure_maintenance_lock(initialized_settings)["enabled"] is True
    with get_connection(initialized_settings) as connection:
        session_row = connection.execute(
            "SELECT revoked_at, revoked_reason FROM sessions WHERE id = ?",
            (standard_session["id"],),
        ).fetchone()
        user_row = connection.execute(
            "SELECT enabled FROM users WHERE username = ?",
            ("prepare-standard",),
        ).fetchone()
    assert session_row["revoked_at"] is not None
    assert session_row["revoked_reason"] == "maintenance_mode"
    assert user_row["enabled"] == 1


def test_clear_prepared_switch_requires_password_and_preserves_pending_draft(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_private_pending_draft(client, admin_credentials)
    _enable_maintenance_lock(initialized_settings)
    prepared = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert prepared.status_code == 200

    wrong_password = client.request(
        "DELETE",
        "/api/admin/exposure/prepared-switch",
        json={"current_admin_password": "wrong-password", "acknowledgement": False},
    )
    assert wrong_password.status_code == 401

    cleared = client.request(
        "DELETE",
        "/api/admin/exposure/prepared-switch",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert cleared.status_code == 200
    assert cleared.json() == {"prepared_switch": None, "takes_effect": False}
    with get_connection(initialized_settings) as connection:
        prepared_row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (service.EXPOSURE_MODE_PREPARED_SWITCH_KEY,),
        ).fetchone()
        draft_row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (service.EXPOSURE_MODE_PENDING_DRAFT_KEY,),
        ).fetchone()
    assert prepared_row is None
    assert draft_row is not None


def test_prepare_switch_stores_safe_plan_without_runtime_side_effects(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _create_user(initialized_settings, username="prepared-standard-user")
    _save_private_pending_draft(client, admin_credentials)
    _enable_maintenance_lock(initialized_settings)
    original_private_network_only = initialized_settings.private_network_only
    original_public_app_origin = initialized_settings.public_app_origin
    original_backend_origin = initialized_settings.backend_origin
    original_session_ttl = initialized_settings.session_ttl_hours
    with get_connection(initialized_settings) as connection:
        before_users = [
            (row["id"], row["enabled"])
            for row in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]
        before_revoked_sessions = connection.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE revoked_at IS NOT NULL"
        ).fetchone()["count"]

    response = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )

    assert response.status_code == 200
    prepared = response.json()["prepared_switch"]
    assert prepared["restart_required"] is True
    assert prepared["maintenance_lock_required"] is False
    assert prepared["maintenance_mode_auto_enabled"] is True
    assert prepared["verification_required"] is True
    assert prepared["takes_effect"] is False
    with get_connection(initialized_settings) as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (service.EXPOSURE_MODE_PREPARED_SWITCH_KEY,),
        ).fetchone()
        after_users = [
            (entry["id"], entry["enabled"])
            for entry in connection.execute("SELECT id, enabled FROM users ORDER BY id").fetchall()
        ]
        after_revoked_sessions = connection.execute(
            "SELECT COUNT(*) AS count FROM sessions WHERE revoked_at IS NOT NULL"
        ).fetchone()["count"]
    assert row is not None
    assert after_users == before_users
    assert after_revoked_sessions == before_revoked_sessions
    assert initialized_settings.private_network_only == original_private_network_only
    assert initialized_settings.public_app_origin == original_public_app_origin
    assert initialized_settings.backend_origin == original_backend_origin
    assert initialized_settings.session_ttl_hours == original_session_ttl


def test_activation_route_and_runtime_side_effects_are_not_implemented(client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.post("/api/admin/exposure/activate", json={})
    assert response.status_code == 404

    switch_now_response = client.post("/api/admin/exposure/switch-now", json={})
    assert switch_now_response.status_code == 404

    service_source = Path("backend/app/services/exposure_mode_service.py").read_text(encoding="utf-8")
    maintenance_source = Path("backend/app/services/exposure_maintenance_service.py").read_text(encoding="utf-8")
    route_source = Path("backend/app/routes/admin.py").read_text(encoding="utf-8")
    exposure_route_source = route_source[
        route_source.index('@router.get("/exposure/status"') : route_source.index('@router.post("/users"')
    ]
    assert "rotate_url_prefix" not in service_source
    assert "revoke_sessions_for_user" not in service_source
    assert "UPDATE users" not in service_source
    assert "os.environ" not in service_source
    assert "write_text" not in service_source
    assert "rotate_url_prefix" not in maintenance_source
    assert "revoke_sessions_for_user" not in maintenance_source
    assert "UPDATE users" not in maintenance_source
    assert "os.environ" not in maintenance_source
    assert "rotate_url_prefix" not in exposure_route_source
    assert "revoke_sessions_for_user" not in exposure_route_source
    assert "UPDATE users" not in exposure_route_source


def test_maintenance_mode_revocation_scope_static_guard() -> None:
    maintenance_source = Path("backend/app/services/exposure_maintenance_service.py").read_text(encoding="utf-8")

    assert "created_from_auth_session_id" not in maintenance_source
    assert "revoke_download_sessions_for_user" not in maintenance_source
    assert "_revoke_native_playback_by_users" not in maintenance_source
    assert "_revoke_desktop_handoffs_by_users" not in maintenance_source
    assert "WHERE user_id IN" not in maintenance_source


def test_broad_trusted_proxy_cidr_warns(test_settings) -> None:
    settings = replace(test_settings, trusted_proxy_cidrs=("0.0.0.0/0", "::1/128"))
    request = _request_with_origin(settings, origin="https://media.example.com")

    result = service.validate_exposure_plan(
        settings,
        request,
        {
            "desired_mode": "public",
            "public_entry_kind": "custom_domain",
            "public_origin": "https://media.example.com",
            "reverse_proxy_provider": "caddy",
        },
    )

    assert any("Trusted proxy CIDRs are broad" in warning for warning in result["validation"]["warnings"])
