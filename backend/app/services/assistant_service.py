from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, status

from ..config import Settings
from ..db import get_connection, utcnow_iso
from ..models import AuthenticatedUser
from ..security import generate_session_token, hash_session_token
from .audit_service import log_audit_event


REQUEST_TYPES = {
    "bug_report",
    "improvement_suggestion",
    "library_issue",
    "playback_issue",
    "security_concern",
    "account_request",
    "other",
}
REQUEST_URGENCY = {"low", "normal", "high"}
REQUEST_STATUS = {"new", "triaged", "awaiting_admin", "approved", "rejected", "closed"}
TRIAGE_CREATED_BY = {"assistant", "admin_user"}
RISK_LEVELS = {"low", "medium", "high", "critical"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
REVERSIBILITY_IMPACTS = {"none", "r0_possible", "r1_possible", "r2_or_higher", "unknown"}
ACTION_CREATED_BY_TYPES = {"assistant", "admin_user"}
ACTION_TYPES = {
    "create_backup_checkpoint",
    "library_rescan",
    "service_restart",
    "prepare_patch_in_sandbox",
    "save_change_record_draft",
    "send_admin_notification",
}
ACTION_TARGET_SCOPES = {
    "library_local",
    "library_cloud",
    "library_all",
    "service_backend",
    "service_frontend",
    "sandbox_repo_copy",
    "other",
}
ACTION_STATUSES = {"draft", "awaiting_admin", "approved", "rejected", "cancelled", "executed", "failed"}
APPROVAL_DECISIONS = {"approved", "rejected", "needs_more_info"}
CHANGE_STATUSES = {"draft", "prepared", "executed", "reverted", "failed"}
REVERSIBILITY_LEVELS = {"r0", "r1", "r2", "r3", "unknown"}
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
ASSISTANT_EXTERNAL_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
    "image/avif",
    "image/bmp",
}


def assistant_beta_enabled_for_user(settings: Settings, *, user_id: int) -> bool:
    with get_connection(settings) as connection:
        return _assistant_beta_enabled_in_connection(connection, user_id=user_id)


def build_assistant_access_map(connection: sqlite3.Connection, user_ids: list[int]) -> dict[int, dict[str, object]]:
    if not user_ids:
        return {}
    placeholders = ",".join("?" for _ in user_ids)
    rows = connection.execute(
        f"""
        SELECT
            user_id,
            assistant_beta_enabled,
            enabled_by_user_id,
            enabled_at,
            disabled_at,
            note,
            created_at,
            updated_at
        FROM assistant_user_access
        WHERE user_id IN ({placeholders})
        """,
        tuple(user_ids),
    ).fetchall()
    by_user_id = {
        int(row["user_id"]): _assistant_access_payload_from_row(row)
        for row in rows
    }
    return {
        int(user_id): by_user_id.get(
            int(user_id),
            {
                "user_id": int(user_id),
                "assistant_beta_enabled": False,
                "enabled_by_user_id": None,
                "enabled_at": None,
                "disabled_at": None,
                "note": None,
                "created_at": None,
                "updated_at": None,
            },
        )
        for user_id in user_ids
    }


def update_assistant_user_access(
    settings: Settings,
    *,
    target_user_id: int,
    assistant_beta_enabled: bool,
    note: str | None,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    now = utcnow_iso()
    normalized_note = _normalize_optional_text(note, max_length=800)
    with get_connection(settings) as connection:
        user_row = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ? LIMIT 1",
            (target_user_id,),
        ).fetchone()
        if user_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if (user_row["role"] or "standard_user") != "standard_user":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assistant (Beta) access can only be changed for standard users",
            )
        existing = connection.execute(
            """
            SELECT id, assistant_beta_enabled, enabled_by_user_id, enabled_at, disabled_at, created_at
            FROM assistant_user_access
            WHERE user_id = ?
            LIMIT 1
            """,
            (target_user_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO assistant_user_access (
                    user_id,
                    assistant_beta_enabled,
                    enabled_by_user_id,
                    enabled_at,
                    disabled_at,
                    note,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_user_id,
                    int(assistant_beta_enabled),
                    actor.id if assistant_beta_enabled else None,
                    now if assistant_beta_enabled else None,
                    now if not assistant_beta_enabled else None,
                    normalized_note,
                    now,
                    now,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE assistant_user_access
                SET assistant_beta_enabled = ?,
                    enabled_by_user_id = ?,
                    enabled_at = CASE WHEN ? = 1 THEN COALESCE(enabled_at, ?) ELSE enabled_at END,
                    disabled_at = CASE WHEN ? = 1 THEN NULL ELSE ? END,
                    note = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    int(assistant_beta_enabled),
                    actor.id if assistant_beta_enabled else existing["enabled_by_user_id"],
                    int(assistant_beta_enabled),
                    now,
                    int(assistant_beta_enabled),
                    now,
                    normalized_note,
                    now,
                    target_user_id,
                ),
            )
        row = connection.execute(
            """
            SELECT
                user_id,
                assistant_beta_enabled,
                enabled_by_user_id,
                enabled_at,
                disabled_at,
                note,
                created_at,
                updated_at
            FROM assistant_user_access
            WHERE user_id = ?
            LIMIT 1
            """,
            (target_user_id,),
        ).fetchone()
        connection.commit()

    log_audit_event(
        settings,
        action="admin.assistant.user_access.update",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="user",
        target_id=target_user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "assistant_beta_enabled": bool(row["assistant_beta_enabled"]),
            "note_present": bool(normalized_note),
        },
    )
    return _assistant_access_payload_from_row(row)


