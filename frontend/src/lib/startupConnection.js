import {
  createPublicConnectivityProbeRunner,
  resolvePublicConnectivityProbeRegistry,
} from "./publicConnectivityProbes.js";
import {
  CONNECTION_RUNTIME_CONTRACT,
  deriveFastOopsCandidate,
  deriveConnectivityClassification,
  getRuntimeConnectionOopsCopy,
  matchesFastOopsCandidates,
} from "./connectivityRuntimeCore.js";
import { detectClientPlatform, isDesktopClientPlatform } from "./platformDetection.js";


export const STARTUP_UNREACHABLE_DELAY_MS = CONNECTION_RUNTIME_CONTRACT.offlineDocumentOopsDelayMs;
export const STARTUP_HEALTH_PROBE_INTERVAL_MS = CONNECTION_RUNTIME_CONTRACT.offlineRecoveryProbeIntervalMs;
export const STARTUP_HEALTH_PROBE_TIMEOUT_MS = CONNECTION_RUNTIME_CONTRACT.healthProbeTimeoutMs;
export const DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS = 8_000;
export const PUBLIC_CONNECTIVITY_CONFIRMATION_DELAY_MS = CONNECTION_RUNTIME_CONTRACT.publicProbeConfirmationDelayMs;
export const FAST_OOPS_CONFIRMATION_DELAY_MS = CONNECTION_RUNTIME_CONTRACT.fastOopsConfirmationDelayMs;
export const STARTUP_SHELL_REVEAL_DELAY_MS = 400;
export const NO_INTERNET_REAPPEAR_MS = 10_000;
export const STARTUP_CONNECTIVITY_FAILURE_EVENT = "elvern:connectivity-failure";
export const STARTUP_APPLICATION_READY_EVENT = "elvern:application-response";
export const CONNECTIVITY_RECOVERED_EVENT = "elvern:connectivity-recovered";
export const STARTUP_MANUAL_SERVICE_RECOVERY_STORAGE_KEY = CONNECTION_RUNTIME_CONTRACT.manualServiceRecoveryStorageKey;
export const FRONTEND_HEALTH_PATH = "/_elvern/frontend-health";
export const BACKEND_HEALTH_PATH = "/health";
export const CONNECTIVITY_INTERNET_OFFLINE = CONNECTION_RUNTIME_CONTRACT.classifications.internetOffline;
export const CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE = CONNECTION_RUNTIME_CONTRACT.classifications.frontendOrVpnUnreachable;
export const CONNECTIVITY_BACKEND_UNREACHABLE = CONNECTION_RUNTIME_CONTRACT.classifications.backendUnreachable;
export const CONNECTIVITY_EVIDENCE_INSUFFICIENT = CONNECTION_RUNTIME_CONTRACT.classifications.evidenceInsufficient;
export const CONNECTIVITY_HEALTHY = CONNECTION_RUNTIME_CONTRACT.classifications.healthy;
export const CONNECTION_OOPS_TITLE = CONNECTION_RUNTIME_CONTRACT.copy.title;
export const CONNECTION_SERVER_OOPS_COPY = CONNECTION_RUNTIME_CONTRACT.copy.server;
export const CONNECTION_VPN_OOPS_COPY = CONNECTION_RUNTIME_CONTRACT.copy.vpn;
export const CONNECTION_OFFLINE_OOPS_COPY = CONNECTION_RUNTIME_CONTRACT.copy.offline;
export const CONNECTION_GENERIC_OOPS_COPY = CONNECTION_RUNTIME_CONTRACT.copy.generic;
export const CONNECTION_STATUS_WORDS = CONNECTION_RUNTIME_CONTRACT.statusWords;
export const CONNECTION_FAMILIARS = CONNECTION_RUNTIME_CONTRACT.familiars;
export const CONNECTION_FAMILIAR_ROTATION_MS = CONNECTION_RUNTIME_CONTRACT.familiarRotationMs;


function isDebugEnabled(windowObject) {
  try {
    return windowObject?.localStorage?.getItem("elvern_connection_shell_debug") === "1";
  } catch {
    return false;
  }
}


export function getConnectionOopsCopy(classification) {
  return getRuntimeConnectionOopsCopy(classification);
}


