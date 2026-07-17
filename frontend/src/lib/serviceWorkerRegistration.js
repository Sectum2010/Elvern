const ELVERN_WORKER_IDENTITY_PARAM = "elvern_worker";
const ELVERN_WORKER_IDENTITY = "offline-shell-v1";
const ELVERN_DYNAMIC_SCOPE_PATTERN = /^\/[a-hjkmnp-z2-9]{8,24}\/$/;


export function buildServiceWorkerRegistration(baseUri = document.baseURI) {
  const scopeUrl = new URL("./", baseUri);
  const scriptUrl = new URL("sw.js", scopeUrl);
  scriptUrl.searchParams.set(ELVERN_WORKER_IDENTITY_PARAM, ELVERN_WORKER_IDENTITY);
  return {
    scriptUrl: scriptUrl.href,
    scope: scopeUrl.pathname,
  };
}


export function isElvernOfflineWorkerRegistration(candidate, currentOrigin) {
  const scriptValue = candidate?.active?.scriptURL
    || candidate?.waiting?.scriptURL
    || candidate?.installing?.scriptURL
    || "";
  try {
    const scopeUrl = new URL(candidate.scope);
    const scriptUrl = new URL(scriptValue);
    return scopeUrl.origin === currentOrigin
      && scriptUrl.origin === currentOrigin
      && ELVERN_DYNAMIC_SCOPE_PATTERN.test(scopeUrl.pathname)
      && scriptUrl.pathname === `${scopeUrl.pathname}sw.js`
      && scriptUrl.searchParams.get(ELVERN_WORKER_IDENTITY_PARAM) === ELVERN_WORKER_IDENTITY;
  } catch {
    return false;
  }
}


export async function registerElvernServiceWorker({
  baseUri = document.baseURI,
  serviceWorker = navigator.serviceWorker,
  warn = console.warn,
} = {}) {
  if (!serviceWorker?.register) {
    return null;
  }
  const registrationConfig = buildServiceWorkerRegistration(baseUri);
  const currentOrigin = new URL(baseUri).origin;
  try {
    const registration = await serviceWorker.register(registrationConfig.scriptUrl, {
      scope: registrationConfig.scope,
      updateViaCache: "none",
    });
    if (registration.active && !registration.installing && !registration.waiting && typeof registration.update === "function") {
      try {
        await registration.update();
      } catch {
        warn("Elvern offline recovery update check failed; the current worker remains available.");
      }
    }
    if (typeof serviceWorker.getRegistrations === "function") {
      const registrations = await serviceWorker.getRegistrations();
      await Promise.all(registrations.map(async (candidate) => {
        if (candidate === registration || candidate.scope === registration.scope) {
          return;
        }
        if (isElvernOfflineWorkerRegistration(candidate, currentOrigin)) {
          await candidate.unregister();
        }
      }));
    }
    return registration;
  } catch {
    warn("Elvern offline recovery could not be installed; online use is unaffected.");
    return null;
  }
}
