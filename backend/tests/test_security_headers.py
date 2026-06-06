from __future__ import annotations

from uuid import uuid4

from backend.app.main import SECURITY_HEADERS
from backend.tests.test_assistant_attachment_security import (
    _create_standard_user,
    _insert_assistant_attachment,
    _login,
)


def _assert_global_security_headers(response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert "Strict-Transport-Security" not in response.headers


def test_health_response_includes_global_security_headers(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    _assert_global_security_headers(response)


def test_api_response_and_auth_error_include_global_security_headers(client, admin_credentials) -> None:
    unauthenticated = client.get("/api/auth/me")
    assert unauthenticated.status_code == 401
    _assert_global_security_headers(unauthenticated)

    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200
    _assert_global_security_headers(login_response)

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    _assert_global_security_headers(me_response)


def test_totp_setup_header_survives_global_security_headers(client, admin_credentials) -> None:
    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.headers["X-Elvern-Totp-Setup-Required"] == "true"
    _assert_global_security_headers(response)


def test_global_security_headers_do_not_overwrite_assistant_attachment_route_csp(
    initialized_settings,
    client,
) -> None:
    owner, password = _create_standard_user(
        initialized_settings,
        username=f"security-headers-owner-{uuid4().hex[:8]}",
    )
    text_attachment_id = _insert_assistant_attachment(
        initialized_settings,
        user_id=int(owner["id"]),
        mime_type="text/plain",
        filename="note.txt",
    )
    html_attachment_id = _insert_assistant_attachment(
        initialized_settings,
        user_id=int(owner["id"]),
        mime_type="text/html",
        filename="active.html",
    )
    image_attachment_id = _insert_assistant_attachment(
        initialized_settings,
        user_id=int(owner["id"]),
        mime_type="image/png",
        filename="safe.png",
    )
    _login(client, username=str(owner["username"]), password=password)

    text_response = client.get(f"/api/assistant/attachments/{text_attachment_id}")
    assert text_response.status_code == 200
    assert text_response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert text_response.headers["X-Frame-Options"] == SECURITY_HEADERS["X-Frame-Options"]
    assert "Strict-Transport-Security" not in text_response.headers

    html_response = client.get(f"/api/assistant/attachments/{html_attachment_id}")
    assert html_response.status_code == 200
    assert html_response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert html_response.headers["X-Frame-Options"] == SECURITY_HEADERS["X-Frame-Options"]
    assert "Strict-Transport-Security" not in html_response.headers

    image_response = client.get(f"/api/assistant/attachments/{image_attachment_id}")
    assert image_response.status_code == 200
    assert image_response.headers["Content-Security-Policy"] == SECURITY_HEADERS["Content-Security-Policy"]
    _assert_global_security_headers(image_response)
