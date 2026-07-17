import { expect, test } from "@playwright/test";


test("production service worker returns offline shell for a deep-link navigation", async ({ context, page, baseURL }) => {
  let healthReachable = true;
  await page.route("**/health", (route) => {
    if (!healthReachable) {
      return route.abort("failed");
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: '{"status":"ok"}',
    });
  });
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: '{"detail":"Authentication required"}',
  }));

  await page.goto("library");
  const registration = await page.evaluate(async () => {
    const ready = await navigator.serviceWorker.ready;
    return { scope: ready.scope, scriptURL: ready.active?.scriptURL || "" };
  });
  expect(registration.scope).toBe(baseURL);
  expect(registration.scriptURL).toBe(`${baseURL}sw.js?elvern_worker=offline-shell-v1`);

  healthReachable = false;
  await context.setOffline(true);
  const deepLink = `${baseURL}library/42`;
  await page.goto(deepLink, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#elvern-connection-shell")).toBeVisible();
  await expect(page.locator("#elvern-connection-shell")).toHaveAttribute("data-state", "connecting");
  await expect(page.locator("[data-connection-retry]")).toBeAttached();
  await expect(page.locator("[data-connection-retry]")).toBeHidden();
  expect(page.url()).toBe(deepLink);

  healthReachable = true;
  const recoveredLoad = page.waitForEvent("load");
  await context.setOffline(false);
  await page.evaluate(() => document.querySelector("[data-connection-retry]")?.click()).catch(() => {
    // The automatic probe may win this race and destroy the offline page context.
  });
  await recoveredLoad;
  expect(await page.evaluate(() => performance.getEntriesByType("navigation")[0]?.name || "")).toBe(deepLink);
});
