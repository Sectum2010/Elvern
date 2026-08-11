from __future__ import annotations

import ast
import hashlib
import inspect
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import pyotp

from backend.app import db as db_module
from backend.app import schemas as schemas_module
from backend.app.routes import admin as admin_routes
from backend.app.routes import auth as auth_routes
from backend.app.db import ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME, get_connection, init_db, utcnow_iso
from backend.app.security import TOKEN_HASH_PREFIX, hash_password
from backend.app.services import totp_service as totp_service_module
from backend.app.services.at_rest_encryption import CIPHERTEXT_PREFIX, decrypt_at_rest, encrypt_at_rest
from backend.app.services.totp_service import (
    CHALLENGE_TOKEN_TTL_SECONDS,
    LOGIN_CHALLENGE_TOKEN_HASH_PURPOSE,
    RECOVERY_CODE_COUNT,
    RECOVERY_CODE_HASH_PREFIX,
    build_provisioning_uri,
    generate_recovery_codes,
    generate_totp_secret,
    hash_challenge_token,
    hash_recovery_code,
    legacy_hash_recovery_code,
    normalize_recovery_input,
    render_qr_svg,
    verify_totp_code,
)


def _login(client, *, username: str = "admin", password: str = "test-admin-password"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _logout(client):
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def _enable_totp(client, initialized_settings):
    login_response = _login(client)
    assert login_response.status_code == 200
    setup = client.post("/api/auth/totp/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    verify = client.post("/api/auth/totp/setup/verify", json={"code": code})
    assert verify.status_code == 200, verify.text
    with get_connection(initialized_settings) as connection:
        connection.execute(
            "UPDATE users SET totp_last_used_window = NULL WHERE username = ?",
            ("admin",),
        )
        connection.commit()
    return secret, verify.json()["recovery_codes"]


def _json_keys(value) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(_json_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(_json_keys(child))
        return keys
    return set()


def _assert_response_has_no_secret_fields(payload) -> None:
    keys = _json_keys(payload)
    assert "totp_secret" not in keys
    assert "secret" not in keys
    assert "qr_svg" not in keys
    assert "recovery_codes" not in keys
    serialized = json.dumps(payload, sort_keys=True)
    assert "otpauth" not in serialized
    assert "provisioning_uri" not in serialized


def _assert_log_calls_have_no_secret_fields(module) -> None:
    forbidden = {"secret", "totp_secret", "qr_svg", "provisioning_uri", "otpauth", "recovery_codes"}
    source = inspect.getsource(module)
    tree = ast.parse(source)
    for call in [node for node in ast.walk(tree) if isinstance(node, ast.Call)]:
        function_name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        if function_name not in {"log_security_event", "log_audit_event"}:
            continue
        for keyword in call.keywords:
            if keyword.arg and keyword.arg.lower() in forbidden:
                raise AssertionError(f"{module.__name__} logs sensitive keyword {keyword.arg}")
            for node in ast.walk(keyword.value):
                if not isinstance(node, ast.Dict):
                    continue
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        assert key.value.lower() not in forbidden


def _assert_totp_secret_writes_are_encrypted_or_clear(source: str) -> None:
    for match in re.finditer(r"SET\s+totp_secret\s*=", source, flags=re.IGNORECASE):
        snippet = source[max(0, match.start() - 160) : match.start() + 700]
        assert "encrypt_at_rest(" in snippet or "totp_secret = NULL" in snippet


def _assert_pending_secret_writes_are_encrypted(source: str) -> None:
    for marker in (
        "INSERT OR REPLACE INTO totp_pending_secrets",
        "UPDATE totp_pending_secrets SET secret",
    ):
        for match in re.finditer(marker, source):
            snippet = source[match.start() : match.start() + 700]
            assert "encrypt_at_rest(" in snippet


class TestTotpSecret:
    def test_generate_secret_is_base32_32_chars(self) -> None:
        secret = generate_totp_secret()
        assert len(secret) == 32
        assert re.fullmatch(r"[A-Z2-7]+", secret)

    def test_provisioning_uri_contains_issuer_and_account(self) -> None:
        uri = build_provisioning_uri("JBSWY3DPEHPK3PXP", "admin")
        assert "issuer=Elvern" in uri
        assert "admin" in uri

    def test_qr_svg_renders(self) -> None:
        assert "<svg" in render_qr_svg("otpauth://totp/Elvern:admin?secret=JBSWY3DPEHPK3PXP")


class TestVerifyTotpCode:
    def test_correct_code_passes(self) -> None:
        secret = generate_totp_secret()
        ok, window = verify_totp_code(secret, pyotp.TOTP(secret).now(), None)
        assert ok is True
        assert window is not None

    def test_wrong_code_fails(self) -> None:
        secret = generate_totp_secret()
        assert verify_totp_code(secret, "000000", None) == (False, None)

    def test_old_window_replay_blocked(self) -> None:
        secret = generate_totp_secret()
        current_window = int(time.time()) // 30
        code = pyotp.TOTP(secret).at(current_window * 30)
        assert verify_totp_code(secret, code, current_window) == (False, None)

    def test_format_validation_rejects_bad_values(self) -> None:
        secret = generate_totp_secret()
        assert verify_totp_code(secret, "abcdef", None) == (False, None)
        assert verify_totp_code(secret, "12345", None) == (False, None)

    def test_clock_drift_tolerance(self) -> None:
        secret = generate_totp_secret()
        now = int(time.time())
        assert verify_totp_code(secret, pyotp.TOTP(secret).at(now - 30), None)[0] is True
        assert verify_totp_code(secret, pyotp.TOTP(secret).at(now + 30), None)[0] is True


class TestRecoveryCodes:
    def test_generate_returns_10_unique_codes(self) -> None:
        codes = generate_recovery_codes()
        assert len(codes) == RECOVERY_CODE_COUNT
        assert len(set(codes)) == RECOVERY_CODE_COUNT

    def test_format_is_elvn_dashed(self) -> None:
        assert all(re.fullmatch(r"elvn(-[a-z2-9]{4}){3}", code) for code in generate_recovery_codes())

    def test_normalize_and_hash_are_deterministic(self, initialized_settings) -> None:
        assert normalize_recovery_input(" ELVN-ABCD-EFGH-JKMN ") == "elvn-abcd-efgh-jkmn"
        assert hash_recovery_code("ELVN-ABCD-EFGH-JKMN", initialized_settings) == hash_recovery_code(
            "elvn-abcd-efgh-jkmn",
            initialized_settings,
        )

    def test_new_hash_uses_hmac_marker(self, initialized_settings) -> None:
        hashed = hash_recovery_code("elvn-abcd-efgh-jkmn", initialized_settings)
        assert hashed.startswith(RECOVERY_CODE_HASH_PREFIX)
        assert hashed != legacy_hash_recovery_code("elvn-abcd-efgh-jkmn")

    def test_recovery_code_hash_path_stays_on_existing_policy(self) -> None:
        source = inspect.getsource(hash_recovery_code)
        assert "RECOVERY_CODE_HASH_NAMESPACE" in source
        assert "hash_token_hmac" not in source


class TestTotpStartupHardening:
    def test_init_db_encrypts_legacy_plaintext_user_totp_secret(self, initialized_settings, caplog) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        enabled_at = "2026-01-02T03:04:05+00:00"
        updated_at = "2026-01-02T03:05:06+00:00"
        recovery_hash = hash_recovery_code("elvn-abcd-efgh-jkmn", initialized_settings)
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                UPDATE users
                SET totp_secret = ?, totp_enabled_at = ?, totp_last_used_window = ?,
                    totp_setup_prompt_enabled = 1, updated_at = ?
                WHERE id = 1
                """,
                (secret, enabled_at, 123, updated_at),
            )
            connection.execute(
                """
                INSERT INTO user_recovery_codes (user_id, code_hash, created_at)
                VALUES (?, ?, ?)
                """,
                (1, recovery_hash, utcnow_iso()),
            )
            connection.commit()

        caplog.set_level(logging.INFO, logger=db_module.logger.name)
        init_db(initialized_settings)

        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                """
                SELECT totp_secret, totp_enabled_at, totp_last_used_window,
                       totp_setup_prompt_enabled, updated_at
                FROM users
                WHERE id = 1
                """,
            ).fetchone()
            code_count = connection.execute("SELECT COUNT(*) FROM user_recovery_codes WHERE user_id = 1").fetchone()[0]
        assert row["totp_secret"] != secret
        assert str(row["totp_secret"]).startswith(CIPHERTEXT_PREFIX)
        assert decrypt_at_rest(row["totp_secret"], initialized_settings) == (secret, True)
        assert row["totp_enabled_at"] == enabled_at
        assert row["totp_last_used_window"] == 123
        assert row["totp_setup_prompt_enabled"] == 1
        assert row["updated_at"] == updated_at
        assert code_count == 1
        assert secret not in caplog.text

    def test_init_db_leaves_encrypted_user_totp_secret_unchanged(self, initialized_settings) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        encrypted = encrypt_at_rest(secret, initialized_settings)
        with get_connection(initialized_settings) as connection:
            connection.execute(
                "UPDATE users SET totp_secret = ?, totp_enabled_at = ?, totp_last_used_window = ? WHERE id = 1",
                (encrypted, utcnow_iso(), 456),
            )
            connection.commit()

        init_db(initialized_settings)

        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                "SELECT totp_secret, totp_last_used_window FROM users WHERE id = 1",
            ).fetchone()
        assert row["totp_secret"] == encrypted
        assert row["totp_last_used_window"] == 456

    def test_init_db_deletes_expired_pending_totp_secret(self, initialized_settings) -> None:
        old_created_at = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO totp_pending_secrets (user_id, secret, created_at) VALUES (?, ?, ?)",
                (1, "JBSWY3DPEHPK3PXP", old_created_at),
            )
            connection.commit()

        init_db(initialized_settings)

        with get_connection(initialized_settings) as connection:
            row = connection.execute("SELECT secret FROM totp_pending_secrets WHERE user_id = 1").fetchone()
        assert row is None

    def test_init_db_deletes_malformed_pending_totp_secret_timestamp(self, initialized_settings) -> None:
        with get_connection(initialized_settings) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO totp_pending_secrets (user_id, secret, created_at) VALUES (?, ?, ?)",
                (1, "JBSWY3DPEHPK3PXP", "not-a-date"),
            )
            connection.commit()

        init_db(initialized_settings)

        with get_connection(initialized_settings) as connection:
            row = connection.execute("SELECT secret FROM totp_pending_secrets WHERE user_id = 1").fetchone()
        assert row is None

    def test_init_db_encrypts_unexpired_legacy_pending_totp_secret(self, initialized_settings) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        created_at = datetime.now(timezone.utc).isoformat()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO totp_pending_secrets (user_id, secret, created_at) VALUES (?, ?, ?)",
                (1, secret, created_at),
            )
            connection.commit()

        init_db(initialized_settings)

        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                "SELECT secret, created_at FROM totp_pending_secrets WHERE user_id = 1",
            ).fetchone()
        assert row is not None
        assert row["secret"] != secret
        assert str(row["secret"]).startswith(CIPHERTEXT_PREFIX)
        assert decrypt_at_rest(row["secret"], initialized_settings) == (secret, True)
        assert row["created_at"] == created_at

    def test_init_db_leaves_encrypted_unexpired_pending_secret_unchanged(self, initialized_settings) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        encrypted = encrypt_at_rest(secret, initialized_settings)
        created_at = datetime.now(timezone.utc).isoformat()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO totp_pending_secrets (user_id, secret, created_at) VALUES (?, ?, ?)",
                (1, encrypted, created_at),
            )
            connection.commit()

        init_db(initialized_settings)

        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                "SELECT secret, created_at FROM totp_pending_secrets WHERE user_id = 1",
            ).fetchone()
        assert row is not None
        assert row["secret"] == encrypted
        assert row["created_at"] == created_at


class TestLoginFlow:
    def test_admin_without_totp_returns_setup_required(self, client) -> None:
        response = _login(client)
        assert response.status_code == 200
        payload = response.json()
        assert payload["session"] == "ok"
        assert payload["totp_setup_required"] is True

    def test_admin_skipped_within_30_days_does_not_force_setup(self, client) -> None:
        assert _login(client).status_code == 200
        skip = client.post("/api/auth/totp/skip")
        assert skip.status_code == 200
        admin_response = client.get("/api/admin/users")
        assert admin_response.status_code == 200
        _logout(client)
        response = _login(client)
        assert response.json()["totp_setup_required"] is False

    def test_admin_skipped_over_30_days_forces_setup_again(self, client, initialized_settings) -> None:
        assert _login(client).status_code == 200
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                "UPDATE users SET totp_setup_skipped_at = ?, totp_setup_prompt_enabled = 1 WHERE username = ?",
                (old, "admin"),
            )
            connection.commit()
        _logout(client)
        response = _login(client)
        assert response.json()["totp_setup_required"] is True
        status = client.get("/api/auth/totp/status")
        assert status.status_code == 200
        assert status.json()["setup_available"] is True

    def test_standard_user_prompt_flag_requires_setup_then_skip_keeps_settings_setup_available(self, client, initialized_settings) -> None:
        now = utcnow_iso()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, enabled, totp_setup_prompt_enabled, created_at, updated_at
                )
                VALUES (?, ?, 'standard_user', 1, 1, ?, ?)
                """,
                ("caleb", hash_password("standard-password", initialized_settings), now, now),
            )
            connection.commit()

        response = _login(client, username="caleb", password="standard-password")
        assert response.status_code == 200
        assert response.json()["totp_setup_required"] is True

        skip = client.post("/api/auth/totp/skip")
        assert skip.status_code == 200
        with get_connection(initialized_settings) as connection:
            prompt = connection.execute(
                "SELECT totp_setup_prompt_enabled FROM users WHERE username = ?",
                ("caleb",),
            ).fetchone()[0]
        assert prompt == 1

        _logout(client)
        response = _login(client, username="caleb", password="standard-password")
        assert response.status_code == 200
        assert response.json()["totp_setup_required"] is False
        status = client.get("/api/auth/totp/status")
        assert status.status_code == 200
        assert status.json()["setup_available"] is True

    def test_user_with_totp_returns_pending_state(self, client, initialized_settings) -> None:
        _enable_totp(client, initialized_settings)
        _logout(client)
        response = _login(client)
        assert response.status_code == 200
        assert response.json()["session"] == "pending_totp"
        challenge_token = response.json()["challenge_token"]
        assert challenge_token
        assert response.json()["expires_in_seconds"] == CHALLENGE_TOKEN_TTL_SECONDS
        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                """
                SELECT challenge_token_hash
                FROM login_challenges
                WHERE user_id = 1
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        assert row is not None
        assert row["challenge_token_hash"].startswith(TOKEN_HASH_PREFIX)
        assert row["challenge_token_hash"] != challenge_token
        assert row["challenge_token_hash"] == hash_challenge_token(
            challenge_token,
            initialized_settings.session_secret,
        )
        assert LOGIN_CHALLENGE_TOKEN_HASH_PURPOSE == "auth.login_challenge"

    def test_legacy_login_challenge_is_deleted_by_hmac_migration(self, client, initialized_settings) -> None:
        secret, _codes = _enable_totp(client, initialized_settings)
        del secret
        _logout(client)
        legacy_token = "legacy-login-challenge-token"
        legacy_hash = hashlib.sha256(
            f"{initialized_settings.session_secret}:{legacy_token}".encode("utf-8")
        ).hexdigest()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                "DELETE FROM schema_migrations WHERE name = ?",
                (ACCOUNT_SHORT_TOKEN_HMAC_MIGRATION_NAME,),
            )
            connection.execute(
                """
                INSERT INTO login_challenges (
                    challenge_token_hash,
                    user_id,
                    created_at,
                    expires_at_unix,
                    ip_address,
                    user_agent
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    legacy_hash,
                    1,
                    utcnow_iso(),
                    time.time() + CHALLENGE_TOKEN_TTL_SECONDS,
                    "127.0.0.1",
                    "pytest",
                ),
            )
            connection.commit()

        init_db(initialized_settings)

        with get_connection(initialized_settings) as connection:
            assert connection.execute("SELECT COUNT(*) FROM login_challenges").fetchone()[0] == 0
        response = client.post(
            "/api/auth/login/totp",
            json={"challenge_token": legacy_token, "code": "000000"},
        )
        assert response.status_code == 401

    def test_valid_totp_code_completes_login(self, client, initialized_settings) -> None:
        secret, _codes = _enable_totp(client, initialized_settings)
        _logout(client)
        challenge = _login(client).json()["challenge_token"]
        response = client.post(
            "/api/auth/login/totp",
            json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert response.status_code == 200, response.text
        assert response.json()["session"] == "ok"

    def test_recovery_code_completes_login_and_cannot_be_reused(self, client, initialized_settings) -> None:
        _secret, codes = _enable_totp(client, initialized_settings)
        _logout(client)
        challenge = _login(client).json()["challenge_token"]
        first = client.post("/api/auth/login/totp", json={"challenge_token": challenge, "code": codes[0]})
        assert first.status_code == 200, first.text
        _logout(client)
        second_challenge = _login(client).json()["challenge_token"]
        second = client.post("/api/auth/login/totp", json={"challenge_token": second_challenge, "code": codes[0]})
        assert second.status_code == 401

    def test_legacy_sha256_recovery_code_is_accepted_once(self, client, initialized_settings) -> None:
        _secret, _codes = _enable_totp(client, initialized_settings)
        legacy_code = "elvn-abcd-efgh-jkmn"
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                INSERT INTO user_recovery_codes (user_id, code_hash, created_at)
                VALUES (?, ?, ?)
                """,
                (1, legacy_hash_recovery_code(legacy_code), utcnow_iso()),
            )
            connection.commit()
        _logout(client)

        challenge = _login(client).json()["challenge_token"]
        first = client.post("/api/auth/login/totp", json={"challenge_token": challenge, "code": legacy_code})
        assert first.status_code == 200, first.text
        _logout(client)

        second_challenge = _login(client).json()["challenge_token"]
        second = client.post("/api/auth/login/totp", json={"challenge_token": second_challenge, "code": legacy_code})
        assert second.status_code == 401

    def test_same_totp_window_cannot_complete_login_twice(self, client, initialized_settings, monkeypatch) -> None:
        secret, _codes = _enable_totp(client, initialized_settings)
        fixed_epoch = 1_800_000_000
        monkeypatch.setattr(totp_service_module.time, "time", lambda: fixed_epoch)
        code = pyotp.TOTP(secret).at(fixed_epoch)

        _logout(client)
        challenge = _login(client).json()["challenge_token"]
        first = client.post("/api/auth/login/totp", json={"challenge_token": challenge, "code": code})
        assert first.status_code == 200, first.text

        _logout(client)
        second_challenge = _login(client).json()["challenge_token"]
        second = client.post("/api/auth/login/totp", json={"challenge_token": second_challenge, "code": code})
        assert second.status_code == 401


class TestSetupFlow:
    def test_setup_initiation_returns_qr_and_secret(self, client) -> None:
        assert _login(client).status_code == 200
        response = client.post("/api/auth/totp/setup")
        assert response.status_code == 200
        payload = response.json()
        assert payload["secret"]
        assert "<svg" in payload["qr_svg"]

    def test_pending_secret_is_stored_encrypted(self, client, initialized_settings) -> None:
        assert _login(client).status_code == 200
        response = client.post("/api/auth/totp/setup")
        assert response.status_code == 200
        secret = response.json()["secret"]
        with get_connection(initialized_settings) as connection:
            stored = connection.execute(
                "SELECT secret FROM totp_pending_secrets WHERE user_id = 1",
            ).fetchone()[0]
        assert stored != secret
        assert str(stored).startswith("fernet1$")
        assert decrypt_at_rest(stored, initialized_settings) == (secret, True)

    def test_setup_verify_with_wrong_code_does_not_enable(self, client, initialized_settings) -> None:
        assert _login(client).status_code == 200
        assert client.post("/api/auth/totp/setup").status_code == 200
        response = client.post("/api/auth/totp/setup/verify", json={"code": "000000"})
        assert response.status_code == 400
        with get_connection(initialized_settings) as connection:
            assert connection.execute("SELECT totp_secret FROM users WHERE username = 'admin'").fetchone()[0] is None

    def test_setup_verify_returns_recovery_codes_once(self, client, initialized_settings) -> None:
        _secret, codes = _enable_totp(client, initialized_settings)
        assert len(codes) == 10
        with get_connection(initialized_settings) as connection:
            prompt_enabled = connection.execute(
                "SELECT totp_setup_prompt_enabled FROM users WHERE username = ?",
                ("admin",),
            ).fetchone()[0]
        assert prompt_enabled == 1

    def test_setup_stores_encrypted_totp_secret(self, client, initialized_settings) -> None:
        secret, _codes = _enable_totp(client, initialized_settings)
        with get_connection(initialized_settings) as connection:
            stored = connection.execute(
                "SELECT totp_secret FROM users WHERE username = ?",
                ("admin",),
            ).fetchone()[0]
        assert stored != secret
        assert str(stored).startswith("fernet1$")
        assert decrypt_at_rest(stored, initialized_settings) == (secret, True)

    def test_recovery_codes_are_stored_as_hmac_hashes_only(self, client, initialized_settings) -> None:
        _secret, codes = _enable_totp(client, initialized_settings)
        with get_connection(initialized_settings) as connection:
            rows = connection.execute(
                "SELECT code_hash FROM user_recovery_codes WHERE user_id = 1 ORDER BY id",
            ).fetchall()
        stored_hashes = [str(row["code_hash"]) for row in rows]
        assert len(stored_hashes) == RECOVERY_CODE_COUNT
        assert all(value.startswith(RECOVERY_CODE_HASH_PREFIX) for value in stored_hashes)
        assert set(codes).isdisjoint(stored_hashes)
        assert all(code not in "".join(stored_hashes) for code in codes)

    def test_legacy_plaintext_totp_secret_migrates_on_login(self, client, initialized_settings) -> None:
        secret = generate_totp_secret()
        now = utcnow_iso()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                UPDATE users
                SET totp_secret = ?, totp_enabled_at = ?, totp_last_used_window = NULL,
                    totp_setup_prompt_enabled = 1, updated_at = ?
                WHERE username = ?
                """,
                (secret, now, now, "admin"),
            )
            connection.commit()
        response = _login(client)
        assert response.status_code == 200
        assert response.json()["session"] == "pending_totp"
        with get_connection(initialized_settings) as connection:
            stored = connection.execute(
                "SELECT totp_secret FROM users WHERE username = ?",
                ("admin",),
            ).fetchone()[0]
        assert stored != secret
        assert str(stored).startswith("fernet1$")
        assert decrypt_at_rest(stored, initialized_settings) == (secret, True)

    def test_corrupted_encrypted_totp_secret_requires_reenrollment(self, client, initialized_settings) -> None:
        now = utcnow_iso()
        with get_connection(initialized_settings) as connection:
            connection.execute(
                """
                UPDATE users
                SET totp_secret = ?, totp_enabled_at = ?, totp_setup_prompt_enabled = 1, updated_at = ?
                WHERE username = ?
                """,
                ("fernet1$corrupted", now, now, "admin"),
            )
            connection.commit()
        response = _login(client)
        assert response.status_code == 200
        assert response.json()["session"] == "ok"
        assert response.json()["totp_setup_required"] is True
        with get_connection(initialized_settings) as connection:
            row = connection.execute(
                "SELECT totp_secret, totp_setup_prompt_enabled FROM users WHERE username = ?",
                ("admin",),
            ).fetchone()
        assert row["totp_secret"] is None
        assert row["totp_setup_prompt_enabled"] == 1


class TestDisableAndAdminReset:
    def test_user_disable_totp_clears_secret_and_recovery_codes(self, client, initialized_settings) -> None:
        _secret, codes = _enable_totp(client, initialized_settings)
        response = client.post(
            "/api/auth/totp/disable",
            json={"password": "test-admin-password", "totp_or_recovery": codes[0]},
        )
        assert response.status_code == 200, response.text
        with get_connection(initialized_settings) as connection:
            row = connection.execute("SELECT totp_secret, totp_setup_prompt_enabled FROM users WHERE id = 1").fetchone()
            code_count = connection.execute("SELECT COUNT(*) FROM user_recovery_codes WHERE user_id = 1").fetchone()[0]
        assert row["totp_secret"] is None
        assert row["totp_setup_prompt_enabled"] == 0
        assert code_count == 0

    def test_admin_disable_other_user_requires_admin_password(self, client, initialized_settings) -> None:
        secret, _codes = _enable_totp(client, initialized_settings)
        response = client.post("/api/admin/users/1/2fa/disable", json={"current_admin_password": "wrong"})
        assert response.status_code == 401
        with get_connection(initialized_settings) as connection:
            stored = connection.execute("SELECT totp_secret FROM users WHERE id = 1").fetchone()[0]
        assert decrypt_at_rest(stored, initialized_settings) == (secret, True)

    def test_admin_disable_other_user_clears_secret_codes_and_requirement(self, client, initialized_settings) -> None:
        _enable_totp(client, initialized_settings)
        response = client.post(
            "/api/admin/users/1/2fa/disable",
            json={"current_admin_password": "test-admin-password"},
        )
        assert response.status_code == 200, response.text
        with get_connection(initialized_settings) as connection:
            row = connection.execute("SELECT totp_secret, totp_setup_prompt_enabled FROM users WHERE id = 1").fetchone()
            code_count = connection.execute("SELECT COUNT(*) FROM user_recovery_codes WHERE user_id = 1").fetchone()[0]
        assert row["totp_secret"] is None
        assert row["totp_setup_prompt_enabled"] == 0
        assert code_count == 0


class TestAdminUiData:
    def test_status_endpoint_returns_correct_state(self, client, initialized_settings) -> None:
        _enable_totp(client, initialized_settings)
        response = client.get("/api/auth/totp/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["recovery_codes_remaining"] == 10

    def test_admin_user_list_exposes_setup_prompt_flag(self, client) -> None:
        assert _login(client).status_code == 200
        response = client.get("/api/admin/users")
        assert response.status_code == 200
        admin = next(entry for entry in response.json()["users"] if entry["username"] == "admin")
        assert admin["totp_setup_prompt_enabled"] is True

    def test_auth_me_response_does_not_expose_totp_secret_material(self, client, initialized_settings) -> None:
        _enable_totp(client, initialized_settings)
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        _assert_response_has_no_secret_fields(response.json())

    def test_totp_status_response_does_not_expose_secret_material(self, client, initialized_settings) -> None:
        _enable_totp(client, initialized_settings)
        response = client.get("/api/auth/totp/status")
        assert response.status_code == 200
        payload = response.json()
        _assert_response_has_no_secret_fields(payload)
        assert "recovery_codes_remaining" in payload

    def test_admin_user_list_response_does_not_expose_secret_material(self, client, initialized_settings) -> None:
        _enable_totp(client, initialized_settings)
        response = client.get("/api/admin/users")
        assert response.status_code == 200
        _assert_response_has_no_secret_fields(response.json())

    def test_admin_can_enable_then_disable_user_2fa_requirement(self, client, initialized_settings) -> None:
        now = utcnow_iso()
        with get_connection(initialized_settings) as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)
                VALUES (?, ?, 'standard_user', 1, ?, ?)
                """,
                ("caleb", hash_password("standard-password", initialized_settings), now, now),
            )
            user_id = int(cursor.lastrowid)
            connection.commit()

        assert _login(client).status_code == 200
        enabled = client.patch(f"/api/admin/users/{user_id}/2fa/setup-prompt", json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["totp_setup_prompt_enabled"] is True

        disabled = client.post(
            f"/api/admin/users/{user_id}/2fa/disable",
            json={"current_admin_password": "test-admin-password"},
        )
        assert disabled.status_code == 200, disabled.text
        refreshed = client.get("/api/admin/users")
        assert refreshed.status_code == 200
        caleb = next(entry for entry in refreshed.json()["users"] if entry["id"] == user_id)
        assert caleb["totp_setup_prompt_enabled"] is False


class TestTotpStaticGuards:
    def test_totp_log_calls_do_not_include_secret_material(self) -> None:
        _assert_log_calls_have_no_secret_fields(auth_routes)
        _assert_log_calls_have_no_secret_fields(admin_routes)

    def test_users_totp_secret_writes_use_encryption_or_clear(self) -> None:
        _assert_totp_secret_writes_are_encrypted_or_clear(inspect.getsource(auth_routes))
        _assert_totp_secret_writes_are_encrypted_or_clear(inspect.getsource(db_module._harden_totp_at_rest_values))

    def test_pending_totp_secret_writes_use_encryption_or_delete(self) -> None:
        _assert_pending_secret_writes_are_encrypted(inspect.getsource(auth_routes))
        _assert_pending_secret_writes_are_encrypted(inspect.getsource(db_module._harden_totp_at_rest_values))

    def test_recovery_code_inserts_hash_codes(self) -> None:
        source = inspect.getsource(auth_routes._insert_recovery_codes)
        assert "INSERT INTO user_recovery_codes" in source
        assert "hash_recovery_code(code, settings)" in source

    def test_totp_response_schemas_do_not_expose_secret_material_outside_intentional_flows(self) -> None:
        for model in (
            schemas_module.AuthUserEnvelope,
            schemas_module.UserResponse,
            schemas_module.AdminUserResponse,
            schemas_module.AdminUserListResponse,
            schemas_module.TotpStatusResponse,
        ):
            fields = set(model.model_fields)
            assert "totp_secret" not in fields
            assert "secret" not in fields
            assert "qr_svg" not in fields
            assert "recovery_codes" not in fields

        assert {"secret", "qr_svg"}.issubset(schemas_module.TotpSetupStartResponse.model_fields)
        assert "recovery_codes" in schemas_module.TotpSetupVerifyResponse.model_fields
        assert "recovery_codes" in schemas_module.TotpRecoveryCodesResponse.model_fields
