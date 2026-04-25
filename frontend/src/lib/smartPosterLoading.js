export const INTERSECTION_ROOT_MARGIN = "150% 20% 150% 20%";
export const LANDSCAPE_INTERSECTION_ROOT_MARGIN = "60% 8% 60% 8%";
export const INTERSECTION_THRESHOLD = 0;
export const VERY_FAST_SCROLL_PX_PER_MS = 1.8;
export const FAST_SCROLL_PX_PER_MS = 1.1;
export const MEDIUM_SCROLL_PX_PER_MS = 0.45;
export const SCROLL_IDLE_SETTLE_MS = 140;
export const ORIENTATION_SETTLE_MS = 200;
export const LANDSCAPE_ORIENTATION_SETTLE_MS = 540;
export const ADMISSION_TICK_MS = 90;
export const LANDSCAPE_ADMISSION_TICK_MS = 160;
export const MAX_IN_FLIGHT_IMAGES = 8;
export const MAX_NEW_ADMISSIONS_PER_TICK = 4;
export const MAX_MOUNTED_IMAGES = 96;
export const LANDSCAPE_MAX_MOUNTED_IMAGES = 36;

export const POSTER_MODE_ATTACH = "attach";
export const POSTER_MODE_QUEUED = "queued";
export const POSTER_MODE_DEFER = "defer";
export const POSTER_MODE_DETACH = "detach";

export const SMART_POSTER_ORIENTATION_PORTRAIT = "portrait";
export const SMART_POSTER_ORIENTATION_LANDSCAPE = "landscape";

export const SPEED_BAND_SETTLING = "settling";
export const SPEED_BAND_VERY_FAST = "very_fast";
export const SPEED_BAND_FAST = "fast";
export const SPEED_BAND_MEDIUM = "medium";
export const SPEED_BAND_SLOW = "slow";
export const SPEED_BAND_IDLE = "idle";

const DEBUG_STORAGE_KEY = "elvern_smart_poster_debug";
const VELOCITY_SMOOTHING_ALPHA = 0.58;
const SPEED_BAND_ORDER = Object.freeze({
  [SPEED_BAND_SETTLING]: 0,
  [SPEED_BAND_VERY_FAST]: 1,
  [SPEED_BAND_FAST]: 2,
  [SPEED_BAND_MEDIUM]: 3,
  [SPEED_BAND_SLOW]: 4,
  [SPEED_BAND_IDLE]: 5,
});

const PORTRAIT_ADMISSION_POLICIES = Object.freeze({
  [SPEED_BAND_SETTLING]: Object.freeze({
    speedBand: SPEED_BAND_SETTLING,
    maxNewAdmissionsPerTick: 0,
    maxInFlight: 2,
    allowVisible: true,
    allowNearAhead: false,
    allowNearBehind: false,
  }),
  [SPEED_BAND_VERY_FAST]: Object.freeze({
    speedBand: SPEED_BAND_VERY_FAST,
    maxNewAdmissionsPerTick: 0,
    maxInFlight: 1,
    allowVisible: true,
    allowNearAhead: false,
    allowNearBehind: false,
  }),
  [SPEED_BAND_FAST]: Object.freeze({
    speedBand: SPEED_BAND_FAST,
    maxNewAdmissionsPerTick: 1,
    maxInFlight: 3,
    allowVisible: true,
    allowNearAhead: false,
    allowNearBehind: false,
  }),
  [SPEED_BAND_MEDIUM]: Object.freeze({
    speedBand: SPEED_BAND_MEDIUM,
    maxNewAdmissionsPerTick: 2,
    maxInFlight: 5,
    allowVisible: true,
    allowNearAhead: true,
    allowNearBehind: false,
  }),
  [SPEED_BAND_SLOW]: Object.freeze({
    speedBand: SPEED_BAND_SLOW,
    maxNewAdmissionsPerTick: 3,
    maxInFlight: 7,
    allowVisible: true,
    allowNearAhead: true,
    allowNearBehind: true,
  }),
  [SPEED_BAND_IDLE]: Object.freeze({
    speedBand: SPEED_BAND_IDLE,
    maxNewAdmissionsPerTick: MAX_NEW_ADMISSIONS_PER_TICK,
    maxInFlight: MAX_IN_FLIGHT_IMAGES,
    allowVisible: true,
    allowNearAhead: true,
    allowNearBehind: true,
  }),
});

const LANDSCAPE_ADMISSION_POLICIES = Object.freeze({
  [SPEED_BAND_SETTLING]: Object.freeze({
    speedBand: SPEED_BAND_SETTLING,
    maxNewAdmissionsPerTick: 0,
    maxInFlight: 1,
    allowVisible: true,
    allowNearAhead: false,
    allowNearBehind: false,
  }),
  [SPEED_BAND_VERY_FAST]: Object.freeze({
    speedBand: SPEED_BAND_VERY_FAST,
    maxNewAdmissionsPerTick: 0,
    maxInFlight: 1,
    allowVisible: true,
    allowNearAhead: false,
    allowNearBehind: false,
  }),
  [SPEED_BAND_FAST]: Object.freeze({
    speedBand: SPEED_BAND_FAST,
    maxNewAdmissionsPerTick: 1,
    maxInFlight: 1,
    allowVisible: true,
    allowNearAhead: false,
    allowNearBehind: false,
  }),
  [SPEED_BAND_MEDIUM]: Object.freeze({
    speedBand: SPEED_BAND_MEDIUM,
    maxNewAdmissionsPerTick: 1,
    maxInFlight: 2,
    allowVisible: true,
    allowNearAhead: true,
    allowNearBehind: false,
  }),
  [SPEED_BAND_SLOW]: Object.freeze({
    speedBand: SPEED_BAND_SLOW,
    maxNewAdmissionsPerTick: 1,
    maxInFlight: 3,
    allowVisible: true,
    allowNearAhead: true,
    allowNearBehind: true,
  }),
  [SPEED_BAND_IDLE]: Object.freeze({
    speedBand: SPEED_BAND_IDLE,
    maxNewAdmissionsPerTick: 2,
    maxInFlight: 4,
    allowVisible: true,
    allowNearAhead: true,
    allowNearBehind: true,
  }),
});

