import { PLAYBACK_DIAGNOSTICS_CLOCK_EXCHANGE_SAMPLES } from "./constants";

export function estimateTimerResolution({ samples = 64, now = () => performance.now() } = {}) {
  const deltas = [];
  let previous = now();
  for (let index = 0; index < samples; index += 1) {
    const current = now();
    const delta = current - previous;
    if (delta > 0) deltas.push(delta);
    previous = current;
  }
  if (!deltas.length) return null;
  return Math.min(...deltas) * 1_000;
}

export function estimateClockOffset(samples, { timerResolutionUs = 0, previous = null } = {}) {
  const normalized = samples.map((sample) => {
    const clientSendNs = BigInt(Math.round(sample.clientSendWallMs * 1_000_000));
    const monotonicElapsedNs = BigInt(Math.max(
      0,
      Math.round((sample.clientReceiveMonotonicUs - sample.clientSendMonotonicUs) * 1_000),
    ));
    const clientReceiveNs = clientSendNs + monotonicElapsedNs;
    const serverReceiveNs = BigInt(sample.serverReceiveWallNs);
    const serverSendNs = BigInt(sample.serverSendWallNs);
    const serverProcessingNs = serverSendNs > serverReceiveNs
      ? serverSendNs - serverReceiveNs
      : 0n;
    const rttNs = monotonicElapsedNs > serverProcessingNs
      ? monotonicElapsedNs - serverProcessingNs
      : 0n;
    const clientMidpoint = (clientSendNs + clientReceiveNs) / 2n;
    const serverMidpoint = (serverReceiveNs + serverSendNs) / 2n;
    return {
      offsetNs: serverMidpoint - clientMidpoint,
      rttNs,
      clientSendWallMs: sample.clientSendWallMs,
      clientSendMonotonicUs: sample.clientSendMonotonicUs,
    };
  }).sort((left, right) => (left.rttNs < right.rttNs ? -1 : left.rttNs > right.rttNs ? 1 : 0));
  if (!normalized.length) {
    throw new TypeError("At least one clock sample is required");
  }
  const selected = normalized.slice(0, Math.max(1, Math.ceil(normalized.length / 2)));
  const offsets = selected.map((sample) => sample.offsetNs).sort((left, right) => (
    left < right ? -1 : left > right ? 1 : 0
  ));
  const offsetNs = offsets[Math.floor(offsets.length / 2)];
  const minimumRttNs = selected[0].rttNs;
  const spreadNs = offsets.at(-1) - offsets[0];
  const timerResolutionNs = BigInt(Math.max(0, Math.round(Number(timerResolutionUs) * 1_000)));
  const transportUncertainty = minimumRttNs / 2n > spreadNs / 2n
    ? minimumRttNs / 2n
    : spreadNs / 2n;
  const uncertaintyNs = transportUncertainty + timerResolutionNs;
  const anchor = selected[0];
  let driftPpm = null;
  if (previous?.clock_offset_ns != null && previous?.client_anchor_monotonic_us != null) {
    const elapsedUs = Number(anchor.clientSendMonotonicUs) - Number(previous.client_anchor_monotonic_us);
    if (elapsedUs > 0) {
      driftPpm = Number(offsetNs - BigInt(previous.clock_offset_ns)) / (elapsedUs * 1_000) * 1_000_000;
      if (!Number.isFinite(driftPpm)) driftPpm = null;
    }
  }
  return {
    clock_offset_ns: offsetNs.toString(),
    network_rtt_ns: minimumRttNs.toString(),
    clock_uncertainty_ns: uncertaintyNs.toString(),
    selected_sample_rtt_ns: minimumRttNs.toString(),
    offset_spread_ns: spreadNs.toString(),
    client_timer_resolution_us: Number(timerResolutionUs) || null,
    client_anchor_wall_ms: anchor.clientSendWallMs,
    client_anchor_monotonic_us: anchor.clientSendMonotonicUs,
    observed_drift_ppm: driftPpm,
    clock_generation: Math.max(0, Number(previous?.clock_generation) || 0) + 1,
    clock_valid: true,
    clock_invalid_reason: null,
    sample_count: normalized.length,
    algorithm_version: "monotonic-rtt-median-offset-v2",
  };
}

export async function synchronizeDiagnosticClock(exchange, {
  sampleCount = PLAYBACK_DIAGNOSTICS_CLOCK_EXCHANGE_SAMPLES,
  timerResolutionUs = 0,
  previous = null,
  wallStepThresholdMs = 1_000,
  maxAbsoluteDriftPpm = 10_000,
} = {}) {
  const samples = [];
  for (let index = 0; index < sampleCount; index += 1) {
    const clientSendWallMs = Date.now();
    const clientSendMonotonicUs = performance.now() * 1_000;
    const response = await exchange({
      sampleId: `clock-${index}-${clientSendWallMs}`,
      clientSendWallMs,
      clientSendMonotonicUs,
    });
    const clientReceiveMonotonicUs = performance.now() * 1_000;
    const clientReceiveWallMs = Date.now();
    const monotonicElapsedMs = (clientReceiveMonotonicUs - clientSendMonotonicUs) / 1_000;
    const wallElapsedMs = clientReceiveWallMs - clientSendWallMs;
    samples.push({
      clientSendWallMs,
      clientSendMonotonicUs,
      clientReceiveMonotonicUs,
      clientReceiveWallMs,
      wallStepDetected: Math.abs(wallElapsedMs - monotonicElapsedMs) > wallStepThresholdMs,
      serverReceiveWallNs: response.server_receive_wall_time_ns,
      serverSendWallNs: response.server_send_wall_time_ns,
    });
  }
  const estimate = estimateClockOffset(samples, { timerResolutionUs, previous });
  const clockStepDetected = samples.some((sample) => sample.wallStepDetected);
  const implausibleDrift = estimate.observed_drift_ppm != null
    && Math.abs(estimate.observed_drift_ppm) > maxAbsoluteDriftPpm;
  if (clockStepDetected || implausibleDrift) {
    return {
      ...estimate,
      aligned_wall_time_ns: null,
      clock_offset_ns: null,
      clock_valid: false,
      clock_invalid_reason: clockStepDetected ? "wall_clock_step" : "implausible_drift",
      clock_step_detected: clockStepDetected,
    };
  }
  return {
    ...estimate,
    clock_step_detected: false,
  };
}
