import { expect, test } from "@playwright/test";
import { createServer } from "node:http";


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
    poster_url: state.posterRecoveryEnabled
      ? `/api/posters/${entry.id}?cache_token=${opaqueToken(entry.id, 9)}`
      : entry.poster_url,
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


function desktopHelperStatus(state = {}) {
  return {
    device_id: "phase7-desktop",
    platform: "linux",
    helper_required: false,
    state: "helper_not_required",
    same_host: true,
    same_host_detection_source: "loopback_client_ip",
    last_seen_helper_version: null,
    vlc_detection_state: "installed",
    vlc_detection_path: "/usr/bin/vlc",
    vlc_detection_checked_at: "2026-07-22T00:00:00Z",
    runtime_included: true,
    latest_releases: [],
    notes: [],
    ...(state.desktopHelperStatus || {}),
  };
}


async function installFixture(page, requests, state = {}) {
  for (const probe of PUBLIC_PROBES) {
    await page.route(probe, (route) => route.fulfill({ status: 204, body: "" }));
  }
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({
    status: 204, body: "", headers: { "X-Elvern-Frontend-Health": "1" },
  }));
  await page.route("**/health", async (route) => {
    state.healthRequestCount = Number(state.healthRequestCount || 0) + 1;
    const delay = Number(state.nextHealthDelayMs || 0);
    state.nextHealthDelayMs = 0;
    if (delay > 0) {
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
    const healthy = state.healthHealthy !== false
      || state.healthRequestCount <= Number(state.allowHealthyHealthRequests || 0);
    await route.fulfill({
      status: healthy ? 200 : 503,
      headers: healthy ? { "X-Elvern-Backend-Health": "1" } : {},
      contentType: "application/json",
      body: healthy ? '{"status":"ok"}' : '{"detail":"temporarily unavailable"}',
    });
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/[^/]+(?=\/api\/)/, "");
    requests.push(`${path}${url.search}`);
    state.pathRequestCounts ||= {};
    state.pathRequestCounts[path] = Number(state.pathRequestCounts[path] || 0) + 1;
    if (path.startsWith("/api/posters/")) {
      state.posterRequestCounts ||= {};
      state.posterRequestCounts[path] = Number(state.posterRequestCounts[path] || 0) + 1;
      if (
        state.posterRecoveryEnabled
        && state.posterFailureEnabled !== false
        && path === "/api/posters/1"
        && state.posterRequestCounts[path] === 1
      ) {
        if (state.waitBeforeFirstPosterFailure) {
          await new Promise((resolve) => {
            state.releaseFirstPosterFailure = resolve;
          });
        }
        await route.fulfill({ status: 503, body: "" });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="15"><rect width="10" height="15" fill="#222"/></svg>',
      });
      return;
    }
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
    } else if (path === "/api/cloud-libraries") {
      payload = { libraries: [], connected: true };
    } else if (path === "/api/admin/media-library-reference") {
      payload = { effective_value: "Configured", default_value: "Configured" };
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
    } else if (path === "/api/desktop-helper/status") {
      state.desktopStatusRequestCount = Number(state.desktopStatusRequestCount || 0) + 1;
      const delay = Number(
        state.desktopStatusRequestDelays?.[state.desktopStatusRequestCount - 1]
        || state.desktopStatusDelayMs
        || 0,
      );
      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
      payload = desktopHelperStatus(state);
    } else if (path === "/api/desktop-helper/verify") {
      const delay = Number(state.desktopVerifyDelayMs || 0);
      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
      payload = { status: desktopHelperStatus(state) };
    } else if (path === "/api/progress/1" && route.request().method() === "GET") {
      payload = {
        media_item_id: 1,
        position_seconds: Number(state.progressSeconds || 0),
        duration_seconds: Number(state.progressDuration || 7200),
        completed: Boolean(state.completed),
      };
    } else if (path === "/api/playback/1") {
      payload = {
        mode: "direct",
        transcode_status: "idle",
        manifest_complete: false,
      };
    } else if (path.startsWith("/api/desktop-playback/1")) {
      payload = {
        available: true,
        helper_required: false,
        vlc_available: true,
      };
    } else if (path === "/api/browser-playback/items/1/active") {
      payload = null;
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
        source_kind: state.detailSourceKind || selected.source_kind,
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


async function dispatchGenuinePageReturn(page) {
  await page.evaluate(() => {
    window.dispatchEvent(new Event("blur"));
    window.dispatchEvent(new Event("focus"));
  });
}


async function installFetchFaults(page, faults) {
  await page.addInitScript((faultDefinitions) => {
    const originalFetch = window.fetch.bind(window);
    const controls = Object.fromEntries(faultDefinitions.map((definition) => [
      definition.id,
      {
        ...definition,
        armed: definition.armed !== false,
        remaining: Number(definition.count || 1),
        triggered: 0,
      },
    ]));
    window.__elvernFetchFaults = controls;
    window.fetch = async (input, init = {}) => {
      const url = typeof input === "string" ? input : input?.url || "";
      const control = Object.values(controls).find(
        (candidate) => (
          candidate.armed
          && candidate.remaining > 0
          && url.includes(candidate.match)
        ),
      );
      if (!control) {
        return originalFetch(input, init);
      }
      control.remaining -= 1;
      control.triggered += 1;
      if (control.mode === "fetch") {
        throw new TypeError("NetworkError when attempting to fetch resource");
      }
      if (control.mode === "malformed_401") {
        return new Response("{", {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (control.mode === "body") {
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("{"));
            queueMicrotask(() => {
              controller.error(new TypeError("NetworkError when attempting to fetch resource"));
            });
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (control.mode === "body_network") {
        return originalFetch(control.faultUrl, {
          cache: "no-store",
          credentials: "omit",
          signal: init?.signal,
        });
      }
      if (control.mode === "pending_body") {
        const stream = new ReadableStream({
          start(controller) {
            const signal = init?.signal;
            const abort = () => controller.error(new DOMException("Aborted", "AbortError"));
            if (signal?.aborted) {
              abort();
              return;
            }
            signal?.addEventListener("abort", abort, { once: true });
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return originalFetch(input, init);
    };
  }, faults);
}


async function startBodyFaultServer() {
  let requestCount = 0;
  const server = createServer((_request, response) => {
    requestCount += 1;
    response.writeHead(200, {
      "Access-Control-Allow-Origin": "*",
      "Content-Type": "application/json",
      "Transfer-Encoding": "chunked",
    });
    response.flushHeaders();
    response.write("{");
    setTimeout(() => response.socket?.destroy(), 30);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  return {
    get requestCount() {
      return requestCount;
    },
    url: `http://127.0.0.1:${address.port}/body-fault`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
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
  await dispatchGenuinePageReturn(page);

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
  await dispatchGenuinePageReturn(page);

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
  await dispatchGenuinePageReturn(page);
  await expect(page.getByRole("heading", { name: "Continue watching" })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/progress-state").length).toBe(1);
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 1);

  await dispatchGenuinePageReturn(page);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThanOrEqual(3);
  expect(requests.filter((request) => request === "/api/library/v2/progress-state")).toHaveLength(1);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore + 1);

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Catalog Still Syncs";
  await dispatchGenuinePageReturn(page);
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
  await dispatchGenuinePageReturn(page);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect(page.getByText("Phase Seven Alpha Validated", { exact: true })).toHaveCount(0);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore);

  state.revisionExtraFields = null;
  await dispatchGenuinePageReturn(page);
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
    await dispatchGenuinePageReturn(pageB);
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
    await dispatchGenuinePageReturn(pageB);
    await expect(pageB.getByRole("heading", { name: "Continue watching" })).toHaveCount(0);
    await expect(pageB.locator(".media-card__progress")).toHaveCount(0);
    await expect(pageB.getByText("Loading library...")).toHaveCount(0);

    await pageA.getByRole("button", { name: "Rescan library" }).click();
    await dispatchGenuinePageReturn(pageB);
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
    await dispatchGenuinePageReturn(pageA);
    await expect(pageA.getByRole("heading", { name: "Continue watching" })).toBeVisible();
    await expect(pageA.locator(".media-card__progress")).not.toHaveCount(0);

    await dispatchGenuinePageReturn(pageB);
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
  const summaryRequestCount = requests.filter(
    (request) => request.startsWith("/api/library/v2/summary"),
  ).length;
  const searchRequestCount = requests.filter(
    (request) => request.startsWith("/api/library/search"),
  ).length;

  await page.getByRole("button", { name: "Collapse search" }).click();

  await expect(staticSearch).toBeEnabled();
  await expect(staticSearch).toHaveValue("");
  expect(requests.filter(
    (request) => request.startsWith("/api/library/v2/summary"),
  )).toHaveLength(summaryRequestCount);
  expect(requests.filter(
    (request) => request.startsWith("/api/library/search"),
  )).toHaveLength(searchRequestCount);
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


test("third-party VLC stays in a separate tab while the Elvern route remains healthy", async ({
  context,
  page,
  baseURL,
}) => {
  const requests = [];
  const state = { desktopStatusDelayMs: 400 };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await context.route("https://www.videolan.org/**", (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<!doctype html><title>Mock VLC download</title><p>Mock VLC download</p>",
  }));

  await page.goto("install");
  const originalUrl = `${baseURL}install`;
  await expect(page).toHaveURL(originalUrl);
  const vlcLink = page.getByRole("link", { name: "Download VLC" });
  await expect(vlcLink).toHaveAttribute("target", "_blank");
  await expect(vlcLink).toHaveAttribute("rel", /noopener/);
  await expect(vlcLink).toHaveAttribute("rel", /noreferrer/);

  const popupPromise = context.waitForEvent("page");
  await vlcLink.click();
  const popup = await popupPromise;
  await popup.waitForLoadState("domcontentloaded");
  await expect(popup).toHaveTitle("Mock VLC download");
  await expect(page).toHaveURL(originalUrl);
  await popup.close();

  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await page.locator(".media-card__poster-link").first().click();
  await expect(page).toHaveURL(`${baseURL}library/1`);
  await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
});


test("pagehide abort followed by persisted pageshow cannot let stale Helper status overwrite recovery", async ({
  page,
}) => {
  const requests = [];
  const state = {
    desktopStatusRequestDelays: [2_000, 0],
    desktopHelperStatus: {
      state: "up_to_date",
      helper_required: true,
      same_host: false,
      same_host_detection_source: "client_ip_not_local",
      vlc_detection_state: "installed",
    },
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect.poll(() => state.desktopStatusRequestCount || 0).toBe(1);

  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
  });

  await expect.poll(() => state.desktopStatusRequestCount || 0).toBe(2);
  await expect(page.getByText("Ready")).toBeVisible();
  await page.waitForTimeout(2_100);
  await expect(page.getByText("Ready")).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  expect(requests.filter((request) => request.startsWith("/api/desktop-helper/status"))).toHaveLength(2);
});


for (const faultMode of ["fetch", "body_network"]) {
  test(`a no-cache Library ${faultMode === "fetch" ? "fetch" : "body"}-stage failure recovers without an empty-state lie`, async ({
    page,
  }) => {
    const bodyFaultServer = faultMode === "body_network"
      ? await startBodyFaultServer()
      : null;
    const requests = [];
    const state = {};
    try {
      await page.unrouteAll({ behavior: "wait" });
      await installFixture(page, requests, state);
      await installFetchFaults(page, [{
        id: "library",
        match: "/api/library/v2/summary",
        mode: faultMode,
        faultUrl: bodyFaultServer?.url,
      }]);

      await page.goto("library?category=movies&quality=all");

      await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
      await expect(page.getByText("No media indexed yet")).toHaveCount(0);
      await expect(page.getByText("No matches yet")).toHaveCount(0);
      await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
      expect(await page.evaluate(() => window.__elvernFetchFaults.library.triggered)).toBe(1);
      expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(1);
      if (bodyFaultServer) {
        expect(bodyFaultServer.requestCount).toBe(1);
      }
    } finally {
      await bodyFaultServer?.close();
    }
  });
}


test("a no-cache Source failure recovers on the exact source URL without duplicate refetch", async ({
  page,
  baseURL,
}) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  await installFetchFaults(page, [{
    id: "source",
    match: "/api/library/v2/summary",
    mode: "fetch",
  }]);

  await page.goto("library/cloud?genre=Drama&q=Beta");

  await expect(page).toHaveURL(`${baseURL}library/cloud?genre=Drama&q=Beta`);
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
  await expect(page.getByText("No media indexed yet")).toHaveCount(0);
  await expect(page.getByText("No matches yet")).toHaveCount(0);
  expect(await page.evaluate(() => window.__elvernFetchFaults.source.triggered)).toBe(1);
  expect(requests.filter((request) => (
    request.startsWith("/api/library/v2/summary") && request.includes("source=cloud")
  ))).toHaveLength(1);
});


test("cached Library waits for a late recovery without clearing content or filters", async ({
  page,
  baseURL,
}) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1) };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "cachedLibrary",
    match: "/api/library/v2/summary",
    mode: "fetch",
    armed: false,
  }]);
  await page.goto("library?category=movies&quality=silver");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  state.healthHealthy = false;
  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Recovered";
  await page.evaluate(() => {
    window.__elvernFetchFaults.cachedLibrary.armed = true;
  });
  await dispatchGenuinePageReturn(page);

  await expect.poll(
    () => page.evaluate(() => window.__elvernFetchFaults.cachedLibrary.triggered),
  ).toBe(1);
  await expect(page).toHaveURL(`${baseURL}library?category=movies&quality=silver`);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect(page.getByText("Loading library...")).toHaveCount(0);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));

  await expect(page.getByText("Phase Seven Alpha Recovered", { exact: true })).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(2);
});