export const SMART_POSTER_POLICY_PORTRAIT = Object.freeze({
  name: SMART_POSTER_ORIENTATION_PORTRAIT,
  orientation: SMART_POSTER_ORIENTATION_PORTRAIT,
  intersectionRootMargin: INTERSECTION_ROOT_MARGIN,
  intersectionThreshold: INTERSECTION_THRESHOLD,
  veryFastScrollPxPerMs: VERY_FAST_SCROLL_PX_PER_MS,
  fastScrollPxPerMs: FAST_SCROLL_PX_PER_MS,
  mediumScrollPxPerMs: MEDIUM_SCROLL_PX_PER_MS,
  scrollIdleSettleMs: SCROLL_IDLE_SETTLE_MS,
  orientationSettleMs: ORIENTATION_SETTLE_MS,
  admissionTickMs: ADMISSION_TICK_MS,
  maxMountedImages: MAX_MOUNTED_IMAGES,
  preferNearBehindEviction: false,
  admissionPolicies: PORTRAIT_ADMISSION_POLICIES,
});

export const SMART_POSTER_POLICY_LANDSCAPE = Object.freeze({
  name: SMART_POSTER_ORIENTATION_LANDSCAPE,
  orientation: SMART_POSTER_ORIENTATION_LANDSCAPE,
  intersectionRootMargin: LANDSCAPE_INTERSECTION_ROOT_MARGIN,
  intersectionThreshold: INTERSECTION_THRESHOLD,
  veryFastScrollPxPerMs: VERY_FAST_SCROLL_PX_PER_MS,
  fastScrollPxPerMs: FAST_SCROLL_PX_PER_MS,
  mediumScrollPxPerMs: MEDIUM_SCROLL_PX_PER_MS,
  scrollIdleSettleMs: SCROLL_IDLE_SETTLE_MS,
  orientationSettleMs: LANDSCAPE_ORIENTATION_SETTLE_MS,
  admissionTickMs: LANDSCAPE_ADMISSION_TICK_MS,
  maxMountedImages: LANDSCAPE_MAX_MOUNTED_IMAGES,
  preferNearBehindEviction: true,
  admissionPolicies: LANDSCAPE_ADMISSION_POLICIES,
});

const EMPTY_CARD_SNAPSHOT = Object.freeze({
  mode: POSTER_MODE_DEFER,
  isMounted: false,
  inFlight: false,
  loadedBefore: false,
});

let schedulerSingleton = null;

function nowMs() {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function getScrollY() {
  if (typeof window === "undefined") {
    return 0;
  }
  return Number(window.scrollY || window.pageYOffset || 0);
}

function getViewportMetrics() {
  if (typeof window === "undefined") {
    return {
      height: 0,
      width: 0,
      centerY: 0,
    };
  }
  const viewport = window.visualViewport;
  const height = Math.round(viewport?.height || window.innerHeight || 0);
  const width = Math.round(viewport?.width || window.innerWidth || 0);
  return {
    height,
    width,
    centerY: height / 2,
  };
}

function toFiniteNumber(value, fallback = 0) {
  return Number.isFinite(value) ? Number(value) : fallback;
}

function isAheadOfScrollDirection(centerY, viewportCenterY, scrollDirection) {
  if (scrollDirection > 0) {
    return centerY >= viewportCenterY;
  }
  if (scrollDirection < 0) {
    return centerY <= viewportCenterY;
  }
  return centerY >= viewportCenterY;
}

function compareById(left, right) {
  if (left.id === right.id) {
    return 0;
  }
  return String(left.id).localeCompare(String(right.id));
}

export function getSmartPosterOrientation({ width = 0, height = 0 } = {}) {
  return width > height
    ? SMART_POSTER_ORIENTATION_LANDSCAPE
    : SMART_POSTER_ORIENTATION_PORTRAIT;
}

export function getSmartPosterSchedulerPolicy({
  width = 0,
  height = 0,
  orientation = null,
} = {}) {
  const resolvedOrientation = orientation || getSmartPosterOrientation({ width, height });
  return resolvedOrientation === SMART_POSTER_ORIENTATION_LANDSCAPE
    ? SMART_POSTER_POLICY_LANDSCAPE
    : SMART_POSTER_POLICY_PORTRAIT;
}

export function getSmartPosterObserverConfig({ policy = SMART_POSTER_POLICY_PORTRAIT } = {}) {
  return {
    root: null,
    rootMargin: policy.intersectionRootMargin,
    threshold: policy.intersectionThreshold,
  };
}

function getCandidateRelation(candidate, { viewportCenterY = 0, scrollDirection = 0 } = {}) {
  if (!candidate || (!candidate.isVisible && !candidate.isCandidate)) {
    return "far";
  }
  if (candidate.isVisible) {
    return "visible";
  }
  const centerY = toFiniteNumber(candidate.centerY, viewportCenterY);
  return isAheadOfScrollDirection(centerY, viewportCenterY, scrollDirection) ? "ahead" : "behind";
}

function isCandidateAllowedByPolicy(candidate, { admissionPolicy, viewportCenterY = 0, scrollDirection = 0 } = {}) {
  const relation = getCandidateRelation(candidate, { viewportCenterY, scrollDirection });
  if (relation === "far") {
    return false;
  }
  if (relation === "visible") {
    return Boolean(admissionPolicy?.allowVisible);
  }
  if (relation === "ahead") {
    return Boolean(admissionPolicy?.allowNearAhead);
  }
  return Boolean(admissionPolicy?.allowNearBehind);
}

export function isSmartPosterLoadingSupported() {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return false;
  }
  if (document.documentElement?.dataset?.deviceShell !== "iphone") {
    return false;
  }
  return typeof window.IntersectionObserver !== "undefined";
}

