export const DETAIL_PERFORMANCE_MARKS = Object.freeze({
  cardClick: "elvern:detail:card-click",
  routeCommit: "elvern:detail:route-commit",
  shellVisible: "elvern:detail:shell-visible",
  metadataReceived: "elvern:detail:metadata-received",
  progressReceived: "elvern:detail:progress-received",
  playbackCapabilityReceived: "elvern:detail:playback-capability-received",
  desktopCapabilityReceived: "elvern:detail:desktop-capability-received",
  interactiveReady: "elvern:detail:interactive-ready",
});


function timingEnabled() {
  const value = String(import.meta.env.VITE_ELVERN_DETAIL_TIMING_ENABLED || "").trim().toLowerCase();
  return ["1", "true", "on"].includes(value);
}


export function markDetailPerformance(name, {
  enabled = timingEnabled(),
  performanceObject = globalThis.performance,
} = {}) {
  if (!enabled || !Object.values(DETAIL_PERFORMANCE_MARKS).includes(name)) {
    return false;
  }
  performanceObject?.mark?.(name);
  return true;
}


export function startDetailPerformanceTrace(options = {}) {
  const { performanceObject = globalThis.performance } = options;
  if (!(options.enabled ?? timingEnabled())) {
    return false;
  }
  for (const markName of Object.values(DETAIL_PERFORMANCE_MARKS)) {
    performanceObject?.clearMarks?.(markName);
  }
  return markDetailPerformance(DETAIL_PERFORMANCE_MARKS.cardClick, {
    ...options,
    enabled: true,
    performanceObject,
  });
}
