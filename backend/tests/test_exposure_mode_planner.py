from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from backend.app.db import get_connection
from backend.app.models import AuthenticatedUser
from backend.app.services import exposure_mode_service as service
from backend.app.services import exposure_maintenance_service as maintenance_service
from backend.app.services.account_access_service import generate_invite_code
from backend.app.services.admin_service import create_user
from backend.app.services.app_settings_service import set_global_app_setting


MAINTENANCE_MESSAGE = "The server is currently under construction, please try again later"


def _request_with_origin(
    settings,
    *,
    origin: str = "http://testserver",
    peer_host: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
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
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings, url_prefix="abcdfghj")),
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
            SELECT s.id, s.revoked_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE u.username = ?
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (username,),
        ).fetchone()


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


def test_security_exposure_maintenance_lock_routes_require_admin_password_and_ack(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    assert client.get("/api/admin/exposure/maintenance-lock").status_code == 401

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    missing_ack = client.post(
        "/api/admin/exposure/maintenance-lock",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert missing_ack.status_code == 400

    wrong_password = client.post(
        "/api/admin/exposure/maintenance-lock",
        json={"current_admin_password": "wrong-password", "acknowledgement": True},
    )
    assert wrong_password.status_code == 401

    response = client.post(
        "/api/admin/exposure/maintenance-lock",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True

    status = client.get("/api/admin/exposure/status")
    assert status.status_code == 200
    assert status.json()["active"]["maintenance_lock"]["enabled"] is True

    wrong_disable = client.request(
        "DELETE",
        "/api/admin/exposure/maintenance-lock",
        json={"current_admin_password": "wrong-password", "acknowledgement": False},
    )
    assert wrong_disable.status_code == 401

    disabled = client.request(
        "DELETE",
        "/api/admin/exposure/maintenance-lock",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert maintenance_service.get_exposure_maintenance_lock(initialized_settings)["enabled"] is False


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
        "/api/admin/exposure/maintenance-lock",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )

    assert response.status_code == 200
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


def test_security_exposure_maintenance_lock_blocks_existing_standard_session_without_revocation(
    client,
    initialized_settings,
) -> None:
    _create_user(initialized_settings, username="session-standard")
    _login(client, username="session-standard", password="standard-user-password")
    session_row = _session_row_for_username(initialized_settings, "session-standard")
    assert session_row is not None

    maintenance_service.set_exposure_maintenance_lock(
        initialized_settings,
        _admin_actor(initialized_settings),
        enabled=True,
    )

    response = client.get("/api/auth/me")

    assert response.status_code == 503
    assert response.json()["detail"] == MAINTENANCE_MESSAGE
    with get_connection(initialized_settings) as connection:
        fresh_session = connection.execute(
            "SELECT revoked_at FROM sessions WHERE id = ?",
            (session_row["id"],),
        ).fetchone()
        user_row = connection.execute(
            "SELECT enabled FROM users WHERE username = ?",
            ("session-standard",),
        ).fetchone()
    assert fresh_session["revoked_at"] is None
    assert user_row["enabled"] == 1

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "Logged out"


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

    post_response = client.post(
        "/api/admin/exposure/prepare-switch",
        json={"current_admin_password": admin_credentials["password"], "acknowledgement": True},
    )
    assert post_response.status_code == 401

    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    response = client.get("/api/admin/exposure/prepared-switch")
    assert response.status_code == 200
    assert response.json() == {"prepared_switch": None, "takes_effect": False}

    status_response = client.get("/api/admin/exposure/status")
    assert status_response.status_code == 200
    assert status_response.json()["prepared_switch"] is None
    assert status_response.json()["takes_effect"] is False


def test_prepare_switch_requires_password_ack_pending_draft_and_maintenance_lock(
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
    lock_off = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert lock_off.status_code == 400
    assert lock_off.json()["detail"] == "Enable the temporary maintenance lock before preparing a manual switch."

    _enable_maintenance_lock(initialized_settings)
    prepared = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert prepared.status_code == 200
    assert prepared.json()["takes_effect"] is False
    assert prepared.json()["prepared_switch"]["status"] == "prepared_for_manual_apply"


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


def test_prepare_switch_public_custom_domain_requires_and_accepts_current_origin_match(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_public_custom_pending_draft(client, admin_credentials)
    _enable_maintenance_lock(initialized_settings)

    mismatch = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["detail"] == "Open this admin page through the proposed public address and validate again before preparing."

    matched = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
        headers={
            "Host": "127.0.0.1",
            "X-Forwarded-Host": "media.example.com",
            "X-Forwarded-Proto": "https",
        },
    )

    assert matched.status_code == 200
    prepared = matched.json()["prepared_switch"]
    assert prepared["status"] == "prepared_for_manual_apply"
    assert prepared["takes_effect"] is False
    assert prepared["desired"]["public_origin"] == "https://media.example.com"
    assert "ELVERN_PRIVATE_NETWORK_ONLY=false" in prepared["env_block"]
    assert "ELVERN_PUBLIC_APP_ORIGIN=https://media.example.com" in prepared["env_block"]
    assert "ELVERN_BACKEND_ORIGIN=https://media.example.com" in prepared["env_block"]
    assert "ELVERN_COOKIE_SECURE=true" in prepared["env_block"]


def test_prepare_switch_public_direct_ip_requires_origin_match_and_preserves_warning(
    client,
    initialized_settings,
    admin_credentials,
) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])
    _save_public_direct_ip_pending_draft(client, admin_credentials)
    _enable_maintenance_lock(initialized_settings)

    mismatch = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["detail"] == "Open this admin page through the proposed public address and validate again before preparing."

    matched = client.post(
        "/api/admin/exposure/prepare-switch",
        json=_prepare_request_payload(admin_credentials),
        headers={
            "Host": "127.0.0.1",
            "X-Forwarded-Host": "203.0.113.10:4173",
            "X-Forwarded-Proto": "http",
        },
    )

    assert matched.status_code == 200
    prepared = matched.json()["prepared_switch"]
    assert prepared["desired"]["public_entry_kind"] == "direct_ip"
    assert service.DIRECT_PUBLIC_IP_WARNING in prepared["validation"]["warnings"]
    assert any(
        check["name"] == "current_origin_match" and check["status"] == "pass"
        for check in prepared["validation"]["checks"]
    )


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
    assert prepared["maintenance_lock_required"] is True
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
