from __future__ import annotations

import inspect
import logging
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app import argon2_calibration as calibration
from backend.app import security
from backend.app.auth import authenticate_user
from backend.app.config import ConfigError, get_settings, refresh_settings
from backend.app.db import get_connection, init_db, utcnow_iso
from backend.app.security import (
    ARGON2_PREFIX,
    TOKEN_HASH_PREFIX,
    _hash_password_pbkdf2,
    generate_session_token,
    hash_password,
    hash_session_token,
    hash_token_hmac,
    looks_like_password_hash,
    perform_dummy_verify,
    verify_hmac_token_hash,
    verify_password,
)
from backend.app.services.external_redirect_security_service import FORBIDDEN_CUSTOM_APP_SCHEMES


@pytest.fixture(autouse=True)
def clear_security_caches():
    with security._cache_lock:
        security._hasher_cache.clear()
        security._dummy_hash_cache.clear()
    yield
    with security._cache_lock:
        security._hasher_cache.clear()
        security._dummy_hash_cache.clear()


@pytest.fixture()
def fast_settings(test_settings):
    return replace(
        test_settings,
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
    )


class FakePasswordHasher:
    def __init__(self, *, time_cost: int, memory_cost: int, parallelism: int) -> None:
        self.time_cost = time_cost
        self.memory_cost = memory_cost
        self.parallelism = parallelism

    def hash(self, password: str) -> str:
        return f"$argon2id$fake$t={self.time_cost},m={self.memory_cost},p={self.parallelism}${password}"

    def verify(self, password_hash: str, password: str) -> bool:
        if not password_hash.endswith(f"${password}"):
            raise ValueError("password mismatch")
        return True


