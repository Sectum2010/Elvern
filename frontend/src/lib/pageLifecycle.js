let lifecycleGeneration = 0;
let lifecycleController = new AbortController();


function renewLifecycleSignal() {
  if (!lifecycleController.signal.aborted) {
    return;
  }
  lifecycleGeneration += 1;
  lifecycleController = new AbortController();
}

function handlePageHide() {
  lifecycleController.abort();
}

function handlePageShow() {
  renewLifecycleSignal();
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", handlePageHide);
  window.addEventListener("pageshow", handlePageShow);
}


export function getPageLifecycleGeneration() {
  return lifecycleGeneration;
}


export function getPageLifecycleSignal() {
  return lifecycleController.signal;
}

export function resetPageLifecycleForTests() {
  lifecycleController.abort();
  lifecycleGeneration = 0;
  lifecycleController = new AbortController();
}


export function combineAbortSignals(signals) {
  const activeSignals = signals.filter(Boolean);
  if (activeSignals.length === 0) {
    return { signal: undefined, cleanup: () => {} };
  }
  if (activeSignals.length === 1) {
    return { signal: activeSignals[0], cleanup: () => {} };
  }
  const controller = new AbortController();
  const abort = (event) => controller.abort(event?.target?.reason);
  activeSignals.forEach((signal) => {
    if (signal.aborted) {
      controller.abort(signal.reason);
    } else {
      signal.addEventListener("abort", abort, { once: true });
    }
  });
  return {
    signal: controller.signal,
    cleanup() {
      activeSignals.forEach((signal) => signal.removeEventListener("abort", abort));
    },
  };
}
