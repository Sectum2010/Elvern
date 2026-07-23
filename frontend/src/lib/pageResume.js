export const PAGE_RESUME_EVENT = "elvern:page-resume";
export const PAGE_RESUME_COALESCE_MS = 300;

let installed = false;
let resumeGeneration = 0;
let pendingTimer = 0;
let pendingPersisted = false;


function navigationTypeCategory() {
  const value = performance?.getEntriesByType?.("navigation")?.[0]?.type;
  return ["navigate", "reload", "back_forward", "prerender"].includes(value)
    ? value
    : "unknown";
}


function dispatchResume() {
  pendingTimer = 0;
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


function scheduleResume(event) {
  if (document.visibilityState === "hidden") {
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
  window.addEventListener("pageshow", scheduleResume);
  document.addEventListener("visibilitychange", scheduleResume);
}


export function resetPageResumeCoordinatorForTests() {
  if (typeof window !== "undefined" && typeof document !== "undefined") {
    window.removeEventListener("focus", scheduleResume);
    window.removeEventListener("pageshow", scheduleResume);
    document.removeEventListener("visibilitychange", scheduleResume);
    if (pendingTimer) {
      window.clearTimeout(pendingTimer);
    }
  }
  installed = false;
  resumeGeneration = 0;
  pendingTimer = 0;
  pendingPersisted = false;
}