def list_user_assistant_requests(settings: Settings, *, user_id: int) -> list[dict[str, object]]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                request_number,
                submitted_by_user_id,
                submitted_by_display_name_snapshot,
                request_type,
                title,
                urgency,
                status,
                created_at,
                updated_at,
                status_updated_at,
                page_context,
                platform,
                source_context,
                is_archived
            FROM assistant_requests
            WHERE submitted_by_user_id = ? AND is_archived = 0
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        return [_assistant_request_summary_from_row(row) for row in rows]


def get_user_assistant_request_detail(
    settings: Settings,
    *,
    user_id: int,
    request_id: int,
) -> dict[str, object]:
    with get_connection(settings) as connection:
        row = _fetch_request_row_for_user(connection, request_id=request_id, user_id=user_id)
        return _assistant_request_detail_from_row(connection, row)


def list_admin_assistant_requests(settings: Settings) -> list[dict[str, object]]:
    with get_connection(settings) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                request_number,
                submitted_by_user_id,
                submitted_by_display_name_snapshot,
                request_type,
                title,
                urgency,
                status,
                created_at,
                updated_at,
                status_updated_at,
                page_context,
                platform,
                source_context,
                is_archived
            FROM assistant_requests
            WHERE is_archived = 0
            ORDER BY datetime(created_at) DESC, id DESC
            """
        ).fetchall()
        return [_assistant_request_summary_from_row(row) for row in rows]


def get_admin_assistant_request_detail(settings: Settings, *, request_id: int) -> dict[str, object]:
    with get_connection(settings) as connection:
        row = _fetch_request_row(connection, request_id=request_id)
        return _assistant_request_detail_from_row(connection, row)


def create_assistant_request(
    settings: Settings,
    *,
    user: AuthenticatedUser,
    request_type: str,
    title: str,
    description: str,
    repro_steps: str | None,
    expected_result: str | None,
    actual_result: str | None,
    urgency: str,
    page_context: str | None,
    platform: str | None,
    app_version: str | None,
    source_context: str | None,
    related_entity_type: str | None,
    related_entity_id: str | None,
    attachments: list[dict[str, object]] | None,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_request_type = _validated_enum(request_type, REQUEST_TYPES, "Unsupported request type")
    normalized_urgency = _validated_enum(urgency, REQUEST_URGENCY, "Unsupported urgency")
    normalized_title = _normalize_required_text(title, field_name="Title", max_length=200)
    normalized_description = _normalize_required_text(description, field_name="Description", max_length=5000)
    normalized_repro_steps = _normalize_optional_text(repro_steps, max_length=4000)
    normalized_expected_result = _normalize_optional_text(expected_result, max_length=3000)
    normalized_actual_result = _normalize_optional_text(actual_result, max_length=3000)
    normalized_page_context = _normalize_optional_text(page_context, max_length=240)
    normalized_platform = _normalize_optional_text(platform, max_length=120)
    normalized_app_version = _normalize_optional_text(app_version, max_length=80)
    normalized_source_context = _normalize_optional_text(source_context, max_length=120)
    normalized_related_entity_type = _normalize_optional_text(related_entity_type, max_length=80)
    normalized_related_entity_id = _normalize_optional_text(related_entity_id, max_length=120)
    normalized_attachments = list(attachments or [])
    now = utcnow_iso()
    created_files: list[Path] = []

    with get_connection(settings) as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO assistant_requests (
                    request_number,
                    submitted_by_user_id,
                    submitted_by_display_name_snapshot,
                    request_type,
                    title,
                    description,
                    repro_steps,
                    expected_result,
                    actual_result,
                    urgency,
                    page_context,
                    platform,
                    app_version,
                    source_context,
                    related_entity_type,
                    related_entity_id,
                    status,
                    status_updated_at,
                    status_updated_by_user_id,
                    admin_note,
                    duplicate_group_key,
                    is_archived,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, NULL, NULL, 0, ?, ?)
                """,
                (
                    "PENDING",
                    user.id,
                    user.username,
                    normalized_request_type,
                    normalized_title,
                    normalized_description,
                    normalized_repro_steps,
                    normalized_expected_result,
                    normalized_actual_result,
                    normalized_urgency,
                    normalized_page_context,
                    normalized_platform,
                    normalized_app_version,
                    normalized_source_context,
                    normalized_related_entity_type,
                    normalized_related_entity_id,
                    now,
                    user.id,
                    now,
                    now,
                ),
            )
            request_id = int(cursor.lastrowid)
            request_number = f"AR-{request_id:06d}"
            connection.execute(
                """
                UPDATE assistant_requests
                SET request_number = ?, updated_at = ?
                WHERE id = ?
                """,
                (request_number, now, request_id),
            )
            for attachment in normalized_attachments:
                created_safe_ref, created_file_path = _store_request_attachment(
                    settings,
                    request_number=request_number,
                    attachment=attachment,
                )
                created_files.append(created_file_path)
                connection.execute(
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
                        request_id,
                        str(attachment["attachment_type"]),
                        created_safe_ref,
                        attachment.get("original_filename"),
                        attachment.get("mime_type"),
                        int(attachment.get("size_bytes") or 0),
                        now,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            for created_file_path in reversed(created_files):
                if created_file_path.exists():
                    created_file_path.unlink(missing_ok=True)
                    _cleanup_empty_parent_dirs(created_file_path.parent, stop_at=_assistant_upload_root(settings))
            raise

    log_audit_event(
        settings,
        action="assistant.request.submit",
        outcome="success",
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_id=user.session_id,
        target_type="assistant_request",
        target_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={
            "request_number": request_number,
            "request_type": normalized_request_type,
            "urgency": normalized_urgency,
            "attachment_count": len(normalized_attachments),
        },
    )
    return get_user_assistant_request_detail(settings, user_id=user.id, request_id=request_id)


def get_assistant_attachment_file(
    settings: Settings,
    *,
    attachment_id: int,
    user: AuthenticatedUser,
) -> dict[str, object]:
    with get_connection(settings) as connection:
        row = _require_assistant_attachment_access_row(
            connection,
            attachment_id=attachment_id,
            user=user,
        )
    file_path = _resolve_assistant_attachment_path(settings, safe_ref=str(row["storage_path_safe_ref"]))
    return {
        "path": file_path,
        "filename": row["original_filename"] or file_path.name,
        "mime_type": row["mime_type"] or "application/octet-stream",
    }


def issue_assistant_image_external_open_ticket(
    settings: Settings,
    *,
    attachment_id: int,
    user: AuthenticatedUser,
) -> dict[str, object]:
    cleanup_assistant_attachment_external_open_tickets(settings)
    backend_origin = _assistant_external_open_origin(settings)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.assistant_attachment_external_open_ttl_seconds)
    created_at_iso = now.isoformat()
    expires_at_iso = expires_at.isoformat()
    ticket_id = generate_session_token()
    access_token = generate_session_token()
    access_token_hash = hash_session_token(access_token, settings.session_secret)

    with get_connection(settings) as connection:
        row = _require_assistant_attachment_access_row(
            connection,
            attachment_id=attachment_id,
            user=user,
        )
        mime_type = _validated_external_image_mime(row["mime_type"])
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
            (
                ticket_id,
                access_token_hash,
                int(row["id"]),
                user.id,
                created_at_iso,
                expires_at_iso,
            ),
        )
        connection.commit()

    return {
        "external_open_kind": "raw_image_ticket",
        "external_open_url": (
            f"{backend_origin.rstrip('/')}/raw/assistant-images/{quote(ticket_id, safe='')}"
            f"?token={quote(access_token, safe='')}"
        ),
        "external_open_expires_at": expires_at_iso,
        "mime_type": mime_type,
    }