test("an incident opened during an older health probe receives a second qualifying probe", async ({
  page,
}) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1) };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "oldProbe",
    match: "/api/library/v2/summary",
    mode: "fetch",
    armed: false,
  }]);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  const healthCountBefore = state.healthRequestCount;
  state.nextHealthDelayMs = 700;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => state.healthRequestCount).toBe(healthCountBefore + 1);

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Qualified";
  await page.evaluate(() => {
    window.__elvernFetchFaults.oldProbe.armed = true;
  });
  await dispatchGenuinePageReturn(page);

  await expect(page.getByText("Phase Seven Alpha Qualified", { exact: true })).toBeVisible();
  await expect.poll(() => state.healthRequestCount).toBeGreaterThanOrEqual(healthCountBefore + 2);
  expect(await page.evaluate(() => window.__elvernFetchFaults.oldProbe.triggered)).toBe(1);
});


test("metadata recovery starts every applicable Detail auxiliary read", async ({ page }) => {
  const requests = [];
  const state = {
    role: "admin",
    detailSourceKind: "cloud",
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "metadata",
    match: "/api/library/item/1",
    mode: "fetch",
  }]);

  await page.goto("library/1");

  await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();
  for (const path of [
    "/api/progress/1",
    "/api/playback/1",
    "/api/desktop-playback/1",
    "/api/browser-playback/items/1/active",
    "/api/cloud-libraries",
    "/api/admin/media-library-reference",
  ]) {
    await expect.poll(() => state.pathRequestCounts?.[path] || 0).toBeGreaterThanOrEqual(1);
  }
  expect(await page.evaluate(() => window.__elvernFetchFaults.metadata.triggered)).toBe(1);
  await expect(page.getByText("Reconnecting…")).toHaveCount(0);
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
});


