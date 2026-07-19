import {
  CONNECTION_RUNTIME_CONTRACT,
  createPublicProbeCircuit,
} from "./connectivityRuntimeCore.js";


export const PUBLIC_PROBE_ATTEMPT_TIMEOUT_MS = CONNECTION_RUNTIME_CONTRACT.publicProbeAttemptTimeoutMs;
export const PUBLIC_PROBE_CONFIRMATION_DELAY_MS = CONNECTION_RUNTIME_CONTRACT.publicProbeConfirmationDelayMs;
export const PUBLIC_PROBE_TRUST_MAX_AGE_MS = CONNECTION_RUNTIME_CONTRACT.publicProbeTrustMaxAgeMs;
export const PUBLIC_PROBE_TRUST_STORAGE_KEY = CONNECTION_RUNTIME_CONTRACT.publicProbeTrustStorageKey;
export const PUBLIC_PROBE_MAX_ENDPOINTS = 8;
export const PUBLIC_PROBE_ENDPOINT_FAILURE_THRESHOLD = CONNECTION_RUNTIME_CONTRACT.publicProbeEndpointFailureThreshold;
export const PUBLIC_PROBE_ENDPOINT_COOLDOWN_MS = CONNECTION_RUNTIME_CONTRACT.publicProbeEndpointCooldownMs;

const PUBLIC_PROBE_TRUST_SCHEMA_VERSION = 1;
const OPERATOR_EXPECTED_STATUSES = Object.freeze([200, 204]);


function freezeProbe({ id, url, expectedStatuses, order }) {
  return Object.freeze({
    id,
    url,
    expectedStatuses: Object.freeze([...expectedStatuses]),
    order,
    perAttemptTimeoutMs: PUBLIC_PROBE_ATTEMPT_TIMEOUT_MS,
    consecutiveFailureCount: 0,
    cooldownUntil: 0,
  });
}


export const DEFAULT_PUBLIC_CONNECTIVITY_PROBES = Object.freeze([
  freezeProbe({
    id: "cloudflare-trace",
    url: "https://www.cloudflare.com/cdn-cgi/trace",
    expectedStatuses: [200],
    order: 0,
  }),
  freezeProbe({
    id: "ipify-api64",
    url: "https://api64.ipify.org/",
    expectedStatuses: [200],
    order: 1,
  }),
  freezeProbe({
    id: "httpbin-204",
    url: "https://httpbin.org/status/204",
    expectedStatuses: [204],
    order: 2,
  }),
]);


function normalizeProbeUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  try {
    const parsed = new URL(raw);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
  } catch {
    return "";
  }
}


function normalizeExpectedStatuses(value) {
  const values = Array.isArray(value) ? value : OPERATOR_EXPECTED_STATUSES;
  const statuses = [...new Set(values
    .map((status) => Number(status))
    .filter((status) => Number.isInteger(status) && status >= 100 && status <= 599))];
  return statuses.length ? statuses : [...OPERATOR_EXPECTED_STATUSES];
}


function parsePluralValue(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return null;
  }
  if (raw.toLowerCase() === "none") {
    return [];
  }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return raw.split(/[\n,]/).map((entry) => entry.trim()).filter(Boolean);
  }
}


function buildOperatorRegistry(entries) {
  const seen = new Set();
  const probes = [];
  for (const entry of entries) {
    if (probes.length >= PUBLIC_PROBE_MAX_ENDPOINTS) {
      break;
    }
    const url = normalizeProbeUrl(typeof entry === "string" ? entry : entry?.url);
    if (!url || seen.has(url)) {
      continue;
    }
    seen.add(url);
    probes.push(freezeProbe({
      id: `operator-${probes.length + 1}`,
      url,
      expectedStatuses: normalizeExpectedStatuses(entry?.expectedStatuses),
      order: probes.length,
    }));
  }
  return Object.freeze(probes);
}