def _set_minimal_env(monkeypatch, tmp_path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir(exist_ok=True)
    monkeypatch.setenv("ELVERN_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("ELVERN_DB_PATH", str(tmp_path / "backend" / "data" / "test.db"))
    monkeypatch.setenv("ELVERN_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ELVERN_ADMIN_BOOTSTRAP_PASSWORD", "test-admin-password")
    monkeypatch.setenv("ELVERN_SESSION_SECRET", "test-session-secret-value-with-32-chars")
    monkeypatch.setenv("ELVERN_COOKIE_SECURE", "false")
    monkeypatch.setenv("ELVERN_SCAN_ON_STARTUP", "false")
    monkeypatch.setenv("ELVERN_TRANSCODE_ENABLED", "false")
    monkeypatch.setenv("ELVERN_BROWSER_PLAYBACK_ROUTE2_ENABLED", "false")
    monkeypatch.setenv("ELVERN_LIBRARY_ROOT_LINUX", str(media_root))
    monkeypatch.setenv("ELVERN_HELPER_RELEASES_DIR", str(tmp_path / "helper_releases"))
    monkeypatch.setenv("ELVERN_TRANSCODE_DIR", str(tmp_path / "transcodes"))
    get_settings.cache_clear()


class TestPasswordHashing:
    def test_argon2id_roundtrip_correct_password(self, fast_settings) -> None:
        password_hash = hash_password("correct horse battery staple", fast_settings)

        ok, new_hash = verify_password("correct horse battery staple", password_hash, fast_settings)

        assert password_hash.startswith(ARGON2_PREFIX)
        assert ok is True
        assert new_hash is None

    def test_argon2id_roundtrip_wrong_password(self, fast_settings) -> None:
        password_hash = hash_password("correct horse battery staple", fast_settings)

        ok, new_hash = verify_password("wrong password", password_hash, fast_settings)

        assert ok is False
        assert new_hash is None

    def test_pbkdf2_legacy_roundtrip_correct_password(self, fast_settings) -> None:
        legacy_hash = _hash_password_pbkdf2("correct horse battery staple")

        ok, _new_hash = verify_password("correct horse battery staple", legacy_hash, fast_settings)

        assert ok is True

    def test_pbkdf2_legacy_roundtrip_wrong_password(self, fast_settings) -> None:
        legacy_hash = _hash_password_pbkdf2("correct horse battery staple")

        ok, new_hash = verify_password("wrong password", legacy_hash, fast_settings)

        assert ok is False
        assert new_hash is None

    def test_pbkdf2_success_returns_new_argon2_hash(self, fast_settings) -> None:
        legacy_hash = _hash_password_pbkdf2("correct horse battery staple")

        ok, new_hash = verify_password("correct horse battery staple", legacy_hash, fast_settings)

        assert ok is True
        assert new_hash is not None
        assert new_hash.startswith(ARGON2_PREFIX)

    def test_argon2_success_returns_no_new_hash(self, fast_settings) -> None:
        password_hash = hash_password("correct horse battery staple", fast_settings)

        ok, new_hash = verify_password("correct horse battery staple", password_hash, fast_settings)

        assert ok is True
        assert new_hash is None

    @pytest.mark.parametrize("bad_hash", ["garbage", "$argon2id$broken", "pbkdf2_sha256$xx", ""])
    def test_malformed_hash_string_safely_fails(self, fast_settings, bad_hash: str) -> None:
        ok, new_hash = verify_password("password", bad_hash, fast_settings)

        assert ok is False
        assert new_hash is None

    def test_none_password_safely_fails(self, fast_settings) -> None:
        ok, new_hash = verify_password(None, hash_password("password", fast_settings), fast_settings)

        assert ok is False
        assert new_hash is None

    def test_none_hash_safely_fails(self, fast_settings) -> None:
        ok, new_hash = verify_password("password", None, fast_settings)

        assert ok is False
        assert new_hash is None

    def test_distinct_salts_produce_distinct_hashes(self, fast_settings) -> None:
        first = hash_password("same password", fast_settings)
        second = hash_password("same password", fast_settings)

        assert first != second

    def test_looks_like_password_hash_recognizes_argon2id(self) -> None:
        assert looks_like_password_hash("$argon2id$v=19$m=8192,t=1,p=1$salt$hash")

    def test_looks_like_password_hash_recognizes_pbkdf2(self) -> None:
        assert looks_like_password_hash(_hash_password_pbkdf2("password"))

    def test_looks_like_password_hash_rejects_garbage(self) -> None:
        assert not looks_like_password_hash("not-a-password-hash")


class TestDummyVerify:
    def test_dummy_verify_completes_without_exception(self, fast_settings) -> None:
        perform_dummy_verify(fast_settings)

    def test_dummy_verify_timing_within_factor_of_real_verify(self, fast_settings) -> None:
        password_hash = hash_password("correct horse battery staple", fast_settings)
        perform_dummy_verify(fast_settings)

        real_samples = []
        dummy_samples = []
        for _index in range(5):
            start = time.perf_counter()
            verify_password("correct horse battery staple", password_hash, fast_settings)
            real_samples.append((time.perf_counter() - start) * 1000)

            start = time.perf_counter()
            perform_dummy_verify(fast_settings)
            dummy_samples.append((time.perf_counter() - start) * 1000)

        real_ms = sorted(real_samples)[2]
        dummy_ms = sorted(dummy_samples)[2]
        assert real_ms * 0.5 <= dummy_ms <= real_ms * 2.0


class TestCalibration:
    def test_calibration_returns_valid_params(self, monkeypatch) -> None:
        monkeypatch.setattr(calibration, "PasswordHasher", FakePasswordHasher)

        params, measured_ms = calibration.calibrate_argon2()

        assert params.time_cost >= 2
        assert params.memory_cost == 65536
        assert params.parallelism >= 1
        assert measured_ms >= 0

    def test_calibration_respects_min_time_cost(self, monkeypatch) -> None:
        monkeypatch.setattr(calibration, "PasswordHasher", FakePasswordHasher)

        params, _measured_ms = calibration.calibrate_argon2(target_ms=0)

        assert params.time_cost >= calibration.MIN_TIME_COST

    def test_calibration_failure_returns_fallback(self, monkeypatch) -> None:
        class FailingPasswordHasher:
            def __init__(self, **_kwargs) -> None:
                raise RuntimeError("boom")

        monkeypatch.setattr(calibration, "PasswordHasher", FailingPasswordHasher)

        assert calibration.calibrate_argon2() == (calibration.FALLBACK_PARAMS, 0.0)

    def test_host_fingerprint_is_deterministic(self) -> None:
        assert calibration.compute_host_fingerprint() == calibration.compute_host_fingerprint()

    def test_host_fingerprint_changes_with_cpu_count(self, monkeypatch) -> None:
        first = calibration.compute_host_fingerprint()
        monkeypatch.setattr(calibration.os, "cpu_count", lambda: 999)

        assert calibration.compute_host_fingerprint() != first

    def test_calibration_file_round_trip(self, fast_settings, tmp_path) -> None:
        settings = replace(fast_settings, db_path=tmp_path / "backend" / "data" / "test.db")
        record = calibration.CalibrationRecord(
            calibrated_at=datetime.now(timezone.utc).isoformat(),
            calibration_version=calibration.CALIBRATION_VERSION,
            host_fingerprint="abc123",
            target_verify_ms=calibration.TARGET_VERIFY_MS,
            measured_verify_ms=42.5,
            params=calibration.Argon2Params(time_cost=3, memory_cost=65536, parallelism=2),
        )

        calibration.write_calibration(record, settings)

        assert calibration.read_calibration(settings) == record

    def test_calibration_file_corrupted_returns_none(self, fast_settings, tmp_path) -> None:
        settings = replace(fast_settings, db_path=tmp_path / "backend" / "data" / "test.db")
        path = calibration.calibration_file_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")

        assert calibration.read_calibration(settings) is None

    def test_calibration_version_mismatch_returns_none(self, fast_settings, tmp_path) -> None:
        settings = replace(fast_settings, db_path=tmp_path / "backend" / "data" / "test.db")
        path = calibration.calibration_file_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"calibration_version": 999, "params": {"time_cost": 3, "memory_cost": 65536, "parallelism": 2}}',
            encoding="utf-8",
        )

        assert calibration.read_calibration(settings) is None


