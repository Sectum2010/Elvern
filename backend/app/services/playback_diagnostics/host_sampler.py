from __future__ import annotations

import json
import hashlib
import ipaddress
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable, Iterable

from .constants import (
    GPU_SAMPLE_INTERVAL_MS,
    HOST_AGGREGATE_INTERVAL_MS,
    HOST_RING_SAMPLE_INTERVAL_MS,
    INCIDENT_POST_WINDOW_SECONDS,
    INCIDENT_PRE_WINDOW_SECONDS,
    PSS_SAMPLE_INTERVAL_MS,
    TAILSCALE_SAMPLE_INTERVAL_MS,
)
from .privacy import pseudonymize_ip


MAX_SUBPROCESS_OUTPUT_BYTES = 1_000_000
MAX_UNSUPPORTED_CAPABILITY_LINKS = 8_192
MAX_CAPTURED_INCIDENT_LINKS = 4_096


def _run_bounded_command(
    argv: list[str],
    *,
    timeout: float,
    max_output_bytes: int = MAX_SUBPROCESS_OUTPUT_BYTES,
) -> tuple[bytes, bytes]:
    """Run an argv command while draining but never retaining unbounded output."""

    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    capture_lock = threading.Lock()

    def drain(name: str, stream) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                with capture_lock:
                    retained = sum(len(value) for value in captured.values())
                    remaining = max(0, max_output_bytes - retained)
                    captured[name].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
        finally:
            stream.close()

    readers = [
        threading.Thread(
            target=drain,
            args=(name, stream),
            daemon=False,
            name=f"elvern-diagnostics-command-{name}",
        )
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
        if stream is not None
    ]
    for reader in readers:
        reader.start()
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)
        raise
    finally:
        for reader in readers:
            reader.join()
    if overflow.is_set():
        raise subprocess.SubprocessError("diagnostics command output exceeded its bound")
    stdout = bytes(captured["stdout"])
    stderr = bytes(captured["stderr"])
    if return_code != 0:
        raise subprocess.CalledProcessError(
            return_code,
            argv,
            output=stdout,
            stderr=stderr,
        )
    return stdout, stderr


def _read_text(path: Path, *, max_bytes: int = 1_000_000) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_bytes)
    except OSError:
        return None


def _nearest_existing_path(path: Path) -> Path:
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def parse_proc_stat(payload: str) -> dict[str, Any]:
    cpu_rows: dict[str, list[int]] = {}
    context_switches = None
    interrupts = None
    runnable_tasks = None
    for line in payload.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "cpu" or parts[0].startswith("cpu") and parts[0][3:].isdigit():
            try:
                cpu_rows[parts[0]] = [int(value) for value in parts[1:11]]
            except ValueError:
                continue
        elif parts[0] == "ctxt" and len(parts) > 1:
            context_switches = int(parts[1])
        elif parts[0] == "intr" and len(parts) > 1:
            interrupts = int(parts[1])
        elif parts[0] == "procs_running" and len(parts) > 1:
            runnable_tasks = int(parts[1])
    return {
        "cpu_rows": cpu_rows,
        "context_switches": context_switches,
        "interrupts": interrupts,
        "runnable_tasks": runnable_tasks,
    }


def cpu_percent_between(previous: list[int], current: list[int]) -> dict[str, float] | None:
    if len(previous) < 5 or len(current) < 5:
        return None
    deltas = [max(0, current[index] - previous[index]) for index in range(min(len(previous), len(current)))]
    total = sum(deltas)
    if total <= 0:
        return None
    idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0)
    return {
        "cpu_percent": round((total - idle) * 100 / total, 3),
        "user": round(deltas[0] * 100 / total, 3),
        "system": round(deltas[2] * 100 / total, 3),
        "idle": round(deltas[3] * 100 / total, 3),
        "iowait": round((deltas[4] if len(deltas) > 4 else 0) * 100 / total, 3),
        "steal": round((deltas[7] if len(deltas) > 7 else 0) * 100 / total, 3),
        "total": total,
    }