export function resolvePublicConnectivityProbeRegistry({
  pluralValue = import.meta.env?.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URLS,
  singularValue = import.meta.env?.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL,
} = {}) {
  const pluralEntries = parsePluralValue(pluralValue);
  if (pluralEntries !== null) {
    return buildOperatorRegistry(pluralEntries);
  }
  const singular = String(singularValue || "").trim();
  if (singular) {
    if (singular.toLowerCase() === "none") {
      return Object.freeze([]);
    }
    return buildOperatorRegistry([singular]);
  }
  return DEFAULT_PUBLIC_CONNECTIVITY_PROBES;
}


export function hashPublicConnectivityProbeRegistry(probes) {
  const serialized = JSON.stringify((probes || []).map((probe) => [
    probe.id,
    probe.url,
    [...(probe.expectedStatuses || [])],
  ]));
  let hash = 0xcbf29ce484222325n;
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= BigInt(serialized.charCodeAt(index));
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return hash.toString(16).padStart(16, "0");
}


function safeStorage(storage) {
  return storage && typeof storage.getItem === "function" ? storage : null;
}


function readTrustRecord({ storage, endpointListHash, now }) {
  const target = safeStorage(storage);
  if (!target) {
    return { state: "unverified", trusted: false };
  }
  try {
    const parsed = JSON.parse(target.getItem(PUBLIC_PROBE_TRUST_STORAGE_KEY) || "null");
    const lastSuccessfulAt = Number(parsed?.last_successful_at);
    const matches = parsed?.schema_version === PUBLIC_PROBE_TRUST_SCHEMA_VERSION
      && parsed?.endpoint_list_hash === endpointListHash
      && Number.isFinite(lastSuccessfulAt)
      && lastSuccessfulAt > 0
      && lastSuccessfulAt <= now
      && now - lastSuccessfulAt <= PUBLIC_PROBE_TRUST_MAX_AGE_MS;
    if (!matches) {
      if (parsed) {
        target.removeItem(PUBLIC_PROBE_TRUST_STORAGE_KEY);
      }
      return {
        state: parsed && parsed?.endpoint_list_hash === endpointListHash ? "stale" : "unverified",
        trusted: false,
      };
    }
    return {
      state: "trusted",
      trusted: true,
      lastSuccessfulAt,
      lastSuccessfulEndpointId: typeof parsed.last_successful_endpoint_id === "string"
        ? parsed.last_successful_endpoint_id
        : null,
    };
  } catch {
    try {
      target.removeItem(PUBLIC_PROBE_TRUST_STORAGE_KEY);
    } catch {
      // Storage is optional evidence only.
    }
    return { state: "unverified", trusted: false };
  }
}


function writeTrustRecord({ storage, endpointListHash, endpointId, now }) {
  const target = safeStorage(storage);
  if (!target) {
    return;
  }
  try {
    target.setItem(PUBLIC_PROBE_TRUST_STORAGE_KEY, JSON.stringify({
      schema_version: PUBLIC_PROBE_TRUST_SCHEMA_VERSION,
      endpoint_list_hash: endpointListHash,
      last_successful_at: now,
      last_successful_endpoint_id: endpointId || null,
    }));
  } catch {
    // Private browsing and storage policies must not block connectivity checks.
  }
}


function delay(ms, setTimeoutImpl) {
  if (ms <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => setTimeoutImpl(resolve, ms));
}


