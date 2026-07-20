import { expect, test } from "@playwright/test";


const PUBLIC_PROBES = [
  "https://www.cloudflare.com/cdn-cgi/trace",
  "https://api64.ipify.org/",
  "https://httpbin.org/status/204",
];


function item(id, title, sourceKind = "local") {
  return {
    id,
    title,
    year: 2026,
    poster_url: null,
    source_kind: sourceKind,
    quality_rank: {
      key: "silver",
      label: "Silver",
      score: 7,
      description: "Good quality, highly watchable.",
      detected: ["1080p"],
      tooltip: "Good quality, highly watchable. Detected: 1080p.",
    },
    duration_seconds: 7200,
    progress_seconds: 0,
    progress_duration_seconds: 7200,
    completed: false,
  };
}


const ITEMS = [
  item(1, "Phase Seven Alpha", "local"),
  item(2, "Phase Seven Beta", "cloud"),
];


function opaqueToken(sequence, namespace = 0) {
  return (BigInt(namespace) * 1_000_000n + BigInt(sequence)).toString(16).padStart(64, "0");
}


function v2Summary(source = "all", state = {}) {
  const sourceItems = source === "all" ? ITEMS : ITEMS.filter((entry) => entry.source_kind === source);
  const visible = sourceItems.map((entry) => ({
    ...entry,
    title: `${entry.title}${state.titleSuffix || ""}`,
    ...(entry.id === 1 ? {
      progress_seconds: Number(state.progressSeconds || 0),
      progress_duration_seconds: Number(state.progressDuration || 7200),
      completed: Boolean(state.completed),
    } : {}),
  }));
  const continueWatchingItemIds = Number(state.progressSeconds || 0) > 0 && !state.completed
    ? visible.filter((entry) => entry.id === 1).map((entry) => entry.id)
    : [];
  return {
    schema_version: "library-summary-v2",
    revision: (source === "local" ? "b" : source === "cloud" ? "c" : "a").repeat(64),
    view: { category: "movies", source, genre: null, quality: "all", sort: "smart" },
    items_by_id: Object.fromEntries(visible.map((entry) => [String(entry.id), entry])),
    sections: {
      item_ids: visible.map((entry) => entry.id),
      series_rails: [],
      cloud_series_rails: [],
      continue_watching_item_ids: continueWatchingItemIds,
      recently_added_item_ids: [],
    },
    available_genres: [],
    total_items: visible.length,
    scan_in_progress: false,
  };
}


function v1SearchPayload(query) {
  const visible = ITEMS.filter((entry) => entry.title.toLowerCase().includes(query.toLowerCase()));
  return {
    items: visible.map((entry) => ({
      ...entry,
      quality_tier: "silver",
      quality_label: "Silver",
      source_label: entry.source_kind === "cloud" ? "Cloud" : "DGX",
      genres: [],
      genre_display: "Unknown",
      hidden_for_user: false,
      hidden_globally: false,
      file_size: 1024,
      width: 1920,
      height: 1080,
      video_codec: "h264",
      audio_codec: "aac",
      container: "mkv",
    })),
    series_rails: [],
    cloud_series_rails: [],
    continue_watching: [],
    recently_added: [],
    arrange: { source: "all", genre: null, quality: "all", sort: "smart" },
    available_genres: [],
    scan_in_progress: false,
    total_items: visible.length,
  };
}


