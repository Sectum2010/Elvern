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
  return map;
}
