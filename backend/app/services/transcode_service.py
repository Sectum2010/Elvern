from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..config import Settings
from ..db import utcnow_iso
from ..media_stream import ensure_media_path_within_root


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TranscodeJob:
    media_item_id: int
    title: str
    owner_user_id: int | None
    source_key: str
    source_input: str
    source_input_kind: str
    output_dir: Path
    manifest_path: Path
    expected_duration_seconds: float | None
    started_at: str
    last_access_at: str
    last_access_ts: float
    state: str = "starting"
    last_error: str | None = None
    generated_duration_seconds: float = 0.0
    segment_count: int = 0
    manifest_complete: bool = False
    process: subprocess.Popen[str] | None = field(default=None, repr=False)


class TranscodeManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._jobs: dict[int, TranscodeJob] = {}
        self._last_error: str | None = None
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: threading.Thread | None = None

    def start(self) -> None:
        self.settings.transcode_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_orphaned_dirs()
        if self._cleanup_thread is None:
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop,
                daemon=True,
                name="elvern-transcode-cleanup",
            )
            self._cleanup_thread.start()
        logger.info(
            "Transcode manager ready: enabled=%s ffmpeg=%s cache=%s",
            self.settings.transcode_enabled,
            self.settings.ffmpeg_path or "missing",
            self.settings.transcode_dir,
        )

    def shutdown(self) -> None:
        self._cleanup_stop.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=2)
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            self._terminate_process(job)

    def get_debug_status(self) -> dict[str, object]:
        self.cleanup_stale()
        with self._lock:
            for job in self._jobs.values():
                self._update_manifest_stats_locked(job)
            active_jobs = [
                {
                    "media_item_id": job.media_item_id,
                    "title": job.title,
                    "status": job.state,
                    "started_at": job.started_at,
                    "last_access_at": job.last_access_at,
                    "manifest_ready": job.manifest_path.exists(),
                    "expected_duration_seconds": job.expected_duration_seconds,
                    "generated_duration_seconds": job.generated_duration_seconds,
                    "segment_count": job.segment_count,
                    "manifest_complete": job.manifest_complete,
                    "output_dir": str(job.output_dir),
                    "last_error": job.last_error,
                }
                for job in self._jobs.values()
            ]
        return {
            "enabled": self.settings.transcode_enabled,
            "ffmpeg_available": bool(self.settings.ffmpeg_path),
            "cache_dir": str(self.settings.transcode_dir),
            "ttl_minutes": self.settings.transcode_ttl_minutes,
            "max_concurrent_transcodes": self.settings.max_concurrent_transcodes,
            "active_jobs": active_jobs,
            "last_error": self._last_error,
        }

    def get_job_snapshot(self, item: dict[str, object]) -> dict[str, object]:
        self.cleanup_stale()
        expected_duration_seconds = self._coerce_duration(item.get("duration_seconds"))
        if not self.settings.transcode_enabled:
            return {
                "enabled": False,
                "status": "disabled",
                "manifest_ready": False,
                "expected_duration_seconds": expected_duration_seconds,
                "generated_duration_seconds": 0.0,
                "manifest_complete": False,
                "last_error": "Transcoding is disabled",
            }
        if not self.settings.ffmpeg_path:
            return {
                "enabled": True,
                "status": "disabled",
                "manifest_ready": False,
                "expected_duration_seconds": expected_duration_seconds,
                "generated_duration_seconds": 0.0,
                "manifest_complete": False,
                "last_error": "ffmpeg was not found on the server",
            }

        source_key = self._source_key(item)
        with self._lock:
            job = self._jobs.get(int(item["id"]))
            if job and job.source_key == source_key:
                self._touch_locked(job)
                self._update_manifest_stats_locked(job)
                if job.process and job.process.poll() is None and job.state == "starting":
                    job.state = "running"
                if job.state == "completed" and not job.manifest_path.exists():
                    job.state = "failed"
                    job.last_error = "HLS manifest disappeared from the transcode cache"
                return self._snapshot_locked(job)
        return {
            "enabled": True,
            "status": "idle",
            "manifest_ready": False,
            "expected_duration_seconds": expected_duration_seconds,
            "generated_duration_seconds": 0.0,
            "manifest_complete": False,
            "last_error": self._last_error,
        }

    def ensure_started(
        self,
        item: dict[str, object],
        *,
        reason: str,
        owner_user_id: int | None = None,
        source_input: str | None = None,
        source_input_kind: str = "path",
    ) -> dict[str, object]:
        self.cleanup_stale()
        snapshot = self.get_job_snapshot(item)
        if snapshot["status"] in {"running", "completed"}:
            logger.info(
                "Reusing transcode session for item=%s status=%s",
                item["id"],
                snapshot["status"],
            )
            return snapshot
        if snapshot["status"] == "disabled":
            return snapshot

        if source_input is None:
            resolved_source_path = ensure_media_path_within_root(Path(str(item["file_path"])), self.settings)
            resolved_source_input = str(resolved_source_path)
            resolved_source_input_kind = "path"
        else:
            resolved_source_input = str(source_input)
            resolved_source_input_kind = source_input_kind
        source_key = self._source_key(item)
        output_dir = self.settings.transcode_dir / source_key
        manifest_path = output_dir / "index.m3u8"
        existing: TranscodeJob | None = None
        replaced_job: TranscodeJob | None = None

        with self._lock:
            existing = self._jobs.get(int(item["id"]))
            if existing:
                self._jobs.pop(existing.media_item_id, None)
            running_jobs = [
                job
                for job in self._jobs.values()
                if job.process and job.process.poll() is None
            ]
            if len(running_jobs) >= self.settings.max_concurrent_transcodes:
                if owner_user_id is not None:
                    same_owner_jobs = [
                        job
                        for job in running_jobs
                        if job.owner_user_id == owner_user_id
                    ]
                    if same_owner_jobs:
                        replaced_job = max(same_owner_jobs, key=lambda job: job.last_access_ts)
                        self._jobs.pop(replaced_job.media_item_id, None)
                    else:
                        message = (
                            "Maximum concurrent transcodes reached; wait for another playback session to finish"
                        )
                        self._last_error = message
                        return {
                            "enabled": True,
                            "status": "busy",
                            "manifest_ready": False,
                            "last_error": message,
                        }
                else:
                    message = (
                        "Maximum concurrent transcodes reached; wait for another playback session to finish"
                    )
                    self._last_error = message
                    return {
                        "enabled": True,
                        "status": "busy",
                        "manifest_ready": False,
                        "last_error": message,
                    }
            job = TranscodeJob(
                media_item_id=int(item["id"]),
                title=str(item["title"]),
                owner_user_id=owner_user_id,
                source_key=source_key,
                source_input=resolved_source_input,
                source_input_kind=resolved_source_input_kind,
                output_dir=output_dir,
                manifest_path=manifest_path,
                expected_duration_seconds=self._coerce_duration(item.get("duration_seconds")),
                started_at=utcnow_iso(),
                last_access_at=utcnow_iso(),
                last_access_ts=time.time(),
            )
            self._jobs[job.media_item_id] = job

        if existing:
            self._terminate_process(existing)
            shutil.rmtree(existing.output_dir, ignore_errors=True)
        if replaced_job and (not existing or replaced_job.media_item_id != existing.media_item_id):
            logger.info(
                "Replacing active transcode item=%s with item=%s for user=%s",
                replaced_job.media_item_id,
                item["id"],
                owner_user_id,
            )
            self._terminate_process(replaced_job)
            shutil.rmtree(replaced_job.output_dir, ignore_errors=True)

        self._start_process(job, reason=reason)
        return self.get_job_snapshot(item)

    def stop_item_for_owner(self, item: dict[str, object], *, owner_user_id: int) -> bool:
        source_key = self._source_key(item)
        with self._lock:
            job = self._jobs.get(int(item["id"]))
            if not job or job.source_key != source_key or job.owner_user_id != owner_user_id:
                return False
            self._jobs.pop(job.media_item_id, None)
        logger.info(
            "Stopping transcode item=%s for user=%s after client abandoned playback",
            item["id"],
            owner_user_id,
        )
        self._terminate_process(job)
        shutil.rmtree(job.output_dir, ignore_errors=True)
        return True

    def touch(self, item_id: int) -> None:
        with self._lock:
            job = self._jobs.get(item_id)
            if job:
                self._touch_locked(job)

    def get_manifest_path(self, item: dict[str, object]) -> Path | None:
        source_key = self._source_key(item)
        with self._lock:
            job = self._jobs.get(int(item["id"]))
            if not job or job.source_key != source_key:
                return None
            self._touch_locked(job)
            self._update_manifest_stats_locked(job)
            if not job.manifest_path.exists():
                return None
            return job.manifest_path

    def get_manifest_content(self, item: dict[str, object]) -> str | None:
        source_key = self._source_key(item)
        with self._lock:
            job = self._jobs.get(int(item["id"]))
            if not job or job.source_key != source_key:
                return None
            self._touch_locked(job)
            self._update_manifest_stats_locked(job)
            manifest_path = job.manifest_path
            manifest_complete = job.manifest_complete
        if not manifest_path.exists():
            return None
        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return self._normalize_manifest(content, manifest_complete=manifest_complete)

    def get_segment_path(self, item: dict[str, object], segment_name: str) -> Path | None:
        if "/" in segment_name or "\\" in segment_name:
            return None
        source_key = self._source_key(item)
        with self._lock:
            job = self._jobs.get(int(item["id"]))
            if not job or job.source_key != source_key:
                return None
            self._touch_locked(job)
            self._update_manifest_stats_locked(job)
            candidate = (job.output_dir / segment_name).resolve()
            try:
                candidate.relative_to(job.output_dir.resolve())
            except ValueError:
                return None
            return candidate if candidate.exists() else None

    def cleanup_stale(self) -> None:
        ttl_seconds = self.settings.transcode_ttl_minutes * 60
        abandoned_seconds = min(ttl_seconds, 120)
        now = time.time()
        removable: list[tuple[TranscodeJob, str]] = []
        with self._lock:
            for job in list(self._jobs.values()):
                self._update_manifest_stats_locked(job)
                idle_seconds = now - job.last_access_ts
                if job.process and job.process.poll() is None and idle_seconds > abandoned_seconds:
                    removable.append((job, "abandoned"))
                    self._jobs.pop(job.media_item_id, None)
                    continue
                if idle_seconds > ttl_seconds:
                    removable.append((job, "stale"))
                    self._jobs.pop(job.media_item_id, None)
        for job, reason in removable:
            logger.info(
                "Cleaning up %s transcode cache for item=%s title=%s",
                reason,
                job.media_item_id,
                job.title,
            )
            self._terminate_process(job)
            shutil.rmtree(job.output_dir, ignore_errors=True)

    def _cleanup_loop(self) -> None:
        while not self._cleanup_stop.wait(60):
            self.cleanup_stale()
            self._cleanup_orphaned_dirs()

    def _cleanup_orphaned_dirs(self) -> None:
        if not self.settings.transcode_dir.exists():
            return
        cutoff = time.time() - (self.settings.transcode_ttl_minutes * 60)
        for child in self.settings.transcode_dir.iterdir():
            if not child.is_dir():
                continue
            if child.stat().st_mtime >= cutoff:
                continue
            logger.info("Removing stale transcode directory %s", child)
            shutil.rmtree(child, ignore_errors=True)

    def _start_process(self, job: TranscodeJob, *, reason: str) -> None:
        shutil.rmtree(job.output_dir, ignore_errors=True)
        job.output_dir.mkdir(parents=True, exist_ok=True)
        command = self._build_command(job)
        logger.info(
            "Starting ffmpeg transcode for item=%s reason=%s command=%s",
            job.media_item_id,
            reason,
            " ".join(self._redact_command(command)),
        )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            logger.exception("Failed to start ffmpeg for item=%s", job.media_item_id)
            with self._lock:
                stored = self._jobs.get(job.media_item_id)
                if stored:
                    stored.state = "failed"
                    stored.last_error = str(exc)
                self._last_error = str(exc)
            return

        with self._lock:
            stored = self._jobs.get(job.media_item_id)
            if stored:
                stored.process = process
                stored.state = "running"
                stored.last_error = None
                self._last_error = None
        watcher = threading.Thread(
            target=self._watch_process,
            args=(job.media_item_id,),
            daemon=True,
            name=f"elvern-ffmpeg-{job.media_item_id}",
        )
        watcher.start()

    def _watch_process(self, media_item_id: int) -> None:
        with self._lock:
            job = self._jobs.get(media_item_id)
            if not job or not job.process:
                return
            process = job.process

        if process.stderr:
            for line in process.stderr:
                message = line.strip()
                if message:
                    logger.warning("ffmpeg[%s] %s", media_item_id, message)

        return_code = process.wait()
        with self._lock:
            current = self._jobs.get(media_item_id)
            if not current:
                return
            current.process = None
            self._update_manifest_stats_locked(current)
            if return_code == 0:
                current.state = "completed"
                current.last_error = None
                logger.info("ffmpeg transcode completed for item=%s", media_item_id)
            else:
                current.state = "failed"
                current.last_error = f"ffmpeg exited with code {return_code}"
                self._last_error = current.last_error
                logger.error(
                    "ffmpeg transcode failed for item=%s return_code=%s",
                    media_item_id,
                    return_code,
                )

    def _build_command(self, job: TranscodeJob) -> list[str]:
        segment_pattern = job.output_dir / "segment_%05d.ts"
        command = [
            str(self.settings.ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
        ]
        if job.source_input_kind == "url":
            command.extend(
                [
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_on_network_error",
                    "1",
                    "-rw_timeout",
                    "15000000",
                ]
            )
        command.extend(
            [
                "-i",
                job.source_input,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-profile:v",
                "main",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-b:a",
                "160k",
                "-max_muxing_queue_size",
                "2048",
                "-f",
                "hls",
                "-hls_time",
                "6",
                "-hls_list_size",
                "0",
                "-hls_flags",
                "independent_segments+append_list+temp_file",
                "-hls_segment_filename",
                str(segment_pattern),
                str(job.manifest_path),
            ]
        )
        return command

    def _source_key(self, item: dict[str, object]) -> str:
        mtime_token = int(float(item.get("file_mtime") or 0))
        return f"item-{int(item['id'])}-{mtime_token}"

    def _snapshot_locked(self, job: TranscodeJob) -> dict[str, object]:
        self._update_manifest_stats_locked(job)
        return {
            "enabled": True,
            "status": job.state,
            "manifest_ready": job.manifest_path.exists(),
            "expected_duration_seconds": job.expected_duration_seconds,
            "generated_duration_seconds": job.generated_duration_seconds,
            "manifest_complete": job.manifest_complete,
            "last_error": job.last_error,
        }

    def _touch_locked(self, job: TranscodeJob) -> None:
        job.last_access_at = utcnow_iso()
        job.last_access_ts = time.time()

    def _terminate_process(self, job: TranscodeJob) -> None:
        process = job.process
        if not process or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _coerce_duration(self, value: object) -> float | None:
        if value in {None, ""}:
            return None
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None

    def _update_manifest_stats_locked(self, job: TranscodeJob) -> None:
        if not job.manifest_path.exists():
            job.generated_duration_seconds = 0.0
            job.segment_count = 0
            job.manifest_complete = False
            return
        generated_duration_seconds, segment_count, manifest_complete = self._read_manifest_stats(
            job.manifest_path
        )
        job.generated_duration_seconds = generated_duration_seconds
        job.segment_count = segment_count
        job.manifest_complete = manifest_complete

    def _read_manifest_stats(self, manifest_path: Path) -> tuple[float, int, bool]:
        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError:
            return 0.0, 0, False
        generated_duration_seconds = 0.0
        segment_count = 0
        manifest_complete = False
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if line.startswith("#EXTINF:"):
                duration_token = line.removeprefix("#EXTINF:").split(",", 1)[0].strip()
                try:
                    generated_duration_seconds += float(duration_token)
                    segment_count += 1
                except ValueError:
                    continue
            elif line == "#EXT-X-ENDLIST":
                manifest_complete = True
        return round(generated_duration_seconds, 2), segment_count, manifest_complete

    def _normalize_manifest(self, content: str, *, manifest_complete: bool) -> str:
        lines: list[str] = []
        insertion_index = 1
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-PLAYLIST-TYPE:"):
                continue
            lines.append(line)
            if line.startswith("#EXT-X-TARGETDURATION:"):
                insertion_index = len(lines)
        lines.insert(
            insertion_index,
            "#EXT-X-PLAYLIST-TYPE:VOD" if manifest_complete else "#EXT-X-PLAYLIST-TYPE:EVENT",
        )
        return "\n".join(lines) + "\n"

    def _redact_command(self, command: list[str]) -> list[str]:
        redacted: list[str] = []
        for token in command:
            parsed = urlsplit(token)
            if not parsed.scheme or not parsed.netloc or not parsed.query:
                redacted.append(token)
                continue
            query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
            sanitized_pairs = [
                (key, "***" if key.lower() == "token" else value)
                for key, value in query_pairs
            ]
            redacted.append(urlunsplit(parsed._replace(query=urlencode(sanitized_pairs))))
        return redacted
