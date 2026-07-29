from __future__ import annotations

import asyncio
import ast
import inspect
import textwrap
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.app.auth import authenticate_user
from backend.app.db import get_connection, utcnow_iso
from backend.app.routes import assistant as assistant_routes
from backend.app.security import TOKEN_HASH_PREFIX, hash_session_token
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


def _assistant_request_form() -> dict[str, str]:
    return {
        "request_type": "bug_report",
        "title": "Attachment upload limit test",
        "description": "Verifies assistant attachment upload handling.",
        "urgency": "normal",
    }


def _assistant_table_counts(settings) -> tuple[int, int]:
    with get_connection(settings) as connection:
        request_count = int(connection.execute("SELECT COUNT(*) FROM assistant_requests").fetchone()[0])
        attachment_count = int(
            connection.execute("SELECT COUNT(*) FROM assistant_request_attachments").fetchone()[0]
        )
    return request_count, attachment_count


def _raw_image_response_for_external_url(settings, external_url: str):
    parsed = urlsplit(external_url)
    return assistant_routes.assistant_attachment_raw_image(
        parsed.path.rsplit("/", maxsplit=1)[-1],
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))),
        token=parse_qs(parsed.query)["token"][0],
    )


def _assistant_upload_files(settings) -> list[Path]:
    upload_root = settings.db_path.parent / "assistant_uploads"
    if not upload_root.exists():
        return []
    return sorted(path for path in upload_root.rglob("*") if path.is_file())


