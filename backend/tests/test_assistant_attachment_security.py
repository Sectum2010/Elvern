from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from backend.app.auth import authenticate_user
from backend.app.db import get_connection, utcnow_iso
from backend.app.routes import assistant as assistant_routes
from backend.app.services import assistant_service
from backend.app.services.admin_service import create_user
from backend.app.services.assistant_service import (
    assistant_attachment_response_policy,
    update_assistant_user_access,
)


def _admin_user(settings):
    user, failure_reason = authenticate_user(
        settings,
        settings.admin_username,
        settings.admin_bootstrap_password or "",
    )
    assert failure_reason is None
    assert user is not None
    return user


def _create_standard_user(
    settings,
    *,
    username: str,
    assistant_enabled: bool = True,
) -> tuple[dict[str, object], str]:
    password = f"{username}-password-123"
    actor = _admin_user(settings)
    created = create_user(
        settings,
        username=username,
        password=password,
        role="standard_user",
        enabled=True,
        actor=actor,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    update_assistant_user_access(
        settings,
        target_user_id=int(created["id"]),
        assistant_beta_enabled=assistant_enabled,
        note=None,
        actor=actor,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    return created, password


def _login(client, *, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _logout(client) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _assistant_attachment_type(mime_type: object) -> str:
    normalized = str(mime_type or "").split(";", maxsplit=1)[0].strip().lower()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("text/"):
        return "text"
    return "other"


def _insert_assistant_attachment(
    settings,
    *,
    user_id: int,
    mime_type: object,
    filename: str,
    content: bytes = b"attachment-bytes",
) -> int:
    request_number = f"AR-F01-{uuid4().hex[:12]}"
    stored_name = f"{uuid4().hex}{Path(filename).suffix.lower() or '.bin'}"
    safe_ref = f"{request_number}/{stored_name}"
    upload_path = settings.db_path.parent / "assistant_uploads" / request_number / stored_name
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(content)

    now = utcnow_iso()
    with get_connection(settings) as connection:
        request_cursor = connection.execute(
            """
            INSERT INTO assistant_requests (
                request_number,
                submitted_by_user_id,
                submitted_by_display_name_snapshot,
                request_type,
                title,
                description,
                urgency,
                status,
                status_updated_at,
                status_updated_by_user_id,
                is_archived,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, 'other', ?, ?, 'normal', 'new', ?, ?, 0, ?, ?)
            """,
            (
                request_number,
                user_id,
                f"user-{user_id}",
                "Attachment policy test",
                "Fixture for assistant attachment response policy.",
                now,
                user_id,
                now,
                now,
            ),
        )
        attachment_cursor = connection.execute(
            """
            INSERT INTO assistant_request_attachments (
                request_id,
                attachment_type,
                storage_kind,
                storage_path_safe_ref,
                original_filename,
                mime_type,
                size_bytes,
                created_at
            ) VALUES (?, ?, 'local_upload', ?, ?, ?, ?, ?)
            """,
            (
                int(request_cursor.lastrowid),
                _assistant_attachment_type(mime_type),
                safe_ref,
                filename,
                mime_type,
                len(content),
                now,
            ),
        )
        connection.commit()
        return int(attachment_cursor.lastrowid)


def _assert_common_attachment_headers(response) -> None:
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"


def test_assistant_attachment_response_policy_normalizes_safe_and_active_mime_types() -> None:
    image_policy = assistant_attachment_response_policy(" IMAGE/PNG ; ignored=yes ")
    assert image_policy["media_type"] == "image/png"
    assert image_policy["content_disposition_type"] == "inline"
    assert "Content-Security-Policy" not in image_policy["headers"]

    text_policy = assistant_attachment_response_policy("text/plain; charset=utf-8")
    assert str(text_policy["media_type"]).startswith("text/plain")
    assert text_policy["content_disposition_type"] == "inline"
    assert text_policy["headers"]["Content-Security-Policy"] == "default-src 'none'; sandbox"

    for active_mime in ("image/svg+xml", "text/html; charset=utf-8", "application/xml"):
        download_policy = assistant_attachment_response_policy(active_mime)
        assert download_policy["media_type"] == "application/octet-stream"
        assert download_policy["content_disposition_type"] == "attachment"
        assert download_policy["headers"]["Content-Security-Policy"] == "default-src 'none'; sandbox"


@pytest.mark.parametrize(
    ("mime_type", "filename", "expected_content_type", "expected_disposition", "expect_csp"),
    [
        ("image/png", "safe.png", "image/png", "inline", False),
        ("text/plain", "note.txt", "text/plain", "inline", True),
        ("text/plain; charset=utf-8", "note-params.txt", "text/plain", "inline", True),
        ("image/svg+xml", "active.svg", "application/octet-stream", "attachment", True),
        ("text/html", "active.html", "application/octet-stream", "attachment", True),
        ("text/html; charset=utf-8", "active-params.html", "application/octet-stream", "attachment", True),
        ("application/pdf", "report.pdf", "application/octet-stream", "attachment", True),
        ("application/octet-stream", "unknown.bin", "application/octet-stream", "attachment", True),
        (None, "empty.bin", "application/octet-stream", "attachment", True),
    ],
)
def test_assistant_attachment_view_applies_safe_inline_or_download_policy(
    initialized_settings,
    client,
    mime_type,
    filename: str,
    expected_content_type: str,
    expected_disposition: str,
    expect_csp: bool,
) -> None:
    owner, password = _create_standard_user(
        initialized_settings,
        username=f"assistant-owner-{uuid4().hex[:8]}",
    )
    attachment_id = _insert_assistant_attachment(
        initialized_settings,
        user_id=int(owner["id"]),
        mime_type=mime_type,
        filename=filename,
    )
    _login(client, username=str(owner["username"]), password=password)

    response = client.get(f"/api/assistant/attachments/{attachment_id}")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith(expected_content_type)
    assert response.headers["Content-Disposition"].startswith(expected_disposition)
    _assert_common_attachment_headers(response)
    if expect_csp:
        assert response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    else:
        assert "Content-Security-Policy" not in response.headers


def test_assistant_attachment_view_permissions_remain_unchanged(initialized_settings, client) -> None:
    owner, owner_password = _create_standard_user(
        initialized_settings,
        username=f"assistant-owner-{uuid4().hex[:8]}",
    )
    other, other_password = _create_standard_user(
        initialized_settings,
        username=f"assistant-other-{uuid4().hex[:8]}",
    )
    attachment_id = _insert_assistant_attachment(
        initialized_settings,
        user_id=int(owner["id"]),
        mime_type="image/png",
        filename="safe.png",
    )

    _login(client, username=str(other["username"]), password=other_password)
    other_response = client.get(f"/api/assistant/attachments/{attachment_id}")
    assert other_response.status_code == 404
    _logout(client)

    _login(
        client,
        username=initialized_settings.admin_username,
        password=initialized_settings.admin_bootstrap_password or "",
    )
    admin_response = client.get(f"/api/assistant/attachments/{attachment_id}")
    assert admin_response.status_code == 200
    _logout(client)

    update_assistant_user_access(
        initialized_settings,
        target_user_id=int(owner["id"]),
        assistant_beta_enabled=False,
        note=None,
        actor=_admin_user(initialized_settings),
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    _login(client, username=str(owner["username"]), password=owner_password)
    disabled_access_response = client.get(f"/api/assistant/attachments/{attachment_id}")
    assert disabled_access_response.status_code == 403
    assert disabled_access_response.json()["detail"] == "Assistant (Beta) is not enabled for this account"


def test_assistant_attachment_external_open_stays_image_only_with_raw_headers(
    initialized_settings,
    client,
) -> None:
    settings = replace(initialized_settings, backend_origin="http://testserver")
    client.app.state.settings = settings
    owner, password = _create_standard_user(
        settings,
        username=f"assistant-image-open-{uuid4().hex[:8]}",
    )
    image_attachment_id = _insert_assistant_attachment(
        settings,
        user_id=int(owner["id"]),
        mime_type="image/png",
        filename="safe.png",
        content=b"fake-png",
    )
    svg_attachment_id = _insert_assistant_attachment(
        settings,
        user_id=int(owner["id"]),
        mime_type="image/svg+xml",
        filename="active.svg",
        content=b"<svg><script>alert(1)</script></svg>",
    )
    _login(client, username=str(owner["username"]), password=password)

    ticket_response = client.post(f"/api/assistant/attachments/{image_attachment_id}/external-open")
    assert ticket_response.status_code == 200
    external_url = ticket_response.json()["external_open_url"]
    parsed = urlsplit(external_url)
    raw_response = assistant_routes.assistant_attachment_raw_image(
        parsed.path.rsplit("/", maxsplit=1)[-1],
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))),
        token=parse_qs(parsed.query)["token"][0],
    )

    assert raw_response.media_type == "image/png"
    assert raw_response.headers["Content-Disposition"].startswith("inline")
    _assert_common_attachment_headers(raw_response)

    svg_response = client.post(f"/api/assistant/attachments/{svg_attachment_id}/external-open")
    assert svg_response.status_code == 400
    assert svg_response.json()["detail"] == "Assistant external-open is only available for supported image attachments"


def test_assistant_attachment_view_static_guard_uses_policy_not_global_inline() -> None:
    route_source = inspect.getsource(assistant_routes.assistant_attachment_view)
    policy_source = inspect.getsource(assistant_service.assistant_attachment_response_policy)
    service_source = inspect.getsource(assistant_service)

    assert "assistant_attachment_response_policy" in route_source
    assert 'content_disposition_type="inline"' not in route_source
    assert "ASSISTANT_INLINE_RASTER_IMAGE_MIME_TYPES" in policy_source
    assert "ASSISTANT_INLINE_TEXT_MIME_TYPES" in policy_source
    assert "ASSISTANT_ACTIVE_CONTENT_MIME_TYPES" in service_source
    assert "image/svg+xml" in service_source
    assert "text/html" in service_source