export function computeScrollSpeedBand({
  velocityPxPerMs = 0,
  idleForMs = Number.POSITIVE_INFINITY,
  viewportSettling = false,
  policy = SMART_POSTER_POLICY_PORTRAIT,
  veryFastScrollPxPerMs = policy.veryFastScrollPxPerMs,
  fastScrollPxPerMs = policy.fastScrollPxPerMs,
  mediumScrollPxPerMs = policy.mediumScrollPxPerMs,
  scrollIdleSettleMs = policy.scrollIdleSettleMs,
} = {}) {
  if (viewportSettling) {
    return SPEED_BAND_SETTLING;
  }
  if (idleForMs >= scrollIdleSettleMs) {
    return SPEED_BAND_IDLE;
  }
  if (velocityPxPerMs >= veryFastScrollPxPerMs) {
    return SPEED_BAND_VERY_FAST;
  }
  if (velocityPxPerMs >= fastScrollPxPerMs) {
    return SPEED_BAND_FAST;
  }
  if (velocityPxPerMs >= mediumScrollPxPerMs) {
    return SPEED_BAND_MEDIUM;
  }
  return SPEED_BAND_SLOW;
}

export function computeScrollMode({
  velocityPxPerMs = 0,
  fastScrolling = false,
  timeSinceLastScrollMs = Number.POSITIVE_INFINITY,
  viewportSettling = false,
  policy = SMART_POSTER_POLICY_PORTRAIT,
} = {}) {
  return computeScrollSpeedBand({
    velocityPxPerMs: Math.max(velocityPxPerMs, fastScrolling ? policy.fastScrollPxPerMs : 0),
    idleForMs: timeSinceLastScrollMs,
    viewportSettling,
    policy,
  });
}

export function getAdmissionPolicyForSpeedBand(
  speedBand = SPEED_BAND_IDLE,
  { policy = SMART_POSTER_POLICY_PORTRAIT } = {},
) {
  return policy.admissionPolicies[speedBand] || policy.admissionPolicies[SPEED_BAND_IDLE];
}

export function shouldTriggerImmediateAdmission({
  previousSpeedBand = SPEED_BAND_IDLE,
  nextSpeedBand = SPEED_BAND_IDLE,
  previousPolicy = SMART_POSTER_POLICY_PORTRAIT,
  nextPolicy = previousPolicy,
} = {}) {
  if (previousSpeedBand === nextSpeedBand && previousPolicy.name === nextPolicy.name) {
    return false;
  }
  const previousAdmissionPolicy = getAdmissionPolicyForSpeedBand(previousSpeedBand, { policy: previousPolicy });
  const nextAdmissionPolicy = getAdmissionPolicyForSpeedBand(nextSpeedBand, { policy: nextPolicy });
  if (previousSpeedBand === SPEED_BAND_SETTLING && nextSpeedBand !== SPEED_BAND_SETTLING) {
    return nextAdmissionPolicy.maxNewAdmissionsPerTick > 0;
  }
  return (
    SPEED_BAND_ORDER[nextSpeedBand] > SPEED_BAND_ORDER[previousSpeedBand]
    && (
      nextAdmissionPolicy.maxNewAdmissionsPerTick > previousAdmissionPolicy.maxNewAdmissionsPerTick
      || nextAdmissionPolicy.maxInFlight > previousAdmissionPolicy.maxInFlight
      || nextAdmissionPolicy.allowNearAhead !== previousAdmissionPolicy.allowNearAhead
      || nextAdmissionPolicy.allowNearBehind !== previousAdmissionPolicy.allowNearBehind
      || previousPolicy.name !== nextPolicy.name
    )
  );
}

