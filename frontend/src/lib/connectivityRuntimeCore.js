export const CONNECTION_RUNTIME_CONTRACT = Object.freeze({
  schemaVersion: 1,
  offlineDocumentOopsDelayMs: 60_000,
  offlineRecoveryProbeIntervalMs: 10_000,
  healthProbeTimeoutMs: 5_000,
  publicProbeAttemptTimeoutMs: 2_000,
  publicProbeConfirmationDelayMs: 500,
  publicProbeTrustMaxAgeMs: 7 * 24 * 60 * 60 * 1000,
  publicProbeTrustStorageKey: "elvern_public_probe_trust_v1",
  publicProbeEndpointFailureThreshold: 3,
  publicProbeEndpointCooldownMs: 5 * 60 * 1000,
  navigationHandoffTimeoutMs: 8_000,
  recoveryNavigationTimeoutMs: 15_000,
  recoveryNavigationArmTtlMs: 15_000,
  appShellHeader: "X-Elvern-App-Shell",
  offlineShellHeader: "X-Elvern-Offline-Shell",
  recoveryMessageType: "ELVERN_ARM_RECOVERY_NAVIGATION",
  recoveryMessageAckType: "ELVERN_RECOVERY_NAVIGATION_ARMED",
  familiarRotationMs: 7_000,
  statusWords: Object.freeze([
    "Flibbertigibbeting...",
    "Ruminating...",
    "Conjuring...",
    "Recombobulating...",
    "Scrying...",
    "Divining...",
    "Wayfinding...",
    "Enchanting...",
  ]),
  familiars: Object.freeze(["raven", "wisp", "horned", "gargoyle", "keeper"]),
  classifications: Object.freeze({
    internetOffline: "internet_offline",
    frontendOrVpnUnreachable: "frontend_or_vpn_unreachable",
    backendUnreachable: "backend_unreachable",
    evidenceInsufficient: "connectivity_evidence_insufficient",
    healthy: "healthy",
  }),
  copy: Object.freeze({
    title: "Oops!",
    server: "Seems like the server has been bamboozled, we will fix it as soon as possible.",
    vpn: "Elvern could not be reached, check your VPN connection and try again.",
    offline: "It looks like you're offline. Please check your connection and try again.",
    generic: "Elvern could not be reached at the moment, please check your connection and try again.",
  }),
});


