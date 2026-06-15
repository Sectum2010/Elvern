from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network
import json
import re
import sqlite3
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from ..auth import _client_host_from_request, _is_trusted_proxy_peer
from ..config import Settings
from ..db import utcnow_iso
from .app_settings_service import get_global_app_setting, set_global_app_setting
from .exposure_maintenance_service import (
    EXPOSURE_MAINTENANCE_LOCK_MESSAGE,
    enable_maintenance_mode,
    get_exposure_maintenance_lock,
)


EXPOSURE_MODE_PENDING_DRAFT_KEY = "exposure_mode_pending_draft_json"
EXPOSURE_MODE_PREPARED_SWITCH_KEY = "exposure_mode_prepared_switch_json"
PREPARED_SWITCH_STATUS_PREPARED = "prepared_for_manual_apply"
PREPARED_SWITCH_STATUS_VERIFIED = "verified_after_restart"
PREPARED_SWITCH_STATUSES = {PREPARED_SWITCH_STATUS_PREPARED, PREPARED_SWITCH_STATUS_VERIFIED}
EXPOSURE_VERIFY_PREPARED_SWITCH_ACKNOWLEDGEMENT = (
    "I understand this only verifies the prepared manual switch. It does not release Maintenance Mode, "
    "write env files, restart Elvern, rotate the URL prefix, revoke sessions, disable users, or activate exposure mode."
)
DIRECT_PUBLIC_IP_WARNING = (
    "Direct public IP exposure is not recommended. A purchased domain with HTTPS is safer and easier to maintain."
)
CURRENT_ORIGIN_REVALIDATION_MESSAGE = (
    "Open this admin page through the proposed public address and validate again before activation."
)
FUTURE_STANDARD_USER_MESSAGE = EXPOSURE_MAINTENANCE_LOCK_MESSAGE
PROVIDER_CHOICES = ("caddy", "nginx", "cloudflare_tunnel", "manual_other")
PUBLIC_ENTRY_KINDS = ("custom_domain", "direct_ip")