test("Detail selectively retries only transient progress and clears its old error", async ({ page }) => {
  const bodyFaultServer = await startBodyFaultServer();
  const requests = [];
  const state = {
    healthHealthy: false,
    allowHealthyHealthRequests: 1,
  };
  try {
    await page.unrouteAll({ behavior: "wait" });
    await installFixture(page, requests, state);
    await installFetchFaults(page, [{
      id: "progress",
      match: "/api/progress/1",
      mode: "body_network",
      faultUrl: bodyFaultServer.url,
    }]);

    await page.goto("library/1");
    await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();
    await expect(page.getByText("Reconnecting…")).toBeVisible();
    const metadataRequests = state.pathRequestCounts?.["/api/library/item/1"] || 0;
    const playbackRequests = state.pathRequestCounts?.["/api/playback/1"] || 0;

    state.healthHealthy = true;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));

    await expect(page.getByText("Reconnecting…")).toHaveCount(0);
    expect(state.pathRequestCounts?.["/api/library/item/1"] || 0).toBe(metadataRequests);
    expect(state.pathRequestCounts?.["/api/playback/1"] || 0).toBe(playbackRequests);
    expect(state.pathRequestCounts?.["/api/progress/1"] || 0).toBe(1);
    expect(await page.evaluate(() => window.__elvernFetchFaults.progress.triggered)).toBe(1);
    expect(bodyFaultServer.requestCount).toBe(1);
    await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  } finally {
    await bodyFaultServer.close();
  }
});