export function resolvePosterAttachmentMode({
  enabled,
  isNearViewport,
  isMounted,
  speedBand = SPEED_BAND_IDLE,
  eligibleForAdmission = false,
  fastScrolling = false,
  viewportSettling = false,
  policy = SMART_POSTER_POLICY_PORTRAIT,
} = {}) {
  const effectiveSpeedBand = speedBand || computeScrollMode({
    velocityPxPerMs: fastScrolling ? policy.fastScrollPxPerMs : 0,
    timeSinceLastScrollMs: fastScrolling ? 0 : Number.POSITIVE_INFINITY,
    viewportSettling,
    policy,
  });
  if (!enabled) {
    return POSTER_MODE_ATTACH;
  }
  if (isMounted) {
    return POSTER_MODE_ATTACH;
  }
  if (!isNearViewport) {
    return POSTER_MODE_DETACH;
  }
  if (effectiveSpeedBand === SPEED_BAND_SETTLING || effectiveSpeedBand === SPEED_BAND_VERY_FAST) {
    return POSTER_MODE_DEFER;
  }
  if (eligibleForAdmission) {
    return POSTER_MODE_QUEUED;
  }
  return POSTER_MODE_DEFER;
}

export function scorePosterCandidate(candidate, { viewportCenterY = 0, scrollDirection = 0 } = {}) {
  const relation = getCandidateRelation(candidate, { viewportCenterY, scrollDirection });
  if (relation === "far") {
    return Number.NEGATIVE_INFINITY;
  }
  const centerY = toFiniteNumber(candidate.centerY, viewportCenterY);
  const distance = Math.abs(centerY - viewportCenterY);
  let score = 0;
  if (relation === "visible") {
    score += 20000;
  } else if (relation === "ahead") {
    score += 6000;
  } else {
    score += 2000;
  }
  if (candidate.loadedBefore) {
    score += 1000;
  }
  score -= Math.round(distance * 1.15);
  return score;
}

export function rankPosterCandidates(
  candidates,
  {
    viewportCenterY = 0,
    scrollDirection = 0,
    admissionPolicy = getAdmissionPolicyForSpeedBand(SPEED_BAND_IDLE),
  } = {},
) {
  return [...candidates]
    .filter((candidate) => isCandidateAllowedByPolicy(candidate, {
      admissionPolicy,
      viewportCenterY,
      scrollDirection,
    }))
    .map((candidate) => ({
      ...candidate,
      priority: scorePosterCandidate(candidate, { viewportCenterY, scrollDirection }),
    }))
    .filter((candidate) => Number.isFinite(candidate.priority))
    .sort((left, right) => {
      if (right.priority !== left.priority) {
        return right.priority - left.priority;
      }
      return compareById(left, right);
    });
}

export function admitPosterBatch(
  candidates,
  {
    inFlightCount = 0,
    admissionPolicy = null,
    maxInFlightImages = MAX_IN_FLIGHT_IMAGES,
    maxNewAdmissionsPerTick = MAX_NEW_ADMISSIONS_PER_TICK,
  } = {},
) {
  const effectiveMaxInFlight = admissionPolicy?.maxInFlight ?? maxInFlightImages;
  const effectiveMaxNewAdmissions = admissionPolicy?.maxNewAdmissionsPerTick ?? maxNewAdmissionsPerTick;
  const availableByInflight = Math.max(0, effectiveMaxInFlight - Math.max(0, inFlightCount));
  const allowed = Math.max(0, Math.min(effectiveMaxNewAdmissions, availableByInflight));
  if (!allowed) {
    return [];
  }
  return candidates.slice(0, allowed);
}

export function evictMountedPosters(
  cards,
  {
    maxMountedImages = MAX_MOUNTED_IMAGES,
    viewportCenterY = 0,
    scrollDirection = 0,
    preferNearBehindEviction = false,
  } = {},
) {
  const mountedCards = cards.filter((card) => card?.isMounted);
  if (mountedCards.length <= maxMountedImages) {
    return [];
  }
  const overflow = mountedCards.length - maxMountedImages;
  const relationPriority = preferNearBehindEviction
    ? { far: 0, behind: 1, ahead: 2, visible: 3 }
    : { far: 0, ahead: 1, behind: 1, visible: 2 };
  const evictableCards = mountedCards
    .filter((card) => !card.isVisible)
    .sort((left, right) => {
      const leftRelation = getCandidateRelation(left, { viewportCenterY, scrollDirection });
      const rightRelation = getCandidateRelation(right, { viewportCenterY, scrollDirection });
      const leftRelationPriority = relationPriority[leftRelation] ?? relationPriority.far;
      const rightRelationPriority = relationPriority[rightRelation] ?? relationPriority.far;
      if (leftRelationPriority !== rightRelationPriority) {
        return leftRelationPriority - rightRelationPriority;
      }
      const leftDistance = Math.abs(toFiniteNumber(left.centerY, viewportCenterY) - viewportCenterY);
      const rightDistance = Math.abs(toFiniteNumber(right.centerY, viewportCenterY) - viewportCenterY);
      if (rightDistance !== leftDistance) {
        return rightDistance - leftDistance;
      }
      const leftSeen = toFiniteNumber(left.lastSeenAt, 0);
      const rightSeen = toFiniteNumber(right.lastSeenAt, 0);
      if (leftSeen !== rightSeen) {
        return leftSeen - rightSeen;
      }
      return compareById(left, right);
    });
  return evictableCards.slice(0, overflow).map((card) => card.id);
}

function getCardSnapshot(card) {
  if (!card) {
    return EMPTY_CARD_SNAPSHOT;
  }
  return {
    mode: card.mode,
    isMounted: card.isMounted,
    inFlight: card.inFlight,
    loadedBefore: card.loadedBefore,
  };
}