_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RFC1918_V4_NETWORKS = tuple(
    ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_PRIVATE_V6_NETWORKS = tuple(ip_network(value) for value in ("fc00::/7", "fe80::/10"))


@dataclass(frozen=True)
class NormalizedOrigin:
    origin: str
    scheme: str
    hostname: str
    port: int | None
    host_class: str


def normalize_origin(value: str | None) -> dict[str, Any]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return {"ok": False, "origin": "", "errors": ["Origin is required."]}
    try:
        parsed = urlsplit(raw_value)
        port = parsed.port
    except ValueError as exc:
        return {"ok": False, "origin": raw_value, "errors": [f"Origin is invalid: {exc}."]}

    errors: list[str] = []
    if parsed.scheme not in {"http", "https"}:
        errors.append("Origin must use http or https.")
    if not parsed.netloc or not parsed.hostname:
        errors.append("Origin must include a host.")
    if parsed.username or parsed.password:
        errors.append("Origin must not include credentials.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        errors.append("Origin must contain only scheme, host, and optional port.")

    hostname = (parsed.hostname or "").strip().lower()
    host_class = classify_origin_host(hostname) if hostname else "invalid"
    if host_class == "invalid":
        errors.append("Origin host is invalid.")
    if port is not None and (port < 1 or port > 65535):
        errors.append("Origin port must be between 1 and 65535.")

    origin = raw_value
    if not errors:
        host = hostname
        try:
            parsed_ip = ip_address(hostname)
        except ValueError:
            parsed_ip = None
        if parsed_ip is not None and ":" in hostname:
            host = f"[{hostname}]"
        default_port = 443 if parsed.scheme == "https" else 80
        port_suffix = f":{port}" if port is not None and port != default_port else ""
        origin = f"{parsed.scheme}://{host}{port_suffix}"

    return {
        "ok": not errors,
        "origin": origin,
        "scheme": parsed.scheme,
        "hostname": hostname,
        "port": port,
        "host_class": host_class,
        "errors": errors,
    }


def classify_origin_host(hostname: str | None) -> str:
    host = str(hostname or "").strip().lower().strip("[]")
    if not host:
        return "invalid"
    if host in {"localhost", "localhost.localdomain"}:
        return "localhost"
    try:
        parsed_ip = ip_address(host)
    except ValueError:
        return "custom_domain" if _looks_like_dns_name(host) else "invalid"

    if parsed_ip.is_loopback:
        return "loopback"
    if parsed_ip.is_unspecified or parsed_ip.is_multicast or parsed_ip.is_reserved:
        return "invalid"
    if parsed_ip.is_link_local or _is_private_lan_ip(parsed_ip):
        return "private_ip"
    return "public_ip"


def resolve_current_request_origin(settings: Settings, request: Request) -> str:
    peer_host = _client_host_from_request(request)
    scheme = request.url.scheme or "http"
    host = request.headers.get("host") or request.url.netloc
    if _is_trusted_proxy_peer(settings, peer_host):
        forwarded_host = _first_forwarded_header_value(request.headers.get("x-forwarded-host", ""))
        forwarded_proto = _first_forwarded_header_value(request.headers.get("x-forwarded-proto", ""))
        if forwarded_host:
            host = forwarded_host
        if forwarded_proto in {"http", "https"}:
            scheme = forwarded_proto
    return _normalize_origin_from_parts(scheme=scheme, host=host)


def build_current_exposure_status(settings: Settings, request: Request) -> dict[str, Any]:
    pending_draft = get_pending_exposure_draft(settings)
    return {
        "active": _active_settings_payload(settings, request),
        "desired": {},
        "validation": {
            "status": "ready",
            "errors": [],
            "warnings": [],
            "checks": [],
        },
        "plan": _build_plan(settings, desired={}, normalized_public=None, normalized_private=None),
        "pending_draft": pending_draft,
        "prepared_switch": get_prepared_exposure_switch(settings),
        "provider_choices": list(PROVIDER_CHOICES),
        "public_entry_kinds": list(PUBLIC_ENTRY_KINDS),
        "takes_effect": False,
    }


def validate_exposure_plan(settings: Settings, request: Request, payload: Any) -> dict[str, Any]:
    desired = _desired_payload(payload)
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, str]] = []
    normalized_public: dict[str, Any] | None = None
    normalized_private: dict[str, Any] | None = None

    _add_check(
        checks,
        "server_side_origin_probe",
        "pass",
        "Validation uses parsing and current request comparison only; no server-side origin probe is performed.",
    )

    if desired["desired_mode"] not in {"private", "public"}:
        errors.append("Desired exposure mode must be private or public.")

    provider = desired["reverse_proxy_provider"]
    if provider and provider not in PROVIDER_CHOICES:
        errors.append("Reverse proxy provider is not supported.")
    elif provider:
        _add_check(checks, "provider_supported", "pass", f"Provider option {provider} is supported.")

    current_request_origin = resolve_current_request_origin(settings, request)
    if desired["desired_mode"] == "public":
        public_entry_kind = desired["public_entry_kind"]
        if public_entry_kind not in PUBLIC_ENTRY_KINDS:
            errors.append("Public mode requires a custom_domain or direct_ip entry kind.")
        normalized_public = normalize_origin(desired["public_origin"])
        _extend_origin_errors(errors, normalized_public)
        if normalized_public["ok"]:
            if public_entry_kind == "custom_domain":
                _validate_public_custom_domain(
                    settings=settings,
                    normalized_origin=normalized_public,
                    current_request_origin=current_request_origin,
                    errors=errors,
                    warnings=warnings,
                    checks=checks,
                )
            elif public_entry_kind == "direct_ip":
                _validate_public_direct_ip(
                    normalized_origin=normalized_public,
                    current_request_origin=current_request_origin,
                    provider=provider,
                    errors=errors,
                    warnings=warnings,
                    checks=checks,
                )
    else:
        private_origin = desired["private_origin"]
        if private_origin:
            normalized_private = normalize_origin(private_origin)
            _extend_origin_errors(errors, normalized_private)
            if normalized_private["ok"]:
                _add_check(
                    checks,
                    "private_origin_parse",
                    "pass",
                    "Private origin is syntactically valid.",
                )
        _add_check(
            checks,
            "private_mode_scope",
            "info",
            "Private mode access depends on bind host, firewall, Tailscale/LAN, and any reverse proxy.",
        )

    _add_all_mode_checks(settings, request, desired, warnings, checks)

    status = "blocked" if errors else "warnings" if warnings else "ready"
    return {
        "active": _active_settings_payload(settings, request),
        "desired": _validated_desired_payload(
            desired,
            normalized_public=normalized_public,
            normalized_private=normalized_private,
        ),
        "validation": {
            "status": status,
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
        },
        "plan": _build_plan(
            settings,
            desired=desired,
            normalized_public=normalized_public,
            normalized_private=normalized_private,
        ),
        "pending_draft": get_pending_exposure_draft(settings),
        "prepared_switch": get_prepared_exposure_switch(settings),
        "provider_choices": list(PROVIDER_CHOICES),
        "public_entry_kinds": list(PUBLIC_ENTRY_KINDS),
        "takes_effect": False,
    }


def save_pending_exposure_draft(
    settings: Settings,
    actor: Any,
    payload: Any,
    *,
    validation_snapshot: dict[str, Any],
) -> dict[str, Any]:
    now = utcnow_iso()
    draft = {
        "desired": validation_snapshot.get("desired", {}),
        "validation": validation_snapshot.get("validation", {}),
        "plan": validation_snapshot.get("plan", {}),
        "created_by_user_id": getattr(actor, "id", None),
        "created_by_username": getattr(actor, "username", None),
        "created_at": now,
        "updated_at": now,
        "takes_effect": False,
        "direct_ip_not_recommended_acknowledgement": bool(
            _payload_value(payload, "direct_ip_not_recommended_acknowledgement", False)
        ),
    }
    existing = get_pending_exposure_draft(settings)
    if existing and existing.get("created_at"):
        draft["created_at"] = existing["created_at"]
    set_global_app_setting(
        settings,
        key=EXPOSURE_MODE_PENDING_DRAFT_KEY,
        value=json.dumps(draft, sort_keys=True),
    )
    response = dict(validation_snapshot)
    response["pending_draft"] = draft
    response["takes_effect"] = False
    return response


def get_pending_exposure_draft(settings: Settings) -> dict[str, Any] | None:
    try:
        raw_value = get_global_app_setting(settings, key=EXPOSURE_MODE_PENDING_DRAFT_KEY)
    except sqlite3.OperationalError:
        return None
    if not raw_value:
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    parsed["takes_effect"] = False
    return parsed


def clear_pending_exposure_draft(settings: Settings, actor: Any) -> dict[str, Any]:
    del actor
    set_global_app_setting(settings, key=EXPOSURE_MODE_PENDING_DRAFT_KEY, value=None)
    return {
        "pending_draft": None,
        "takes_effect": False,
    }


