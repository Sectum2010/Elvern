import test from "node:test";
import assert from "node:assert/strict";

import {
  ADMISSION_TICK_MS,
  admitPosterBatch,
  computeScrollSpeedBand,
  evictMountedPosters,
  FAST_SCROLL_PX_PER_MS,
  getAdmissionPolicyForSpeedBand,
  getSmartPosterObserverConfig,
  getSmartPosterOrientation,
  getSmartPosterSchedulerPolicy,
  INTERSECTION_ROOT_MARGIN,
  LANDSCAPE_ADMISSION_TICK_MS,
  LANDSCAPE_INTERSECTION_ROOT_MARGIN,
  LANDSCAPE_MAX_MOUNTED_IMAGES,
  LANDSCAPE_ORIENTATION_SETTLE_MS,
  MAX_IN_FLIGHT_IMAGES,
  MAX_MOUNTED_IMAGES,
  MAX_NEW_ADMISSIONS_PER_TICK,
  MEDIUM_SCROLL_PX_PER_MS,
  ORIENTATION_SETTLE_MS,
  POSTER_MODE_ATTACH,
  POSTER_MODE_DEFER,
  POSTER_MODE_DETACH,
  POSTER_MODE_QUEUED,
  resolvePosterAttachmentMode,
  SCROLL_IDLE_SETTLE_MS,
  shouldTriggerImmediateAdmission,
  SMART_POSTER_ORIENTATION_LANDSCAPE,
  SMART_POSTER_ORIENTATION_PORTRAIT,
  SMART_POSTER_POLICY_LANDSCAPE,
  SMART_POSTER_POLICY_PORTRAIT,
  SPEED_BAND_FAST,
  SPEED_BAND_IDLE,
  SPEED_BAND_MEDIUM,
  SPEED_BAND_SETTLING,
  SPEED_BAND_SLOW,
  SPEED_BAND_VERY_FAST,
  VERY_FAST_SCROLL_PX_PER_MS,
} from "./smartPosterLoading.js";


test("scheduler disabled or desktop behavior attaches posters immediately", () => {
  assert.equal(
    resolvePosterAttachmentMode({
      enabled: false,
      isNearViewport: false,
      isMounted: false,
    }),
    POSTER_MODE_ATTACH,
  );
});


test("portrait policy values remain exactly unchanged", () => {
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.intersectionRootMargin, INTERSECTION_ROOT_MARGIN);
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.veryFastScrollPxPerMs, VERY_FAST_SCROLL_PX_PER_MS);
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.fastScrollPxPerMs, FAST_SCROLL_PX_PER_MS);
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.mediumScrollPxPerMs, MEDIUM_SCROLL_PX_PER_MS);
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.scrollIdleSettleMs, SCROLL_IDLE_SETTLE_MS);
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.orientationSettleMs, ORIENTATION_SETTLE_MS);
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.admissionTickMs, ADMISSION_TICK_MS);
  assert.equal(SMART_POSTER_POLICY_PORTRAIT.maxMountedImages, MAX_MOUNTED_IMAGES);
  assert.equal(
    getAdmissionPolicyForSpeedBand(SPEED_BAND_IDLE, { policy: SMART_POSTER_POLICY_PORTRAIT }).maxNewAdmissionsPerTick,
    MAX_NEW_ADMISSIONS_PER_TICK,
  );
  assert.equal(
    getAdmissionPolicyForSpeedBand(SPEED_BAND_IDLE, { policy: SMART_POSTER_POLICY_PORTRAIT }).maxInFlight,
    MAX_IN_FLIGHT_IMAGES,
  );
  assert.equal(
    getAdmissionPolicyForSpeedBand(SPEED_BAND_SLOW, { policy: SMART_POSTER_POLICY_PORTRAIT }).maxNewAdmissionsPerTick,
    3,
  );
  assert.equal(
    getAdmissionPolicyForSpeedBand(SPEED_BAND_SLOW, { policy: SMART_POSTER_POLICY_PORTRAIT }).maxInFlight,
    7,
  );
});


test("landscape rootMargin is tightened to 60 percent by 8 percent", () => {
  assert.equal(SMART_POSTER_POLICY_LANDSCAPE.intersectionRootMargin, LANDSCAPE_INTERSECTION_ROOT_MARGIN);
  assert.equal(LANDSCAPE_INTERSECTION_ROOT_MARGIN, "60% 8% 60% 8%");
});


