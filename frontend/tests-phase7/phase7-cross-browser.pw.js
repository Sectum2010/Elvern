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


function v2Summary(source = "all", titleSuffix = "") {
  const sourceItems = source === "all" ? ITEMS : ITEMS.filter((entry) => entry.source_kind === source);
  const visible = sourceItems.map((entry) => ({ ...entry, title: `${entry.title}${titleSuffix}` }));
  return {
    schema_version: "library-summary-v2",
    revision: (source === "local" ? "b" : source === "cloud" ? "c" : "a").repeat(64),
    view: { category: "movies", source, genre: null, quality: "all", sort: "smart" },
    items_by_id: Object.fromEntries(visible.map((entry) => [String(entry.id), entry])),
    sections: {
      item_ids: visible.map((entry) => entry.id),
      series_rails: [],
      cloud_series_rails: [],
      continue_watching_item_ids: [],
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
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({ status: 204, body: "" }));
  await page.route("**/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: '{"status":"ok"}',
  }));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/[^/]+(?=\/api\/)/, "");
    requests.push(`${path}${url.search}`);
    let payload = {};
    if (path === "/api/auth/me") {
      payload = { user: {
        id: 7,
        username: "phase7-browser",
        display_name: "Phase 7 Browser",
        role: "standard_user",
        assistant_beta_enabled: false,
        age_credential: 18,
      } };
    } else if (path === "/api/auth/heartbeat") {
      payload = { ok: true };
    } else if (path === "/api/user-settings") {
      payload = {
        hide_duplicate_movies: true,
        hide_recently_added: true,
        floating_library_search_enabled: true,
        poster_card_appearance: "classic",
        poster_card_display_max_width: "1400",
      };
    } else if (path === "/api/provider-auth/status") {
      payload = { provider_auth_required: false, reconnect_required: false };
    } else if (path === "/api/library/v2/summary") {
      payload = v2Summary(url.searchParams.get("source") || "all", state.titleSuffix || "");
    } else if (path === "/api/library/search") {
      payload = v1SearchPayload(url.searchParams.get("q") || "");
    } else if (path === "/api/library/v2/revision") {
      payload = {
        schema_version: "library-revision-v1",
        catalog: state.catalogToken || "a", presentation: "b", permission: "c",
        user_overlay: "d", progress: "e", combined_library: "f",
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
      status: 200,
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
  const state = { catalogToken: "a", titleSuffix: "" };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length).toBeGreaterThan(0);

  state.catalogToken = "z";
  state.titleSuffix = " Updated";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));

  await expect(page.getByText("Phase Seven Alpha Updated", { exact: true })).toBeVisible();
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("desktop poster context menu remains available", async ({ page }) => {
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await page.locator(".media-card").first().click({ button: "right" });
  await expect(page.getByText("Edit", { exact: true })).toBeVisible();
  await expect(page.getByText("Generate", { exact: true })).toBeVisible();
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