function shouldLogDebugSummary() {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem(DEBUG_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function createCardState(id, posterUrl = "") {
  return {
    id,
    node: null,
    posterUrl,
    subscribers: new Set(),
    mode: POSTER_MODE_DEFER,
    isCandidate: false,
    isVisible: false,
    isMounted: false,
    inFlight: false,
    loadedBefore: false,
    lastSeenAt: 0,
    lastObservedScrollY: 0,
    lastObservedTop: 0,
    lastObservedBottom: 0,
    lastObservedCenterY: 0,
  };
}

function createScheduler() {
  const cards = new Map();
  const nodeToCardId = new WeakMap();
  const candidateIds = new Set();
  const mountedIds = new Set();
  const loadedPosterUrls = new Set();

  let mountedCount = 0;
  let inFlightCount = 0;
  let scrollDirection = 0;
  let speedBand = SPEED_BAND_IDLE;
  let viewportSettling = false;
  let currentPolicy = getSmartPosterSchedulerPolicy(getViewportMetrics());
  let intersectionObserver = null;
  let lastScrollEventAt = Number.NEGATIVE_INFINITY;
  let lastScrollSampleAt = nowMs();
  let lastScrollY = getScrollY();
  let lastVelocityPxPerMs = 0;
  let idleTimerId = 0;
  let viewportSettlingTimerId = 0;
  let scrollFrameId = 0;
  let recomputeFrameId = 0;
  let admissionTimerId = 0;
  let debugLoggedAt = 0;

  function createIntersectionObserver() {
    return new IntersectionObserver(handleIntersection, getSmartPosterObserverConfig({ policy: currentPolicy }));
  }

  function reobserveRegisteredCards() {
    cards.forEach((card) => {
      if (!card.node) {
        return;
      }
      nodeToCardId.set(card.node, card.id);
      intersectionObserver.observe(card.node);
    });
  }

  function recreateIntersectionObserver() {
    intersectionObserver?.disconnect();
    intersectionObserver = createIntersectionObserver();
    reobserveRegisteredCards();
  }

  function restartAdmissionTimer() {
    if (typeof window === "undefined") {
      return;
    }
    if (admissionTimerId) {
      window.clearInterval(admissionTimerId);
    }
    admissionTimerId = window.setInterval(() => {
      if (!cards.size) {
        return;
      }
      updatePolicyFromViewport(nowMs());
      syncSpeedBand(nowMs());
      recomputeCardModes();
    }, currentPolicy.admissionTickMs);
  }

  function updatePolicyFromViewport(referenceNow = nowMs()) {
    const previousPolicy = currentPolicy;
    const nextPolicy = getSmartPosterSchedulerPolicy(getViewportMetrics());
    const changed = previousPolicy.name !== nextPolicy.name;
    currentPolicy = nextPolicy;
    if (changed) {
      recreateIntersectionObserver();
      restartAdmissionTimer();
    }
    return {
      changed,
      previousPolicy,
      nextPolicy,
      referenceNow,
    };
  }

  function pruneCard(id) {
    const card = cards.get(id);
    if (!card) {
      return;
    }
    if (card.node || card.subscribers.size > 0 || card.isMounted || card.inFlight) {
      return;
    }
    cards.delete(id);
    if (!cards.size) {
      destroy();
    }
  }

  function notifyCard(card) {
    card.subscribers.forEach((callback) => {
      callback();
    });
  }

  function commitCardSnapshot(card, nextSnapshot) {
    const currentSnapshot = getCardSnapshot(card);
    if (
      currentSnapshot.mode === nextSnapshot.mode
      && currentSnapshot.isMounted === nextSnapshot.isMounted
      && currentSnapshot.inFlight === nextSnapshot.inFlight
      && currentSnapshot.loadedBefore === nextSnapshot.loadedBefore
    ) {
      return;
    }
    card.mode = nextSnapshot.mode;
    card.loadedBefore = nextSnapshot.loadedBefore;
    notifyCard(card);
  }

  function setCardInFlight(card, nextInFlight) {
    if (card.inFlight === nextInFlight) {
      return;
    }
    card.inFlight = nextInFlight;
    inFlightCount += nextInFlight ? 1 : -1;
    if (inFlightCount < 0) {
      inFlightCount = 0;
    }
  }

  function setCardMounted(card, nextMounted) {
    if (card.isMounted === nextMounted) {
      return false;
    }
    card.isMounted = nextMounted;
    if (nextMounted) {
      mountedIds.add(card.id);
      mountedCount += 1;
      return true;
    }
    mountedIds.delete(card.id);
    mountedCount -= 1;
    if (mountedCount < 0) {
      mountedCount = 0;
    }
    if (card.inFlight) {
      setCardInFlight(card, false);
    }
    return true;
  }

  function detachCard(card) {
    return setCardMounted(card, false);
  }

  function refreshCandidateMembership(card) {
    if (card.isCandidate || card.isVisible) {
      candidateIds.add(card.id);
      return;
    }
    candidateIds.delete(card.id);
  }

  function estimateCardPosition(card) {
    const deltaScroll = getScrollY() - toFiniteNumber(card.lastObservedScrollY, getScrollY());
    const top = toFiniteNumber(card.lastObservedTop, 0) - deltaScroll;
    const bottom = toFiniteNumber(card.lastObservedBottom, top) - deltaScroll;
    const centerY = toFiniteNumber(card.lastObservedCenterY, (top + bottom) / 2) - deltaScroll;
    return {
      top,
      bottom,
      centerY,
    };
  }

  function computeCurrentSpeedBand(referenceNow = nowMs()) {
    return computeScrollSpeedBand({
      velocityPxPerMs: lastVelocityPxPerMs,
      idleForMs: referenceNow - lastScrollEventAt,
      viewportSettling,
      policy: currentPolicy,
    });
  }

  function syncSpeedBand(referenceNow = nowMs(), policyChange = null) {
    const previousSpeedBand = speedBand;
    const previousPolicy = policyChange?.previousPolicy || currentPolicy;
    const nextPolicy = policyChange?.nextPolicy || currentPolicy;
    const nextSpeedBand = computeCurrentSpeedBand(referenceNow);
    speedBand = nextSpeedBand;
    return {
      previousSpeedBand,
      nextSpeedBand,
      immediateAdmission: shouldTriggerImmediateAdmission({
        previousSpeedBand,
        nextSpeedBand,
        previousPolicy,
        nextPolicy,
      }),
    };
  }

  function logDebugSummary({
    queuedCount = 0,
    visibleCount = 0,
    nearCount = 0,
    admittedLastTick = 0,
    detachedLastTick = 0,
  } = {}) {
    if (!shouldLogDebugSummary()) {
      return;
    }
    const now = nowMs();
    if (now - debugLoggedAt < 1000) {
      return;
    }
    debugLoggedAt = now;
    console.info("[smart-poster]", {
      orientation: currentPolicy.orientation,
      policyName: currentPolicy.name,
      rootMargin: currentPolicy.intersectionRootMargin,
      orientationSettleMs: currentPolicy.orientationSettleMs,
      scrollIdleSettleMs: currentPolicy.scrollIdleSettleMs,
      maxMountedImages: currentPolicy.maxMountedImages,
      admissionTickMs: currentPolicy.admissionTickMs,
      admissionPolicy: getAdmissionPolicyForSpeedBand(speedBand, { policy: currentPolicy }),
      speedBand,
      velocityPxPerMs: Number(lastVelocityPxPerMs.toFixed(3)),
      mountedCount,
      inFlightCount,
      queuedCount,
      visibleCount,
      nearCount,
      admittedLastTick,
      detachedLastTick,
      registeredCount: cards.size,
    });
  }

  function recomputeCardModes() {
    if (!cards.size) {
      return;
    }

    const { centerY: viewportCenterY } = getViewportMetrics();
    const relevantIds = new Set([...candidateIds, ...mountedIds]);
    const relevantCards = [];
    let detachedLastTick = 0;

    relevantIds.forEach((id) => {
      const card = cards.get(id);
      if (!card?.node) {
        return;
      }
      const position = estimateCardPosition(card);
      relevantCards.push({
        card,
        centerY: position.centerY,
        isCandidate: card.isCandidate,
        isVisible: card.isVisible,
      });
    });

    relevantCards.forEach(({ card, centerY }) => {
      if (card.isMounted && !card.isCandidate && !card.isVisible) {
        if (detachCard(card)) {
          detachedLastTick += 1;
        }
        commitCardSnapshot(card, {
          mode: POSTER_MODE_DETACH,
          isMounted: card.isMounted,
          inFlight: card.inFlight,
          loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
        });
      } else if ((card.isCandidate || card.isVisible) && card.isMounted) {
        card.lastSeenAt = nowMs();
        commitCardSnapshot(card, {
          mode: POSTER_MODE_ATTACH,
          isMounted: card.isMounted,
          inFlight: card.inFlight,
          loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
        });
      } else if (!card.isCandidate && !card.isVisible) {
        commitCardSnapshot(card, {
          mode: POSTER_MODE_DETACH,
          isMounted: card.isMounted,
          inFlight: card.inFlight,
          loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
        });
      }

      card.lastObservedCenterY = centerY;
    });

    const evictedIds = new Set(
      evictMountedPosters(
        relevantCards.map(({ card, centerY }) => ({
          id: card.id,
          isMounted: card.isMounted,
          isVisible: card.isVisible,
          isCandidate: card.isCandidate,
          centerY,
          lastSeenAt: card.lastSeenAt,
        })),
        {
          maxMountedImages: currentPolicy.maxMountedImages,
          viewportCenterY,
          scrollDirection,
          preferNearBehindEviction: currentPolicy.preferNearBehindEviction,
        },
      ),
    );

    evictedIds.forEach((id) => {
      const card = cards.get(id);
      if (!card) {
        return;
      }
      if (detachCard(card)) {
        detachedLastTick += 1;
      }
    });

    const admissionPolicy = getAdmissionPolicyForSpeedBand(speedBand, { policy: currentPolicy });
    const rankedCandidates = rankPosterCandidates(
      relevantCards
        .map(({ card, centerY }) => ({
          id: card.id,
          isVisible: card.isVisible,
          isCandidate: card.isCandidate,
          centerY,
          loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
        }))
        .filter((candidate) => {
          const card = cards.get(candidate.id);
          return candidate.isCandidate && !card?.isMounted;
        }),
      {
        viewportCenterY,
        scrollDirection,
        admissionPolicy,
      },
    );

    const admittedIds = new Set(
      admitPosterBatch(rankedCandidates, {
        inFlightCount,
        admissionPolicy,
      }).map((candidate) => candidate.id),
    );

    const queuedIds = new Set();
    rankedCandidates.forEach((candidate) => {
      if (!admittedIds.has(candidate.id)) {
        queuedIds.add(candidate.id);
      }
    });

    admittedIds.forEach((id) => {
      const card = cards.get(id);
      if (!card) {
        return;
      }
      setCardMounted(card, true);
      setCardInFlight(card, true);
      card.lastSeenAt = nowMs();
      commitCardSnapshot(card, {
        mode: POSTER_MODE_ATTACH,
        isMounted: card.isMounted,
        inFlight: card.inFlight,
        loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
      });
    });

    relevantCards.forEach(({ card }) => {
      if (card.isMounted || admittedIds.has(card.id)) {
        return;
      }
      const eligibleForAdmission = queuedIds.has(card.id);
      const nextMode = resolvePosterAttachmentMode({
        enabled: true,
        isNearViewport: card.isCandidate || card.isVisible,
        isMounted: card.isMounted,
        speedBand,
        eligibleForAdmission,
        policy: currentPolicy,
      });
      commitCardSnapshot(card, {
        mode: nextMode,
        isMounted: card.isMounted,
        inFlight: card.inFlight,
        loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
      });
    });

    logDebugSummary({
      queuedCount: queuedIds.size,
      visibleCount: relevantCards.filter(({ isVisible }) => isVisible).length,
      nearCount: relevantCards.filter(({ isCandidate, isVisible }) => isCandidate && !isVisible).length,
      admittedLastTick: admittedIds.size,
      detachedLastTick,
    });
  }

  function queueRecompute() {
    if (recomputeFrameId || typeof window === "undefined") {
      return;
    }
    recomputeFrameId = window.requestAnimationFrame(() => {
      recomputeFrameId = 0;
      recomputeCardModes();
    });
  }

  function handleIntersection(entries) {
    const currentScrollY = getScrollY();
    entries.forEach((entry) => {
      const cardId = nodeToCardId.get(entry.target);
      if (!cardId) {
        return;
      }
      const card = cards.get(cardId);
      if (!card) {
        return;
      }
      const rootBounds = entry.rootBounds || {
        top: 0,
        left: 0,
        right: getViewportMetrics().width,
        bottom: getViewportMetrics().height,
      };
      const rect = entry.boundingClientRect;
      card.isCandidate = entry.isIntersecting;
      card.isVisible = (
        rect.bottom > rootBounds.top
        && rect.top < rootBounds.bottom
        && rect.right > rootBounds.left
        && rect.left < rootBounds.right
      );
      card.lastObservedTop = rect.top;
      card.lastObservedBottom = rect.bottom;
      card.lastObservedCenterY = (rect.top + rect.bottom) / 2;
      card.lastObservedScrollY = currentScrollY;
      if (card.isCandidate || card.isVisible) {
        card.lastSeenAt = nowMs();
      } else if (!card.isMounted) {
        commitCardSnapshot(card, {
          mode: POSTER_MODE_DETACH,
          isMounted: card.isMounted,
          inFlight: card.inFlight,
          loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
        });
      }
      refreshCandidateMembership(card);
    });
    queueRecompute();
  }

  function handleScrollFrame() {
    scrollFrameId = 0;
    const currentScrollY = getScrollY();
    const timestamp = nowMs();
    const deltaY = currentScrollY - lastScrollY;
    const deltaTime = Math.max(1, timestamp - lastScrollSampleAt);
    const sampledVelocity = Math.abs(deltaY) / deltaTime;
    lastVelocityPxPerMs = lastVelocityPxPerMs
      ? ((lastVelocityPxPerMs * (1 - VELOCITY_SMOOTHING_ALPHA)) + (sampledVelocity * VELOCITY_SMOOTHING_ALPHA))
      : sampledVelocity;
    if (deltaY > 0) {
      scrollDirection = 1;
    } else if (deltaY < 0) {
      scrollDirection = -1;
    }
    lastScrollY = currentScrollY;
    lastScrollSampleAt = timestamp;
    const policyChange = updatePolicyFromViewport(timestamp);
    syncSpeedBand(timestamp, policyChange);
    recomputeCardModes();
  }

  function scheduleScrollFrame() {
    if (scrollFrameId || typeof window === "undefined") {
      return;
    }
    scrollFrameId = window.requestAnimationFrame(handleScrollFrame);
  }

  function settleScrolling() {
    idleTimerId = 0;
    lastVelocityPxPerMs = 0;
    const policyChange = updatePolicyFromViewport(nowMs());
    syncSpeedBand(nowMs(), policyChange);
    recomputeCardModes();
  }

  function handleWindowScroll() {
    lastScrollEventAt = nowMs();
    if (idleTimerId && typeof window !== "undefined") {
      window.clearTimeout(idleTimerId);
    }
    if (typeof window !== "undefined") {
      idleTimerId = window.setTimeout(settleScrolling, currentPolicy.scrollIdleSettleMs);
    }
    scheduleScrollFrame();
  }

  function finishViewportSettling() {
    viewportSettlingTimerId = 0;
    viewportSettling = false;
    const policyChange = updatePolicyFromViewport(nowMs());
    syncSpeedBand(nowMs(), policyChange);
    recomputeCardModes();
  }

  function handleViewportChange() {
    viewportSettling = true;
    const policyChange = updatePolicyFromViewport(nowMs());
    syncSpeedBand(nowMs(), policyChange);
    if (viewportSettlingTimerId && typeof window !== "undefined") {
      window.clearTimeout(viewportSettlingTimerId);
    }
    if (typeof window !== "undefined") {
      viewportSettlingTimerId = window.setTimeout(finishViewportSettling, currentPolicy.orientationSettleMs);
    }
    queueRecompute();
  }

  function registerCard({ id, node, posterUrl = "" }) {
    const card = cards.get(id) || createCardState(id, posterUrl);
    card.posterUrl = posterUrl;
    cards.set(id, card);
    if (card.node && card.node !== node) {
      intersectionObserver.unobserve(card.node);
      nodeToCardId.delete(card.node);
    }
    card.node = node;
    if (node) {
      nodeToCardId.set(node, id);
      intersectionObserver.observe(node);
    }
    queueRecompute();
  }

  function unregisterCard(id) {
    const card = cards.get(id);
    if (!card) {
      return;
    }
    if (card.node) {
      intersectionObserver.unobserve(card.node);
      nodeToCardId.delete(card.node);
    }
    candidateIds.delete(id);
    mountedIds.delete(id);
    if (card.isMounted) {
      mountedCount -= 1;
      if (mountedCount < 0) {
        mountedCount = 0;
      }
    }
    if (card.inFlight) {
      inFlightCount -= 1;
      if (inFlightCount < 0) {
        inFlightCount = 0;
      }
    }
    card.node = null;
    card.isCandidate = false;
    card.isVisible = false;
    card.isMounted = false;
    card.inFlight = false;
    pruneCard(id);
  }

  function subscribeCard(id, callback) {
    const card = cards.get(id) || createCardState(id);
    cards.set(id, card);
    card.subscribers.add(callback);
    return () => {
      const currentCard = cards.get(id);
      if (!currentCard) {
        return;
      }
      currentCard.subscribers.delete(callback);
      pruneCard(id);
    };
  }

  function markCardLoaded(id) {
    const card = cards.get(id);
    if (!card) {
      return;
    }
    setCardInFlight(card, false);
    card.loadedBefore = true;
    if (card.posterUrl) {
      loadedPosterUrls.add(card.posterUrl);
    }
    card.lastSeenAt = nowMs();
    commitCardSnapshot(card, {
      mode: POSTER_MODE_ATTACH,
      isMounted: card.isMounted,
      inFlight: card.inFlight,
      loadedBefore: true,
    });
    queueRecompute();
  }

  function markCardError(id) {
    const card = cards.get(id);
    if (!card) {
      return;
    }
    detachCard(card);
    commitCardSnapshot(card, {
      mode: POSTER_MODE_DETACH,
      isMounted: card.isMounted,
      inFlight: card.inFlight,
      loadedBefore: card.loadedBefore || loadedPosterUrls.has(card.posterUrl),
    });
    queueRecompute();
  }

  function getSnapshot(id) {
    return getCardSnapshot(cards.get(id));
  }

  function destroy() {
    if (typeof window !== "undefined") {
      window.removeEventListener("scroll", handleWindowScroll);
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("orientationchange", handleViewportChange);
      if (idleTimerId) {
        window.clearTimeout(idleTimerId);
      }
      if (viewportSettlingTimerId) {
        window.clearTimeout(viewportSettlingTimerId);
      }
      if (recomputeFrameId) {
        window.cancelAnimationFrame(recomputeFrameId);
      }
      if (scrollFrameId) {
        window.cancelAnimationFrame(scrollFrameId);
      }
      if (admissionTimerId) {
        window.clearInterval(admissionTimerId);
      }
    }
    intersectionObserver?.disconnect();
    schedulerSingleton = null;
  }

  intersectionObserver = createIntersectionObserver();

  if (typeof window !== "undefined") {
    window.addEventListener("scroll", handleWindowScroll, { passive: true });
    window.addEventListener("resize", handleViewportChange, { passive: true });
    window.addEventListener("orientationchange", handleViewportChange, { passive: true });
    restartAdmissionTimer();
  }

  return {
    registerCard,
    unregisterCard,
    subscribeCard,
    getSnapshot,
    markCardLoaded,
    markCardError,
  };
}

function getScheduler() {
  if (!isSmartPosterLoadingSupported()) {
    return null;
  }
  if (!schedulerSingleton) {
    schedulerSingleton = createScheduler();
  }
  return schedulerSingleton;
}

export function subscribeSmartPosterCard(id, callback) {
  const scheduler = getScheduler();
  if (!scheduler) {
    return () => {};
  }
  return scheduler.subscribeCard(id, callback);
}

export function getSmartPosterCardSnapshot(id) {
  const scheduler = getScheduler();
  if (!scheduler) {
    return EMPTY_CARD_SNAPSHOT;
  }
  return scheduler.getSnapshot(id);
}

export function registerSmartPosterCard({ id, node, posterUrl = "" }) {
  const scheduler = getScheduler();
  if (!scheduler) {
    return;
  }
  scheduler.registerCard({ id, node, posterUrl });
}

export function unregisterSmartPosterCard(id) {
  const scheduler = getScheduler();
  if (!scheduler) {
    return;
  }
  scheduler.unregisterCard(id);
}

export function markSmartPosterCardLoaded(id) {
  const scheduler = getScheduler();
  if (!scheduler) {
    return;
  }
  scheduler.markCardLoaded(id);
}

export function markSmartPosterCardError(id) {
  const scheduler = getScheduler();
  if (!scheduler) {
    return;
  }
  scheduler.markCardError(id);
}