def resolve_assistant_image_external_open_ticket(
    settings: Settings,
    *,
    ticket_id: str,
    token: str,
) -> dict[str, object]:
    cleanup_assistant_attachment_external_open_tickets(settings)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    with get_connection(settings) as connection:
        ticket_row = connection.execute(
            """
            SELECT
                t.id,
                t.ticket_id,
                t.access_token_hash,
                t.attachment_id,
                t.expires_at
            FROM assistant_attachment_external_open_tickets t
            WHERE t.ticket_id = ?
            LIMIT 1
            """,
            (ticket_id,),
        ).fetchone()
        if ticket_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant image external-open link not found",
            )
        if str(ticket_row["expires_at"]) <= now_iso:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Assistant image external-open link has expired",
            )
        token_hash = hash_session_token(token, settings.session_secret)
        if str(ticket_row["access_token_hash"]) != token_hash:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant image external-open link not found",
            )
        attachment_row = connection.execute(
            """
            SELECT
                a.id,
                a.storage_path_safe_ref,
                a.original_filename,
                a.mime_type
            FROM assistant_request_attachments a
            WHERE a.id = ?
            LIMIT 1
            """,
            (int(ticket_row["attachment_id"]),),
        ).fetchone()
        if attachment_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant image attachment not found",
            )
        mime_type = _validated_external_image_mime(attachment_row["mime_type"])
        connection.execute(
            """
            UPDATE assistant_attachment_external_open_tickets
            SET last_opened_at = ?
            WHERE id = ?
            """,
            (now_iso, int(ticket_row["id"])),
        )
        connection.commit()

    file_path = _resolve_assistant_attachment_path(
        settings,
        safe_ref=str(attachment_row["storage_path_safe_ref"]),
    )
    return {
        "path": file_path,
        "filename": attachment_row["original_filename"] or file_path.name,
        "mime_type": mime_type,
        "expires_at": str(ticket_row["expires_at"]),
    }


