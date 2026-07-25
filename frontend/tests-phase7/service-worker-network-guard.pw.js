import { expect, test } from "@playwright/test";


const EXPECTED_EXTERNAL_ORIGIN = "http://elvern-guard-test.invalid";


async function proxyControl(path, init) {
  const controlOrigin = process.env.ELVERN_PHASE7_NETWORK_PROXY_CONTROL;
  if (!controlOrigin) {
    throw new Error("The loopback-only browser proxy control endpoint is required.");
  }
  return fetch(`${controlOrigin}${path}`, {
    cache: "no-store",
    ...init,
  });
}


test("browser-level authority blocks a Service Worker external fetch", async ({
  page,
  baseURL,
}) => {
  await proxyControl("/__elvern_network_guard_clear", { method: "POST" });
  try {
    await page.goto("network-guard-fixture/index.html");
    const scriptUrl = `${baseURL}network-guard-fixture/service-worker.js`;
    const blocked = await page.evaluate(async ({ url }) => {
      const registration = await navigator.serviceWorker.register(url, {
        scope: new URL("./", url).pathname,
      });
      let worker = registration.active || registration.waiting || registration.installing;
      if (worker?.state !== "activated") {
        await new Promise((resolve) => {
          worker.addEventListener("statechange", () => {
            if (worker.state === "activated") resolve();
          });
        });
      }
      worker = registration.active || worker;
      const result = await new Promise((resolve) => {
        navigator.serviceWorker.addEventListener("message", (event) => resolve(event.data), {
          once: true,
        });
        worker.postMessage({ probe: true });
      });
      await registration.unregister();
      return result.blocked;
    }, { url: scriptUrl });
    expect(blocked).toBe(true);

    await expect.poll(async () => {
      const response = await proxyControl("/__elvern_network_guard_state");
      return response.json();
    }).toMatchObject({
      attempts: expect.arrayContaining([{
        scheme: "http",
        origin: EXPECTED_EXTERNAL_ORIGIN,
        pathname_hash: expect.stringMatching(/^[0-9a-f]{12}$/),
      }]),
    });
    const response = await proxyControl("/__elvern_network_guard_state");
    const state = await response.json();
    expect(state.attempts.length).toBeGreaterThanOrEqual(1);
    expect(state.attempts.every((attempt) => (
      attempt.scheme === "http"
      && attempt.origin === EXPECTED_EXTERNAL_ORIGIN
      && /^[0-9a-f]{12}$/.test(attempt.pathname_hash)
    ))).toBe(true);
    expect(JSON.stringify(state)).not.toContain("token");
  } finally {
    await proxyControl("/__elvern_network_guard_clear", { method: "POST" });
  }
});
