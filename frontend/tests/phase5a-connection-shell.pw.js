import { expect, test } from "@playwright/test";


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
  await page.route("**/health", (route) => route.fulfill({
    status: 200,
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
  await page.route("**/health", async (route) => {
    if (reachable) {
      await route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
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