def cleanup_assistant_attachment_external_open_tickets(settings: Settings) -> None:
    now_iso = utcnow_iso()
    with get_connection(settings) as connection:
        connection.execute(
            """
            DELETE FROM assistant_attachment_external_open_tickets
            WHERE expires_at <= ?
            """,
            (now_iso,),
        )
        connection.commit()


def update_assistant_request_status(
    settings: Settings,
    *,
    request_id: int,
    status_value: str,
    admin_note: str | None,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_status = _validated_enum(status_value, REQUEST_STATUS, "Unsupported request status")
    normalized_admin_note = _normalize_optional_text(admin_note, max_length=3000)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        row = _fetch_request_row(connection, request_id=request_id)
        connection.execute(
            """
            UPDATE assistant_requests
            SET status = ?,
                status_updated_at = ?,
                status_updated_by_user_id = ?,
                admin_note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (normalized_status, now, actor.id, normalized_admin_note, now, request_id),
        )
        connection.commit()

    log_audit_event(
        settings,
        action="admin.assistant.request.status",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="assistant_request",
        target_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"from_status": row["status"], "to_status": normalized_status},
    )
    return get_admin_assistant_request_detail(settings, request_id=request_id)


def create_assistant_triage_draft(
    settings: Settings,
    *,
    request_id: int,
    created_by: str,
    model_provider: str | None,
    model_name: str | None,
    summary: str,
    classification: str,
    risk_level: str,
    confidence_level: str,
    possible_duplicate_request_ids: list[int],
    suggested_next_step: str | None,
    suggested_owner: str | None,
    needs_admin_approval: bool,
    needs_external_access_approval: bool,
    reversibility_impact_if_action_taken: str,
    notes_for_admin: str | None,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_created_by = _validated_enum(created_by, TRIAGE_CREATED_BY, "Unsupported triage origin")
    normalized_summary = _normalize_required_text(summary, field_name="Summary", max_length=4000)
    normalized_classification = _normalize_required_text(classification, field_name="Classification", max_length=200)
    normalized_risk_level = _validated_enum(risk_level, RISK_LEVELS, "Unsupported risk level")
    normalized_confidence = _validated_enum(confidence_level, CONFIDENCE_LEVELS, "Unsupported confidence level")
    normalized_reversibility = _validated_enum(
        reversibility_impact_if_action_taken,
        REVERSIBILITY_IMPACTS,
        "Unsupported reversibility impact",
    )
    normalized_model_provider = _normalize_optional_text(model_provider, max_length=80)
    normalized_model_name = _normalize_optional_text(model_name, max_length=120)
    normalized_next_step = _normalize_optional_text(suggested_next_step, max_length=2000)
    normalized_owner = _normalize_optional_text(suggested_owner, max_length=120)
    normalized_notes = _normalize_optional_text(notes_for_admin, max_length=3000)
    duplicate_ids = _normalized_duplicate_ids(possible_duplicate_request_ids)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        _fetch_request_row(connection, request_id=request_id)
        cursor = connection.execute(
            """
            INSERT INTO assistant_triage_drafts (
                request_id,
                created_by,
                model_provider,
                model_name,
                summary,
                classification,
                risk_level,
                confidence_level,
                possible_duplicate_request_ids_json,
                suggested_next_step,
                suggested_owner,
                needs_admin_approval,
                needs_external_access_approval,
                reversibility_impact_if_action_taken,
                notes_for_admin,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                normalized_created_by,
                normalized_model_provider,
                normalized_model_name,
                normalized_summary,
                normalized_classification,
                normalized_risk_level,
                normalized_confidence,
                json.dumps(duplicate_ids),
                normalized_next_step,
                normalized_owner,
                int(needs_admin_approval),
                int(needs_external_access_approval),
                normalized_reversibility,
                normalized_notes,
                now,
                now,
            ),
        )
        triage_id = int(cursor.lastrowid)
        connection.commit()

    log_audit_event(
        settings,
        action="admin.assistant.triage_draft.create",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="assistant_request",
        target_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"triage_draft_id": triage_id, "created_by": normalized_created_by},
    )
    return get_admin_assistant_request_detail(settings, request_id=request_id)