test("landscape orientation settle is 540ms", () => {
  assert.equal(SMART_POSTER_POLICY_LANDSCAPE.orientationSettleMs, LANDSCAPE_ORIENTATION_SETTLE_MS);
  assert.equal(LANDSCAPE_ORIENTATION_SETTLE_MS, 540);
});


test("landscape admission tick is 160ms", () => {
  assert.equal(SMART_POSTER_POLICY_LANDSCAPE.admissionTickMs, LANDSCAPE_ADMISSION_TICK_MS);
  assert.equal(LANDSCAPE_ADMISSION_TICK_MS, 160);
});


test("landscape max mounted images is 36", () => {
  assert.equal(SMART_POSTER_POLICY_LANDSCAPE.maxMountedImages, LANDSCAPE_MAX_MOUNTED_IMAGES);
  assert.equal(LANDSCAPE_MAX_MOUNTED_IMAGES, 36);
});


test("landscape idle admits 2 per tick with maxInFlight 4", () => {
  const policy = getAdmissionPolicyForSpeedBand(SPEED_BAND_IDLE, { policy: SMART_POSTER_POLICY_LANDSCAPE });
  assert.equal(policy.maxNewAdmissionsPerTick, 2);
  assert.equal(policy.maxInFlight, 4);
});


test("landscape slow admits 1 per tick with maxInFlight 3", () => {
  const policy = getAdmissionPolicyForSpeedBand(SPEED_BAND_SLOW, { policy: SMART_POSTER_POLICY_LANDSCAPE });
  assert.equal(policy.maxNewAdmissionsPerTick, 1);
  assert.equal(policy.maxInFlight, 3);
  assert.equal(policy.allowNearBehind, true);
});


test("landscape medium admits 1 per tick and not behind", () => {
  const policy = getAdmissionPolicyForSpeedBand(SPEED_BAND_MEDIUM, { policy: SMART_POSTER_POLICY_LANDSCAPE });
  assert.equal(policy.maxNewAdmissionsPerTick, 1);
  assert.equal(policy.maxInFlight, 2);
  assert.equal(policy.allowVisible, true);
  assert.equal(policy.allowNearAhead, true);
  assert.equal(policy.allowNearBehind, false);
});


test("landscape fast admits visible only with maxInFlight 1", () => {
  const policy = getAdmissionPolicyForSpeedBand(SPEED_BAND_FAST, { policy: SMART_POSTER_POLICY_LANDSCAPE });
  assert.equal(policy.maxNewAdmissionsPerTick, 1);
  assert.equal(policy.maxInFlight, 1);
  assert.equal(policy.allowVisible, true);
  assert.equal(policy.allowNearAhead, false);
  assert.equal(policy.allowNearBehind, false);
});


test("policy selection returns portrait for portrait viewport", () => {
  assert.equal(
    getSmartPosterOrientation({ width: 390, height: 844 }),
    SMART_POSTER_ORIENTATION_PORTRAIT,
  );
  assert.equal(
    getSmartPosterSchedulerPolicy({ width: 390, height: 844 }).name,
    SMART_POSTER_ORIENTATION_PORTRAIT,
  );
});


test("policy selection returns landscape for landscape viewport", () => {
  assert.equal(
    getSmartPosterOrientation({ width: 844, height: 390 }),
    SMART_POSTER_ORIENTATION_LANDSCAPE,
  );
  assert.equal(
    getSmartPosterSchedulerPolicy({ width: 844, height: 390 }).name,
    SMART_POSTER_ORIENTATION_LANDSCAPE,
  );
});


test("observer config rootMargin changes between portrait and landscape", () => {
  const portraitObserverConfig = getSmartPosterObserverConfig({ policy: SMART_POSTER_POLICY_PORTRAIT });
  const landscapeObserverConfig = getSmartPosterObserverConfig({ policy: SMART_POSTER_POLICY_LANDSCAPE });

  assert.equal(portraitObserverConfig.rootMargin, INTERSECTION_ROOT_MARGIN);
  assert.equal(landscapeObserverConfig.rootMargin, LANDSCAPE_INTERSECTION_ROOT_MARGIN);
  assert.notEqual(portraitObserverConfig.rootMargin, landscapeObserverConfig.rootMargin);
});