def parse_meminfo(payload: str) -> dict[str, int]:
    values: dict[str, int] = {}
    names = {
        "MemTotal": "total",
        "MemAvailable": "available",
        "MemFree": "free",
        "Buffers": "buffers",
        "Cached": "cache",
        "Active": "active",
        "Inactive": "inactive",
        "SwapTotal": "swap_total",
        "SwapFree": "swap_free",
    }
    for line in payload.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        if key not in names:
            continue
        try:
            values[names[key]] = int(raw.strip().split()[0]) * 1024
        except (IndexError, ValueError):
            continue
    return values


def parse_pressure(payload: str) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for line in payload.splitlines():
        parts = line.split()
        if not parts:
            continue
        values: dict[str, float | int] = {}
        for token in parts[1:]:
            if "=" not in token:
                continue
            key, raw = token.split("=", 1)
            try:
                values[key] = int(raw) if key == "total" else float(raw)
            except ValueError:
                continue
        result[parts[0]] = values
    return result


def parse_process_stat(payload: str, *, clock_ticks: int) -> dict[str, int | float | None]:
    closing = payload.rfind(")")
    if closing < 0:
        return {}
    fields = payload[closing + 2 :].split()
    if len(fields) < 22 or clock_ticks <= 0:
        return {}
    try:
        return {
            "minor_faults": int(fields[7]),
            "major_faults": int(fields[9]),
            "cpu_seconds": round((int(fields[11]) + int(fields[12])) / clock_ticks, 6),
            "worker_threads": int(fields[17]),
        }
    except (IndexError, ValueError):
        return {}


def parse_process_io(payload: str) -> dict[str, int]:
    values: dict[str, int] = {}
    names = {"read_bytes": "read_bytes", "write_bytes": "write_bytes"}
    for line in payload.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        if key not in names:
            continue
        try:
            values[names[key]] = int(raw.strip())
        except ValueError:
            continue
    return values