def create_assistant_action_request(
    settings: Settings,
    *,
    request_id: int,
    triage_draft_id: int | None,
    created_by_type: str,
    action_type: str,
    target_scope: str,
    reason: str,
    proposed_plan: str | None,
    risk_level: str,
    requires_admin_approval: bool,
    requires_external_access_approval: bool,
    reversibility_level: str,
    warning_if_not_fully_reversible: str | None,
    status_value: str,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_created_by = _validated_enum(created_by_type, ACTION_CREATED_BY_TYPES, "Unsupported action origin")
    normalized_action_type = _validated_enum(action_type, ACTION_TYPES, "Unsupported action type")
    normalized_target_scope = _validated_enum(target_scope, ACTION_TARGET_SCOPES, "Unsupported target scope")
    normalized_reason = _normalize_required_text(reason, field_name="Reason", max_length=3000)
    normalized_plan = _normalize_optional_text(proposed_plan, max_length=4000)
    normalized_risk = _validated_enum(risk_level, RISK_LEVELS, "Unsupported risk level")
    normalized_reversibility = _validated_enum(reversibility_level, REVERSIBILITY_LEVELS, "Unsupported reversibility level")
    normalized_warning = _normalize_optional_text(warning_if_not_fully_reversible, max_length=2000)
    normalized_status = _validated_enum(status_value, ACTION_STATUSES, "Unsupported action request status")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        _fetch_request_row(connection, request_id=request_id)
        if triage_draft_id is not None:
            triage_row = connection.execute(
                "SELECT id FROM assistant_triage_drafts WHERE id = ? AND request_id = ? LIMIT 1",
                (triage_draft_id, request_id),
            ).fetchone()
            if triage_row is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Triage draft not found for this request")
        cursor = connection.execute(
            """
            INSERT INTO assistant_action_requests (
                request_id,
                triage_draft_id,
                created_by_type,
                created_by_user_id,
                action_type,
                target_scope,
                reason,
                proposed_plan,
                risk_level,
                requires_admin_approval,
                requires_external_access_approval,
                reversibility_level,
                warning_if_not_fully_reversible,
                status,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                triage_draft_id,
                normalized_created_by,
                actor.id if normalized_created_by == "admin_user" else None,
                normalized_action_type,
                normalized_target_scope,
                normalized_reason,
                normalized_plan,
                normalized_risk,
                int(requires_admin_approval),
                int(requires_external_access_approval),
                normalized_reversibility,
                normalized_warning,
                normalized_status,
                now,
                now,
            ),
        )
        action_request_id = int(cursor.lastrowid)
        connection.commit()

    log_audit_event(
        settings,
        action="admin.assistant.action_request.create",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="assistant_request",
        target_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"action_request_id": action_request_id, "action_type": normalized_action_type},
    )
    return get_admin_assistant_request_detail(settings, request_id=request_id)


def create_assistant_approval_record(
    settings: Settings,
    *,
    action_request_id: int,
    decision: str,
    decision_note: str | None,
    backup_required: bool,
    rollback_plan_required: bool,
    external_access_approved: bool,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_decision = _validated_enum(decision, APPROVAL_DECISIONS, "Unsupported approval decision")
    normalized_note = _normalize_optional_text(decision_note, max_length=3000)
    now = utcnow_iso()
    with get_connection(settings) as connection:
        action_row = connection.execute(
            "SELECT id, request_id FROM assistant_action_requests WHERE id = ? LIMIT 1",
            (action_request_id,),
        ).fetchone()
        if action_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action request not found")
        connection.execute(
            """
            INSERT INTO assistant_approval_records (
                action_request_id,
                decision,
                decided_by_user_id,
                decision_note,
                backup_required,
                rollback_plan_required,
                external_access_approved,
                decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_request_id,
                normalized_decision,
                actor.id,
                normalized_note,
                int(backup_required),
                int(rollback_plan_required),
                int(external_access_approved),
                now,
            ),
        )
        next_status = None
        if normalized_decision == "approved":
            next_status = "approved"
        elif normalized_decision == "rejected":
            next_status = "rejected"
        elif normalized_decision == "needs_more_info":
            next_status = "awaiting_admin"
        if next_status is not None:
            connection.execute(
                "UPDATE assistant_action_requests SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, now, action_request_id),
            )
        connection.commit()
        request_id = int(action_row["request_id"])

    log_audit_event(
        settings,
        action="admin.assistant.approval_record.create",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="assistant_action_request",
        target_id=action_request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"decision": normalized_decision},
    )
    return get_admin_assistant_request_detail(settings, request_id=request_id)


