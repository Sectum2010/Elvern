from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher


@dataclass(frozen=True, slots=True)
class Argon2Params:
    time_cost: int
    memory_cost: int
    parallelism: int


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    calibrated_at: str
    calibration_version: int
    host_fingerprint: str
    target_verify_ms: int
    measured_verify_ms: float
    params: Argon2Params


CALIBRATION_VERSION = 1
TARGET_VERIFY_MS = 100
FALLBACK_PARAMS = Argon2Params(time_cost=3, memory_cost=65536, parallelism=2)
MEMORY_COST_FLOOR_KIB = 65536
MIN_TIME_COST = 2
MAX_TIME_COST = 10
CALIBRATION_FILE_NAME = "argon2_calibration.json"
CALIBRATION_AGE_WARNING_DAYS = 180
TEST_PASSWORD = "elvern_calibration_anchor_v1"


def _system_memory_gib() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().total // (1024**3))
    except Exception:
        pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    try:
        return int((int(page_size) * int(page_count)) // (1024**3))
    except (TypeError, ValueError):
        return None


def compute_host_fingerprint() -> str:
    values: list[str] = []
    for value in (
        platform.machine(),
        platform.processor(),
        str(os.cpu_count() or 1),
    ):
        normalized = str(value or "").strip()
        if normalized:
            values.append(normalized)
    memory_gib = _system_memory_gib()
    if memory_gib is not None:
        values.append(str(memory_gib))
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def derive_parallelism() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(4, cpu_count // 2))


def calibrate_argon2(
    target_ms: int = TARGET_VERIFY_MS,
    memory_cost: int = MEMORY_COST_FLOOR_KIB,
    parallelism: int | None = None,
) -> tuple[Argon2Params, float]:
    try:
        effective_parallelism = parallelism or derive_parallelism()
        best_time_cost = MIN_TIME_COST
        best_median_ms = 0.0
        for time_cost in (2, 3, 4, 5, 6, 8, 10):
            hasher = PasswordHasher(
                time_cost=time_cost,
                memory_cost=memory_cost,
                parallelism=effective_parallelism,
            )
            verify_times_ms: list[float] = []
            for _index in range(5):
                password_hash = hasher.hash(TEST_PASSWORD)
                start = time.perf_counter()
                hasher.verify(password_hash, TEST_PASSWORD)
                verify_times_ms.append((time.perf_counter() - start) * 1000)
            median_ms = sorted(verify_times_ms)[2]
            if not verify_times_ms:
                raise RuntimeError("Argon2id calibration produced no timing samples")
            if median_ms <= target_ms:
                best_time_cost = time_cost
                best_median_ms = median_ms
                continue
            if best_median_ms == 0.0:
                best_median_ms = median_ms
            break
        best_time_cost = max(MIN_TIME_COST, min(MAX_TIME_COST, best_time_cost))
        return (
            Argon2Params(
                time_cost=best_time_cost,
                memory_cost=memory_cost,
                parallelism=effective_parallelism,
            ),
            best_median_ms,
        )
    except Exception:
        return FALLBACK_PARAMS, 0.0


def calibration_file_path(settings) -> Path:
    return Path(settings.db_path).parent / CALIBRATION_FILE_NAME


def write_calibration(record: CalibrationRecord, settings) -> None:
    path = calibration_file_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def read_calibration(settings) -> CalibrationRecord | None:
    path = calibration_file_path(settings)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("calibration_version", 0)) != CALIBRATION_VERSION:
            return None
        params_payload = payload.get("params") or {}
        params = Argon2Params(
            time_cost=int(params_payload["time_cost"]),
            memory_cost=int(params_payload["memory_cost"]),
            parallelism=int(params_payload["parallelism"]),
        )
        return CalibrationRecord(
            calibrated_at=str(payload["calibrated_at"]),
            calibration_version=int(payload["calibration_version"]),
            host_fingerprint=str(payload["host_fingerprint"]),
            target_verify_ms=int(payload["target_verify_ms"]),
            measured_verify_ms=float(payload["measured_verify_ms"]),
            params=params,
        )
    except Exception:
        return None


def _calibration_age_days(record: CalibrationRecord) -> int | None:
    try:
        calibrated_at = datetime.fromisoformat(record.calibrated_at).astimezone(timezone.utc)
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - calibrated_at).days)


def resolve_argon2_params(settings, logger) -> Argon2Params:
    if settings.argon2_params_manually_set:
        logger.info("Argon2id using manually configured parameters")
        return Argon2Params(
            time_cost=int(settings.argon2_time_cost),
            memory_cost=int(settings.argon2_memory_cost),
            parallelism=int(settings.argon2_parallelism),
        )

    host_fingerprint = compute_host_fingerprint()
    record = read_calibration(settings)
    if record is not None and record.host_fingerprint == host_fingerprint:
        age_days = _calibration_age_days(record)
        if age_days is not None and age_days > CALIBRATION_AGE_WARNING_DAYS:
            logger.warning(
                "Argon2id calibration is %s days old, consider re-running "
                "'python -m backend.app.cli calibrate-argon2'",
                age_days,
            )
        else:
            logger.debug("Argon2id using stored calibration parameters")
        return record.params
    if record is not None and record.host_fingerprint != host_fingerprint:
        logger.info("Hardware change detected, recalibrating Argon2id")

    logger.info("Calibrating Argon2id parameters for current hardware")
    params, measured_ms = calibrate_argon2()
    if params == FALLBACK_PARAMS and measured_ms == 0.0:
        logger.warning(
            "Argon2id calibration failed, using safe fallback parameters "
            "(t=3, m=64MB, p=2). Login may be slow on this hardware."
        )
    record = CalibrationRecord(
        calibrated_at=datetime.now(timezone.utc).isoformat(),
        calibration_version=CALIBRATION_VERSION,
        host_fingerprint=host_fingerprint,
        target_verify_ms=TARGET_VERIFY_MS,
        measured_verify_ms=measured_ms,
        params=params,
    )
    try:
        write_calibration(record, settings)
    except Exception as exc:
        logger.warning("Unable to write Argon2id calibration file: %s", exc)
    logger.info(
        "Calibrated Argon2id: t=%s m=%s p=%s verify=%.1fms",
        params.time_cost,
        params.memory_cost,
        params.parallelism,
        measured_ms,
    )
    return params