class TestSettingsArgon2Validation:
    def test_all_three_set_is_valid(self, monkeypatch, tmp_path) -> None:
        _set_minimal_env(monkeypatch, tmp_path)
        monkeypatch.setenv("ELVERN_ARGON2_TIME_COST", "2")
        monkeypatch.setenv("ELVERN_ARGON2_MEMORY_COST", "65536")
        monkeypatch.setenv("ELVERN_ARGON2_PARALLELISM", "2")

        settings = refresh_settings()

        assert settings.argon2_params_manually_set is True

    def test_none_set_is_valid(self, monkeypatch, tmp_path) -> None:
        _set_minimal_env(monkeypatch, tmp_path)

        settings = refresh_settings()

        assert settings.argon2_params_manually_set is False

    def test_partial_set_raises(self, monkeypatch, tmp_path) -> None:
        _set_minimal_env(monkeypatch, tmp_path)
        monkeypatch.setenv("ELVERN_ARGON2_TIME_COST", "2")

        with pytest.raises(ConfigError, match="Argon2id parameters must be either all set"):
            refresh_settings()


class TestResolveArgon2Params:
    def test_manual_path_skips_calibration(self, fast_settings, monkeypatch) -> None:
        def fail_read(_settings):
            raise AssertionError("read_calibration should not be called")

        monkeypatch.setattr(calibration, "read_calibration", fail_read)

        params = calibration.resolve_argon2_params(fast_settings, logging.getLogger("test"))

        assert params == calibration.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)

    def test_calibration_path_when_no_env_set(self, fast_settings, tmp_path, monkeypatch) -> None:
        settings = replace(
            fast_settings,
            db_path=tmp_path / "backend" / "data" / "test.db",
            argon2_time_cost=None,
            argon2_memory_cost=None,
            argon2_parallelism=None,
        )
        monkeypatch.setattr(calibration, "compute_host_fingerprint", lambda: "host-a")
        monkeypatch.setattr(
            calibration,
            "calibrate_argon2",
            lambda: (calibration.Argon2Params(time_cost=4, memory_cost=65536, parallelism=2), 44.0),
        )

        params = calibration.resolve_argon2_params(settings, logging.getLogger("test"))

        assert params == calibration.Argon2Params(time_cost=4, memory_cost=65536, parallelism=2)
        assert calibration.read_calibration(settings).host_fingerprint == "host-a"

    def test_existing_calibration_used_when_fingerprint_matches(self, fast_settings, tmp_path, monkeypatch) -> None:
        settings = replace(
            fast_settings,
            db_path=tmp_path / "backend" / "data" / "test.db",
            argon2_time_cost=None,
            argon2_memory_cost=None,
            argon2_parallelism=None,
        )
        expected = calibration.Argon2Params(time_cost=5, memory_cost=65536, parallelism=2)
        record = calibration.CalibrationRecord(
            calibrated_at=datetime.now(timezone.utc).isoformat(),
            calibration_version=calibration.CALIBRATION_VERSION,
            host_fingerprint="host-a",
            target_verify_ms=calibration.TARGET_VERIFY_MS,
            measured_verify_ms=55.0,
            params=expected,
        )
        calibration.write_calibration(record, settings)
        monkeypatch.setattr(calibration, "compute_host_fingerprint", lambda: "host-a")
        monkeypatch.setattr(
            calibration,
            "calibrate_argon2",
            lambda: pytest.fail("calibrate_argon2 should not be called"),
        )

        assert calibration.resolve_argon2_params(settings, logging.getLogger("test")) == expected

    def test_recalibrates_when_fingerprint_differs(self, fast_settings, tmp_path, monkeypatch) -> None:
        settings = replace(
            fast_settings,
            db_path=tmp_path / "backend" / "data" / "test.db",
            argon2_time_cost=None,
            argon2_memory_cost=None,
            argon2_parallelism=None,
        )
        old = calibration.CalibrationRecord(
            calibrated_at=datetime.now(timezone.utc).isoformat(),
            calibration_version=calibration.CALIBRATION_VERSION,
            host_fingerprint="old-host",
            target_verify_ms=calibration.TARGET_VERIFY_MS,
            measured_verify_ms=55.0,
            params=calibration.Argon2Params(time_cost=5, memory_cost=65536, parallelism=2),
        )
        calibration.write_calibration(old, settings)
        monkeypatch.setattr(calibration, "compute_host_fingerprint", lambda: "new-host")
        monkeypatch.setattr(
            calibration,
            "calibrate_argon2",
            lambda: (calibration.Argon2Params(time_cost=3, memory_cost=65536, parallelism=1), 33.0),
        )

        params = calibration.resolve_argon2_params(settings, logging.getLogger("test"))

        assert params == calibration.Argon2Params(time_cost=3, memory_cost=65536, parallelism=1)
        assert calibration.read_calibration(settings).host_fingerprint == "new-host"

    def test_age_warning_logged_when_calibration_old(self, fast_settings, tmp_path, monkeypatch, caplog) -> None:
        settings = replace(
            fast_settings,
            db_path=tmp_path / "backend" / "data" / "test.db",
            argon2_time_cost=None,
            argon2_memory_cost=None,
            argon2_parallelism=None,
        )
        record = calibration.CalibrationRecord(
            calibrated_at=(datetime.now(timezone.utc) - timedelta(days=181)).isoformat(),
            calibration_version=calibration.CALIBRATION_VERSION,
            host_fingerprint="host-a",
            target_verify_ms=calibration.TARGET_VERIFY_MS,
            measured_verify_ms=55.0,
            params=calibration.Argon2Params(time_cost=5, memory_cost=65536, parallelism=2),
        )
        calibration.write_calibration(record, settings)
        monkeypatch.setattr(calibration, "compute_host_fingerprint", lambda: "host-a")

        with caplog.at_level(logging.WARNING):
            params = calibration.resolve_argon2_params(settings, logging.getLogger("test"))

        assert params == record.params
        assert "Argon2id calibration is" in caplog.text