export function classifyStartupHealthResponse(path, response) {
  const status = Number(response?.status);
  if (!(status >= 200 && status < 300)) {
    return {
      reachable: false,
      reason: status >= 500 && status <= 599
        ? CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.httpUnhealthy
        : CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.unexpectedHttpStatus,
      status: Number.isFinite(status) ? status : null,
    };
  }
  const expectedMarker = path === FRONTEND_HEALTH_PATH
    ? CONNECTION_RUNTIME_CONTRACT.frontendHealthHeader
    : CONNECTION_RUNTIME_CONTRACT.backendHealthHeader;
  if (response?.headers?.get?.(expectedMarker) !== "1") {
    return {
      reachable: false,
      reason: CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.markerMissing,
      status,
    };
  }
  return {
    reachable: true,
    reason: CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.httpSuccess,
    status,
  };
}


export function dispatchStartupConnectivityFailure(detail = null) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }
  window.dispatchEvent(new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT, { detail }));
}


export function dispatchStartupApplicationReady() {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }
  window.__elvernRuntimeReady = true;
  window.__elvernBootstrapPhase = "runtime_ready";
  window.dispatchEvent(new CustomEvent(STARTUP_APPLICATION_READY_EVENT));
}


export function createStartupConnectionController({
  fetchImpl = globalThis.fetch?.bind(globalThis),
  windowObject = globalThis.window,
  documentObject = globalThis.document,
  navigatorObject = globalThis.navigator,
  requireApplicationReady = false,
  initialOutageStartedAt = Number(windowObject?.__elvernConnectionStartedAt) || 0,
  platform = detectClientPlatform(),
  publicConnectivityProbes = null,
  publicConnectivityProbeUrl = "",
  publicProbeConfirmationDelayMs = PUBLIC_CONNECTIVITY_CONFIRMATION_DELAY_MS,
  publicProbeStorage = windowObject?.localStorage,
  recoveryStorage = windowObject?.sessionStorage,
} = {}) {
  const initiallyOffline = navigatorObject?.onLine === false;
  const probes = publicConnectivityProbes || (publicConnectivityProbeUrl
    ? resolvePublicConnectivityProbeRegistry({ pluralValue: "", singularValue: publicConnectivityProbeUrl })
    : resolvePublicConnectivityProbeRegistry());
  const publicRunner = createPublicConnectivityProbeRunner({
    fetchImpl,
    probes,
    storage: publicProbeStorage,
    setTimeoutImpl: windowObject?.setTimeout?.bind(windowObject),
    clearTimeoutImpl: windowObject?.clearTimeout?.bind(windowObject),
    debug: isDebugEnabled(windowObject)
      ? ({ endpointId, success, status, elapsedMs }) => console.debug("Elvern public connectivity probe", {
        endpointId,
        success,
        status,
        elapsedMs,
      })
      : null,
  });
  const initialTrust = publicRunner.getTrustState();
  let applicationReady = !requireApplicationReady;
  let manualServiceRecoveryAuthorized = false;
  try {
    manualServiceRecoveryAuthorized = recoveryStorage?.getItem?.(
      STARTUP_MANUAL_SERVICE_RECOVERY_STORAGE_KEY,
    ) === "1";
    recoveryStorage?.removeItem?.(STARTUP_MANUAL_SERVICE_RECOVERY_STORAGE_KEY);
  } catch {
    manualServiceRecoveryAuthorized = false;
  }
  let runtimeReady = false;
  let offlineOopsRequired = initiallyOffline;
  let forceOfflineOopsPending = false;
  let started = false;
  let lifecycleGeneration = 0;
  let outageGeneration = 1;
  let oopsLatchedGeneration = 0;
  let outageActive = true;
  // A transport incident is a bounded recovery generation that is independent
  // from the full-screen outage/Oops machinery. It opens when a genuine
  // ApiNetworkError is reported while the visible application is still healthy,
  // shares the monotonic `outageGeneration` space so recovery generations stay
  // globally strictly-increasing, and never mutates the visible status, the
  // Oops deadline, or the paint surface.
  let transportIncidentActive = false;
  let runtimeOutageGeneration = 0;
  let recoveredOutageGeneration = 0;
  let browserOfflineEvidenceGeneration = 0;
  let initialProbeCompleted = false;
  let outageStartedAt = Number.isFinite(initialOutageStartedAt) && initialOutageStartedAt > 0
    ? initialOutageStartedAt
    : Date.now();
  let unreachableTimer = 0;
  let scheduledProbeTimer = 0;
  let inFlightProbe = null;
  const activeHealthControllers = new Set();
  const activeConfirmationTimers = new Map();
  const listeners = new Set();
  const desktopWatchdogEnabled = isDesktopClientPlatform(platform);
  let snapshot = {
    internetState: initiallyOffline ? "offline" : "unknown",
    internetOutageLatched: initiallyOffline,
    publicEvidenceReason: initiallyOffline
      ? CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.browserExplicitOffline
      : null,
    publicProbeTrusted: initialTrust.trusted,
    frontendState: "unknown",
    backendState: "unknown",
    status: "connecting",
    serviceReachable: false,
    runtimeReady: false,
    offlineOopsRequired,
    classification: initiallyOffline
      ? CONNECTIVITY_INTERNET_OFFLINE
      : CONNECTIVITY_EVIDENCE_INSUFFICIENT,
    oopsLatched: false,
    outageGeneration,
    oopsLatchedGeneration,
    oopsEvidenceReason: null,
  };

  function emit(next = {}) {
    const candidate = {
      ...snapshot,
      ...next,
      runtimeReady,
      offlineOopsRequired,
    };
    candidate.classification = deriveConnectivityClassification(candidate);
    const keys = [
      "internetState",
      "internetOutageLatched",
      "publicEvidenceReason",
      "publicProbeTrusted",
      "frontendState",
      "backendState",
      "status",
      "serviceReachable",
      "runtimeReady",
      "offlineOopsRequired",
      "classification",
      "oopsLatched",
      "outageGeneration",
      "oopsLatchedGeneration",
      "oopsEvidenceReason",
    ];
    if (keys.every((key) => snapshot[key] === candidate[key])) {
      return;
    }
    snapshot = candidate;
    listeners.forEach((listener) => listener());
  }

  function clearUnreachableTimer() {
    if (unreachableTimer) {
      windowObject?.clearTimeout?.(unreachableTimer);
      unreachableTimer = 0;
    }
  }

  function clearScheduledProbe() {
    if (scheduledProbeTimer) {
      windowObject?.clearTimeout?.(scheduledProbeTimer);
      scheduledProbeTimer = 0;
    }
  }

  function shouldSuppressRuntimeOops() {
    return runtimeReady && snapshot.internetOutageLatched && !offlineOopsRequired;
  }

  function cancelConfirmationTimers() {
    activeConfirmationTimers.forEach((resolve, timerId) => {
      windowObject?.clearTimeout?.(timerId);
      resolve(false);
    });
    activeConfirmationTimers.clear();
  }

  function beginOutage() {
    if (outageActive) return;
    outageActive = true;
    if (transportIncidentActive) {
      // Escalate an already-open lightweight transport incident into a full
      // outage, reusing the generation it has already issued so a single
      // incident never consumes two recovery generations.
      transportIncidentActive = false;
    } else {
      outageGeneration += 1;
      if (runtimeReady) {
        runtimeOutageGeneration = outageGeneration;
      }
    }
    outageStartedAt = Date.now();
    cancelConfirmationTimers();
    emit({
      outageGeneration,
      oopsLatched: false,
      oopsLatchedGeneration: 0,
      oopsEvidenceReason: null,
    });
  }

  function openTransportIncident() {
    // Join the current incident when one is already open (coalesce overlapping
    // transport failures), and never open one while the full-screen outage
    // machinery already owns recovery.
    if (outageActive || transportIncidentActive) return;
    transportIncidentActive = true;
    outageGeneration += 1;
    if (runtimeReady) {
      runtimeOutageGeneration = outageGeneration;
    }
  }

  function endTransportIncidentIfRecovered() {
    if (
      !transportIncidentActive
      || !started
      || !snapshot.serviceReachable
      || !applicationReady
      || snapshot.internetOutageLatched
      || offlineOopsRequired
    ) {
      return;
    }
    const recoveredGeneration = outageGeneration;
    transportIncidentActive = false;
    if (
      runtimeOutageGeneration === recoveredGeneration
      && recoveredOutageGeneration !== recoveredGeneration
      && typeof windowObject?.dispatchEvent === "function"
    ) {
      recoveredOutageGeneration = recoveredGeneration;
      windowObject.dispatchEvent(new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, {
        detail: {
          generation: recoveredGeneration,
          previousClassification: "service_unreachable",
        },
      }));
    }
  }

  function endOutageIfRecovered() {
    if (
      !outageActive
      || !snapshot.serviceReachable
      || !applicationReady
      || snapshot.internetOutageLatched
      || offlineOopsRequired
    ) {
      return;
    }
    const recoveredGeneration = outageGeneration;
    outageActive = false;
    oopsLatchedGeneration = 0;
    clearUnreachableTimer();
    cancelConfirmationTimers();
    outageStartedAt = 0;
    emit({
      status: "connected",
      oopsLatched: false,
      oopsLatchedGeneration: 0,
      oopsEvidenceReason: null,
    });
    if (
      runtimeOutageGeneration === recoveredGeneration
      && recoveredOutageGeneration !== recoveredGeneration
      && typeof windowObject?.dispatchEvent === "function"
    ) {
      recoveredOutageGeneration = recoveredGeneration;
      windowObject.dispatchEvent(new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, {
        detail: {
          generation: recoveredGeneration,
          previousClassification: "service_unreachable",
        },
      }));
    }
  }

  function latchOops({ classification = snapshot.classification, evidenceReason } = {}) {
    if (
      snapshot.status === "connected"
      || oopsLatchedGeneration === outageGeneration
    ) {
      return;
    }
    oopsLatchedGeneration = outageGeneration;
    emit({
      classification,
      oopsLatched: true,
      oopsLatchedGeneration,
      oopsEvidenceReason: evidenceReason || null,
      status: "unreachable",
    });
  }

  function scheduleUnreachable() {
    if (unreachableTimer || shouldSuppressRuntimeOops()) {
      return;
    }
    const remaining = Math.max(0, STARTUP_UNREACHABLE_DELAY_MS - Math.max(0, Date.now() - outageStartedAt));
    unreachableTimer = windowObject?.setTimeout?.(() => {
      unreachableTimer = 0;
      if (!snapshot.serviceReachable || !applicationReady || offlineOopsRequired) {
        latchOops({ evidenceReason: CONNECTION_RUNTIME_CONTRACT.fastOopsReasons.deadlineTimeout });
      }
    }, remaining) || 0;
  }

  function scheduleNextProbe() {
    clearScheduledProbe();
    if (!started || documentObject?.visibilityState === "hidden") {
      return;
    }
    const needsRecovery = !snapshot.serviceReachable
      || !applicationReady
      || snapshot.internetOutageLatched
      || offlineOopsRequired;
    if (!desktopWatchdogEnabled && !needsRecovery) {
      return;
    }
    const delayMs = desktopWatchdogEnabled && runtimeReady
      ? DESKTOP_CONNECTIVITY_WATCHDOG_INTERVAL_MS
      : STARTUP_HEALTH_PROBE_INTERVAL_MS;
    scheduledProbeTimer = windowObject?.setTimeout?.(() => {
      scheduledProbeTimer = 0;
      void probe();
    }, delayMs) || 0;
  }

  function markInternetOffline({
    initialCycle = false,
    publicEvidenceReason = CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.browserExplicitOffline,
  } = {}) {
    beginOutage();
    if (!outageStartedAt) {
      outageStartedAt = Date.now();
    }
    if (initialCycle || forceOfflineOopsPending) {
      offlineOopsRequired = true;
    }
    forceOfflineOopsPending = false;
    emit({
      internetState: "offline",
      internetOutageLatched: true,
      publicEvidenceReason,
    });
    if (shouldSuppressRuntimeOops()) {
      clearUnreachableTimer();
      if (snapshot.serviceReachable && applicationReady) {
        emit({ status: "connected" });
      }
      return;
    }
    emit({ status: snapshot.status === "unreachable" ? "unreachable" : "connecting" });
    if (
      (!runtimeReady || offlineOopsRequired)
      && publicEvidenceReason === CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.browserExplicitOffline
    ) {
      latchOops({
        classification: CONNECTIVITY_INTERNET_OFFLINE,
        evidenceReason: CONNECTION_RUNTIME_CONTRACT.fastOopsReasons.browserOffline,
      });
      return;
    }
    scheduleUnreachable();
  }

  function applyInternetEvidence(result, { initialCycle = false } = {}) {
    if (result.internetState === "online") {
      offlineOopsRequired = false;
      forceOfflineOopsPending = false;
      emit({
        internetState: "online",
        internetOutageLatched: false,
        publicEvidenceReason: result.publicEvidenceReason,
        publicProbeTrusted: true,
      });
      if (snapshot.serviceReachable && applicationReady) {
        clearUnreachableTimer();
        emit({ status: "connected" });
        endOutageIfRecovered();
      }
      return;
    }
    if (result.internetState === "offline") {
      emit({
        publicEvidenceReason: result.publicEvidenceReason,
        publicProbeTrusted: Boolean(result.trusted),
      });
      markInternetOffline({ initialCycle, publicEvidenceReason: result.publicEvidenceReason });
      return;
    }
    emit({
      internetState: snapshot.internetOutageLatched ? "offline" : "unknown",
      publicEvidenceReason: result.publicEvidenceReason,
      publicProbeTrusted: Boolean(result.trusted),
    });
  }

  function markServiceHealthy() {
    const canConnect = applicationReady && !offlineOopsRequired;
    if (canConnect) {
      runtimeReady = true;
      clearUnreachableTimer();
    }
    emit({
      frontendState: "reachable",
      backendState: "reachable",
      serviceReachable: true,
      status: canConnect ? "connected" : (snapshot.status === "unreachable" ? "unreachable" : "connecting"),
    });
    endOutageIfRecovered();
    endTransportIncidentIfRecovered();
  }

  function markServiceFailure({ frontendState, backendState }) {
    const wasConnected = snapshot.status === "connected";
    if (wasConnected || !outageActive) beginOutage();
    if (!outageStartedAt) outageStartedAt = Date.now();
    emit({
      frontendState,
      backendState,
      serviceReachable: false,
      status: shouldSuppressRuntimeOops() ? "connecting" : (snapshot.status === "unreachable" ? "unreachable" : "connecting"),
    });
    if (shouldSuppressRuntimeOops()) {
      clearUnreachableTimer();
    } else {
      scheduleUnreachable();
    }
  }

  function createHealthResult({ reachable, reason, status = null }) {
    return {
      reachable,
      reason,
      status: Number.isFinite(Number(status)) ? Number(status) : null,
      completedAt: Date.now(),
    };
  }

  async function requestHealth(path, generation) {
    const abortController = new AbortController();
    let timedOut = false;
    activeHealthControllers.add(abortController);
    const timeoutId = windowObject?.setTimeout?.(() => {
      timedOut = true;
      abortController.abort();
    }, STARTUP_HEALTH_PROBE_TIMEOUT_MS) || 0;
    try {
      const response = await fetchImpl(path, {
        cache: "no-store",
        credentials: "same-origin",
        signal: abortController.signal,
      });
      if (generation !== lifecycleGeneration) {
        return createHealthResult({
          reachable: false,
          reason: CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.aborted,
        });
      }
      return createHealthResult(classifyStartupHealthResponse(path, response));
    } catch (error) {
      const activelyCancelled = generation !== lifecycleGeneration || !started;
      return createHealthResult({
        reachable: false,
        reason: timedOut
          ? CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.timeout
          : (activelyCancelled || error?.name === "AbortError"
            ? CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.aborted
            : CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons.networkError),
      });
    } finally {
      if (timeoutId) windowObject?.clearTimeout?.(timeoutId);
      activeHealthControllers.delete(abortController);
    }
  }

  async function collectServiceEvidence(generation) {
    const frontendHealth = await requestHealth(FRONTEND_HEALTH_PATH, generation);
    if (!frontendHealth.reachable) {
      return {
        frontendHealth,
        backendHealth: null,
        frontendState: "unreachable",
        backendState: "unknown",
      };
    }
    const backendHealth = await requestHealth(BACKEND_HEALTH_PATH, generation);
    return {
      frontendHealth,
      backendHealth,
      frontendState: "reachable",
      backendState: backendHealth.reachable ? "reachable" : "unreachable",
    };
  }

  function waitForFastOopsConfirmation(generation, candidateOutageGeneration) {
    return new Promise((resolve) => {
      const timerId = windowObject?.setTimeout?.(() => {
        activeConfirmationTimers.delete(timerId);
        resolve(
          started
          && generation === lifecycleGeneration
          && candidateOutageGeneration === outageGeneration
        );
      }, FAST_OOPS_CONFIRMATION_DELAY_MS) || 0;
      if (!timerId) {
        resolve(false);
        return;
      }
      activeConfirmationTimers.set(timerId, resolve);
    });
  }

  function collectInternetEvidence() {
    return navigatorObject?.onLine === false
      ? Promise.resolve({
        internetState: "offline",
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.browserExplicitOffline,
        trusted: snapshot.publicProbeTrusted,
        endpointId: null,
        rounds: 0,
      })
      : publicRunner.probeConfirmed({ confirmationDelayMs: publicProbeConfirmationDelayMs });
  }

  async function collectProbeEvidence(generation) {
    const [internet, service] = await Promise.all([
      collectInternetEvidence(),
      collectServiceEvidence(generation),
    ]);
    return { internet, service };
  }

  function applyProbeEvidence({ internet, service }, { initialCycle = false } = {}) {
    const serviceHealthy = service.frontendState === "reachable" && service.backendState === "reachable";
    if (
      initialCycle
      && manualServiceRecoveryAuthorized
      && navigatorObject?.onLine !== false
      && serviceHealthy
    ) {
      manualServiceRecoveryAuthorized = false;
      offlineOopsRequired = false;
      forceOfflineOopsPending = false;
      clearUnreachableTimer();
      emit({
        internetState: "unknown",
        internetOutageLatched: false,
        publicEvidenceReason: internet.publicEvidenceReason,
        publicProbeTrusted: Boolean(internet.trusted),
      });
      markServiceHealthy();
      return;
    }
    if (initialCycle) {
      manualServiceRecoveryAuthorized = false;
    }
    applyInternetEvidence(internet, { initialCycle });
    if (serviceHealthy) {
      markServiceHealthy();
    } else {
      markServiceFailure(service);
    }
  }

  function fastOopsCandidateFor(evidence) {
    return deriveFastOopsCandidate({
      navigatorOnline: navigatorObject?.onLine !== false,
      publicEvidence: evidence.internet,
      frontendHealth: evidence.service.frontendHealth,
      backendHealth: evidence.service.backendHealth,
      localhostServicesHealthy: evidence.service.frontendState === "reachable"
        && evidence.service.backendState === "reachable",
    });
  }

  function probe() {
    if (inFlightProbe || typeof fetchImpl !== "function") {
      return inFlightProbe || Promise.resolve(false);
    }
    clearScheduledProbe();
    const generation = lifecycleGeneration;
    const offlineGeneration = browserOfflineEvidenceGeneration;
    const initialCycle = !initialProbeCompleted;
    const transactionIsCurrent = () => started
      && generation === lifecycleGeneration
      && offlineGeneration === browserOfflineEvidenceGeneration;
    const operation = collectProbeEvidence(generation).then(async (firstEvidence) => {
      if (!transactionIsCurrent()) return false;
      applyProbeEvidence(firstEvidence, { initialCycle });
      const firstCandidate = fastOopsCandidateFor(firstEvidence);
      const candidateOutageGeneration = outageGeneration;
      if (firstCandidate?.evidenceReason === CONNECTION_RUNTIME_CONTRACT.fastOopsReasons.browserOffline) {
        if (!runtimeReady || offlineOopsRequired) latchOops(firstCandidate);
      } else if (
        firstCandidate
        && await waitForFastOopsConfirmation(generation, candidateOutageGeneration)
        && transactionIsCurrent()
        && candidateOutageGeneration === outageGeneration
      ) {
        const secondEvidence = await collectProbeEvidence(generation);
        if (!transactionIsCurrent()) return false;
        applyProbeEvidence(secondEvidence);
        const secondCandidate = fastOopsCandidateFor(secondEvidence);
        if (matchesFastOopsCandidates(firstCandidate, secondCandidate)) {
          latchOops(secondCandidate);
        }
        firstEvidence = secondEvidence;
      }
      return firstEvidence.internet.internetState === "online"
        && firstEvidence.service.frontendState === "reachable"
        && firstEvidence.service.backendState === "reachable";
    });
    const tracked = operation.finally(() => {
      if (generation === lifecycleGeneration) {
        initialProbeCompleted = true;
      }
      if (inFlightProbe === tracked) {
        inFlightProbe = null;
      }
      if (started && generation === lifecycleGeneration) {
        scheduleNextProbe();
      }
    });
    inFlightProbe = tracked;
    return tracked;
  }

  function handleVisibilityChange() {
    if (documentObject?.visibilityState === "hidden") {
      clearScheduledProbe();
      return;
    }
    void probe();
  }

  function handleOnline() {
    void probe();
  }

  function handleOffline() {
    browserOfflineEvidenceGeneration += 1;
    markInternetOffline({
      initialCycle: !initialProbeCompleted,
      publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.browserExplicitOffline,
    });
    void probe();
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    lifecycleGeneration += 1;
    if (!outageStartedAt) {
      outageStartedAt = Date.now();
    }
    if (initiallyOffline || navigatorObject?.onLine === false) {
      markInternetOffline({
        initialCycle: true,
        publicEvidenceReason: CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons.browserExplicitOffline,
      });
    }
    scheduleUnreachable();
    windowObject?.addEventListener?.("online", handleOnline);
    windowObject?.addEventListener?.("offline", handleOffline);
    documentObject?.addEventListener?.("visibilitychange", handleVisibilityChange);
    void probe();
  }

  function stop() {
    if (!started) {
      return;
    }
    started = false;
    lifecycleGeneration += 1;
    // Abandon any pending transport incident so a stopped/obsolete lifecycle
    // can never emit a late recovery event.
    transportIncidentActive = false;
    clearUnreachableTimer();
    clearScheduledProbe();
    activeHealthControllers.forEach((controller) => controller.abort());
    activeHealthControllers.clear();
    cancelConfirmationTimers();
    publicRunner.abort();
    inFlightProbe = null;
    windowObject?.removeEventListener?.("online", handleOnline);
    windowObject?.removeEventListener?.("offline", handleOffline);
    documentObject?.removeEventListener?.("visibilitychange", handleVisibilityChange);
  }

  function reportFailure({ forceOfflineOops = false } = {}) {
    if (forceOfflineOops) {
      forceOfflineOopsPending = true;
      if (snapshot.internetOutageLatched || navigatorObject?.onLine === false) {
        offlineOopsRequired = true;
        if (!outageStartedAt) {
          outageStartedAt = Date.now();
        }
        emit({ status: "connecting" });
        scheduleUnreachable();
      }
    }
    if (navigatorObject?.onLine === false) {
      markInternetOffline();
    } else if (!snapshot.serviceReachable) {
      markServiceFailure({ frontendState: snapshot.frontendState, backendState: snapshot.backendState });
    } else {
      // A genuine transport failure while the last health snapshot is still
      // reachable: open a bounded incident so the immediate health probe below
      // can confirm recovery and emit CONNECTIVITY_RECOVERED_EVENT even though
      // serviceReachable was already true.
      openTransportIncident();
    }
    return probe();
  }

  function reportApplicationReady() {
    applicationReady = true;
    runtimeReady = true;
    if (windowObject) {
      windowObject.__elvernRuntimeReady = true;
      windowObject.__elvernBootstrapPhase = "runtime_ready";
    }
    if (snapshot.serviceReachable && !offlineOopsRequired) {
      clearUnreachableTimer();
      emit({ status: "connected" });
      endOutageIfRecovered();
    } else {
      emit({ status: snapshot.status === "unreachable" ? "unreachable" : "connecting" });
      scheduleUnreachable();
    }
    scheduleNextProbe();
  }

  function retry() {
    return probe();
  }

  return {
    getSnapshot: () => snapshot,
    probe,
    reportApplicationReady,
    reportFailure,
    retry,
    start,
    stop,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
