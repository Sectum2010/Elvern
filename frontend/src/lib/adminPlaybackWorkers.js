function isFiniteNumber(value) {
  return Number.isFinite(value);
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

export function formatCpuGaugeValue(value) {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  return `${Math.round(Number(value))}%`;
}

export function formatMemoryGaugeValue(value) {
  if (!isFiniteNumber(value)) {
    return "—";
  }
  return formatByteValue(Number(value));
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
  const cpuValues = items
    .map((item) => (isFiniteNumber(item?.cpu_percent) ? Number(item.cpu_percent) : null))
    .filter((value) => value != null);
  const memoryBytesValues = items
    .map((item) => (isFiniteNumber(item?.memory_bytes) ? Number(item.memory_bytes) : null))
    .filter((value) => value != null);
  const memoryPercentValues = items
    .map((item) => (isFiniteNumber(item?.memory_percent) ? Number(item.memory_percent) : null))
    .filter((value) => value != null);

  const cpuPercent = cpuValues.length > 0
    ? cpuValues.reduce((sum, value) => sum + value, 0)
    : null;
  const memoryBytes = memoryBytesValues.length > 0
    ? memoryBytesValues.reduce((sum, value) => sum + value, 0)
    : null;
  const memoryGaugePercent = memoryPercentValues.length > 0
    ? clampGaugePercent(memoryPercentValues.reduce((sum, value) => sum + value, 0))
    : null;

  return {
    ...group,
    items,
    totalWorkers: items.length,
    cpuPercent,
    cpuGaugePercent: cpuPercent == null ? null : clampGaugePercent(cpuPercent),
    memoryBytes,
    memoryGaugePercent,
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
