from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend.app.auth import resolve_client_ip
from backend.app.config import ConfigError, DEFAULT_TRUSTED_PROXY_CIDRS, refresh_settings
from backend.app.db import get_connection


def _request_with_peer(settings, *, peer_host: str, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
        "client": (peer_host, 50000),
        "server": ("testserver", 80),
        "scheme": "http",
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
    }
    return Request(scope)


def test_client_ip_without_forwarded_header_uses_immediate_peer(test_settings) -> None:
    request = _request_with_peer(test_settings, peer_host="203.0.113.10")

    assert resolve_client_ip(request) == "203.0.113.10"


def test_client_ip_ignores_spoofed_forwarded_for_from_untrusted_peer(test_settings) -> None:
    request = _request_with_peer(
        test_settings,
        peer_host="203.0.113.10",
        headers={"X-Forwarded-For": "1.2.3.4"},
    )

    assert resolve_client_ip(request) == "203.0.113.10"


def test_client_ip_trusts_valid_forwarded_for_from_loopback_proxy(test_settings) -> None:
    request = _request_with_peer(
        test_settings,
        peer_host="127.0.0.1",
        headers={"X-Forwarded-For": "198.51.100.5"},
    )

    assert resolve_client_ip(request) == "198.51.100.5"


def test_client_ip_trusts_first_forwarded_for_chain_value_from_loopback_proxy(test_settings) -> None:
    request = _request_with_peer(
        test_settings,
        peer_host="127.0.0.1",
        headers={"X-Forwarded-For": "198.51.100.5, 10.0.0.1"},
    )

    assert resolve_client_ip(request) == "198.51.100.5"


def test_client_ip_invalid_forwarded_for_from_trusted_proxy_falls_back_to_peer(test_settings) -> None:
    request = _request_with_peer(
        test_settings,
        peer_host="127.0.0.1",
        headers={"X-Forwarded-For": "bad-ip"},
    )

    assert resolve_client_ip(request) == "127.0.0.1"


def test_client_ip_trusts_forwarded_for_from_ipv6_loopback_proxy(test_settings) -> None:
    request = _request_with_peer(
        test_settings,
        peer_host="::1",
        headers={"X-Forwarded-For": "198.51.100.5"},
    )

    assert resolve_client_ip(request) == "198.51.100.5"


def test_client_ip_trusts_forwarded_for_from_custom_proxy_cidr(test_settings) -> None:
    settings = replace(test_settings, trusted_proxy_cidrs=("10.0.0.0/8",))
    request = _request_with_peer(
        settings,
        peer_host="10.1.2.3",
        headers={"X-Forwarded-For": "198.51.100.5"},
    )

    assert resolve_client_ip(request) == "198.51.100.5"


def test_trusted_proxy_cidrs_default_to_loopback_only(test_settings) -> None:
    assert test_settings.trusted_proxy_cidrs == DEFAULT_TRUSTED_PROXY_CIDRS


def test_invalid_trusted_proxy_cidr_env_fails_settings_load(test_settings, monkeypatch) -> None:
    del test_settings
    monkeypatch.setenv("ELVERN_TRUSTED_PROXY_CIDRS", "not-a-cidr")

    with pytest.raises(ConfigError, match="ELVERN_TRUSTED_PROXY_CIDRS contains invalid CIDR: not-a-cidr"):
        refresh_settings()


def test_login_attribution_ignores_spoofed_forwarded_for_when_peer_is_not_trusted(
    initialized_settings,
    client,
    admin_credentials,
) -> None:
    client.app.state.settings = replace(initialized_settings, trusted_proxy_cidrs=())

    response = client.post(
        "/api/auth/login",
        json={
            "username": admin_credentials["username"],
            "password": "definitely-wrong",
        },
        headers={
            "X-Forwarded-For": "1.2.3.4",
            "User-Agent": "Pytest Spoofed XFF",
        },
    )

    assert response.status_code == 401
    with get_connection(initialized_settings) as connection:
        security_row = connection.execute(
            """
            SELECT ip_address
            FROM security_events
            WHERE event_kind = 'login_failure'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        failure_row = connection.execute(
            """
            SELECT bucket_key
            FROM login_failures
            WHERE bucket_kind = 'ip'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert security_row["ip_address"] == "127.0.0.1"
    assert failure_row["bucket_key"] == "127.0.0.1"