def create_assistant_change_record(
    settings: Settings,
    *,
    request_id: int,
    linked_action_request_id: int | None,
    created_by_type: str,
    change_summary: str | None,
    reversibility_level: str,
    backup_reference: str | None,
    revert_recipe_draft: str | None,
    verification_plan_draft: str | None,
    status_value: str,
    actor: AuthenticatedUser,
    ip_address: str | None,
    user_agent: str | None,
) -> dict[str, object]:
    normalized_created_by = _validated_enum(created_by_type, ACTION_CREATED_BY_TYPES, "Unsupported change record origin")
    normalized_change_summary = _normalize_optional_text(change_summary, max_length=3000)
    normalized_reversibility = _validated_enum(reversibility_level, REVERSIBILITY_LEVELS, "Unsupported reversibility level")
    normalized_backup_reference = _normalize_optional_text(backup_reference, max_length=240)
    normalized_revert_recipe = _normalize_optional_text(revert_recipe_draft, max_length=4000)
    normalized_verification_plan = _normalize_optional_text(verification_plan_draft, max_length=4000)
    normalized_status = _validated_enum(status_value, CHANGE_STATUSES, "Unsupported change record status")
    now = utcnow_iso()
    with get_connection(settings) as connection:
        _fetch_request_row(connection, request_id=request_id)
        if linked_action_request_id is not None:
            action_row = connection.execute(
                "SELECT id FROM assistant_action_requests WHERE id = ? AND request_id = ? LIMIT 1",
                (linked_action_request_id, request_id),
            ).fetchone()
            if action_row is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Linked action request not found for this request")
        connection.execute(
            """
            INSERT INTO assistant_change_records (
                request_id,
                linked_action_request_id,
                created_at,
                created_by_type,
                change_summary,
                reversibility_level,
                backup_reference,
                revert_recipe_draft,
                verification_plan_draft,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                linked_action_request_id,
                now,
                normalized_created_by,
                normalized_change_summary,
                normalized_reversibility,
                normalized_backup_reference,
                normalized_revert_recipe,
                normalized_verification_plan,
                normalized_status,
            ),
        )
        connection.commit()

    log_audit_event(
        settings,
        action="admin.assistant.change_record.create",
        outcome="success",
        user_id=actor.id,
        username=actor.username,
        role=actor.role,
        session_id=actor.session_id,
        target_type="assistant_request",
        target_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"linked_action_request_id": linked_action_request_id},
    )
    return get_admin_assistant_request_detail(settings, request_id=request_id)


def _assistant_beta_enabled_in_connection(connection: sqlite3.Connection, *, user_id: int) -> bool:
    row = connection.execute(
        """
        SELECT assistant_beta_enabled
        FROM assistant_user_access
        WHERE user_id = ?
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return bool(row["assistant_beta_enabled"]) if row is not None else False


def _assistant_access_payload_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "user_id": int(row["user_id"]),
        "assistant_beta_enabled": bool(row["assistant_beta_enabled"]),
        "enabled_by_user_id": row["enabled_by_user_id"],
        "enabled_at": row["enabled_at"],
        "disabled_at": row["disabled_at"],
        "note": row["note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _fetch_request_row_for_user(connection: sqlite3.Connection, *, request_id: int, user_id: int) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT *
        FROM assistant_requests
        WHERE id = ? AND submitted_by_user_id = ?
        LIMIT 1
        """,
        (request_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant request not found")
    return row


def _fetch_request_row(connection: sqlite3.Connection, *, request_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM assistant_requests WHERE id = ? LIMIT 1",
        (request_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant request not found")
    return row


def _assistant_request_summary_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "request_number": row["request_number"],
        "submitted_by_user_id": int(row["submitted_by_user_id"]),
        "submitted_by_display_name_snapshot": row["submitted_by_display_name_snapshot"],
        "request_type": row["request_type"],
        "title": row["title"],
        "urgency": row["urgency"] or "normal",
        "status": row["status"] or "new",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status_updated_at": row["status_updated_at"],
        "page_context": row["page_context"],
        "platform": row["platform"],
        "source_context": row["source_context"],
        "is_archived": bool(row["is_archived"]),
    }


def _assistant_request_detail_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    request_id = int(row["id"])
    summary = _assistant_request_summary_from_row(row)
    attachments = connection.execute(
        """
        SELECT
            id,
            attachment_type,
            storage_kind,
            storage_path_safe_ref,
            original_filename,
            mime_type,
            size_bytes,
            created_at
        FROM assistant_request_attachments
        WHERE request_id = ?
        ORDER BY datetime(created_at) ASC, id ASC
        """,
        (request_id,),
    ).fetchall()
    triage_rows = connection.execute(
        """
        SELECT *
        FROM assistant_triage_drafts
        WHERE request_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        (request_id,),
    ).fetchall()
    action_rows = connection.execute(
        """
        SELECT *
        FROM assistant_action_requests
        WHERE request_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        (request_id,),
    ).fetchall()
    action_ids = [int(entry["id"]) for entry in action_rows]
    approval_rows: list[sqlite3.Row] = []
    if action_ids:
        placeholders = ",".join("?" for _ in action_ids)
        approval_rows = connection.execute(
            f"""
            SELECT *
            FROM assistant_approval_records
            WHERE action_request_id IN ({placeholders})
            ORDER BY datetime(decided_at) DESC, id DESC
            """,
            tuple(action_ids),
        ).fetchall()
    change_rows = connection.execute(
        """
        SELECT *
        FROM assistant_change_records
        WHERE request_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        """,
        (request_id,),
    ).fetchall()
    triage_payload = [_assistant_triage_from_row(entry) for entry in triage_rows]
    return {
        **summary,
        "description": row["description"],
        "repro_steps": row["repro_steps"],
        "expected_result": row["expected_result"],
        "actual_result": row["actual_result"],
        "app_version": row["app_version"],
        "related_entity_type": row["related_entity_type"],
        "related_entity_id": row["related_entity_id"],
        "admin_note": row["admin_note"],
        "duplicate_group_key": row["duplicate_group_key"],
        "status_updated_by_user_id": row["status_updated_by_user_id"],
        "attachments": [_assistant_attachment_from_row(entry) for entry in attachments],
        "latest_triage_draft": triage_payload[0] if triage_payload else None,
        "triage_drafts": triage_payload,
        "action_requests": [_assistant_action_request_from_row(entry) for entry in action_rows],
        "approval_records": [_assistant_approval_record_from_row(entry) for entry in approval_rows],
        "change_records": [_assistant_change_record_from_row(entry) for entry in change_rows],
    }


def _assistant_attachment_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "attachment_type": row["attachment_type"],
        "storage_kind": row["storage_kind"],
        "storage_path_safe_ref": row["storage_path_safe_ref"],
        "original_filename": row["original_filename"],
        "mime_type": row["mime_type"],
        "size_bytes": int(row["size_bytes"] or 0),
        "created_at": row["created_at"],
        "view_url": f"/api/assistant/attachments/{int(row['id'])}",
    }


def _require_assistant_attachment_access_row(
    connection: sqlite3.Connection,
    *,
    attachment_id: int,
    user: AuthenticatedUser,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT
            a.id,
            a.storage_path_safe_ref,
            a.original_filename,
            a.mime_type,
            r.submitted_by_user_id
        FROM assistant_request_attachments a
        JOIN assistant_requests r ON r.id = a.request_id
        WHERE a.id = ?
        LIMIT 1
        """,
        (attachment_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant attachment not found")
    if user.role != "admin" and int(row["submitted_by_user_id"]) != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant attachment not found")
    return row


def _validated_external_image_mime(value: object) -> str:
    mime_type = str(value or "").strip().lower()
    if mime_type in ASSISTANT_EXTERNAL_IMAGE_MIME_TYPES:
        return mime_type
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Assistant external-open is only available for supported image attachments",
    )


def _assistant_external_open_origin(settings: Settings) -> str:
    origin = settings.backend_origin.strip().rstrip("/")
    if origin:
        return origin
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Assistant image external-open is unavailable until ELVERN_BACKEND_ORIGIN "
            "is configured to a real reachable backend origin"
        ),
    )


