import { expect, test } from "@playwright/test";


const PUBLIC_PROBES = [
  ["https://www.cloudflare.com/cdn-cgi/trace", 200],
  ["https://api64.ipify.org/", 200],
  ["https://httpbin.org/status/204", 204],
];


async function installPublicProbeFixture(page, isReachable = () => true) {
  for (const [url, status] of PUBLIC_PROBES) {
    await page.route(url, (route) => {
      if (!isReachable()) {
        return route.abort("failed");
      }
      return route.fulfill({ status, body: "" });
    });
  }
}


function emptyV2Payload(source = "all") {
  return {
    schema_version: "library-summary-v2",
    revision: "a".repeat(64),
    view: { category: "movies", source, genre: null, quality: "all", sort: "smart" },
    items_by_id: {},
    sections: {
      item_ids: [],
      series_rails: [],
      cloud_series_rails: [],
      continue_watching_item_ids: [],
      recently_added_item_ids: [],
    },
    available_genres: [],
    total_items: 0,
    scan_in_progress: false,
  };
}


async function installConnectedFixture(page) {
  await installPublicProbeFixture(page);
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({
    status: 200, body: "", headers: { "X-Elvern-Frontend-Health": "1" },
  }));
  await page.route("**/health", (route) => route.fulfill({
    status: 200,
    headers: { "X-Elvern-Backend-Health": "1" },
    contentType: "application/json",
    body: '{"status":"ok"}',
  }));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let payload = {};
    if (url.pathname === "/api/auth/me") {
      payload = { user: { id: 5, username: "phase5", role: "standard_user", age_credential: 18 } };
    } else if (url.pathname === "/api/auth/heartbeat") {
      payload = { ok: true };
    } else if (url.pathname === "/api/user-settings") {
      payload = { hide_recently_added: true, floating_library_search_enabled: false };
    } else if (url.pathname === "/api/admin/maintenance-mode") {
      payload = { enabled: false };
    } else if (url.pathname === "/api/provider-auth/status") {
      payload = { provider_auth_required: false, reconnect_required: false };
    } else if (url.pathname === "/api/library/v2/summary") {
      payload = emptyV2Payload(url.searchParams.get("source") || "all");
    } else if (url.pathname === "/api/library/item/42") {
      payload = {
        id: 42,
        title: "Canonical Fixture",
        parsed_title: { display_title: "Canonical Fixture" },
        source_kind: "local",
        poster_url: null,
        duration_seconds: 3600,
        progress_seconds: 0,
        progress_duration_seconds: 3600,
        completed: false,
        subtitles: [],
        subtitle_tracks: [],
        audio_tracks: [],
        streams: [],
        track_scan_status: "not_scanned",
        age_requirement: null,
        genres: [],
      };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
}


test("desktop canonicalizes Library routes and renders one root hero", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Desktop canonical structure coverage");
  await installConnectedFixture(page);

  await page.goto("/library/?category=anime#section");
  await expect(page).toHaveURL(/\/library\?category=anime#section$/);
  await expect(page.locator(".library-desktop-hero")).toHaveCount(1);
  await expect(page.locator(".topbar")).toHaveCount(1);
  await expect(page.locator(".app-shell")).toHaveClass(/app-shell--library-root/);

  for (const [input, expected] of [
    ["/library/local/", "/library/local"],
    ["/library/cloud/", "/library/cloud"],
    ["/library/42/", "/library/42"],
  ]) {
    await page.goto(input);
    await expect(page).toHaveURL(new RegExp(`${expected.replaceAll("/", "\\/")}$`));
  }
});


test("mobile shows the dark connection shell and enters Elvern automatically after recovery", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-mobile", "Mobile connection shell coverage");
  let reachable = false;
  await installPublicProbeFixture(page);
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({
    status: 200, body: "", headers: { "X-Elvern-Frontend-Health": "1" },
  }));
  await page.route("**/health", async (route) => {
    if (reachable) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "X-Elvern-Backend-Health": "1" },
        body: '{"status":"ok"}',
      });
      return;
    }
    await route.abort("failed");
  });
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: '{"detail":"Authentication required"}',
  }));

  await page.goto("/");
  const shell = page.locator("#elvern-connection-shell");
  await expect(shell).toBeVisible();
  await expect(shell).toHaveAttribute("data-state", "connecting");
  await expect(page.locator("[data-connection-retry]")).toBeAttached();
  await expect(page.locator("[data-connection-retry]")).toBeHidden();
  expect(await shell.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe("rgb(8, 11, 18)");

  reachable = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(shell).toBeHidden();
});


test("Linux same-host health cannot clear a trusted public outage notice", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Desktop watchdog coverage");
  let publicReachable = true;
  await installPublicProbeFixture(page, () => publicReachable);
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({
    status: 200, body: "", headers: { "X-Elvern-Frontend-Health": "1" },
  }));
  await page.route("**/health", (route) => route.fulfill({
    status: 200, body: "", headers: { "X-Elvern-Backend-Health": "1" },
  }));
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/json",
    body: '{"detail":"Authentication required"}',
  }));

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  publicReachable = false;

  await expect(page.getByText("No Internet", { exact: true })).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(9_000);
  await expect(page.getByText("No Internet", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});


test("Linux connection-shell motion runs normally and stops for reduced motion", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Desktop animation coverage");
  await installPublicProbeFixture(page);
  await page.route("**/_elvern/frontend-health", (route) => route.abort("failed"));

  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/");
  const letter = page.locator(".elvern-connection-shell__letter").first();
  await expect(letter).toBeVisible();
  await expect.poll(() => letter.evaluate((node) => ({
    name: getComputedStyle(node).animationName,
    state: getComputedStyle(node).animationPlayState,
  }))).toEqual({ name: "elvern-letter-wave", state: "running" });

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  const reducedLetter = page.locator(".elvern-connection-shell__letter").first();
  await expect(reducedLetter).toBeVisible();
  expect(await reducedLetter.evaluate((node) => getComputedStyle(node).animationName)).toBe("none");
});
