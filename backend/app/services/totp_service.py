from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from io import BytesIO

import pyotp
import qrcode


TOTP_ISSUER = "Elvern"
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_GROUPS = 3
RECOVERY_CODE_GROUP_LEN = 4
CHALLENGE_TOKEN_BYTES = 32
CHALLENGE_TOKEN_TTL_SECONDS = 300
SKIP_GRACE_DAYS = 30


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def build_provisioning_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name=TOTP_ISSUER,
    )


def render_qr_svg(uri: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=2,
    )
    qr.add_data(uri)
    qr.make(fit=True)
    from qrcode.image.svg import SvgPathImage

    img = qr.make_image(image_factory=SvgPathImage)
    buf = BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def verify_totp_code(secret: str, code: str, last_used_window: int | None) -> tuple[bool, int | None]:
    if not code or not code.isdigit() or len(code) != 6:
        return (False, None)
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    current_window = now // 30
    for offset in (-1, 0, 1):
        window = current_window + offset
        if last_used_window is not None and window <= last_used_window:
            continue
        expected = totp.at(window * 30)
        if hmac.compare_digest(expected, code):
            return (True, window)
    return (False, None)


def generate_recovery_codes() -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        groups = [
            "".join(secrets.choice(alphabet) for _ in range(RECOVERY_CODE_GROUP_LEN))
            for _ in range(RECOVERY_CODE_GROUPS)
        ]
        codes.append(f"elvn-{'-'.join(groups)}")
    return codes


def normalize_recovery_input(raw: str) -> str:
    return raw.strip().lower()


def hash_recovery_code(code: str) -> str:
    normalized = normalize_recovery_input(code)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_challenge_token() -> str:
    return secrets.token_urlsafe(CHALLENGE_TOKEN_BYTES)


def hash_challenge_token(token: str, secret: str) -> str:
    return hashlib.sha256(f"{secret}:{token}".encode("utf-8")).hexdigest()