class TestAuthIntegration:
    def test_pbkdf2_user_login_upgrades_hash_in_db(self, fast_settings) -> None:
        init_db(fast_settings)
        user_id = self._insert_legacy_user(fast_settings)

        user, failure_reason = authenticate_user(fast_settings, "legacy", "correct-password")

        assert failure_reason is None
        assert user is not None
        with get_connection(fast_settings) as connection:
            row = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        assert row["password_hash"].startswith(ARGON2_PREFIX)

    def test_subsequent_login_does_not_rewrite_hash(self, fast_settings) -> None:
        init_db(fast_settings)
        user_id = self._insert_legacy_user(fast_settings)

        authenticate_user(fast_settings, "legacy", "correct-password")
        with get_connection(fast_settings) as connection:
            first = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()["password_hash"]

        authenticate_user(fast_settings, "legacy", "correct-password")
        with get_connection(fast_settings) as connection:
            second = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()["password_hash"]

        assert second == first

    def test_audit_log_records_hash_upgrade(self, fast_settings) -> None:
        init_db(fast_settings)
        self._insert_legacy_user(fast_settings)

        authenticate_user(fast_settings, "legacy", "correct-password")

        with get_connection(fast_settings) as connection:
            row = connection.execute(
                """
                SELECT action, details_json
                FROM audit_logs
                WHERE action = 'password_hash_upgraded'
                LIMIT 1
                """
            ).fetchone()
        assert row is not None
        assert row["details_json"] == '{"from": "pbkdf2_sha256", "to": "argon2id"}'

    @staticmethod
    def _insert_legacy_user(settings) -> int:
        now = utcnow_iso()
        with get_connection(settings) as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (username, password_hash, role, enabled, created_at, updated_at)
                VALUES (?, ?, 'standard_user', 1, ?, ?)
                """,
                ("legacy", _hash_password_pbkdf2("correct-password"), now, now),
            )
            connection.commit()
            return int(cursor.lastrowid)


def test_session_token_generation_and_hashing_are_secret_bound() -> None:
    token_a = generate_session_token()
    token_b = generate_session_token()

    assert token_a != token_b
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token_a)

    digest = hash_session_token(token_a, "secret-one")
    assert len(digest) == 64
    assert digest == hash_session_token(token_a, "secret-one")
    assert digest != hash_session_token(token_a, "secret-two")


def test_hmac_token_hash_is_prefixed_purpose_bound_and_constant_time() -> None:
    token = "sensitive-browser-token-value"
    digest = hash_token_hmac("secret-one", purpose="auth.session", token=token)

    assert digest.startswith(TOKEN_HASH_PREFIX)
    assert token not in digest
    assert verify_hmac_token_hash(
        "secret-one",
        purpose="auth.session",
        token=token,
        stored_hash=digest,
    )
    assert not verify_hmac_token_hash(
        "secret-one",
        purpose="download.session",
        token=token,
        stored_hash=digest,
    )
    assert not verify_hmac_token_hash(
        "secret-one",
        purpose="auth.session",
        token=token + "x",
        stored_hash=digest,
    )
    assert "compare_digest" in inspect.getsource(security.verify_hmac_token_hash)


@pytest.mark.parametrize(
    "scheme",
    ["http", "https", "javascript", "data", "file", "vbscript", "about", "blob"],
)
def test_vlc_helper_protocol_forbidden_schemes_fail_settings_load(monkeypatch, tmp_path, scheme: str) -> None:
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ELVERN_VLC_HELPER_PROTOCOL", scheme)

    with pytest.raises(ConfigError, match="ELVERN_VLC_HELPER_PROTOCOL"):
        refresh_settings()


@pytest.mark.parametrize("scheme", ["", "x", "elvern vlc", "elvern_vlc", "1elvern", "elvern://play"])
def test_vlc_helper_protocol_empty_or_invalid_scheme_fails_settings_load(monkeypatch, tmp_path, scheme: str) -> None:
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ELVERN_VLC_HELPER_PROTOCOL", scheme)

    with pytest.raises(ConfigError, match="ELVERN_VLC_HELPER_PROTOCOL"):
        refresh_settings()


@pytest.mark.parametrize("scheme", ["elvern-vlc-dev", "elvern+vlc"])
def test_vlc_helper_protocol_safe_custom_scheme_is_allowed(monkeypatch, tmp_path, scheme: str) -> None:
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ELVERN_VLC_HELPER_PROTOCOL", scheme)

    settings = refresh_settings()

    assert settings.vlc_helper_protocol == scheme


def test_ci_workflow_uses_minimal_top_level_permissions() -> None:
    workflow_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    workflow_source = workflow_path.read_text(encoding="utf-8")

    assert re.search(r"(?m)^permissions:\n  contents: read\n", workflow_source)
    assert not re.search(r"(?m)^  (actions|checks|contents|deployments|id-token|issues|packages|pull-requests): write", workflow_source)


def test_external_redirect_static_guards_use_safe_validation_helpers() -> None:
    project_root = Path(__file__).resolve().parents[2]
    native_source = (project_root / "backend" / "app" / "routes" / "native_playback.py").read_text(encoding="utf-8")
    desktop_source = (project_root / "backend" / "app" / "routes" / "desktop_playback.py").read_text(encoding="utf-8")

    assert "validate_ios_external_launch_redirect(" in native_source
    assert "RedirectResponse(url=safe_launch_url" in native_source
    assert "RedirectResponse(url=launch_url" not in native_source
    assert "validate_desktop_helper_redirect(" in desktop_source
    assert "RedirectResponse(url=safe_protocol_url" in desktop_source
    assert 'RedirectResponse(url=str(handoff["protocol_url"])' not in desktop_source


def test_forbidden_helper_protocol_scheme_list_covers_browser_and_file_schemes() -> None:
    assert {
        "http",
        "https",
        "javascript",
        "data",
        "file",
        "vbscript",
        "about",
        "blob",
    }.issubset(FORBIDDEN_CUSTOM_APP_SCHEMES)
