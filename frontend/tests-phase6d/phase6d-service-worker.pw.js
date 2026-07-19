import { expect, test } from "@playwright/test";


const PUBLIC_PROBES = [
  ["https://www.cloudflare.com/cdn-cgi/trace", 200],
  ["https://api64.ipify.org/", 200],
  ["https://httpbin.org/status/204", 204],
];


async function verifyRecoveryArmHandshake(page) {
  return page.evaluate(() => new Promise((resolve) => {
    const channel = new MessageChannel();
    const nonce = "playwright-phase6d-recovery-arm";
    const timeout = window.setTimeout(() => resolve(null), 2_000);
    channel.port1.onmessage = (event) => {
      window.clearTimeout(timeout);
      channel.port1.close();
      resolve(event.data);
    };
    channel.port1.start();
    navigator.serviceWorker.controller.postMessage({
      type: "ELVERN_ARM_RECOVERY_NAVIGATION",
      schema_version: 1,
      nonce,
      expires_at: Date.now() + 15_000,
    }, [channel.port2]);
  }));
}


async function installConnectedRoutes(page) {
  for (const [url, status] of PUBLIC_PROBES) {
    await page.route(url, (route) => route.fulfill({ status, body: "" }));
  }
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({ status: 204, body: "" }));
  await page.route("**/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: '{"status":"ok"}',
  }));
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: '{"detail":"Authentication required"}',
  }));
}


test("production worker falls back offline and verified recovery returns to the original deep link", async ({ context, page }) => {
  await installConnectedRoutes(page);
  const initialResponse = await page.goto("library");
  expect(initialResponse.headers()["x-elvern-app-shell"]).toBe("1");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    await registration.update();
  });
  await page.reload();
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);

  await context.setOffline(true);
  await page.goto("library?category=anime#return-target", { waitUntil: "domcontentloaded" });
  const shell = page.locator("#elvern-connection-shell");
  await expect(shell).toBeVisible();
  await expect(shell).toHaveAttribute("data-state", "connecting");
  expect(page.url()).toMatch(/\/abcd2345\/library\?category=anime#return-target$/);

  await expect(verifyRecoveryArmHandshake(page)).resolves.toMatchObject({
    type: "ELVERN_RECOVERY_NAVIGATION_ARMED",
    nonce: "playwright-phase6d-recovery-arm",
    accepted: true,
    durability: "durable",
  });

  await context.setOffline(false);
  const recoveredDeepLinkRequest = page.waitForRequest((request) => (
    request.isNavigationRequest()
    && request.url().endsWith("/abcd2345/library?category=anime")
  ));
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  expect((await recoveredDeepLinkRequest).url()).toMatch(/\/abcd2345\/library\?category=anime$/);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible({ timeout: 20_000 });
  await expect(shell).toBeHidden();
  expect(page.url()).toMatch(/\/abcd2345\/login$/);

  const cacheKeys = await page.evaluate(() => caches.keys());
  expect(cacheKeys.every((key) => key.startsWith("elvern-offline-shell-"))).toBe(true);
});


test("blocked public probes require Retry before service-only recovery", async ({ context, page }) => {
  let publicReachable = true;
  for (const [url, status] of PUBLIC_PROBES) {
    await page.route(url, (route) => publicReachable
      ? route.fulfill({ status, body: "" })
      : route.abort("failed"));
  }
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({ status: 204, body: "" }));
  await page.route("**/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: '{"status":"ok"}',
  }));
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: '{"detail":"Authentication required"}',
  }));

  await page.goto("library");
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    await registration.update();
  });
  await page.reload();
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);

  await context.setOffline(true);
  await page.goto("library?category=anime#manual-retry", { waitUntil: "domcontentloaded" });
  await expect(page.locator("#elvern-connection-shell")).toHaveAttribute("data-state", "connecting");

  publicReachable = false;
  await context.setOffline(false);
  await page.waitForTimeout(1_500);
  await expect(page.locator("#elvern-connection-shell")).toBeVisible();

  await page.locator("[data-connection-retry]").dispatchEvent("click");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#elvern-connection-shell")).toBeHidden();
  expect(page.url()).toMatch(/\/abcd2345\/login$/);
});