def get_prepared_exposure_switch(settings: Settings) -> dict[str, Any] | None:
    try:
        raw_value = get_global_app_setting(settings, key=EXPOSURE_MODE_PREPARED_SWITCH_KEY)
    except sqlite3.OperationalError:
        return None
    if not raw_value:
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("status") not in PREPARED_SWITCH_STATUSES:
        return None
    parsed["takes_effect"] = False
    parsed["activation_not_implemented"] = True
    return parsed


def get_prepared_exposure_switch_verification_status(settings: Settings) -> dict[str, Any]:
    prepared_switch = get_prepared_exposure_switch(settings)
    if prepared_switch is None:
        verification = {
            "status": "not_ready",
            "errors": [],
            "warnings": ["No prepared manual switch is available to verify."],
            "checks": [],
            "takes_effect": False,
        }
    else:
        verification = prepared_switch.get("verification")
        if not isinstance(verification, dict):
            verification = {
                "status": "not_verified",
                "errors": [],
                "warnings": [],
                "checks": [],
                "takes_effect": False,
            }
        else:
            verification = dict(verification)
            verification["takes_effect"] = False
    return {
        "prepared_switch": prepared_switch,
        "verification": verification,
        "takes_effect": False,
    }


def verify_prepared_exposure_switch(
    settings: Settings,
    request: Request,
    actor: Any,
    *,
    acknowledgement: bool,
) -> dict[str, Any]:
    if not acknowledgement:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=EXPOSURE_VERIFY_PREPARED_SWITCH_ACKNOWLEDGEMENT,
        )

    prepared_switch = get_prepared_exposure_switch(settings)
    if prepared_switch is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Save and manually apply a prepared switch before verification.",
                "verification": {
                    "status": "blocked",
                    "errors": ["No prepared manual switch is available to verify."],
                    "warnings": [],
                    "checks": [],
                    "takes_effect": False,
                },
                "takes_effect": False,
            },
        )

    verification = _build_prepared_exposure_switch_verification(settings, request, prepared_switch)
    if verification["errors"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Prepared switch verification blocked.",
                "prepared_switch": prepared_switch,
                "verification": verification,
                "takes_effect": False,
            },
        )

    verified_switch = dict(prepared_switch)
    verified_switch.update(
        {
            "status": PREPARED_SWITCH_STATUS_VERIFIED,
            "verified_at": utcnow_iso(),
            "verified_by_user_id": getattr(actor, "id", None),
            "verified_by_username": getattr(actor, "username", None),
            "verification": verification,
            "takes_effect": False,
            "activation_not_implemented": True,
            "maintenance_mode_release": "manual_only",
        }
    )
    set_global_app_setting(
        settings,
        key=EXPOSURE_MODE_PREPARED_SWITCH_KEY,
        value=json.dumps(verified_switch, sort_keys=True),
    )
    return {
        "prepared_switch": verified_switch,
        "verification": verification,
        "takes_effect": False,
    }


def prepare_exposure_manual_switch(
    settings: Settings,
    request: Request,
    actor: Any,
    *,
    acknowledgement: bool,
    invalidate_auth_session: Callable[..., object] | None = None,
) -> dict[str, Any]:
    if not acknowledgement:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Acknowledge that this only prepares a manual switch plan and does not "
                "write env files or activate exposure mode."
            ),
        )

    pending_draft = get_pending_exposure_draft(settings)
    if pending_draft is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Save a pending exposure draft before preparing a manual switch.",
        )

    desired = pending_draft.get("desired")
    if not isinstance(desired, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prepared switch cannot be created while validation has blocking errors.",
        )

    validation_snapshot = validate_exposure_plan(settings, request, desired)
    validation = validation_snapshot.get("validation", {})
    blocking_errors = validation.get("errors", [])
    if validation.get("status") == "blocked" or blocking_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prepared switch cannot be created while validation has blocking errors.",
        )

    validated_desired = validation_snapshot.get("desired", {})
    if validated_desired.get("desired_mode") == "public":
        if validated_desired.get("public_entry_kind") == "direct_ip":
            if not bool(pending_draft.get("direct_ip_not_recommended_acknowledgement")):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=DIRECT_PUBLIC_IP_WARNING,
                )

    maintenance_mode = enable_maintenance_mode(
        settings,
        actor,
        invalidate_auth_session=invalidate_auth_session,
    )
    prepared_at = utcnow_iso()
    prepared_switch = {
        "status": "prepared_for_manual_apply",
        "prepared_at": prepared_at,
        "prepared_by_user_id": getattr(actor, "id", None),
        "prepared_by_username": getattr(actor, "username", None),
        "takes_effect": False,
        "desired": validated_desired,
        "validation": validation,
        "plan": validation_snapshot.get("plan", {}),
        "env_block": build_copyable_env_block(validation_snapshot.get("plan", {})),
        "manual_steps": _prepared_manual_steps(validation_snapshot.get("plan", {})),
        "restart_required": True,
        "maintenance_lock_required": False,
        "maintenance_mode_enabled": True,
        "maintenance_mode_auto_enabled": True,
        "maintenance_mode_revoked_non_admin_sessions": maintenance_mode.get("revoked_non_admin_sessions", 0),
        "maintenance_mode_affected_non_admin_users": maintenance_mode.get("affected_non_admin_users", 0),
        "verification_required": True,
        "current_origin_match_required_in_phase": "phase_4_verification",
        "url_prefix_rotation": "manual_only",
        "env_writing": "manual_only",
        "activation_not_implemented": True,
    }
    set_global_app_setting(
        settings,
        key=EXPOSURE_MODE_PREPARED_SWITCH_KEY,
        value=json.dumps(prepared_switch, sort_keys=True),
    )
    return {
        "prepared_switch": prepared_switch,
        "takes_effect": False,
    }


