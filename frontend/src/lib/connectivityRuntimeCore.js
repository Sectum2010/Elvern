export const CONNECTION_RUNTIME_CONTRACT = Object.freeze({
  schemaVersion: 1,
  offlineDocumentOopsDelayMs: 60_000,
  offlineRecoveryProbeIntervalMs: 10_000,
  healthProbeTimeoutMs: 5_000,
  publicProbeAttemptTimeoutMs: 2_000,
  publicProbeConfirmationDelayMs: 500,
  fastOopsConfirmationDelayMs: 750,
  publicProbeTrustMaxAgeMs: 7 * 24 * 60 * 60 * 1000,
  publicProbeTrustStorageKey: "elvern_public_probe_trust_v1",
  publicProbeEndpointFailureThreshold: 3,
  publicProbeEndpointCooldownMs: 5 * 60 * 1000,
  navigationHandoffTimeoutMs: 8_000,
  recoveryNavigationTimeoutMs: 15_000,
  recoveryNavigationArmTtlMs: 15_000,
  recoveryNavigationArmMaxRecords: 32,
  recoveryNavigationArmDatabaseName: "elvern-service-worker-state-v1",
  recoveryNavigationArmStoreName: "recovery_arms",
  manualServiceRecoveryStorageKey: "elvern-manual-service-recovery-v1",
  appShellHeader: "X-Elvern-App-Shell",
  offlineShellHeader: "X-Elvern-Offline-Shell",
  recoveryMessageType: "ELVERN_ARM_RECOVERY_NAVIGATION",
  recoveryMessageAckType: "ELVERN_RECOVERY_NAVIGATION_ARMED",
  recoveryTriggers: Object.freeze({
    automatic: "automatic",
    manualRetry: "manual_retry",
    onlineEvent: "online_event",
    visibilityReturn: "visibility_return",
  }),
  recoveryModes: Object.freeze({
    verifiedPublic: "verified_public",
    manualServiceOnly: "manual_service_only",
  }),
  publicEvidenceReasons: Object.freeze({
    endpointSuccess: "endpoint_success",
    browserExplicitOffline: "browser_explicit_offline",
    probeFailureTrusted: "probe_failure_trusted",
    probeFailureUnverified: "probe_failure_unverified",
    probesDisabled: "probes_disabled",
    aborted: "aborted",
  }),
  healthEvidenceReasons: Object.freeze({
    httpSuccess: "http_success",
    httpUnhealthy: "http_unhealthy",
    networkError: "network_error",
    timeout: "timeout",
    aborted: "aborted",
    markerMissing: "marker_missing",
  }),
  fastOopsReasons: Object.freeze({
    deadlineTimeout: "deadline_timeout",
    browserOffline: "conclusive_browser_offline",
    frontendUnreachable: "conclusive_frontend_unreachable",
    backendUnreachable: "conclusive_backend_unreachable",
    trustedPublicFailure: "conclusive_trusted_public_failure",
  }),
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

  function deriveFastOopsCandidate({
    navigatorOnline = true,
    publicEvidence,
    frontendHealth,
    backendHealth,
    localhostServicesHealthy = false,
  } = {}) {
    const classifications = contract.classifications;
    const reasons = contract.publicEvidenceReasons;
    const healthReasons = contract.healthEvidenceReasons;
    const fastReasons = contract.fastOopsReasons;
    if (navigatorOnline === false || publicEvidence?.publicEvidenceReason === reasons.browserExplicitOffline) {
      return {
        classification: classifications.internetOffline,
        evidenceReason: fastReasons.browserOffline,
      };
    }
    if (
      publicEvidence?.internetState === "online"
      && frontendHealth?.reason === healthReasons.networkError
    ) {
      return {
        classification: classifications.frontendOrVpnUnreachable,
        evidenceReason: fastReasons.frontendUnreachable,
      };
    }
    if (
      frontendHealth?.reason === healthReasons.httpSuccess
      && backendHealth?.reason === healthReasons.httpUnhealthy
      && Number(backendHealth.status) >= 500
      && Number(backendHealth.status) <= 599
      && backendHealth.maintenance !== true
    ) {
      return {
        classification: classifications.backendUnreachable,
        evidenceReason: fastReasons.backendUnreachable,
      };
    }
    if (
      publicEvidence?.internetState === "offline"
      && publicEvidence?.publicEvidenceReason === reasons.probeFailureTrusted
      && frontendHealth?.reason === healthReasons.networkError
      && !localhostServicesHealthy
    ) {
      return {
        classification: classifications.internetOffline,
        evidenceReason: fastReasons.trustedPublicFailure,
      };
    }
    return null;
  }

  function matchesFastOopsCandidates(first, second) {
    return Boolean(
      first
      && second
      && first.classification === second.classification
      && first.evidenceReason === second.evidenceReason
    );
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

  function getRecoveryDecision({
    trigger,
    internetState,
    publicEvidenceReason,
    frontendHealthy,
    backendHealthy,
    appShellHealthy,
  }) {
    const servicesVerified = frontendHealthy === true
      && backendHealthy === true
      && appShellHealthy === true;
    if (!servicesVerified) {
      return { accepted: false, recoveryMode: null };
    }
    const reasons = contract.publicEvidenceReasons;
    if (publicEvidenceReason === reasons.browserExplicitOffline) {
      return { accepted: false, recoveryMode: null };
    }
    if (internetState === "online" && publicEvidenceReason === reasons.endpointSuccess) {
      return { accepted: true, recoveryMode: contract.recoveryModes.verifiedPublic };
    }
    const manualReasonAllowed = new Set([
      reasons.probeFailureTrusted,
      reasons.probeFailureUnverified,
      reasons.probesDisabled,
    ]).has(publicEvidenceReason);
    if (trigger === contract.recoveryTriggers.manualRetry && manualReasonAllowed) {
      return { accepted: true, recoveryMode: contract.recoveryModes.manualServiceOnly };
    }
    return { accepted: false, recoveryMode: null };
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
    let oopsClassification = null;
    let oopsEvidenceReason = null;
    let oopsConfidence = null;
    let recovered = false;
    let recoveryInFlight = false;

    function advanceDeadline() {
      if (!recovered && Number(now()) >= oopsDeadlineAt) {
        latchOops({
          evidenceReason: contract.fastOopsReasons.deadlineTimeout,
          confidence: "deadline",
        });
      }
      return getSnapshot();
    }

    function getSnapshot() {
      const visibleState = recovered ? "recovered" : (oopsLatched ? "oops_latched" : "connecting");
      return {
        documentStartedAt: startedAt,
        oopsDeadlineAt,
        oopsLatched,
        oopsClassification,
        oopsEvidenceReason,
        oopsConfidence,
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

    function latchOops({ classification = null, evidenceReason = null, confidence = null } = {}) {
      if (recovered || oopsLatched) return getSnapshot();
      oopsLatched = true;
      oopsClassification = classification;
      oopsEvidenceReason = evidenceReason;
      oopsConfidence = confidence;
      return getSnapshot();
    }

    return { advanceDeadline, beginRecovery, finishRecovery, getSnapshot, latchOops };
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
    deriveFastOopsCandidate,
    deriveConnectivityClassification,
    getRecoveryDecision,
    getConnectionOopsCopy,
    isVerifiedRecoveryEvidence,
    matchesFastOopsCandidates,
  };
}


const sharedRuntime = createConnectivityRuntime();

export const deriveConnectivityClassification = sharedRuntime.deriveConnectivityClassification;
export const deriveFastOopsCandidate = sharedRuntime.deriveFastOopsCandidate;
export const getRuntimeConnectionOopsCopy = sharedRuntime.getConnectionOopsCopy;
export const getRecoveryDecision = sharedRuntime.getRecoveryDecision;
export const isVerifiedRecoveryEvidence = sharedRuntime.isVerifiedRecoveryEvidence;
export const matchesFastOopsCandidates = sharedRuntime.matchesFastOopsCandidates;
export const createOfflineDocumentStateMachine = sharedRuntime.createOfflineDocumentStateMachine;
export const createPublicProbeCircuit = sharedRuntime.createPublicProbeCircuit;


export function buildInlineConnectivityRuntimeSource(globalName = "ElvernConnectivityRuntime") {
  const safeGlobalName = JSON.stringify(String(globalName));
  return `window[${safeGlobalName}] = (${createConnectivityRuntime.toString()})(${JSON.stringify(CONNECTION_RUNTIME_CONTRACT)});`;
}