async function installFixture(page, requests, state = {}) {
  for (const probe of PUBLIC_PROBES) {
    await page.route(probe, (route) => route.fulfill({ status: 204, body: "" }));
  }
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({
    status: 204, body: "", headers: { "X-Elvern-Frontend-Health": "1" },
  }));
  await page.route("**/health", (route) => route.fulfill({
    status: 200,
    headers: { "X-Elvern-Backend-Health": "1" },
    contentType: "application/json",
    body: '{"status":"ok"}',
  }));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/[^/]+(?=\/api\/)/, "");
    requests.push(`${path}${url.search}`);
    let payload = {};
    let status = 200;
    if (path === "/api/auth/me") {
      payload = { user: {
        id: Number(state.userId || 7),
        username: state.username || "phase7-browser",
        display_name: state.displayName || "Phase 7 Browser",
        role: state.role || "standard_user",
        assistant_beta_enabled: false,
        age_credential: 18,
      } };
    } else if (path === "/api/auth/heartbeat") {
      payload = { ok: true };
    } else if (path === "/api/user-settings") {
      payload = {
        hide_duplicate_movies: true,
        hide_recently_added: true,
        floating_library_search_enabled: state.floatingSearchEnabled !== false,
        poster_card_appearance: "classic",
        poster_card_display_max_width: "1400",
      };
    } else if (path === "/api/provider-auth/status") {
      payload = { provider_auth_required: false, reconnect_required: false };
    } else if (path === "/api/library/v2/summary") {
      payload = v2Summary(url.searchParams.get("source") || "all", state);
    } else if (path === "/api/library/search") {
      payload = v1SearchPayload(url.searchParams.get("q") || "");
    } else if (path === "/api/library/v2/revision") {
      payload = {
        schema_version: "library-revision-v1",
        catalog: state.catalogToken || opaqueToken(1, 1),
        presentation: opaqueToken(1, 2),
        permission: opaqueToken(1, 3),
        user_overlay: opaqueToken(1, 4),
        progress: state.progressToken || opaqueToken(1, 5),
        combined_library: opaqueToken(1, 6),
        ...(state.revisionExtraFields || {}),
      };
    } else if (path === "/api/library/v2/progress-state") {
      status = Number(state.progressStateStatus || 200);
      payload = status === 200
        ? {
            schema_version: "library-progress-state-v1",
            progress_revision: state.progressToken || opaqueToken(1, 5),
            items: [{
              id: 1,
              progress_seconds: Number(state.progressSeconds || 0),
              progress_duration_seconds: Number(state.progressDuration || 7200),
              completed: Boolean(state.completed),
            }],
          }
        : { detail: state.progressStateErrorDetail || "Not found" };
    } else if (path === "/api/progress/1" && route.request().method() === "POST") {
      const progress = route.request().postDataJSON();
      state.progressSeconds = Number(progress.position_seconds || 0);
      state.progressDuration = Number(progress.duration_seconds || 7200);
      state.completed = Boolean(progress.completed);
      state.progressSequence = Number(state.progressSequence || 0) + 1;
      state.progressToken = opaqueToken(state.progressSequence, 5);
      payload = {
        media_item_id: 1,
        position_seconds: state.progressSeconds,
        duration_seconds: state.progressDuration,
        completed: state.completed,
      };
    } else if (path === "/api/library/rescan" && route.request().method() === "POST") {
      state.catalogSequence = Number(state.catalogSequence || 0) + 1;
      state.catalogToken = opaqueToken(state.catalogSequence, 1);
      state.titleSuffix = ` Scan ${state.catalogSequence}`;
      payload = {
        message: "Library scan completed.",
        running: false,
        job_id: null,
        cloud_sync: null,
      };
    } else if (/^\/api\/library\/item\/\d+$/.test(path)) {
      const itemId = Number(path.split("/").at(-1));
      const selected = ITEMS.find((entry) => entry.id === itemId);
      payload = {
        ...selected,
        parsed_title: {
          display_title: selected.title,
          base_title: selected.title,
          edition_identity: "standard",
          parsed_year: selected.year,
          title_source: "title",
          parse_confidence: "high",
          warnings: [],
          parser_version: "test",
          suspicious_output: false,
        },
        original_filename: null,
        source_label: selected.source_kind === "cloud" ? "Cloud" : "DGX",
        quality_tier: "silver",
        quality_label: "Silver",
        genres: [],
        genre_display: "Unknown",
        hidden_for_user: false,
        hidden_globally: false,
        file_size: 1024,
        width: 1920,
        height: 1080,
        video_codec: "h264",
        audio_codec: "aac",
        container: "mkv",
        stream_url: `/api/stream/${itemId}`,
        resume_position_seconds: 0,
        subtitles: [],
        subtitle_tracks: [],
        audio_tracks: [],
        track_scan_status: "not_scanned",
        track_scan_error: "",
        track_scan_source: "not_scanned",
        audio_track_diagnostics: {},
        subtitle_track_diagnostics: {},
        age_requirement: null,
        age_requirement_display: "None",
        age_group_key: "",
        genre_group_key: "",
      };
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}