def clear_prepared_exposure_switch(settings: Settings, actor: Any) -> dict[str, Any]:
    del actor
    set_global_app_setting(settings, key=EXPOSURE_MODE_PREPARED_SWITCH_KEY, value=None)
    return {
        "prepared_switch": None,
        "takes_effect": False,
    }


def _build_prepared_exposure_switch_verification(
    settings: Settings,
    request: Request,
    prepared_switch: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, str]] = []
    current_request_origin = resolve_current_request_origin(settings, request)
    desired = prepared_switch.get("desired")
    if not isinstance(desired, dict):
        desired = {}

    _add_check(
        checks,
        "server_side_origin_probe",
        "pass",
        "Verification uses the current admin request and runtime settings only; no server-side origin probe is performed.",
    )
    _add_prepared_switch_status_check(prepared_switch, errors, checks)
    _add_maintenance_mode_verification_check(settings, errors, checks)
    _add_url_prefix_verification_check(settings, request, warnings, checks)

    desired_mode = desired.get("desired_mode")
    if desired_mode == "public":
        _add_public_prepared_switch_verification_checks(
            settings,
            desired,
            current_request_origin=current_request_origin,
            errors=errors,
            warnings=warnings,
            checks=checks,
        )
    elif desired_mode == "private":
        _add_private_prepared_switch_verification_checks(
            settings,
            desired,
            current_request_origin=current_request_origin,
            errors=errors,
            warnings=warnings,
            checks=checks,
        )
    else:
        _add_blocking_verification_check(
            errors,
            checks,
            "desired_mode",
            "Prepared switch desired mode must be private or public.",
        )

    verification_status = "blocked" if errors else "warnings" if warnings else "passed"
    return {
        "status": verification_status,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "current_request_origin": current_request_origin,
        "takes_effect": False,
    }


def _add_prepared_switch_status_check(
    prepared_switch: dict[str, Any],
    errors: list[str],
    checks: list[dict[str, str]],
) -> None:
    prepared_status = str(prepared_switch.get("status") or "")
    if prepared_status in PREPARED_SWITCH_STATUSES:
        _add_check(
            checks,
            "prepared_switch_status",
            "pass",
            f"Prepared switch status is {prepared_status}.",
        )
        return
    _add_blocking_verification_check(
        errors,
        checks,
        "prepared_switch_status",
        "Prepared switch status must be prepared_for_manual_apply or verified_after_restart.",
    )


def _add_maintenance_mode_verification_check(
    settings: Settings,
    errors: list[str],
    checks: list[dict[str, str]],
) -> None:
    maintenance_mode = get_exposure_maintenance_lock(settings)
    if maintenance_mode.get("enabled"):
        _add_check(checks, "maintenance_mode", "pass", "Maintenance Mode is enabled.")
        return
    _add_blocking_verification_check(
        errors,
        checks,
        "maintenance_mode",
        "Maintenance Mode must remain enabled during prepared switch verification.",
    )


