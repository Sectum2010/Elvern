from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network
import json
import re
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request

from ..auth import _client_host_from_request, _is_trusted_proxy_peer
from ..config import Settings
from ..db import utcnow_iso
from .app_settings_service import get_global_app_setting, set_global_app_setting


EXPOSURE_MODE_PENDING_DRAFT_KEY = "exposure_mode_pending_draft_json"
DIRECT_PUBLIC_IP_WARNING = (
    "Direct public IP exposure is not recommended. A purchased domain with HTTPS is safer and easier to maintain."
)
CURRENT_ORIGIN_REVALIDATION_MESSAGE = (
    "Open this admin page through the proposed public address and validate again before activation."
)
FUTURE_STANDARD_USER_MESSAGE = "The server is currently under construction, please try again later"
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


def _active_settings_payload(settings: Settings, request: Request) -> dict[str, Any]:
    url_prefix = getattr(getattr(request.app, "state", None), "url_prefix", None) or settings.url_prefix
    return {
        "private_network_only": settings.private_network_only,
        "public_app_origin": settings.public_app_origin,
        "backend_origin": settings.backend_origin,
        "trusted_proxy_cidrs": list(settings.trusted_proxy_cidrs),
        "cookie_secure": settings.cookie_secure,
        "current_request_origin": resolve_current_request_origin(settings, request),
        "url_prefix_present": bool(url_prefix),
        "global_security_headers_expected": True,
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
    return {
        "env_suggestions": _env_suggestions(mode, public_origin, private_origin),
        "reverse_proxy_notes": _reverse_proxy_notes(provider),
        "manual_steps": _manual_steps(mode),
        "activation_notes": _activation_notes(),
    }


def _env_suggestions(mode: str | None, public_origin: str, private_origin: str) -> list[dict[str, str]]:
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
        suggestions.append({"name": "ELVERN_COOKIE_SECURE", "value": "true", "effect": "Recommended for public HTTPS"})
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
            "Open this admin page through the proposed public address and run validation again.",
            "Confirm HTTPS, secure cookies, proxy forwarding, and trusted proxy CIDRs before any future activation.",
            "Do not write env files from this planner; apply changes manually in a later activation phase.",
        ]
    return [
        "Confirm bind host, firewall, and Tailscale/LAN access for private users.",
        "Keep public DNS and public reverse proxy routes disabled for private mode.",
        "Do not write env files from this planner; apply changes manually in a later activation phase.",
    ]


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