def _assistant_triage_from_row(row: sqlite3.Row) -> dict[str, object]:
    duplicate_ids = []
    if row["possible_duplicate_request_ids_json"]:
        try:
            loaded = json.loads(row["possible_duplicate_request_ids_json"])
            if isinstance(loaded, list):
                duplicate_ids = [int(entry) for entry in loaded if str(entry).isdigit()]
        except (TypeError, ValueError, json.JSONDecodeError):
            duplicate_ids = []
    return {
        "id": int(row["id"]),
        "request_id": int(row["request_id"]),
        "created_by": row["created_by"],
        "model_provider": row["model_provider"],
        "model_name": row["model_name"],
        "summary": row["summary"],
        "classification": row["classification"],
        "risk_level": row["risk_level"],
        "confidence_level": row["confidence_level"],
        "possible_duplicate_request_ids": duplicate_ids,
        "suggested_next_step": row["suggested_next_step"],
        "suggested_owner": row["suggested_owner"],
        "needs_admin_approval": bool(row["needs_admin_approval"]),
        "needs_external_access_approval": bool(row["needs_external_access_approval"]),
        "reversibility_impact_if_action_taken": row["reversibility_impact_if_action_taken"],
        "notes_for_admin": row["notes_for_admin"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _assistant_action_request_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "request_id": int(row["request_id"]),
        "triage_draft_id": row["triage_draft_id"],
        "created_by_type": row["created_by_type"],
        "created_by_user_id": row["created_by_user_id"],
        "action_type": row["action_type"],
        "target_scope": row["target_scope"],
        "reason": row["reason"],
        "proposed_plan": row["proposed_plan"],
        "risk_level": row["risk_level"],
        "requires_admin_approval": bool(row["requires_admin_approval"]),
        "requires_external_access_approval": bool(row["requires_external_access_approval"]),
        "reversibility_level": row["reversibility_level"],
        "warning_if_not_fully_reversible": row["warning_if_not_fully_reversible"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _assistant_approval_record_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "action_request_id": int(row["action_request_id"]),
        "decision": row["decision"],
        "decided_by_user_id": int(row["decided_by_user_id"]),
        "decision_note": row["decision_note"],
        "backup_required": bool(row["backup_required"]),
        "rollback_plan_required": bool(row["rollback_plan_required"]),
        "external_access_approved": bool(row["external_access_approved"]),
        "decided_at": row["decided_at"],
    }


def _assistant_change_record_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "request_id": int(row["request_id"]),
        "linked_action_request_id": row["linked_action_request_id"],
        "created_at": row["created_at"],
        "created_by_type": row["created_by_type"],
        "change_summary": row["change_summary"],
        "reversibility_level": row["reversibility_level"],
        "backup_reference": row["backup_reference"],
        "revert_recipe_draft": row["revert_recipe_draft"],
        "verification_plan_draft": row["verification_plan_draft"],
        "status": row["status"],
    }


def _assistant_upload_root(settings: Settings) -> Path:
    return settings.db_path.parent / "assistant_uploads"


def _store_request_attachment(
    settings: Settings,
    *,
    request_number: str,
    attachment: dict[str, object],
) -> tuple[str, Path]:
    upload_root = _assistant_upload_root(settings)
    request_dir = upload_root / request_number
    request_dir.mkdir(parents=True, exist_ok=True)
    original_filename = _normalize_filename(str(attachment.get("original_filename") or "attachment"))
    mime_type = str(attachment.get("mime_type") or "").strip().lower()
    extension = _safe_attachment_extension(original_filename, mime_type=mime_type)
    stored_name = f"{uuid4().hex}{extension}"
    safe_ref = f"{request_number}/{stored_name}"
    file_path = request_dir / stored_name
    file_path.write_bytes(bytes(attachment["content"]))
    return safe_ref, file_path


def _resolve_assistant_attachment_path(settings: Settings, *, safe_ref: str) -> Path:
    upload_root = _assistant_upload_root(settings).resolve()
    file_path = (upload_root / safe_ref).resolve()
    if upload_root not in file_path.parents:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant attachment not found")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant attachment not found")
    return file_path


def _cleanup_empty_parent_dirs(path: Path, *, stop_at: Path) -> None:
    current = path
    stop = stop_at.resolve()
    while current.exists() and current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _safe_attachment_extension(filename: str, *, mime_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix and len(suffix) <= 8 and SAFE_FILENAME_PATTERN.sub("", suffix) == suffix:
        return suffix
    if mime_type == "image/png":
        return ".png"
    if mime_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    if mime_type == "text/plain":
        return ".txt"
    return ".bin"


def _normalize_filename(value: str) -> str:
    name = value.strip() or "attachment"
    cleaned = SAFE_FILENAME_PATTERN.sub("_", name)
    return cleaned[:160]


def _validated_enum(value: str, allowed_values: set[str], detail: str) -> str:
    candidate = str(value or "").strip()
    if candidate not in allowed_values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    return candidate


def _normalize_required_text(value: str | None, *, field_name: str, max_length: int) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is required")
    if len(candidate) > max_length:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is too long")
    return candidate


def _normalize_optional_text(value: str | None, *, max_length: int) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if len(candidate) > max_length:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submitted text is too long")
    return candidate


def _normalized_duplicate_ids(values: list[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            continue
        if candidate <= 0 or candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
    return normalized[:20]