test.beforeEach(async ({ context, page }) => {
  await context.clearCookies();
  await installFixture(page, []);
});


test("canonical Root Local and Cloud use the production v2 route", async ({ page, baseURL }) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);

  await page.goto("library/?category=movies#phase7");
  await expect(page).toHaveURL(`${baseURL}library?category=movies#phase7`);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  await page.goto("library/local/");
  await expect(page).toHaveURL(`${baseURL}library/local`);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  await page.goto("library/cloud/");
  await expect(page).toHaveURL(`${baseURL}library/cloud`);
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();

  const summaries = requests.filter((request) => request.startsWith("/api/library/v2/summary"));
  expect(summaries.some((request) => request.includes("source=local"))).toBe(true);
  expect(summaries.some((request) => request.includes("source=cloud"))).toBe(true);
  expect(requests.some((request) => request === "/api/library")).toBe(false);
});


test("desktop search commits only on Enter and survives Detail return", async ({ page, baseURL }) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  const search = page.getByRole("searchbox", { name: "Search library" }).first();
  await search.fill("Beta");
  await page.waitForTimeout(500);
  expect(requests.filter((request) => request.startsWith("/api/library/search")).length).toBe(0);
  await search.press("Enter");

  await expect(page).toHaveURL(`${baseURL}library?q=Beta`);
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
  expect(requests.filter((request) => request.startsWith("/api/library/search?q=Beta")).length).toBe(1);

  await page.locator(".media-card__poster-link").first().click();
  await expect(page).toHaveURL(`${baseURL}library/2`);
  await expect(page.getByRole("button", { name: "Open in VLC" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Lite Playback" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Full Playback" })).toBeVisible();
  await page.getByRole("link", { name: "Back to Library" }).click();
  await expect(page).toHaveURL(`${baseURL}library?q=Beta`);
  await expect(page.getByRole("searchbox", { name: "Search library" }).first()).toHaveValue("Beta");
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
});


test("revision change silently refreshes the active Library without a loading reset", async ({ page }) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1), titleSuffix: "" };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length).toBeGreaterThan(0);

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Updated";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));

  await expect(page.getByText("Phase Seven Alpha Updated", { exact: true })).toBeVisible();
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("catalog plus progress membership revision performs one summary refresh", async ({ page }) => {
  const requests = [];
  const state = {
    catalogToken: opaqueToken(1, 1),
    progressToken: opaqueToken(1, 5),
    progressSeconds: 120,
    progressDuration: 7200,
    completed: false,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByRole("heading", { name: "Continue watching" })).toBeVisible();
  await expect(page.locator(".media-card__progress")).not.toHaveCount(0);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThan(0);
  const summaryCallsBefore = requests.filter((request) => request.startsWith("/api/library/v2/summary")).length;
  const progressCallsBefore = requests.filter((request) => request === "/api/library/v2/progress-state").length;

  state.catalogToken = opaqueToken(2, 1);
  state.progressToken = opaqueToken(2, 5);
  state.progressSeconds = 0;
  state.titleSuffix = " Single Refresh";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));

  await expect(page.getByText("Phase Seven Alpha Single Refresh", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Continue watching" })).toHaveCount(0);
  await expect(page.locator(".media-card__progress")).toHaveCount(0);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/progress-state").length)
    .toBe(progressCallsBefore + 1);
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 1);
  await page.waitForTimeout(250);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore + 1);
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("progress-state 404 falls back to summary while later catalog revisions continue", async ({ page }) => {
  const requests = [];
  const state = {
    catalogToken: opaqueToken(1, 1),
    progressToken: opaqueToken(1, 5),
    progressSeconds: 0,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThan(0);
  const summaryCallsBefore = requests.filter((request) => request.startsWith("/api/library/v2/summary")).length;

  state.progressStateStatus = 404;
  state.progressToken = opaqueToken(2, 5);
  state.progressSeconds = 120;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("heading", { name: "Continue watching" })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/progress-state").length).toBe(1);
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 1);

  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThanOrEqual(3);
  expect(requests.filter((request) => request === "/api/library/v2/progress-state")).toHaveLength(1);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore + 1);

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Catalog Still Syncs";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("Phase Seven Alpha Catalog Still Syncs", { exact: true }).first()).toBeVisible();
  expect(requests.filter((request) => request === "/api/library/v2/progress-state")).toHaveLength(1);
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 2);
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("extra revision fields preserve old cache until a valid response arrives", async ({ page }) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1), titleSuffix: "" };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThan(0);
  const summaryCallsBefore = requests.filter((request) => request.startsWith("/api/library/v2/summary")).length;

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Validated";
  state.revisionExtraFields = { title: "must-be-rejected" };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect(page.getByText("Phase Seven Alpha Validated", { exact: true })).toHaveCount(0);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore);

  state.revisionExtraFields = null;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("Phase Seven Alpha Validated", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 1);
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("desktop poster context menu remains available", async ({ page }) => {
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await page.locator(".media-card").first().click({ button: "right" });
  await expect(page.getByText("Edit", { exact: true })).toBeVisible();
  await expect(page.getByText("Generate", { exact: true })).toBeVisible();
});