class HostDiagnosticsSampler:
    def __init__(
        self,
        *,
        active_session_ids: Callable[[], Iterable[str]],
        active_session_clients: Callable[[], dict[str, str]] = lambda: {},
        active_processes: Callable[[], Iterable[dict[str, object]]] = lambda: (),
        observe: Callable[..., None],
        record_host_observation: Callable[..., None] = lambda *_args, **_kwargs: None,
        should_defer_optional: Callable[[], bool] = lambda: False,
        health_callback: Callable[[str], None] = lambda _reason: None,
        pressure_callback: Callable[..., None] = lambda **_values: None,
        identity_key: bytes = b"",
        diagnostics_root: Path,
        transcode_root: Path,
    ) -> None:
        self.active_session_ids = active_session_ids
        self.active_session_clients = active_session_clients
        self.active_processes = active_processes
        self.observe = observe
        self.record_host_observation = record_host_observation
        self.should_defer_optional = should_defer_optional
        self.health_callback = health_callback
        self.pressure_callback = pressure_callback
        self.identity_key = bytes(identity_key)
        self.diagnostics_root = diagnostics_root
        self.transcode_root = transcode_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        ring_samples = max(1, int(INCIDENT_PRE_WINDOW_SECONDS * 1_000 / HOST_RING_SAMPLE_INTERVAL_MS))
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_samples)
        self._previous_cpu: list[int] | None = None
        self._last_aggregate = 0.0
        self._last_pss = 0.0
        self._last_gpu = 0.0
        self._last_tailscale = 0.0
        self._unsupported_emitted: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._incident_lock = threading.Lock()
        self._captured_incidents: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._post_incidents: dict[tuple[str, str], float] = {}
        self._process_cpu_samples: dict[int, tuple[float, float]] = {}
        self._clock_ticks = int(os.sysconf("SC_CLK_TCK"))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=False,
            name="elvern-playback-diagnostics-host",
        )
        self._thread.start()

    def shutdown(self, *, timeout: float = 5.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._thread = None
        return stopped

    def freeze_incident_ring(self, playback_session_id: str, incident_id: str) -> None:
        incident_key = (playback_session_id, incident_id)
        with self._incident_lock:
            if incident_key in self._captured_incidents:
                self._captured_incidents.move_to_end(incident_key)
                return
            self._captured_incidents[incident_key] = None
            while len(self._captured_incidents) > MAX_CAPTURED_INCIDENT_LINKS:
                evicted_key, _ = self._captured_incidents.popitem(last=False)
                self._post_incidents.pop(evicted_key, None)
            self._post_incidents[incident_key] = (
                time.monotonic() + INCIDENT_POST_WINDOW_SECONDS
            )
        for sample in list(self._ring):
            self._persist_sample(
                "host_incident_sample",
                sample,
                ((playback_session_id, incident_id, "pre"),),
                extra_payload={"ring_complete": len(self._ring) == self._ring.maxlen},
            )

    def forget_session(self, playback_session_id: str) -> None:
        """Release sampler-only bookkeeping after a session is durably sealed."""

        with self._incident_lock:
            for key in tuple(self._captured_incidents):
                if key[0] == playback_session_id:
                    self._captured_incidents.pop(key, None)
                    self._post_incidents.pop(key, None)
        for key in tuple(self._unsupported_emitted):
            if key[0] == playback_session_id:
                self._unsupported_emitted.pop(key, None)

    def _run(self) -> None:
        interval = HOST_RING_SAMPLE_INTERVAL_MS / 1_000
        while not self._stop.wait(interval):
            session_ids = tuple(self.active_session_ids())
            if not session_ids:
                self._previous_cpu = None
                continue
            if self._optional_sampling_deferred():
                self.health_callback("host_sampling_deferred_pressure")
                continue
            try:
                sample_started = time.monotonic()
                sample_payload = self._sample()
                self._report_pressure(
                    sample_payload,
                    latency_ms=(time.monotonic() - sample_started) * 1_000,
                )
                sample = self._new_sample_record(sample_payload)
            except Exception:  # noqa: BLE001 - sampling cannot affect playback.
                self.health_callback("host_sample_failed")
                continue
            self._ring.append(sample)
            now = time.monotonic()
            self._emit_incident_post_samples(sample, now)
            if now - self._last_aggregate >= HOST_AGGREGATE_INTERVAL_MS / 1_000:
                self._last_aggregate = now
                self._persist_sample(
                    "host_aggregate",
                    sample,
                    tuple((session_id, None, None) for session_id in session_ids),
                )
                self._sample_processes()
            if now - self._last_gpu >= GPU_SAMPLE_INTERVAL_MS / 1_000:
                self._last_gpu = now
                self._sample_gpu(session_ids)
            if now - self._last_tailscale >= TAILSCALE_SAMPLE_INTERVAL_MS / 1_000:
                self._last_tailscale = now
                self._sample_tailscale(session_ids)

    def _optional_sampling_deferred(self) -> bool:
        try:
            return bool(self.should_defer_optional())
        except Exception:  # noqa: BLE001 - pressure detection is diagnostics-only.
            self.health_callback("host_pressure_check_failed")
            return True

    def _report_pressure(self, sample: dict[str, Any], *, latency_ms: float) -> None:
        cpu = sample.get("cpu") if isinstance(sample.get("cpu"), dict) else {}
        memory = sample.get("memory") if isinstance(sample.get("memory"), dict) else {}
        psi = sample.get("psi") if isinstance(sample.get("psi"), dict) else {}
        io_some = psi.get("io", {}).get("some", {}) if isinstance(psi.get("io"), dict) else {}
        total_memory = float(memory.get("total") or 0)
        available_memory = float(memory.get("available") or 0)
        memory_pressure = (
            max(0.0, min(1.0, 1.0 - (available_memory / total_memory)))
            if total_memory > 0
            else 0.0
        )
        try:
            self.pressure_callback(
                host_sampler_latency_ms=max(0.0, float(latency_ms)),
                cpu_pressure_ratio=max(0.0, min(1.0, float(cpu.get("cpu_percent") or 0) / 100)),
                io_pressure_ratio=max(0.0, min(1.0, float(io_some.get("avg10") or 0) / 100)),
                memory_pressure_ratio=memory_pressure,
            )
        except Exception:  # noqa: BLE001 - health reporting cannot affect sampling.
            self.health_callback("host_pressure_report_failed")

    def _emit_incident_post_samples(self, sample: dict[str, Any], now: float) -> None:
        with self._incident_lock:
            active = list(self._post_incidents.items())
            for incident_key, deadline in active:
                if now > deadline:
                    self._post_incidents.pop(incident_key, None)
            active = [
                (incident_key, deadline)
                for incident_key, deadline in active
                if now <= deadline
            ]
        for (playback_session_id, incident_id), _deadline in active:
            self._persist_sample(
                "host_incident_sample",
                sample,
                ((playback_session_id, incident_id, "post"),),
            )

    @staticmethod
    def _new_sample_record(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "sample_id": f"host_{uuid.uuid4().hex}",
            "observed_wall_time_ns": time.time_ns(),
            "observed_monotonic_time_ns": time.monotonic_ns(),
            "payload": payload,
        }

    def _persist_sample(
        self,
        event_name: str,
        sample: dict[str, Any],
        links: tuple[tuple[str, str | None, str | None], ...],
        *,
        extra_payload: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(sample.get("payload") or {})
        payload.update(extra_payload or {})
        self.record_host_observation(
            event_name,
            sample_id=str(sample["sample_id"]),
            payload=payload,
            session_links=links,
            observed_wall_time_ns=int(sample["observed_wall_time_ns"]),
            observed_monotonic_time_ns=int(sample["observed_monotonic_time_ns"]),
        )

    def _sample(self) -> dict[str, Any]:
        now = time.monotonic()
        stat_payload = _read_text(Path("/proc/stat")) or ""
        parsed_stat = parse_proc_stat(stat_payload)
        current_cpu = parsed_stat["cpu_rows"].get("cpu")
        cpu = cpu_percent_between(self._previous_cpu, current_cpu) if self._previous_cpu and current_cpu else None
        self._previous_cpu = current_cpu
        meminfo = parse_meminfo(_read_text(Path("/proc/meminfo")) or "")
        load_average: list[float] = []
        try:
            load_average = [round(value, 3) for value in os.getloadavg()]
        except OSError:
            self.health_callback("host_load_average_unavailable")
        psi = {
            kind: parse_pressure(_read_text(Path(f"/proc/pressure/{kind}")) or "")
            for kind in ("cpu", "memory", "io")
        }
        process_status = _read_text(Path(f"/proc/{os.getpid()}/status")) or ""
        rss_bytes = None
        for line in process_status.splitlines():
            if line.startswith("VmRSS:"):
                try:
                    rss_bytes = int(line.split()[1]) * 1024
                except (IndexError, ValueError):
                    self.health_callback("host_rss_parse_failed")
                break
        pss_bytes = None
        if now - self._last_pss >= PSS_SAMPLE_INTERVAL_MS / 1_000:
            self._last_pss = now
            rollup = _read_text(Path(f"/proc/{os.getpid()}/smaps_rollup"), max_bytes=100_000) or ""
            for line in rollup.splitlines():
                if line.startswith("Pss:"):
                    try:
                        pss_bytes = int(line.split()[1]) * 1024
                    except (IndexError, ValueError):
                        self.health_callback("host_pss_parse_failed")
                    break
        diagnostics_usage = shutil.disk_usage(_nearest_existing_path(self.diagnostics_root))
        transcode_usage = shutil.disk_usage(_nearest_existing_path(self.transcode_root))
        backend_process = {
            **parse_process_stat(
                _read_text(Path(f"/proc/{os.getpid()}/stat"), max_bytes=32_000) or "",
                clock_ticks=self._clock_ticks,
            ),
            **parse_process_io(
                _read_text(Path(f"/proc/{os.getpid()}/io"), max_bytes=32_000) or ""
            ),
            "memory_rss_bytes": rss_bytes,
            "memory_pss_bytes": pss_bytes,
        }
        boot_id = (_read_text(Path("/proc/sys/kernel/random/boot_id"), max_bytes=256) or "").strip()
        diagnostics_vfs = os.statvfs(_nearest_existing_path(self.diagnostics_root))
        transcode_vfs = os.statvfs(_nearest_existing_path(self.transcode_root))
        return {
            "sample_interval_ms": HOST_RING_SAMPLE_INTERVAL_MS,
            "cpu": cpu or {"available": False, "unavailable_reason": "initial_counter_sample"},
            "memory": {
                "total": meminfo.get("total"),
                "available": meminfo.get("available"),
                "free": meminfo.get("free"),
                "buffers": meminfo.get("buffers"),
                "cache": meminfo.get("cache"),
                "active": meminfo.get("active"),
                "inactive": meminfo.get("inactive"),
                "swap_total": meminfo.get("swap_total"),
                "swap_free": meminfo.get("swap_free"),
                "memory_rss_bytes": rss_bytes,
                "memory_pss_bytes": pss_bytes,
            },
            "load_average": load_average,
            "runnable_tasks": parsed_stat.get("runnable_tasks"),
            "context_switches": parsed_stat.get("context_switches"),
            "interrupts": parsed_stat.get("interrupts"),
            "psi": psi,
            "process": backend_process,
            "host_boot_id_hash": hashlib.sha256(boot_id.encode()).hexdigest() if boot_id else None,
            "host": {
                "diagnostics_free_bytes": diagnostics_usage.free,
                "transcode_free_bytes": transcode_usage.free,
                "diagnostics_free_inodes": diagnostics_vfs.f_favail,
                "transcode_free_inodes": transcode_vfs.f_favail,
            },
        }

    def _sample_processes(self) -> None:
        current_pids: set[int] = set()
        for identity in tuple(self.active_processes()):
            try:
                pid = int(identity.get("pid") or 0)
                playback_session_id = str(identity.get("playback_session_id") or "")
            except (AttributeError, TypeError, ValueError):
                continue
            if pid <= 0 or not playback_session_id:
                continue
            current_pids.add(pid)
            stat = parse_process_stat(
                _read_text(Path(f"/proc/{pid}/stat"), max_bytes=32_000) or "",
                clock_ticks=self._clock_ticks,
            )
            if not stat:
                continue
            status = _read_text(Path(f"/proc/{pid}/status"), max_bytes=100_000) or ""
            rss_bytes = None
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    try:
                        rss_bytes = int(line.split()[1]) * 1024
                    except (IndexError, ValueError):
                        self.health_callback("ffmpeg_rss_parse_failed")
                    break
            now = time.monotonic()
            cpu_seconds = float(stat.get("cpu_seconds") or 0.0)
            previous = self._process_cpu_samples.get(pid)
            cpu_percent = None
            if previous is not None and now > previous[0]:
                cpu_percent = max(0.0, (cpu_seconds - previous[1]) * 100 / (now - previous[0]))
            self._process_cpu_samples[pid] = (now, cpu_seconds)
            payload = {
                **stat,
                **parse_process_io(_read_text(Path(f"/proc/{pid}/io"), max_bytes=32_000) or ""),
                "cpu_percent": round(cpu_percent, 3) if cpu_percent is not None else None,
                "memory_rss_bytes": rss_bytes,
                "pid": pid,
                "process_state": "running",
            }
            self.observe(
                "ffmpeg_process_aggregate",
                playback_session_id=playback_session_id,
                event_source="host",
                observation_kind="measured_kernel",
                worker_id=str(identity.get("worker_id") or "") or None,
                epoch_id=str(identity.get("epoch_id") or "") or None,
                sample_window_ms=HOST_AGGREGATE_INTERVAL_MS,
                payload=payload,
            )
        for pid in tuple(self._process_cpu_samples):
            if pid not in current_pids:
                self._process_cpu_samples.pop(pid, None)

    def _sample_gpu(self, session_ids: tuple[str, ...]) -> None:
        binary = shutil.which("nvidia-smi")
        if not binary:
            self._emit_unsupported(session_ids, "gpu", "nvidia_smi_unavailable")
            return
        try:
            stdout, _stderr = _run_bounded_command(
                [
                    binary,
                    "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder,memory.used,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                timeout=2,
            )
            first = stdout.decode("utf-8", errors="strict").splitlines()[0].split(",")
            values = [float(value.strip()) for value in first[:5]]
            payload = {
                "sample_interval_ms": GPU_SAMPLE_INTERVAL_MS,
                "gpu_utilization_percent": values[0],
                "gpu_encoder_percent": values[1],
                "gpu_decoder_percent": values[2],
                "gpu_memory_bytes": int(values[3] * 1024 * 1024),
                "gpu_temperature_c": values[4],
            }
        except (OSError, ValueError, subprocess.SubprocessError):
            self._emit_unsupported(session_ids, "gpu", "nvidia_smi_query_failed")
            return
        sample = self._new_sample_record(payload)
        self._persist_sample(
            "gpu_aggregate",
            sample,
            tuple((session_id, None, None) for session_id in session_ids),
        )

    def _sample_tailscale(self, session_ids: tuple[str, ...]) -> None:
        binary = shutil.which("tailscale")
        if not binary:
            self._emit_unsupported(session_ids, "tailscale", "tailscale_cli_unavailable")
            return
        try:
            stdout, _stderr = _run_bounded_command(
                [binary, "status", "--json"],
                timeout=2,
            )
            status = json.loads(stdout.decode("utf-8", errors="strict"))
            peers = status.get("Peer") if isinstance(status.get("Peer"), dict) else {}
        except (OSError, ValueError, subprocess.SubprocessError):
            self._emit_unsupported(session_ids, "tailscale", "tailscale_status_failed")
            return
        session_clients = self.active_session_clients()
        for session_id in session_ids:
            client_address = session_clients.get(session_id)
            connection_path, peer_pseudonym = self._classify_session_network_path(
                client_address,
                peers,
            )
            self.observe(
                "tailscale_status",
                playback_session_id=session_id,
                event_source="host",
                observation_kind="measured_kernel",
                sample_window_ms=TAILSCALE_SAMPLE_INTERVAL_MS,
                payload={
                    "state": str(status.get("BackendState") or "unknown")[:64],
                    "tailscale_health": len(status.get("Health") or []),
                    "connection_path": connection_path,
                    "peer_pseudonym": peer_pseudonym,
                },
            )

    def _classify_session_network_path(
        self,
        client_address: str | None,
        peers: dict[str, Any],
    ) -> tuple[str, str | None]:
        if not client_address:
            return "unknown", None
        path_class, peer_pseudonym = pseudonymize_ip(client_address, self.identity_key)
        try:
            parsed = ipaddress.ip_address(client_address)
        except ValueError:
            return "unknown", None
        if parsed.is_loopback:
            return "loopback", peer_pseudonym
        matched_peer = None
        for peer in peers.values():
            if not isinstance(peer, dict) or not peer.get("Active"):
                continue
            addresses = peer.get("TailscaleIPs")
            if not isinstance(addresses, list):
                addresses = peer.get("TailscaleIP")
                addresses = [addresses] if isinstance(addresses, str) else []
            if client_address in addresses:
                matched_peer = peer
                break
        if matched_peer is not None:
            if matched_peer.get("CurAddr"):
                return "tailnet_direct", peer_pseudonym
            if matched_peer.get("PeerRelay"):
                return "peer_relay", peer_pseudonym
            if matched_peer.get("Relay"):
                return "derp", peer_pseudonym
            return "unknown", peer_pseudonym
        tailnet_address = (
            parsed in ipaddress.ip_network("100.64.0.0/10")
            if parsed.version == 4
            else parsed in ipaddress.ip_network("fd7a:115c:a1e0::/48")
        )
        if tailnet_address:
            return "unknown", peer_pseudonym
        if parsed.is_private:
            return "lan_private", peer_pseudonym
        if path_class == "public":
            return "public", peer_pseudonym
        return "unknown", peer_pseudonym

    def _emit_unsupported(self, session_ids: tuple[str, ...], capability: str, reason: str) -> None:
        for session_id in session_ids:
            key = (session_id, capability)
            if key in self._unsupported_emitted:
                self._unsupported_emitted.move_to_end(key)
                continue
            self._unsupported_emitted[key] = None
            while len(self._unsupported_emitted) > MAX_UNSUPPORTED_CAPABILITY_LINKS:
                self._unsupported_emitted.popitem(last=False)
            self.observe(
                f"{capability}_capability",
                playback_session_id=session_id,
                event_source="host",
                observation_kind="unsupported",
                capability_available=False,
                unavailable_reason=reason,
                payload={"available": False, "unavailable_reason": reason},
            )
