export function buildServiceWorkerRegistration(baseUri = document.baseURI) {
  const scopeUrl = new URL("./", baseUri);
  return {
    scriptUrl: new URL("sw.js", scopeUrl).href,
    scope: scopeUrl.pathname,
  };
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
  try {
    const registration = await serviceWorker.register(registrationConfig.scriptUrl, {
      scope: registrationConfig.scope,
      updateViaCache: "none",
    });
    if (typeof serviceWorker.getRegistrations === "function") {
      const registrations = await serviceWorker.getRegistrations();
      await Promise.all(registrations.map(async (candidate) => {
        if (candidate === registration || candidate.scope === registration.scope) {
          return;
        }
        const scriptUrl = candidate.active?.scriptURL || candidate.waiting?.scriptURL || candidate.installing?.scriptURL || "";
        if (new URL(scriptUrl || location.href).pathname.endsWith("/sw.js")) {
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