test("two independent same-account contexts apply progress reset and catalog revision silently", async ({ browser, baseURL }) => {
  const sharedState = {
    catalogToken: opaqueToken(0, 1),
    progressToken: opaqueToken(0, 5),
    progressSeconds: 0,
    progressDuration: 7200,
    completed: false,
  };
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const requestsA = [];
  const requestsB = [];
  await installFixture(pageA, requestsA, sharedState);
  await installFixture(pageB, requestsB, sharedState);
  try {
    await Promise.all([pageA.goto(`${baseURL}library`), pageB.goto(`${baseURL}library`)]);
    await expect(pageA.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect(pageB.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect.poll(() => requestsB.filter((request) => request === "/api/library/v2/revision").length)
      .toBeGreaterThan(0);

    await pageA.evaluate(async () => {
      await fetch(new URL("api/progress/1", document.baseURI), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position_seconds: 120, duration_seconds: 7200, completed: false }),
      });
    });
    await pageB.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(pageB.getByRole("heading", { name: "Continue watching" })).toBeVisible();
    await expect(pageB.locator(".media-card__progress")).not.toHaveCount(0);
    await expect(pageB.getByText("Loading library...")).toHaveCount(0);

    await pageA.evaluate(async () => {
      await fetch(new URL("api/progress/1", document.baseURI), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position_seconds: 0, duration_seconds: 7200, completed: false }),
      });
    });
    await pageB.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(pageB.getByRole("heading", { name: "Continue watching" })).toHaveCount(0);
    await expect(pageB.locator(".media-card__progress")).toHaveCount(0);
    await expect(pageB.getByText("Loading library...")).toHaveCount(0);

    await pageA.getByRole("button", { name: "Rescan library" }).click();
    await pageB.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(pageB.getByText("Phase Seven Alpha Scan 1", { exact: true })).toBeVisible();
    await expect(pageB.getByText("Loading library...")).toHaveCount(0);
  } finally {
    await Promise.all([contextA.close(), contextB.close()]);
  }
});