test("shared settings observers make one production request", async ({ page }) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);

  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await page.locator(".media-card__poster-link").first().click();
  await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();

  expect(state.pathRequestCounts?.["/api/user-settings"] || 0).toBe(1);
});


test("Helper verify coalesces resume and recovery into one follow-up status read", async ({
  page,
}) => {
  const requests = [];
  const state = {
    desktopVerifyDelayMs: 500,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "helperStatus",
    match: "/api/desktop-helper/status",
    mode: "fetch",
    armed: false,
  }]);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.__elvernFetchFaults.helperStatus.armed = true;
  });
  await dispatchGenuinePageReturn(page);
  await expect(page.getByText("Reconnecting…")).toBeVisible();

  await page.getByRole("button", { name: "Check VLC on this host" }).click();
  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await dispatchGenuinePageReturn(page);

  await expect.poll(() => state.pathRequestCounts?.["/api/desktop-helper/verify"] || 0).toBe(1);
  await expect.poll(() => state.pathRequestCounts?.["/api/desktop-helper/status"] || 0).toBe(2);
  await expect(page.getByText("Reconnecting…")).toHaveCount(0);
  await page.waitForTimeout(650);
  expect(state.pathRequestCounts?.["/api/desktop-helper/status"] || 0).toBe(2);
});


