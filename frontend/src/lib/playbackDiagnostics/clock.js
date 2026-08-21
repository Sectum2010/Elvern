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

export function estimateClockOffset(samples) {
  const normalized = samples.map((sample) => {
    const clientSendNs = BigInt(Math.round(sample.clientSendWallMs * 1_000_000));
    const clientReceiveNs = BigInt(Math.round(sample.clientReceiveWallMs * 1_000_000));
    const serverReceiveNs = BigInt(sample.serverReceiveWallNs);
    const serverSendNs = BigInt(sample.serverSendWallNs);
    const serverProcessingNs = serverSendNs > serverReceiveNs
      ? serverSendNs - serverReceiveNs
      : 0n;
    const totalNs = clientReceiveNs > clientSendNs ? clientReceiveNs - clientSendNs : 0n;
    const rttNs = totalNs > serverProcessingNs ? totalNs - serverProcessingNs : 0n;
    const clientMidpoint = (clientSendNs + clientReceiveNs) / 2n;
    const serverMidpoint = (serverReceiveNs + serverSendNs) / 2n;
    return { offsetNs: serverMidpoint - clientMidpoint, rttNs };
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
  const uncertaintyNs = minimumRttNs / 2n > spreadNs / 2n
    ? minimumRttNs / 2n
    : spreadNs / 2n;
  return {
    clock_offset_ns: offsetNs.toString(),
    network_rtt_ns: minimumRttNs.toString(),
    clock_uncertainty_ns: uncertaintyNs.toString(),
    sample_count: normalized.length,
    algorithm_version: "min-rtt-median-offset-v1",
  };
}

export async function synchronizeDiagnosticClock(exchange, {
  sampleCount = PLAYBACK_DIAGNOSTICS_CLOCK_EXCHANGE_SAMPLES,
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
    samples.push({
      clientSendWallMs,
      clientReceiveWallMs: Date.now(),
      serverReceiveWallNs: response.server_receive_wall_time_ns,
      serverSendWallNs: response.server_send_wall_time_ns,
    });
  }
  return estimateClockOffset(samples);
}
