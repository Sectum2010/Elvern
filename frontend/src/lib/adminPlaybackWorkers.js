function isFiniteNumber(value) {
  return Number.isFinite(value);
}

function formatDecimalValue(value, { maximumFractionDigits = 1 } = {}) {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  }).format(Number(value));
}

function formatByteValue(value) {
  if (!isFiniteNumber(value) || value <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const decimals = unitIndex === 0
    ? 0
    : Number.isInteger(size)
      ? 0
      : size >= 10
        ? 1
        : 2;
  return `${size.toFixed(decimals)} ${units[unitIndex]}`;
}

export function clampGaugePercent(value) {
  if (!isFiniteNumber(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Number(value)));
}

export function formatWorkerRuntime(value) {
  if (!isFiniteNumber(value) || value < 0) {
    return "—";
  }
  const roundedSeconds = Math.round(value);
  if (roundedSeconds < 60) {
    return `${roundedSeconds}s`;
  }
  const hours = Math.floor(roundedSeconds / 3600);
  const minutes = Math.floor((roundedSeconds % 3600) / 60);
  const seconds = roundedSeconds % 60;
  if (hours > 0) {
    return seconds > 0 ? `${hours}h ${minutes}m ${seconds}s` : `${hours}h ${minutes}m`;
  }
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

export function formatPreparedRanges(ranges) {
  if (!Array.isArray(ranges) || ranges.length === 0) {
    return "";
  }
  return ranges
    .filter((range) => Array.isArray(range) && range.length >= 2 && isFiniteNumber(range[0]) && isFiniteNumber(range[1]))
    .map(([start, end]) => `${Number(start).toFixed(1)}–${Number(end).toFixed(1)}s`)
    .join(", ");
}

export function formatWorkerModeLabel(playbackMode) {
  return String(playbackMode).toLowerCase() === "full" ? "Full" : "Lite";
}

export function formatWorkerProfileLabel(workerOrProfile) {
  const rawProfile = typeof workerOrProfile === "string"
    ? workerOrProfile
    : (
      workerOrProfile?.display_profile_label
      || workerOrProfile?.transcode_profile_key
      || workerOrProfile?.profile
    );
  if (typeof rawProfile !== "string" || !rawProfile.trim()) {
    return "profile unknown";
  }
  const normalized = rawProfile.trim();
  const withoutMobilePrefix = normalized.replace(/^mobile[_-]/i, "");
  if (/^\d{3,4}p$/i.test(withoutMobilePrefix)) {
    return withoutMobilePrefix.toLowerCase();
  }
  return withoutMobilePrefix.replace(/[_-]+/g, " ");
}

export function formatWorkerSourceLabel(sourceKindOrWorker) {
  const rawSource = typeof sourceKindOrWorker === "string"
    ? sourceKindOrWorker
    : (
      sourceKindOrWorker?.source_label
      || sourceKindOrWorker?.source_kind
    );
  const normalized = String(rawSource || "").trim().toLowerCase();
  if (normalized === "cloud") {
    return "Cloud";
  }
  if (normalized === "local") {
    return "Local";
  }
  if (typeof rawSource === "string" && rawSource.trim()) {
    return rawSource.trim();
  }
  return "Unknown source";
}

export function buildWorkerPlaybackMetadataLabel(worker) {
  const backendLabel = typeof worker?.playback_metadata_label === "string"
    ? worker.playback_metadata_label.trim()
    : "";
  if (backendLabel) {
    return backendLabel;
  }
  const surfaceLabel = typeof worker?.playback_surface_label === "string" && worker.playback_surface_label.trim()
    ? worker.playback_surface_label.trim()
    : formatWorkerModeLabel(worker?.playback_mode);
  const deviceLabel = typeof worker?.device_label === "string" && worker.device_label.trim()
    ? worker.device_label.trim()
    : "Unknown device";
  const profileLabel = formatWorkerProfileLabel(worker);
  const sourceLabel = formatWorkerSourceLabel(worker);
  const deviceAndProfile = [deviceLabel, profileLabel]
    .filter((part) => typeof part === "string" && part.trim())
    .join(" ");
  return [surfaceLabel, deviceAndProfile, sourceLabel]
    .filter((part) => typeof part === "string" && part.trim())
    .join(" · ");
}

export function formatCpuCoresValue(value) {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  return `${formatDecimalValue(value)} cores`;
}

export function formatCpuCoresUsage(value, allocatedCores) {
  if (!isFiniteNumber(value) || !isFiniteNumber(allocatedCores) || Number(allocatedCores) <= 0) {
    return "—";
  }
  return `${formatDecimalValue(value)} / ${formatDecimalValue(allocatedCores)} cores`;
}

export function formatCpuGaugeValue(value) {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  return `${formatDecimalValue(value)}%`;
}

export function formatPercentValue(value) {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  return `${formatDecimalValue(value)}%`;
}

export function formatMemoryGaugeValue(value) {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  return formatByteValue(Number(value));
}

export function buildPlaybackWorkerSummaryBubbles(payload) {
  if (!payload || !isFiniteNumber(payload.total_cpu_cores)) {
    return [];
  }
  const bubbles = [
    `${formatDecimalValue(payload.total_cpu_cores, { maximumFractionDigits: 0 })} Detected CPU cores`,
    `CPU upbound ${payload.cpu_upbound_percent ?? payload.cpu_budget_percent}%`,
  ];
  const hasActiveWorkers = isFiniteNumber(payload.active_worker_count) && Number(payload.active_worker_count) > 0;
  if (hasActiveWorkers && isFiniteNumber(payload.route2_cpu_percent_of_total)) {
    bubbles.push(`${formatDecimalValue(payload.route2_cpu_percent_of_total)}% CPU used`);
  }
  if (hasActiveWorkers && isFiniteNumber(payload.route2_memory_percent_of_total)) {
    bubbles.push(`${formatDecimalValue(payload.route2_memory_percent_of_total)}% RAM used`);
  }
  return bubbles;
}

export function buildPlaybackWorkerTerminatePrompt(title) {
  const normalizedTitle = typeof title === "string" && title.trim()
    ? title.trim()
    : "this playback worker";
  return `Are you sure you want to terminate ${normalizedTitle}?`;
}

export function canTerminatePlaybackWorker(workerState) {
  const normalizedState = String(workerState || "").trim().toLowerCase();
  return normalizedState === "running" || normalizedState === "queued";
}

const STATUS_TONE_CLASSES = new Set(["success", "info", "warning", "danger", "neutral"]);

const RAW_WORKER_STATE_DISPLAY = {
  completed: { status: "complete", label: "Complete", tone: "success" },
  failed: { status: "failed", label: "Failed", tone: "danger" },
  interrupted: { status: "failed", label: "Failed", tone: "danger" },
  queued: { status: "waiting", label: "Waiting", tone: "info" },
  running: { status: "running", label: "Running", tone: "success" },
  stopped: { status: "stopped", label: "Stopped", tone: "neutral" },
  stopping: { status: "stopping", label: "Stopping", tone: "warning" },
};

export function buildWorkerDisplayStatus(worker) {
  const backendLabel = typeof worker?.display_status_label === "string"
    ? worker.display_status_label.trim()
    : "";
  const backendTone = typeof worker?.display_status_tone === "string"
    ? worker.display_status_tone.trim().toLowerCase()
    : "";
  const backendStatus = typeof worker?.display_status === "string"
    ? worker.display_status.trim().toLowerCase()
    : "";
  if (backendLabel) {
    return {
      status: backendStatus || backendLabel.toLowerCase().replace(/\s+/g, "_"),
      label: backendLabel,
      tone: STATUS_TONE_CLASSES.has(backendTone) ? backendTone : "neutral",
      reason: typeof worker?.display_status_reason === "string" ? worker.display_status_reason : "",
    };
  }
  const rawState = String(worker?.state || "unknown").trim().toLowerCase();
  const fallback = RAW_WORKER_STATE_DISPLAY[rawState] || {
    status: rawState || "unknown",
    label: (rawState || "unknown").replace(/_/g, " "),
    tone: "neutral",
  };
  return {
    ...fallback,
    reason: "",
  };
}

export function workerStatusToneClass(displayStatus) {
  const tone = typeof displayStatus?.tone === "string"
    ? displayStatus.tone.trim().toLowerCase()
    : "";
  return `admin-worker-state--${STATUS_TONE_CLASSES.has(tone) ? tone : "neutral"}`;
}

export function shouldShowWorkerCleanupNotice(worker, { delayThresholdSeconds = 30 } = {}) {
  if (!worker?.stop_requested) {
    return false;
  }
  if (worker.cleanup_delayed === true) {
    return true;
  }
  const cleanupDelaySeconds = Number(worker.cleanup_delay_seconds);
  return Number.isFinite(cleanupDelaySeconds) && cleanupDelaySeconds >= delayThresholdSeconds;
}

export function shortenDiagnosticId(value, prefixLength = 6, suffixLength = 4) {
  if (typeof value !== "string" || !value.trim()) {
    return "";
  }
  const normalized = value.trim();
  if (normalized.length <= prefixLength + suffixLength + 1) {
    return normalized;
  }
  return `${normalized.slice(0, prefixLength)}…${normalized.slice(-suffixLength)}`;
}

export function summarizeWorkerGroup(group) {
  const items = Array.isArray(group?.items) ? group.items : [];
  const nativeItems = Array.isArray(group?.nativeItems) ? group.nativeItems : [];
  const cpuCoresValues = items
    .map((item) => (isFiniteNumber(item?.cpu_cores_used) ? Number(item.cpu_cores_used) : null))
    .filter((value) => value != null);
  const memoryBytesValues = items
    .map((item) => (isFiniteNumber(item?.memory_bytes) ? Number(item.memory_bytes) : null))
    .filter((value) => value != null);
  const allocatedCpuCores = isFiniteNumber(group?.allocated_cpu_cores)
    ? Number(group.allocated_cpu_cores)
    : isFiniteNumber(group?.allocated_budget_cores)
      ? Number(group.allocated_budget_cores)
      : null;
  const cpuCoresUsed = isFiniteNumber(group?.cpu_cores_used)
    ? Number(group.cpu_cores_used)
    : (
      cpuCoresValues.length > 0
        ? cpuCoresValues.reduce((sum, value) => sum + value, 0)
        : null
    );
  const cpuPercentOfUserLimit = isFiniteNumber(group?.cpu_percent_of_user_limit)
    ? Number(group.cpu_percent_of_user_limit)
    : (
      cpuCoresUsed != null && allocatedCpuCores != null && allocatedCpuCores > 0
        ? (cpuCoresUsed / allocatedCpuCores) * 100
        : null
    );
  const memoryBytes = isFiniteNumber(group?.memory_bytes)
    ? Number(group.memory_bytes)
    : (
      memoryBytesValues.length > 0
        ? memoryBytesValues.reduce((sum, value) => sum + value, 0)
        : null
    );
  const memoryGaugePercent = isFiniteNumber(group?.memory_percent_of_total)
    ? clampGaugePercent(group.memory_percent_of_total)
    : null;

  return {
    ...group,
    items,
    totalWorkers: isFiniteNumber(group?.total_workers) ? Number(group.total_workers) : items.length,
    allocatedCpuCores,
    cpuCoresUsed,
    cpuGaugePercent: cpuPercentOfUserLimit == null ? null : clampGaugePercent(cpuPercentOfUserLimit),
    cpuPercentOfUserLimit,
    memoryBytes,
    memoryGaugePercent,
    hasRunningWorkers: Number(group?.running_workers || 0) > 0,
    nativeItems,
    totalNativePlaybacks: isFiniteNumber(group?.total_native_playbacks)
      ? Number(group.total_native_playbacks)
      : nativeItems.length,
    runningNativePlaybacks: isFiniteNumber(group?.running_native_playbacks)
      ? Number(group.running_native_playbacks)
      : nativeItems.filter((item) => String(item?.display_status || "").toLowerCase() === "running").length,
    idleNativePlaybacks: isFiniteNumber(group?.idle_native_playbacks)
      ? Number(group.idle_native_playbacks)
      : nativeItems.filter((item) => String(item?.display_status || "").toLowerCase() === "idle").length,
    totalPlaybackItems: (isFiniteNumber(group?.total_workers) ? Number(group.total_workers) : items.length)
      + (isFiniteNumber(group?.total_native_playbacks) ? Number(group.total_native_playbacks) : nativeItems.length),
  };
}

export function buildPlaybackWorkersByUserId(payload) {
  const map = new Map();
  const groups = Array.isArray(payload?.workers_by_user) ? payload.workers_by_user : [];
  for (const group of groups) {
    if (!isFiniteNumber(group?.user_id)) {
      continue;
    }
    map.set(Number(group.user_id), summarizeWorkerGroup(group));
  }
  const nativeGroups = Array.isArray(payload?.native_playbacks_by_user) ? payload.native_playbacks_by_user : [];
  for (const nativeGroup of nativeGroups) {
    if (!isFiniteNumber(nativeGroup?.user_id)) {
      continue;
    }
    const userId = Number(nativeGroup.user_id);
    const existing = map.get(userId) || {
      user_id: userId,
      username: nativeGroup.username,
      allocated_cpu_cores: 0,
      allocated_budget_cores: 0,
      running_workers: 0,
      queued_workers: 0,
      total_workers: 0,
      items: [],
    };
    map.set(
      userId,
      summarizeWorkerGroup({
        ...existing,
        username: existing.username || nativeGroup.username,
        nativeItems: Array.isArray(nativeGroup.items) ? nativeGroup.items : [],
        total_native_playbacks: nativeGroup.total_native_playbacks,
        running_native_playbacks: nativeGroup.running_native_playbacks,
        idle_native_playbacks: nativeGroup.idle_native_playbacks,
      }),
    );
  }
  return map;
}