test("headers-received Helper status aborts cleanly on pagehide and resumes once", async ({
  page,
}) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "pendingBody",
    match: "/api/desktop-helper/status",
    mode: "pending_body",
  }]);

  await page.goto("install");
  await expect.poll(
    () => page.evaluate(() => window.__elvernFetchFaults.pendingBody.triggered),
  ).toBe(1);
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    window.dispatchEvent(new Event("focus"));
  });

  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  expect(state.pathRequestCounts?.["/api/desktop-helper/status"] || 0).toBe(1);
});


test("a hidden page cancels a pending resume until the next visible return", async ({ page }) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  const revisionCount = state.pathRequestCounts?.["/api/library/v2/revision"] || 0;

  await page.evaluate(() => {
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    window.__setTestVisibility = (value) => {
      visibility = value;
      document.dispatchEvent(new Event("visibilitychange"));
    };
    window.dispatchEvent(new Event("blur"));
    window.dispatchEvent(new Event("focus"));
    window.__setTestVisibility("hidden");
  });
  await page.waitForTimeout(350);
  expect(state.pathRequestCounts?.["/api/library/v2/revision"] || 0).toBe(revisionCount);

  await page.evaluate(() => {
    window.__setTestVisibility("visible");
    window.dispatchEvent(new Event("focus"));
  });
  await expect.poll(
    () => state.pathRequestCounts?.["/api/library/v2/revision"] || 0,
  ).toBe(revisionCount + 1);
});


test("malformed 401 body clears protected Library cache without exposing parser text", async ({
  page,
}) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "malformed401",
    match: "/api/desktop-helper/status",
    mode: "malformed_401",
    armed: false,
  }]);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  expect(state.pathRequestCounts?.["/api/library/v2/summary"] || 0).toBe(1);

  await page.evaluate(() => {
    window.__elvernFetchFaults.malformed401.armed = true;
  });
  await page.getByRole("link", { name: "Install" }).click();
  await expect(page.getByText("Elvern received an unreadable response from the server.")).toBeVisible();
  await expect(page.getByText(/SyntaxError|Unexpected end|NetworkError when attempting/i)).toHaveCount(0);

  await page.getByRole("link", { name: "Library" }).click();
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  expect(state.pathRequestCounts?.["/api/library/v2/summary"] || 0).toBe(2);
});


test("a poster error before recovery retries exactly once", async ({ page }) => {
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  state.healthHealthy = false;
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  await page.getByRole("link", { name: "Library" }).click();
  await expect(page).toHaveURL(/\/library$/);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));

  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(2);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  await page.waitForTimeout(700);
  expect(state.posterRequestCounts["/api/posters/1"]).toBe(2);
});


test("a recovered attach-time poster incident still retries a late onError once", async ({ page }) => {
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
    waitBeforeFirstPosterFailure: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  state.healthHealthy = false;
  await page.evaluate(() => {
    window.__posterRecoveryEvents = 0;
    window.addEventListener("elvern:connectivity-recovered", () => {
      window.__posterRecoveryEvents += 1;
    });
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  await page.getByRole("link", { name: "Library" }).click();
  await expect(page).toHaveURL(/\/library$/);
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => page.evaluate(() => window.__posterRecoveryEvents)).toBe(1);
  state.releaseFirstPosterFailure();

  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(2);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  expect(state.posterRequestCounts["/api/posters/1"]).toBe(2);
});


test("poster recovery stays authoritative while no MediaCard subscriber exists", async ({ page }) => {
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
    posterFailureEnabled: false,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.__posterRecoveryEvents = 0;
    window.addEventListener("elvern:connectivity-recovered", () => {
      window.__posterRecoveryEvents += 1;
    });
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => page.evaluate(() => window.__posterRecoveryEvents)).toBe(1);

  await page.getByRole("link", { name: "Library" }).click();
  await expect(page).toHaveURL(/\/library$/);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  expect(state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);
});


test("an active poster incident remains recoverable beyond the former 30 second window", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  await page.getByRole("link", { name: "Library" }).click();
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);
  await page.waitForTimeout(30_100);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(2);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  expect(state.posterRequestCounts["/api/posters/1"]).toBe(2);
});