export function createConnectivityRuntime(contract = CONNECTION_RUNTIME_CONTRACT) {
  function deriveConnectivityClassification({
    internetState,
    internetOutageLatched,
    frontendState,
    backendState,
  }) {
    const classifications = contract.classifications;
    if (internetState === "offline" || internetOutageLatched) {
      return classifications.internetOffline;
    }
    if (frontendState === "reachable" && backendState === "unreachable") {
      return classifications.backendUnreachable;
    }
    if (frontendState === "unreachable") {
      return internetState === "online"
        ? classifications.frontendOrVpnUnreachable
        : classifications.evidenceInsufficient;
    }
    if (frontendState === "reachable" && backendState === "reachable") {
      return classifications.healthy;
    }
    return classifications.evidenceInsufficient;
  }

  function getConnectionOopsCopy(classification) {
    const classifications = contract.classifications;
    if (classification === classifications.backendUnreachable) return contract.copy.server;
    if (classification === classifications.internetOffline) return contract.copy.offline;
    if (classification === classifications.frontendOrVpnUnreachable) return contract.copy.vpn;
    return contract.copy.generic;
  }

  function isVerifiedRecoveryEvidence({
    internetState,
    frontendHealthy,
    backendHealthy,
    appShellHealthy,
  }) {
    return internetState === "online"
      && frontendHealthy === true
      && backendHealthy === true
      && appShellHealthy === true;
  }

  function createOfflineDocumentStateMachine({
    documentStartedAt,
    now = () => Date.now(),
  } = {}) {
    const startedAt = Number.isFinite(Number(documentStartedAt))
      ? Number(documentStartedAt)
      : Number(now());
    const oopsDeadlineAt = startedAt + contract.offlineDocumentOopsDelayMs;
    let oopsLatched = false;
    let recovered = false;
    let recoveryInFlight = false;

    function advanceDeadline() {
      if (!recovered && Number(now()) >= oopsDeadlineAt) {
        oopsLatched = true;
      }
      return getSnapshot();
    }

    function getSnapshot() {
      const visibleState = recovered ? "recovered" : (oopsLatched ? "oops_latched" : "connecting");
      return {
        documentStartedAt: startedAt,
        oopsDeadlineAt,
        oopsLatched,
        recovered,
        recoveryInFlight,
        state: recovered ? "recovered" : (recoveryInFlight ? "recovering" : visibleState),
        visibleState,
      };
    }

    function beginRecovery() {
      advanceDeadline();
      if (recovered || recoveryInFlight) return false;
      recoveryInFlight = true;
      return true;
    }

    function finishRecovery(success) {
      if (!recoveryInFlight) return getSnapshot();
      recoveryInFlight = false;
      if (success) recovered = true;
      advanceDeadline();
      return getSnapshot();
    }

    return { advanceDeadline, beginRecovery, finishRecovery, getSnapshot };
  }

  function createPublicProbeCircuit(endpointIds) {
    const states = new Map((endpointIds || []).map((id) => [id, {
      circuitState: "closed",
      consecutiveFailureCount: 0,
      cooldownUntil: 0,
    }]));

    function selectEndpointIds(at) {
      const currentTime = Number(at) || 0;
      const available = [];
      const cooling = [];
      for (const [id, state] of states.entries()) {
        if (state.circuitState === "open" && state.cooldownUntil > currentTime) {
          cooling.push([id, state]);
          continue;
        }
        if (state.circuitState === "open") state.circuitState = "half_open";
        available.push(id);
      }
      if (available.length || !cooling.length) return available;
      cooling.sort((left, right) => left[1].cooldownUntil - right[1].cooldownUntil);
      cooling[0][1].circuitState = "half_open";
      return [cooling[0][0]];
    }

    function commitSuccessfulChain({ failedEndpointIds, successfulEndpointId, at }) {
      const currentTime = Number(at) || 0;
      const successState = states.get(successfulEndpointId);
      if (successState) {
        successState.circuitState = "closed";
        successState.consecutiveFailureCount = 0;
        successState.cooldownUntil = 0;
      }
      for (const id of failedEndpointIds || []) {
        const state = states.get(id);
        if (!state) continue;
        if (state.circuitState === "half_open") {
          state.consecutiveFailureCount = Math.max(
            contract.publicProbeEndpointFailureThreshold,
            state.consecutiveFailureCount + 1,
          );
        } else {
          state.consecutiveFailureCount += 1;
        }
        if (state.consecutiveFailureCount >= contract.publicProbeEndpointFailureThreshold) {
          state.circuitState = "open";
          state.cooldownUntil = currentTime + contract.publicProbeEndpointCooldownMs;
        }
      }
    }

    function snapshot(at = 0) {
      const currentTime = Number(at) || 0;
      return Object.fromEntries([...states.entries()].map(([id, state]) => [id, {
        ...state,
        cooldownRemainingMs: Math.max(0, state.cooldownUntil - currentTime),
      }]));
    }

    return { commitSuccessfulChain, selectEndpointIds, snapshot };
  }

  return {
    contract,
    createOfflineDocumentStateMachine,
    createPublicProbeCircuit,
    deriveConnectivityClassification,
    getConnectionOopsCopy,
    isVerifiedRecoveryEvidence,
  };
}


const sharedRuntime = createConnectivityRuntime();

export const deriveConnectivityClassification = sharedRuntime.deriveConnectivityClassification;
export const getRuntimeConnectionOopsCopy = sharedRuntime.getConnectionOopsCopy;
export const isVerifiedRecoveryEvidence = sharedRuntime.isVerifiedRecoveryEvidence;
export const createOfflineDocumentStateMachine = sharedRuntime.createOfflineDocumentStateMachine;
export const createPublicProbeCircuit = sharedRuntime.createPublicProbeCircuit;


export function buildInlineConnectivityRuntimeSource(globalName = "ElvernConnectivityRuntime") {
  const safeGlobalName = JSON.stringify(String(globalName));
  return `window[${safeGlobalName}] = (${createConnectivityRuntime.toString()})(${JSON.stringify(CONNECTION_RUNTIME_CONTRACT)});`;
}