export function createPublicConnectivityProbeRunner({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  probes = resolvePublicConnectivityProbeRegistry(),
  storage = globalThis.window?.localStorage,
  now = () => Date.now(),
  setTimeoutImpl = globalThis.setTimeout?.bind(globalThis),
  clearTimeoutImpl = globalThis.clearTimeout?.bind(globalThis),
  debug = null,
} = {}) {
  const registry = Object.freeze([...(probes || [])]);
  const endpointListHash = hashPublicConnectivityProbeRegistry(registry);
  const circuit = createPublicProbeCircuit(registry.map((probe) => probe.id));
  const activeControllers = new Set();
  let abortGeneration = 0;

  async function attempt(probe) {
    const abortController = new AbortController();
    activeControllers.add(abortController);
    const startedAt = now();
    const timeoutId = setTimeoutImpl?.(
      () => abortController.abort(),
      probe.perAttemptTimeoutMs || PUBLIC_PROBE_ATTEMPT_TIMEOUT_MS,
    ) || 0;
    let status = 0;
    let success = false;
    try {
      const response = await fetchImpl(probe.url, {
        method: "GET",
        credentials: "omit",
        cache: "no-store",
        referrerPolicy: "no-referrer",
        mode: "cors",
        signal: abortController.signal,
      });
      status = Number(response?.status) || 0;
      success = Boolean(response?.ok) && probe.expectedStatuses.includes(status);
    } catch {
      success = false;
    } finally {
      if (timeoutId) {
        clearTimeoutImpl?.(timeoutId);
      }
      activeControllers.delete(abortController);
    }
    debug?.({
      endpointId: probe.id,
      success,
      status,
      elapsedMs: Math.max(0, now() - startedAt),
    });
    return { endpointId: probe.id, success, status };
  }

  async function probeChain() {
    const selectedIds = new Set(circuit.selectEndpointIds(now()));
    const failedEndpointIds = [];
    for (const probe of registry) {
      if (!selectedIds.has(probe.id)) {
        continue;
      }
      const result = await attempt(probe);
      if (result.success) {
        const succeededAt = now();
        circuit.commitSuccessfulChain({
          failedEndpointIds,
          successfulEndpointId: result.endpointId,
          at: succeededAt,
        });
        writeTrustRecord({
          storage,
          endpointListHash,
          endpointId: result.endpointId,
          now: succeededAt,
        });
        return { reachable: true, endpointId: result.endpointId };
      }
      failedEndpointIds.push(result.endpointId);
    }
    return { reachable: false, endpointId: null };
  }

  async function probeConfirmed({ confirmationDelayMs = PUBLIC_PROBE_CONFIRMATION_DELAY_MS } = {}) {
    const generation = abortGeneration;
    const trust = readTrustRecord({ storage, endpointListHash, now: now() });
    if (!registry.length || typeof fetchImpl !== "function") {
      return {
        internetState: "unknown",
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.probesDisabled,
        trusted: false,
        trustState: "unverified",
        endpointId: null,
        rounds: 0,
      };
    }
    const first = await probeChain();
    if (generation !== abortGeneration) {
      return {
        internetState: "unknown",
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.aborted,
        trusted: trust.trusted,
        trustState: trust.state,
        endpointId: null,
        rounds: 1,
      };
    }
    if (first.reachable) {
      return {
        internetState: "online",
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.endpointSuccess,
        trusted: true,
        trustState: "trusted",
        endpointId: first.endpointId,
        rounds: 1,
      };
    }
    await delay(Math.max(0, Number(confirmationDelayMs) || 0), setTimeoutImpl);
    if (generation !== abortGeneration) {
      return {
        internetState: "unknown",
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.aborted,
        trusted: trust.trusted,
        trustState: trust.state,
        endpointId: null,
        rounds: 1,
      };
    }
    const second = await probeChain();
    if (generation !== abortGeneration) {
      return {
        internetState: "unknown",
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.aborted,
        trusted: trust.trusted,
        trustState: trust.state,
        endpointId: null,
        rounds: 2,
      };
    }
    if (second.reachable) {
      return {
        internetState: "online",
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.endpointSuccess,
        trusted: true,
        trustState: "trusted",
        endpointId: second.endpointId,
        rounds: 2,
      };
    }
    return {
      internetState: trust.trusted ? "offline" : "unknown",
      publicEvidenceReason: trust.trusted
        ? CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.probeFailureTrusted
        : CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.probeFailureUnverified,
      trusted: trust.trusted,
      trustState: trust.state,
      endpointId: null,
      rounds: 2,
    };
  }

  return {
    abort() {
      abortGeneration += 1;
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
    },
    getEndpointListHash: () => endpointListHash,
    getEndpointStates: () => circuit.snapshot(now()),
    getTrustState: () => readTrustRecord({ storage, endpointListHash, now: now() }),
    probeChain,
    probeConfirmed,
    probes: registry,
  };
}