def _add_url_prefix_verification_check(
    settings: Settings,
    request: Request,
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    url_prefix = getattr(getattr(request.app, "state", None), "url_prefix", None) or settings.url_prefix
    if url_prefix:
        _add_check(checks, "url_prefix_present", "pass", "URL prefix is currently present.")
        return
    _add_warning_verification_check(
        warnings,
        checks,
        "url_prefix_present",
        "URL prefix is not currently present. Public exposure should keep a random URL prefix configured.",
    )


def _add_public_prepared_switch_verification_checks(
    settings: Settings,
    desired: dict[str, Any],
    *,
    current_request_origin: str,
    errors: list[str],
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    public_entry_kind = desired.get("public_entry_kind")
    if public_entry_kind not in PUBLIC_ENTRY_KINDS:
        _add_blocking_verification_check(
            errors,
            checks,
            "public_entry_kind",
            "Public prepared switches require a custom_domain or direct_ip entry kind.",
        )
        return

    normalized_public = normalize_origin(str(desired.get("public_origin") or ""))
    if not normalized_public["ok"]:
        for error in normalized_public.get("errors", []):
            _add_blocking_verification_check(errors, checks, "public_origin", str(error))
        return

    expected_origin = str(normalized_public["origin"])
    expected_scheme = str(normalized_public.get("scheme") or "")
    _add_current_origin_verification_check(
        current_request_origin=current_request_origin,
        expected_origin=expected_origin,
        errors=errors,
        checks=checks,
    )

    if settings.private_network_only:
        _add_blocking_verification_check(
            errors,
            checks,
            "private_network_only",
            "Runtime ELVERN_PRIVATE_NETWORK_ONLY must be false for public prepared switch verification.",
        )
    else:
        _add_check(checks, "private_network_only", "pass", "Runtime private-network-only mode is disabled.")

    _add_public_app_origin_verification_check(
        settings,
        expected_origin=expected_origin,
        errors=errors,
        checks=checks,
    )
    _add_public_cookie_secure_verification_check(
        settings,
        public_entry_kind=str(public_entry_kind),
        expected_scheme=expected_scheme,
        errors=errors,
        checks=checks,
    )
    _add_backend_origin_verification_check(
        settings,
        expected_origin=expected_origin,
        warnings=warnings,
        checks=checks,
    )
    _add_trusted_proxy_verification_check(
        settings,
        block_on_broad=True,
        errors=errors,
        warnings=warnings,
        checks=checks,
    )
    if public_entry_kind == "direct_ip":
        _add_warning_verification_check(warnings, checks, "direct_ip_not_recommended", DIRECT_PUBLIC_IP_WARNING)


def _add_private_prepared_switch_verification_checks(
    settings: Settings,
    desired: dict[str, Any],
    *,
    current_request_origin: str,
    errors: list[str],
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    private_origin = str(desired.get("private_origin") or "").strip()
    normalized_private: dict[str, Any] | None = None
    if private_origin:
        normalized_private = normalize_origin(private_origin)
        if not normalized_private["ok"]:
            for error in normalized_private.get("errors", []):
                _add_blocking_verification_check(errors, checks, "private_origin", str(error))
            return
        expected_origin = str(normalized_private["origin"])
        _add_current_origin_verification_check(
            current_request_origin=current_request_origin,
            expected_origin=expected_origin,
            errors=errors,
            checks=checks,
        )
    else:
        _add_check(
            checks,
            "current_origin_match",
            "info",
            "No private origin was set in the prepared switch; current origin matching is skipped.",
        )

    if not settings.private_network_only:
        _add_blocking_verification_check(
            errors,
            checks,
            "private_network_only",
            "Runtime ELVERN_PRIVATE_NETWORK_ONLY must be true for private prepared switch verification.",
        )
    else:
        _add_check(checks, "private_network_only", "pass", "Runtime private-network-only mode is enabled.")

    _add_private_public_app_origin_verification_check(
        settings,
        normalized_private=normalized_private,
        warnings=warnings,
        checks=checks,
    )
    _add_private_cookie_secure_verification_check(
        settings,
        normalized_private=normalized_private,
        warnings=warnings,
        checks=checks,
    )
    _add_trusted_proxy_verification_check(
        settings,
        block_on_broad=False,
        errors=errors,
        warnings=warnings,
        checks=checks,
    )


def _add_current_origin_verification_check(
    *,
    current_request_origin: str,
    expected_origin: str,
    errors: list[str],
    checks: list[dict[str, str]],
) -> None:
    if current_request_origin == expected_origin:
        _add_check(checks, "current_origin_match", "pass", "Current admin request origin matches the expected origin.")
        return
    _add_blocking_verification_check(
        errors,
        checks,
        "current_origin_match",
        f"Current request origin is {current_request_origin}; expected origin is {expected_origin}.",
    )


def _add_public_app_origin_verification_check(
    settings: Settings,
    *,
    expected_origin: str,
    errors: list[str],
    checks: list[dict[str, str]],
) -> None:
    runtime_origin = _normalized_runtime_origin(settings.public_app_origin)
    if runtime_origin == expected_origin:
        _add_check(checks, "public_app_origin", "pass", "Runtime public_app_origin matches the expected origin.")
        return
    _add_blocking_verification_check(
        errors,
        checks,
        "public_app_origin",
        f"Runtime public_app_origin must be {expected_origin}.",
    )


def _add_public_cookie_secure_verification_check(
    settings: Settings,
    *,
    public_entry_kind: str,
    expected_scheme: str,
    errors: list[str],
    checks: list[dict[str, str]],
) -> None:
    secure_required = public_entry_kind != "direct_ip" or expected_scheme == "https"
    if secure_required and settings.cookie_secure:
        _add_check(checks, "cookie_secure", "pass", "Runtime cookie_secure is enabled for public HTTPS.")
        return
    if not secure_required and not settings.cookie_secure:
        _add_check(checks, "cookie_secure", "pass", "Runtime cookie_secure is disabled for plain HTTP direct IP.")
        return
    if secure_required:
        message = "Runtime ELVERN_COOKIE_SECURE must be true for public HTTPS exposure."
    else:
        message = "Runtime ELVERN_COOKIE_SECURE must be false for plain HTTP direct IP exposure."
    _add_blocking_verification_check(errors, checks, "cookie_secure", message)


def _add_backend_origin_verification_check(
    settings: Settings,
    *,
    expected_origin: str,
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    runtime_origin = _normalized_runtime_origin(settings.backend_origin)
    if not runtime_origin:
        _add_check(checks, "backend_origin", "info", "Runtime backend_origin is empty.")
        return
    if runtime_origin == expected_origin:
        _add_check(checks, "backend_origin", "pass", "Runtime backend_origin matches the expected origin.")
        return
    _add_warning_verification_check(
        warnings,
        checks,
        "backend_origin",
        f"Runtime backend_origin is {runtime_origin}; expected {expected_origin} if the backend is proxied through the app origin.",
    )


def _add_private_public_app_origin_verification_check(
    settings: Settings,
    *,
    normalized_private: dict[str, Any] | None,
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    runtime_origin = _normalized_runtime_origin(settings.public_app_origin)
    if not runtime_origin:
        _add_check(checks, "public_app_origin", "info", "Runtime public_app_origin is empty for private mode.")
        return
    if normalized_private is None:
        _add_check(checks, "public_app_origin", "info", "Runtime public_app_origin is set; no private origin was set to compare.")
        return
    expected_origin = str(normalized_private["origin"])
    if runtime_origin == expected_origin:
        _add_check(checks, "public_app_origin", "pass", "Runtime public_app_origin matches the private origin.")
        return
    _add_warning_verification_check(
        warnings,
        checks,
        "public_app_origin",
        f"Runtime public_app_origin is {runtime_origin}; private prepared origin is {expected_origin}.",
    )


def _add_private_cookie_secure_verification_check(
    settings: Settings,
    *,
    normalized_private: dict[str, Any] | None,
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    if normalized_private is None:
        _add_check(checks, "cookie_secure", "info", "No private origin was set; cookie_secure is not blocked in private mode.")
        return
    expected_scheme = str(normalized_private.get("scheme") or "")
    if expected_scheme == "https" and not settings.cookie_secure:
        _add_warning_verification_check(
            warnings,
            checks,
            "cookie_secure",
            "Runtime cookie_secure is false while the private origin uses HTTPS.",
        )
        return
    if expected_scheme == "http" and settings.cookie_secure:
        _add_warning_verification_check(
            warnings,
            checks,
            "cookie_secure",
            "Runtime cookie_secure is true while the private origin uses HTTP.",
        )
        return
    _add_check(checks, "cookie_secure", "pass", "Runtime cookie_secure is compatible with the private origin.")


def _add_trusted_proxy_verification_check(
    settings: Settings,
    *,
    block_on_broad: bool,
    errors: list[str],
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    broad_cidrs = _broad_trusted_proxy_cidrs(settings)
    if not broad_cidrs:
        _add_check(checks, "trusted_proxy_cidrs", "pass", "Trusted proxy CIDRs are not broad catch-all networks.")
        return
    detail = "Trusted proxy CIDRs include a broad catch-all network."
    if block_on_broad:
        _add_blocking_verification_check(errors, checks, "trusted_proxy_cidrs", detail)
    else:
        _add_warning_verification_check(warnings, checks, "trusted_proxy_cidrs", detail)


def _normalized_runtime_origin(value: str | None) -> str:
    normalized = normalize_origin(value)
    return str(normalized["origin"]) if normalized.get("ok") else ""


def _broad_trusted_proxy_cidrs(settings: Settings) -> list[str]:
    return [str(cidr) for cidr in settings.trusted_proxy_cidrs if str(cidr) in {"0.0.0.0/0", "::/0"}]


def _add_blocking_verification_check(
    errors: list[str],
    checks: list[dict[str, str]],
    name: str,
    detail: str,
) -> None:
    errors.append(detail)
    _add_check(checks, name, "block", detail)


def _add_warning_verification_check(
    warnings: list[str],
    checks: list[dict[str, str]],
    name: str,
    detail: str,
) -> None:
    warnings.append(detail)
    _add_check(checks, name, "warn", detail)


def build_copyable_env_block(plan: dict[str, Any] | None) -> str:
    safe_names = {
        "ELVERN_PRIVATE_NETWORK_ONLY",
        "ELVERN_PUBLIC_APP_ORIGIN",
        "ELVERN_BACKEND_ORIGIN",
        "ELVERN_COOKIE_SECURE",
    }
    lines = [
        "# Elvern prepared manual switch suggestion",
        "# Review and apply manually outside Elvern; this block is not written by the app.",
    ]
    suggestions = plan.get("env_suggestions", []) if isinstance(plan, dict) else []
    if not isinstance(suggestions, list):
        suggestions = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        name = str(suggestion.get("name") or "").strip()
        if name not in safe_names:
            continue
        value = str(suggestion.get("value") if suggestion.get("value") is not None else "").strip()
        lines.append(f"{name}={value}")
    return "\n".join(lines)


def _looks_like_dns_name(host: str) -> bool:
    labels = host.rstrip(".").split(".")
    if len(labels) < 2:
        return False
    return all(_DOMAIN_LABEL_PATTERN.fullmatch(label) for label in labels)


def _is_private_lan_ip(parsed_ip: Any) -> bool:
    networks = _RFC1918_V4_NETWORKS if parsed_ip.version == 4 else _PRIVATE_V6_NETWORKS
    return any(parsed_ip in network for network in networks)


def _first_forwarded_header_value(value: str) -> str:
    return value.split(",", 1)[0].strip().lower()


def _normalize_origin_from_parts(*, scheme: str, host: str | None) -> str:
    normalized_scheme = scheme if scheme in {"http", "https"} else "http"
    normalized_host = str(host or "").strip()
    if not normalized_host:
        return "unknown"
    return normalize_origin(f"{normalized_scheme}://{normalized_host}")["origin"]


def _desired_payload(payload: Any) -> dict[str, Any]:
    desired_mode = _payload_value(payload, "desired_mode", "private")
    public_entry_kind = _payload_value(payload, "public_entry_kind", None)
    reverse_proxy_provider = _payload_value(payload, "reverse_proxy_provider", None)
    return {
        "desired_mode": desired_mode,
        "public_entry_kind": public_entry_kind,
        "public_origin": str(_payload_value(payload, "public_origin", "") or "").strip(),
        "private_origin": str(_payload_value(payload, "private_origin", "") or "").strip(),
        "reverse_proxy_provider": reverse_proxy_provider,
    }


def _payload_value(payload: Any, key: str, default: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _validated_desired_payload(
    desired: dict[str, Any],
    *,
    normalized_public: dict[str, Any] | None,
    normalized_private: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(desired)
    if normalized_public and normalized_public.get("ok"):
        result["public_origin"] = normalized_public["origin"]
    if normalized_private and normalized_private.get("ok"):
        result["private_origin"] = normalized_private["origin"]
    return result


def _extend_origin_errors(errors: list[str], normalized_origin: dict[str, Any]) -> None:
    for error in normalized_origin.get("errors", []):
        errors.append(str(error))


def _validate_public_custom_domain(
    *,
    settings: Settings,
    normalized_origin: dict[str, Any],
    current_request_origin: str,
    errors: list[str],
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    host_class = str(normalized_origin["host_class"])
    if host_class in {"localhost", "loopback", "private_ip"}:
        errors.append("Public custom domains must not point to localhost, loopback, or private IP hosts.")
    elif host_class == "public_ip":
        errors.append("Public custom domain mode requires a DNS name, not a raw IP address.")
    elif host_class == "custom_domain":
        _add_check(checks, "custom_domain_host", "pass", "Public custom domain host is a DNS name.")

    if normalized_origin["scheme"] != "https":
        errors.append("Public custom domains must use HTTPS to be considered ready.")
    else:
        _add_check(checks, "custom_domain_https", "pass", "Public custom domain uses HTTPS.")
        if not settings.cookie_secure:
            warnings.append("ELVERN_COOKIE_SECURE is false. Public HTTPS setups should enable secure cookies before activation.")

    _add_current_origin_match_check(normalized_origin, current_request_origin, warnings, checks)


def _validate_public_direct_ip(
    *,
    normalized_origin: dict[str, Any],
    current_request_origin: str,
    provider: str | None,
    errors: list[str],
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    host_class = str(normalized_origin["host_class"])
    if host_class != "public_ip":
        errors.append("Direct public IP mode requires a non-private, non-loopback IP address.")
    else:
        _add_check(checks, "direct_ip_host", "pass", "Direct IP host parses as a public IP address.")

    warnings.append(DIRECT_PUBLIC_IP_WARNING)
    if normalized_origin["scheme"] == "https":
        warnings.append("HTTPS direct IP exposure is allowed, but certificate setup is usually harder than using a domain.")
    else:
        warnings.append("HTTP direct IP exposure is allowed only as a planning draft and is not recommended.")
    if provider and provider != "manual_other":
        warnings.append("Direct IP mode is normally a manual/other reverse proxy setup.")
    _add_current_origin_match_check(normalized_origin, current_request_origin, warnings, checks)


def _add_current_origin_match_check(
    normalized_origin: dict[str, Any],
    current_request_origin: str,
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    if current_request_origin == normalized_origin["origin"]:
        _add_check(checks, "current_origin_match", "pass", "Current admin request origin matches the proposed public origin.")
        return
    warnings.append(CURRENT_ORIGIN_REVALIDATION_MESSAGE)
    _add_check(
        checks,
        "current_origin_match",
        "warn",
        f"Current request origin is {current_request_origin}; proposed origin is {normalized_origin['origin']}.",
    )


def _add_all_mode_checks(
    settings: Settings,
    request: Request,
    desired: dict[str, Any],
    warnings: list[str],
    checks: list[dict[str, str]],
) -> None:
    url_prefix = getattr(getattr(request.app, "state", None), "url_prefix", None) or settings.url_prefix
    if url_prefix:
        _add_check(checks, "url_prefix_present", "pass", "URL prefix is currently present.")
    else:
        warnings.append("URL prefix is not currently present. Public exposure should keep a random URL prefix configured.")
        _add_check(checks, "url_prefix_present", "warn", "URL prefix is not currently present.")
    warnings.append("URL prefix rotation remains manual. Consider rotating it after completing public-mode setup if desired.")

    if desired["desired_mode"] == "public":
        broad_cidrs = [cidr for cidr in settings.trusted_proxy_cidrs if cidr in {"0.0.0.0/0", "::/0"}]
        if broad_cidrs:
            warnings.append("Trusted proxy CIDRs are broad. Restrict ELVERN_TRUSTED_PROXY_CIDRS to known proxy addresses.")
            _add_check(checks, "trusted_proxy_cidrs", "warn", "Trusted proxy CIDRs include a broad network.")
        else:
            _add_check(checks, "trusted_proxy_cidrs", "pass", "Trusted proxy CIDRs are not broad catch-all networks.")


def _current_origin_match_passed(validation: dict[str, Any]) -> bool:
    checks = validation.get("checks", [])
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(check, dict)
        and check.get("name") == "current_origin_match"
        and check.get("status") == "pass"
        for check in checks
    )


def _active_settings_payload(settings: Settings, request: Request) -> dict[str, Any]:
    url_prefix = getattr(getattr(request.app, "state", None), "url_prefix", None) or settings.url_prefix
    maintenance_mode = get_exposure_maintenance_lock(settings)
    return {
        "private_network_only": settings.private_network_only,
        "public_app_origin": settings.public_app_origin,
        "backend_origin": settings.backend_origin,
        "trusted_proxy_cidrs": list(settings.trusted_proxy_cidrs),
        "cookie_secure": settings.cookie_secure,
        "current_request_origin": resolve_current_request_origin(settings, request),
        "url_prefix_present": bool(url_prefix),
        "global_security_headers_expected": True,
        "maintenance_lock": maintenance_mode,
        "maintenance_mode": maintenance_mode,
    }


def _build_plan(
    settings: Settings,
    *,
    desired: dict[str, Any],
    normalized_public: dict[str, Any] | None,
    normalized_private: dict[str, Any] | None,
) -> dict[str, Any]:
    del settings
    mode = desired.get("desired_mode")
    provider = desired.get("reverse_proxy_provider")
    public_origin = normalized_public["origin"] if normalized_public and normalized_public.get("ok") else desired.get("public_origin", "")
    private_origin = normalized_private["origin"] if normalized_private and normalized_private.get("ok") else desired.get("private_origin", "")
    public_scheme = str(normalized_public.get("scheme") or "") if normalized_public and normalized_public.get("ok") else ""
    return {
        "env_suggestions": _env_suggestions(
            mode,
            public_origin,
            private_origin,
            public_entry_kind=desired.get("public_entry_kind"),
            public_scheme=public_scheme,
        ),
        "reverse_proxy_notes": _reverse_proxy_notes(provider),
        "manual_steps": _manual_steps(mode),
        "activation_notes": _activation_notes(),
    }


def _env_suggestions(
    mode: str | None,
    public_origin: str,
    private_origin: str,
    *,
    public_entry_kind: str | None = None,
    public_scheme: str | None = None,
) -> list[dict[str, str]]:
    if mode == "public":
        suggestions = [
            {"name": "ELVERN_PRIVATE_NETWORK_ONLY", "value": "false", "effect": "Future public-mode activation only"},
        ]
        if public_origin:
            suggestions.append(
                {"name": "ELVERN_PUBLIC_APP_ORIGIN", "value": public_origin, "effect": "Future public app origin"}
            )
            suggestions.append(
                {"name": "ELVERN_BACKEND_ORIGIN", "value": public_origin, "effect": "Use only if the backend remains proxied through the app origin"}
            )
        cookie_secure = "false" if public_entry_kind == "direct_ip" and public_scheme == "http" else "true"
        cookie_effect = (
            "Required for plain HTTP direct IP planning; Secure cookies require HTTPS"
            if cookie_secure == "false"
            else "Recommended for public HTTPS"
        )
        suggestions.append({"name": "ELVERN_COOKIE_SECURE", "value": cookie_secure, "effect": cookie_effect})
        return suggestions
    suggestions = [{"name": "ELVERN_PRIVATE_NETWORK_ONLY", "value": "true", "effect": "Keep private/LAN exposure"}]
    if private_origin:
        suggestions.append(
            {"name": "ELVERN_PUBLIC_APP_ORIGIN", "value": private_origin, "effect": "Optional private-origin documentation"}
        )
    return suggestions


def _reverse_proxy_notes(provider: str | None) -> list[str]:
    notes_by_provider = {
        "caddy": [
            "Create DNS A/AAAA records pointing to the server.",
            "Configure Caddy reverse_proxy to the frontend server.",
            "Caddy can manage automatic HTTPS when ports 80 and 443 are reachable.",
        ],
        "nginx": [
            "Configure a TLS certificate for the public hostname.",
            "Proxy browser traffic to the frontend server.",
            "Forward only the headers required by Elvern and keep trusted proxy CIDRs narrow.",
        ],
        "cloudflare_tunnel": [
            "Configure a Cloudflare Tunnel to the frontend server and attach the purchased public hostname.",
            "This option does not use Tailscale.",
            "Keep Elvern origin validation manual; the backend does not probe the hostname.",
        ],
        "manual_other": [
            "Ensure TLS and reverse proxy forwarding terminate at frontend/server.mjs.",
            "Do not expose the backend directly unless a later phase explicitly designs that path.",
        ],
    }
    if provider in notes_by_provider:
        return notes_by_provider[provider]
    return ["Choose a reverse proxy provider before activation planning."]


def _manual_steps(mode: str | None) -> list[str]:
    if mode == "public":
        return [
            "After manual env/proxy apply and restart, Phase 4 verification checks the target origin.",
            "Confirm HTTPS, secure cookies, proxy forwarding, and trusted proxy CIDRs before any future activation.",
            "Do not write env files from this planner; apply changes manually in a later activation phase.",
        ]
    return [
        "Confirm bind host, firewall, and Tailscale/LAN access for private users.",
        "Keep public DNS and public reverse proxy routes disabled for private mode.",
        "Do not write env files from this planner; apply changes manually in a later activation phase.",
    ]


def _prepared_manual_steps(plan: dict[str, Any] | None) -> list[str]:
    steps: list[str] = []
    if isinstance(plan, dict):
        manual_steps = plan.get("manual_steps")
        if isinstance(manual_steps, list):
            steps.extend(str(step) for step in manual_steps)
        reverse_proxy_notes = plan.get("reverse_proxy_notes")
        if isinstance(reverse_proxy_notes, list):
            steps.extend(str(note) for note in reverse_proxy_notes)
    steps.extend(
        [
            "Manually apply the reviewed env and reverse-proxy changes outside Elvern.",
            "Restart Elvern manually after applying env or reverse-proxy changes.",
            "After manually applying env/reverse-proxy changes and restarting Elvern, return through the target address and verify in a later phase.",
        ]
    )
    return steps


def _activation_notes() -> list[str]:
    return [
        "Phase 1 drafts do not change runtime behavior.",
        "Future activation must require admin re-authentication.",
        "Future activation must not rotate the URL prefix automatically.",
        "Future activation must not change token TTLs automatically.",
        "Future activation should temporarily block non-admin accounts until the server is ready.",
        f'Standard users should see: "{FUTURE_STANDARD_USER_MESSAGE}".',
        "Admin should see a success or next-step message before any later reauth/logout flow.",
    ]


def _add_check(checks: list[dict[str, str]], name: str, status: str, detail: str) -> None:
    checks.append({"name": name, "status": status, "detail": detail})
