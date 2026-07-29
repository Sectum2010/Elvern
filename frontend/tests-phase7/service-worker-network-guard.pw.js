import { expect, test } from "@playwright/test";


const EXPECTED_EXTERNAL_ORIGIN = "http://elvern-guard-test.invalid";


async function proxyControl(path, init) {
  const controlOrigin = process.env.ELVERN_PHASE7_NETWORK_PROXY_CONTROL;
  const controlToken = process.env.ELVERN_PHASE7_NETWORK_PROXY_CONTROL_TOKEN;
  if (!controlOrigin || !controlToken) {
    throw new Error("The loopback-only browser proxy control endpoint is required.");
  }
  return fetch(`${controlOrigin}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "X-Elvern-Network-Guard-Token": controlToken,
      ...init?.headers,
    },
  });
}


test("browser-level authority blocks a Service Worker external fetch", async ({
  page,
}) => {
  const fixtureOrigin = process.env.ELVERN_PHASE7_SW_FIXTURE_ORIGIN;
  if (!fixtureOrigin) {
    throw new Error("The independent Service Worker fixture origin is required.");
  }
  await proxyControl("/__elvern_network_guard_clear", { method: "POST" });
  try {
    await page.goto(`${fixtureOrigin}/index.html`);
    const scriptUrl = `${fixtureOrigin}/service-worker.js`;
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

    const loopbackBlocked = await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.ready;
      const worker = registration.active;
      const result = await new Promise((resolve) => {
        navigator.serviceWorker.addEventListener("message", (event) => resolve(event.data.blocked), {
          once: true,
        });
        worker.postMessage({
          url: "http://127.0.0.1:4174/unregistered-service?token=hidden",
        });
      });
      await registration.unregister();
      return result;
    });
    expect(loopbackBlocked).toBe(true);
    await expect.poll(async () => {
      const stateResponse = await proxyControl("/__elvern_network_guard_state");
      return stateResponse.json();
    }).toMatchObject({
      attempts: expect.arrayContaining([{
        scheme: "http",
        origin: "http://127.0.0.1:4174",
        pathname_hash: expect.stringMatching(/^[0-9a-f]{12}$/),
      }]),
    });
  } finally {
    await proxyControl("/__elvern_network_guard_clear", { method: "POST" });
  }
});
