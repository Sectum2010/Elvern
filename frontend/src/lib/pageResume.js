export const PAGE_RESUME_EVENT = "elvern:page-resume";
export const PAGE_RESUME_COALESCE_MS = 300;

let installed = false;
let resumeGeneration = 0;
let pendingTimer = 0;
let pendingPersisted = false;
// A resume only follows a genuine departure. The initial document load — its
// first non-persisted pageshow and its first normal focus — is not a return, so
// we require evidence that the page was actually backgrounded, blurred, or
// unloaded before treating a focus/visibility signal as a resume. A persisted
// BFCache pageshow is always a return regardless of this flag.
let awayPending = false;


function navigationTypeCategory() {
  const value = performance?.getEntriesByType?.("navigation")?.[0]?.type;
  return ["navigate", "reload", "back_forward", "prerender"].includes(value)
    ? value
    : "unknown";
}


function dispatchResume() {
  pendingTimer = 0;
  if (document.visibilityState === "hidden" || !awayPending) {
    return;
  }
  awayPending = false;
  resumeGeneration += 1;
  window.dispatchEvent(new CustomEvent(PAGE_RESUME_EVENT, {
    detail: {
      generation: resumeGeneration,
      pageshowPersisted: pendingPersisted,
      navigationType: navigationTypeCategory(),
    },
  }));
  pendingPersisted = false;
}


function markAway() {
  if (pendingTimer) {
    window.clearTimeout(pendingTimer);
    pendingTimer = 0;
  }
  awayPending = true;
}


function scheduleResume(event) {
  const type = event?.type;

  // Track departures. A hidden visibility transition or a blur/pagehide arms the
  // next visible/focus signal to count as a genuine return, and still lets
  // consumers pause background work while hidden.
  if (type === "visibilitychange" && document.visibilityState === "hidden") {
    markAway();
    return;
  }
  if (document.visibilityState === "hidden") {
    return;
  }

  let isResume = false;
  if (type === "pageshow") {
    // A persisted BFCache restore is always a real return; a non-persisted
    // pageshow is the initial/fresh document load and never a resume.
    isResume = Boolean(event?.persisted);
    if (isResume) {
      awayPending = true;
    }
  } else if (type === "focus" || type === "visibilitychange") {
    // Focus/visibility only count once the page has actually been away, so the
    // document's first normal focus at load produces no resume generation.
    isResume = awayPending;
  }

  if (!isResume) {
    return;
  }

  pendingPersisted = pendingPersisted || Boolean(event?.persisted);
  if (pendingTimer) {
    window.clearTimeout(pendingTimer);
  }
  pendingTimer = window.setTimeout(dispatchResume, PAGE_RESUME_COALESCE_MS);
}


export function installPageResumeCoordinator() {
  if (installed || typeof window === "undefined" || typeof document === "undefined") {
    return;
  }
  installed = true;
  window.addEventListener("focus", scheduleResume);
  window.addEventListener("blur", markAway);
  window.addEventListener("pageshow", scheduleResume);
  window.addEventListener("pagehide", markAway);
  document.addEventListener("visibilitychange", scheduleResume);
}


export function resetPageResumeCoordinatorForTests() {
  if (typeof window !== "undefined" && typeof document !== "undefined") {
    window.removeEventListener("focus", scheduleResume);
    window.removeEventListener("blur", markAway);
    window.removeEventListener("pageshow", scheduleResume);
    window.removeEventListener("pagehide", markAway);
    document.removeEventListener("visibilitychange", scheduleResume);
    if (pendingTimer) {
      window.clearTimeout(pendingTimer);
    }
  }
  installed = false;
  resumeGeneration = 0;
  pendingTimer = 0;
  pendingPersisted = false;
  awayPending = false;
}