test("two different identity contexts keep same-item progress UI isolated", async ({ browser, baseURL }) => {
  const stateA = {
    userId: 7,
    username: "phase7-user-a",
    progressToken: opaqueToken(1, 5),
    progressSeconds: 0,
  };
  const stateB = {
    userId: 8,
    username: "phase7-user-b",
    progressToken: opaqueToken(1, 5),
    progressSeconds: 0,
  };
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const requestsA = [];
  const requestsB = [];
  await installFixture(pageA, requestsA, stateA);
  await installFixture(pageB, requestsB, stateB);
  try {
    await Promise.all([pageA.goto(`${baseURL}library`), pageB.goto(`${baseURL}library`)]);
    await expect(pageA.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect(pageB.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect.poll(() => requestsA.filter((request) => request === "/api/library/v2/revision").length)
      .toBeGreaterThan(0);
    await expect.poll(() => requestsB.filter((request) => request === "/api/library/v2/revision").length)
      .toBeGreaterThan(0);

    stateA.progressToken = opaqueToken(2, 5);
    stateA.progressSeconds = 120;
    await pageA.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(pageA.getByRole("heading", { name: "Continue watching" })).toBeVisible();
    await expect(pageA.locator(".media-card__progress")).not.toHaveCount(0);

    await pageB.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(pageB.getByRole("heading", { name: "Continue watching" })).toHaveCount(0);
    await expect(pageB.locator(".media-card__progress")).toHaveCount(0);
  } finally {
    await Promise.all([contextA.close(), contextB.close()]);
  }
});


test("Root static search is interactive without a clear button at desktop and laptop widths", async ({ page }, testInfo) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
    { width: 900, height: 700 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("library");
    await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    const search = page.getByRole("searchbox", { name: "Search library" });
    await expect(search).toHaveCount(1);
    await expect(search).toBeEnabled();
    await expect(page.getByRole("button", { name: "Clear search" })).toHaveCount(0);
    await search.click();
    await expect(search).toBeFocused();
    const searchRequestCount = requests.filter((request) => request.startsWith("/api/library/search")).length;
    await search.fill("Beta");
    await page.waitForTimeout(500);
    expect(requests.filter((request) => request.startsWith("/api/library/search")).length).toBe(searchRequestCount);
    await search.press("Enter");
    await expect(page).toHaveURL(/library\?q=Beta$/);
    await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`library-root-${viewport.width}x${viewport.height}.png`),
      fullPage: true,
    });
  }
});


test("collapsing an uncommitted Floating draft immediately unlocks Static search", async ({ page, baseURL }) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  const staticSearch = page.locator(".library-desktop-hero__search input");
  await page.locator(".media-card").last().scrollIntoViewIfNeeded();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Search library" }).click();
  const floatingSearch = page.locator(".floating-library-search input");
  await floatingSearch.fill("uncommitted floating value");
  await expect(staticSearch).toBeDisabled();
  const requestCount = requests.length;

  await page.getByRole("button", { name: "Collapse search" }).click();

  await expect(staticSearch).toBeEnabled();
  await expect(staticSearch).toHaveValue("");
  expect(requests.length).toBe(requestCount);
  await staticSearch.fill("Beta");
  await page.waitForTimeout(500);
  expect(requests.filter((request) => request.includes("uncommitted"))).toHaveLength(0);
  await staticSearch.press("Enter");
  await expect(page).toHaveURL(`${baseURL}library?q=Beta`);
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
});


test("Local and Cloud static search commits locally without another source payload request", async ({ page, baseURL }) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  for (const [source, query, title] of [
    ["local", "Alpha", "Phase Seven Alpha"],
    ["cloud", "Beta", "Phase Seven Beta"],
  ]) {
    await page.goto(`library/${source}`);
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    const staticSearch = page.getByRole("searchbox", { name: `Search ${source === "local" ? "Local" : "Cloud"} Library` });
    await expect(staticSearch).toBeEnabled();
    const sourcePayloadCount = requests.filter((request) => (
      request.startsWith("/api/library/v2/summary") && request.includes(`source=${source}`)
    )).length;
    await staticSearch.fill(query);
    await page.waitForTimeout(500);
    expect(requests.filter((request) => request.startsWith("/api/library/search"))).toHaveLength(0);
    await staticSearch.press("Enter");
    await expect(page).toHaveURL(`${baseURL}library/${source}?q=${query}`);
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    expect(requests.filter((request) => (
      request.startsWith("/api/library/v2/summary") && request.includes(`source=${source}`)
    )).length).toBe(sourcePayloadCount);
  }
});


test("desktop setting false hides Floating while Static remains usable", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], { floatingSearchEnabled: false });
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  await expect(page.locator(".floating-library-search")).toHaveCount(0);
  await expect(page.getByRole("searchbox", { name: "Search library" })).toBeEnabled();
});


test("offline document uses the immediate explicit-offline Oops contract", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
  });
  await page.goto("offline.html");
  await expect(page.locator("#elvern-connection-shell")).toHaveAttribute("data-state", "unreachable");
  await expect(page.locator("[data-connection-oops-copy]")).toHaveText(
    "It looks like you're offline. Please check your connection and try again.",
  );
  await expect(page.locator("[data-connection-retry]")).toBeVisible();
});