test("computeScrollSpeedBand thresholds remain unchanged", () => {
  assert.equal(
    computeScrollSpeedBand({
      velocityPxPerMs: MEDIUM_SCROLL_PX_PER_MS - 0.05,
      idleForMs: 20,
      viewportSettling: false,
      policy: SMART_POSTER_POLICY_LANDSCAPE,
    }),
    SPEED_BAND_SLOW,
  );
  assert.equal(
    computeScrollSpeedBand({
      velocityPxPerMs: MEDIUM_SCROLL_PX_PER_MS,
      idleForMs: 20,
      viewportSettling: false,
      policy: SMART_POSTER_POLICY_LANDSCAPE,
    }),
    SPEED_BAND_MEDIUM,
  );
  assert.equal(
    computeScrollSpeedBand({
      velocityPxPerMs: FAST_SCROLL_PX_PER_MS,
      idleForMs: 20,
      viewportSettling: false,
      policy: SMART_POSTER_POLICY_PORTRAIT,
    }),
    SPEED_BAND_FAST,
  );
  assert.equal(
    computeScrollSpeedBand({
      velocityPxPerMs: VERY_FAST_SCROLL_PX_PER_MS,
      idleForMs: 20,
      viewportSettling: false,
      policy: SMART_POSTER_POLICY_LANDSCAPE,
    }),
    SPEED_BAND_VERY_FAST,
  );
});


test("landscape eviction uses the lower max mounted limit and protects visible cards", () => {
  const cards = [];
  for (let index = 0; index < LANDSCAPE_MAX_MOUNTED_IMAGES + 2; index += 1) {
    cards.push({
      id: `card-${index}`,
      isMounted: true,
      isVisible: index === 0,
      isCandidate: index <= 2,
      centerY: 500 + (index * 40),
      lastSeenAt: index,
    });
  }
  const evicted = evictMountedPosters(cards, {
    maxMountedImages: LANDSCAPE_MAX_MOUNTED_IMAGES,
    viewportCenterY: 500,
    scrollDirection: 1,
    preferNearBehindEviction: true,
  });

  assert.equal(evicted.length, 2);
  assert.equal(evicted.includes("card-0"), false);
});


test("immediate admission still resumes after settling ends", () => {
  assert.equal(
    shouldTriggerImmediateAdmission({
      previousSpeedBand: SPEED_BAND_SETTLING,
      nextSpeedBand: SPEED_BAND_IDLE,
      previousPolicy: SMART_POSTER_POLICY_LANDSCAPE,
      nextPolicy: SMART_POSTER_POLICY_LANDSCAPE,
    }),
    true,
  );
});


test("very fast band admits none in landscape", () => {
  const candidates = Array.from({ length: 4 }, (_, index) => ({ id: `card-${index}` }));
  const policy = getAdmissionPolicyForSpeedBand(SPEED_BAND_VERY_FAST, { policy: SMART_POSTER_POLICY_LANDSCAPE });

  assert.equal(
    admitPosterBatch(candidates, {
      inFlightCount: 0,
      admissionPolicy: policy,
    }).length,
    0,
  );
});


test("landscape dynamic inFlight budget is enforced", () => {
  const candidates = Array.from({ length: 4 }, (_, index) => ({ id: `card-${index}` }));
  const policy = getAdmissionPolicyForSpeedBand(SPEED_BAND_IDLE, { policy: SMART_POSTER_POLICY_LANDSCAPE });

  assert.equal(
    admitPosterBatch(candidates, {
      inFlightCount: policy.maxInFlight - 1,
      admissionPolicy: policy,
    }).length,
    1,
  );
  assert.equal(
    admitPosterBatch(candidates, {
      inFlightCount: policy.maxInFlight,
      admissionPolicy: policy,
    }).length,
    0,
  );
});


test("queued and detach states remain scheduler states not errors", () => {
  assert.equal(
    resolvePosterAttachmentMode({
      enabled: true,
      isNearViewport: true,
      isMounted: false,
      speedBand: SPEED_BAND_IDLE,
      eligibleForAdmission: true,
      policy: SMART_POSTER_POLICY_PORTRAIT,
    }),
    POSTER_MODE_QUEUED,
  );
  assert.equal(
    resolvePosterAttachmentMode({
      enabled: true,
      isNearViewport: false,
      isMounted: false,
      speedBand: SPEED_BAND_IDLE,
      policy: SMART_POSTER_POLICY_PORTRAIT,
    }),
    POSTER_MODE_DETACH,
  );
  assert.equal(
    resolvePosterAttachmentMode({
      enabled: true,
      isNearViewport: true,
      isMounted: false,
      speedBand: SPEED_BAND_VERY_FAST,
      policy: SMART_POSTER_POLICY_LANDSCAPE,
    }),
    POSTER_MODE_DEFER,
  );
});