class _ChunkedFakeUpload:
    filename = "large.txt"
    content_type = "text/plain"

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


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
    ("mime_type", "filename", "expected_content_type", "expected_disposition", "expected_csp"),
    [
        ("image/png", "safe.png", "image/png", "inline", "frame-ancestors 'none'"),
        ("text/plain", "note.txt", "text/plain", "inline", "default-src 'none'; sandbox"),
        ("text/plain; charset=utf-8", "note-params.txt", "text/plain", "inline", "default-src 'none'; sandbox"),
        ("image/svg+xml", "active.svg", "application/octet-stream", "attachment", "default-src 'none'; sandbox"),
        ("text/html", "active.html", "application/octet-stream", "attachment", "default-src 'none'; sandbox"),
        (
            "text/html; charset=utf-8",
            "active-params.html",
            "application/octet-stream",
            "attachment",
            "default-src 'none'; sandbox",
        ),
        ("application/pdf", "report.pdf", "application/octet-stream", "attachment", "default-src 'none'; sandbox"),
        (
            "application/octet-stream",
            "unknown.bin",
            "application/octet-stream",
            "attachment",
            "default-src 'none'; sandbox",
        ),
        (None, "empty.bin", "application/octet-stream", "attachment", "default-src 'none'; sandbox"),
    ],
)
def test_assistant_attachment_view_applies_safe_inline_or_download_policy(
    initialized_settings,
    client,
    mime_type,
    filename: str,
    expected_content_type: str,
    expected_disposition: str,
    expected_csp: str,
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
    assert response.headers["Content-Security-Policy"] == expected_csp


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
    assert disabled_access_response.json()["detail"] == "Assistant is not enabled for this account"


def test_admin_has_assistant_access_without_account_flag(
    initialized_settings,
    client,
) -> None:
    _login(
        client,
        username=initialized_settings.admin_username,
        password=initialized_settings.admin_bootstrap_password or "",
    )

    response = client.get("/api/assistant/requests")

    assert response.status_code == 200
    assert response.json() == {"requests": []}


def test_assistant_external_open_new_ticket_stores_hmac_and_opens(
    initialized_settings,
    client,
) -> None:
    settings = replace(initialized_settings, backend_origin="http://testserver")
    client.app.state.settings = settings
    owner, password = _create_standard_user(
        settings,
        username=f"assistant-image-hmac-{uuid4().hex[:8]}",
    )
    image_attachment_id = _insert_assistant_attachment(
        settings,
        user_id=int(owner["id"]),
        mime_type="image/png",
        filename="safe.png",
        content=b"fake-png",
    )
    _login(client, username=str(owner["username"]), password=password)

    ticket_response = client.post(f"/api/assistant/attachments/{image_attachment_id}/external-open")

    assert ticket_response.status_code == 200
    external_url = ticket_response.json()["external_open_url"]
    parsed = urlsplit(external_url)
    ticket_id = parsed.path.rsplit("/", maxsplit=1)[-1]
    raw_token = parse_qs(parsed.query)["token"][0]
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT access_token_hash, attachment_id
            FROM assistant_attachment_external_open_tickets
            WHERE ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()
    assert row is not None
    assert str(row["access_token_hash"]).startswith(TOKEN_HASH_PREFIX)
    assert raw_token not in str(row["access_token_hash"])
    assert int(row["attachment_id"]) == image_attachment_id

    raw_response = _raw_image_response_for_external_url(settings, external_url)
    assert raw_response.media_type == "image/png"
    assert raw_response.headers["Content-Disposition"].startswith("inline")
    _assert_common_attachment_headers(raw_response)


def test_legacy_assistant_external_open_ticket_opens_and_lazy_rehashes(initialized_settings) -> None:
    settings = replace(initialized_settings, backend_origin="http://testserver")
    owner, _password = _create_standard_user(
        settings,
        username=f"assistant-image-legacy-{uuid4().hex[:8]}",
    )
    image_attachment_id = _insert_assistant_attachment(
        settings,
        user_id=int(owner["id"]),
        mime_type="image/png",
        filename="legacy.png",
        content=b"legacy-png",
    )
    ticket_id = f"legacy-ticket-{uuid4().hex}"
    raw_token = "legacy-assistant-external-open-token"
    legacy_hash = hash_session_token(raw_token, settings.session_secret)
    now = utcnow_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    with get_connection(settings) as connection:
        cursor = connection.execute(
            """
            INSERT INTO assistant_attachment_external_open_tickets (
                ticket_id,
                access_token_hash,
                attachment_id,
                issued_by_user_id,
                created_at,
                expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ticket_id, legacy_hash, image_attachment_id, int(owner["id"]), now, expires_at),
        )
        row_id = int(cursor.lastrowid)
        connection.commit()

    raw_response = assistant_routes.assistant_attachment_raw_image(
        ticket_id,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))),
        token=raw_token,
    )

    assert raw_response.media_type == "image/png"
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT ticket_id, access_token_hash, attachment_id, issued_by_user_id, expires_at, last_opened_at
            FROM assistant_attachment_external_open_tickets
            WHERE id = ?
            """,
            (row_id,),
        ).fetchone()
    assert row is not None
    assert row["ticket_id"] == ticket_id
    assert str(row["access_token_hash"]).startswith(TOKEN_HASH_PREFIX)
    assert row["access_token_hash"] != legacy_hash
    assert int(row["attachment_id"]) == image_attachment_id
    assert int(row["issued_by_user_id"]) == int(owner["id"])
    assert row["expires_at"] == expires_at
    assert row["last_opened_at"] is not None


def test_expired_legacy_assistant_external_open_ticket_is_rejected_without_rehash(initialized_settings) -> None:
    settings = replace(initialized_settings, backend_origin="http://testserver")
    owner, _password = _create_standard_user(
        settings,
        username=f"assistant-image-expired-{uuid4().hex[:8]}",
    )
    image_attachment_id = _insert_assistant_attachment(
        settings,
        user_id=int(owner["id"]),
        mime_type="image/png",
        filename="expired.png",
        content=b"expired-png",
    )
    ticket_id = f"expired-legacy-ticket-{uuid4().hex}"
    raw_token = "expired-legacy-assistant-external-open-token"
    legacy_hash = hash_session_token(raw_token, settings.session_secret)
    now = utcnow_iso()
    expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with get_connection(settings) as connection:
        connection.execute(
            """
            INSERT INTO assistant_attachment_external_open_tickets (
                ticket_id,
                access_token_hash,
                attachment_id,
                issued_by_user_id,
                created_at,
                expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ticket_id, legacy_hash, image_attachment_id, int(owner["id"]), now, expires_at),
        )
        connection.commit()

    with pytest.raises(HTTPException) as exc:
        assistant_service.resolve_assistant_image_external_open_ticket(
            settings,
            ticket_id=ticket_id,
            token=raw_token,
        )

    assert exc.value.status_code in {404, 410}
    with get_connection(settings) as connection:
        row = connection.execute(
            """
            SELECT access_token_hash
            FROM assistant_attachment_external_open_tickets
            WHERE ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if row is not None:
        assert row["access_token_hash"] == legacy_hash


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


def test_assistant_attachment_upload_static_guard_uses_bounded_reads() -> None:
    for helper in (
        assistant_routes._read_limited_attachment_content,
        assistant_routes._read_attachment,
    ):
        source = textwrap.dedent(inspect.getsource(helper))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "read":
                assert call.args or call.keywords


def test_assistant_attachment_upload_exact_limit_succeeds(
    initialized_settings,
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(assistant_routes, "MAX_ATTACHMENT_BYTES", 32)
    username = f"assistant-upload-limit-{uuid4().hex[:8]}"
    owner, password = _create_standard_user(
        initialized_settings,
        username=username,
    )
    _login(client, username=username, password=password)
    before_requests, before_attachments = _assistant_table_counts(initialized_settings)
    content = b"x" * 32

    response = client.post(
        "/api/assistant/requests",
        data=_assistant_request_form(),
        files={"attachment": ("limit.txt", content, "text/plain")},
    )

    assert response.status_code == 201
    request_payload = response.json()["request"]
    assert request_payload["submitted_by_user_id"] == int(owner["id"])
    assert len(request_payload["attachments"]) == 1
    attachment = request_payload["attachments"][0]
    assert attachment["size_bytes"] == 32
    assert attachment["attachment_type"] == "text"
    assert attachment["mime_type"] == "text/plain"
    stored_path = initialized_settings.db_path.parent / "assistant_uploads" / attachment["storage_path_safe_ref"]
    assert stored_path.exists()
    assert stored_path.read_bytes() == content
    assert _assistant_table_counts(initialized_settings) == (before_requests + 1, before_attachments + 1)


def test_assistant_attachment_upload_over_limit_fails_without_db_or_file_residue(
    initialized_settings,
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(assistant_routes, "MAX_ATTACHMENT_BYTES", 32)
    username = f"assistant-upload-too-large-{uuid4().hex[:8]}"
    _owner, password = _create_standard_user(
        initialized_settings,
        username=username,
    )
    _login(client, username=username, password=password)
    before_counts = _assistant_table_counts(initialized_settings)
    before_files = _assistant_upload_files(initialized_settings)

    response = client.post(
        "/api/assistant/requests",
        data=_assistant_request_form(),
        files={"attachment": ("too-large.txt", b"x" * 33, "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Attachment must be 8 MB or smaller"
    assert _assistant_table_counts(initialized_settings) == before_counts
    assert _assistant_upload_files(initialized_settings) == before_files


def test_assistant_attachment_bounded_read_stops_after_limit(monkeypatch) -> None:
    monkeypatch.setattr(assistant_routes, "MAX_ATTACHMENT_BYTES", 16)
    monkeypatch.setattr(assistant_routes, "ATTACHMENT_READ_CHUNK_BYTES", 8)
    upload = _ChunkedFakeUpload([b"a" * 8, b"b" * 8, b"c" * 8, b"d" * 8])

    with pytest.raises(assistant_routes.HTTPException) as exc_info:
        asyncio.run(assistant_routes._read_limited_attachment_content(upload))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Attachment must be 8 MB or smaller"
    assert upload.read_sizes == [8, 8, 8]
    assert upload._chunks == [b"d" * 8]


def test_assistant_attachment_multiple_uploads_over_limit_cleans_up_all_partial_state(
    initialized_settings,
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(assistant_routes, "MAX_ATTACHMENT_BYTES", 32)
    username = f"assistant-upload-multi-{uuid4().hex[:8]}"
    _owner, password = _create_standard_user(
        initialized_settings,
        username=username,
    )
    _login(client, username=username, password=password)
    before_counts = _assistant_table_counts(initialized_settings)
    before_files = _assistant_upload_files(initialized_settings)

    response = client.post(
        "/api/assistant/requests",
        data=_assistant_request_form(),
        files=[
            ("attachments", ("ok.txt", b"safe attachment", "text/plain")),
            ("attachments", ("too-large.txt", b"x" * 33, "text/plain")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Attachment must be 8 MB or smaller"
    assert _assistant_table_counts(initialized_settings) == before_counts
    assert _assistant_upload_files(initialized_settings) == before_files
