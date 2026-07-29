import { expect, test } from "@playwright/test";


async function proxyControl(path, { method = "GET", origin } = {}) {
  const controlOrigin = process.env.ELVERN_PHASE7_NETWORK_PROXY_CONTROL;
  const controlToken = process.env.ELVERN_PHASE7_NETWORK_PROXY_CONTROL_TOKEN;
  if (!controlOrigin || !controlToken) {
    throw new Error("The loopback-only browser proxy control endpoint is required.");
  }
  const response = await fetch(`${controlOrigin}${path}`, {
    method,
    cache: "no-store",
    headers: {
      ...(origin ? { "Content-Type": "application/json" } : {}),
      "X-Elvern-Network-Guard-Token": controlToken,
    },
    ...(origin ? { body: JSON.stringify({ origin }) } : {}),
  });
  if (!response.ok) {
    throw new Error(`Network guard control failed with ${response.status}.`);
  }
  return response;
}


async function openWebSocket(page, url) {
  return page.evaluate((target) => new Promise((resolve) => {
    const socket = new WebSocket(target);
    const timeout = window.setTimeout(() => {
      socket.close();
      resolve("timeout");
    }, 5_000);
    socket.addEventListener("open", () => {
      window.clearTimeout(timeout);
      socket.close();
      resolve("open");
    }, { once: true });
    socket.addEventListener("error", () => {
      window.clearTimeout(timeout);
      resolve("error");
    }, { once: true });
  }), url);
}


test("registered exact WSS CONNECT authority is allowed and revoked without data leakage", async ({
  page,
}) => {
  const pageOrigin = process.env.ELVERN_PHASE7_SW_FIXTURE_ORIGIN;
  const wssOrigin = process.env.ELVERN_PHASE7_WSS_FIXTURE_ORIGIN;
  const httpsOrigin = process.env.ELVERN_PHASE7_HTTPS_FIXTURE_ORIGIN;
  if (!pageOrigin || !wssOrigin || !httpsOrigin) {
    throw new Error("The local WSS integration fixtures are required.");
  }
  const fixtureUrl = new URL(wssOrigin);
  const wrongPort = Number(fixtureUrl.port) === 65535
    ? Number(fixtureUrl.port) - 1
    : Number(fixtureUrl.port) + 1;
  const wrongPortOrigin = `wss://${fixtureUrl.hostname}:${wrongPort}`;

  await proxyControl("/__elvern_network_guard_clear", { method: "POST" });
  await page.goto(`${pageOrigin}/index.html`);

  expect(await openWebSocket(page, `${wssOrigin}/before-register?token=hidden`)).toBe("error");

  await proxyControl("/__elvern_network_guard_register", {
    method: "POST",
    origin: wssOrigin,
  });
  expect(await openWebSocket(page, `${wssOrigin}/registered?token=hidden`)).toBe("open");
  expect(await openWebSocket(page, `${wrongPortOrigin}/wrong-port?token=hidden`)).toBe("error");

  const httpsResult = await page.evaluate(async (target) => {
    try {
      const response = await fetch(target, { cache: "no-store" });
      return { ok: response.ok, text: await response.text() };
    } catch {
      return { ok: false, text: "" };
    }
  }, `${httpsOrigin}/same-authority?token=hidden`);
  expect(httpsResult).toEqual({ ok: true, text: "ok\n" });

  expect(await openWebSocket(
    page,
    "wss://outside.invalid/external?token=must-not-be-recorded",
  )).toBe("error");

  await proxyControl("/__elvern_network_guard_unregister", {
    method: "POST",
    origin: wssOrigin,
  });
  expect(await openWebSocket(page, `${wssOrigin}/after-unregister?token=hidden`)).toBe("error");

  const state = await (
    await proxyControl("/__elvern_network_guard_state")
  ).json();
  expect(state.attempts).toEqual(expect.arrayContaining([
    expect.objectContaining({
      scheme: "connect",
      origin: `connect://127.0.0.1:${fixtureUrl.port}`,
    }),
    expect.objectContaining({
      scheme: "connect",
      origin: `connect://127.0.0.1:${wrongPort}`,
    }),
    expect.objectContaining({
      scheme: "connect",
      origin: "connect://outside.invalid:443",
    }),
  ]));
  expect(JSON.stringify(state)).not.toContain("must-not-be-recorded");
  expect(JSON.stringify(state)).not.toContain("token=hidden");
  await proxyControl("/__elvern_network_guard_clear", { method: "POST" });
});
