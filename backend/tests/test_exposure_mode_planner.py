from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from backend.app.db import get_connection
from backend.app.services import exposure_mode_service as service


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


def test_activation_route_and_runtime_side_effects_are_not_implemented(client, admin_credentials) -> None:
    _login(client, username=admin_credentials["username"], password=admin_credentials["password"])

    response = client.post("/api/admin/exposure/activate", json={})
    assert response.status_code == 404

    service_source = Path("backend/app/services/exposure_mode_service.py").read_text(encoding="utf-8")
    route_source = Path("backend/app/routes/admin.py").read_text(encoding="utf-8")
    exposure_route_source = route_source[
        route_source.index('@router.get("/exposure/status"') : route_source.index('@router.post("/users"')
    ]
    assert "rotate_url_prefix" not in service_source
    assert "revoke_sessions_for_user" not in service_source
    assert "UPDATE users" not in service_source
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
