import { expect, test } from "@playwright/test";


function lightweightItem(id) {
  const sourceKind = id % 3 === 0 ? "cloud" : "local";
  return {
    id,
    title: `V2 Synthetic Film ${id}`,
    year: 2000 + (id % 20),
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
    progress_seconds: id === 72 ? 120 : 0,
    progress_duration_seconds: 7200,
    completed: false,
  };
}


const items = Array.from({ length: 90 }, (_, index) => lightweightItem(index + 1));


function summaryPayload(source = "all") {
  const visibleItems = source === "all"
    ? items
    : items.filter((item) => item.source_kind === source);
  const visibleIds = visibleItems.map((item) => item.id);
  const continueIds = visibleIds.includes(72) ? [72] : [];
  return {
    schema_version: "library-summary-v2",
    revision: (source === "local" ? "b" : source === "cloud" ? "c" : "a").repeat(64),
    view: { category: "movies", source, genre: null, quality: "all", sort: "smart" },
    items_by_id: Object.fromEntries(visibleItems.map((item) => [String(item.id), item])),
    sections: {
      item_ids: visibleIds,
      series_rails: [],
      cloud_series_rails: [],
      continue_watching_item_ids: continueIds,
      recently_added_item_ids: [],
    },
    available_genres: [],
    total_items: visibleItems.length,
    scan_in_progress: false,
  };
}


async function installApiFixture(page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let payload = {};
    if (path === "/api/auth/me") {
      payload = {
        user: {
          id: 2,
          username: "v2-browser-fixture",
          display_name: "V2 Browser Fixture",
          role: "standard_user",
          assistant_beta_enabled: false,
          age_credential: 18,
        },
      };
    } else if (path === "/api/auth/heartbeat") {
      payload = { ok: true };
    } else if (path === "/api/user-settings") {
      payload = {
        hide_duplicate_movies: true,
        hide_recently_added: true,
        floating_library_search_enabled: false,
        poster_card_appearance: "classic",
        poster_card_display_max_width: "1400",
      };
    } else if (path === "/api/provider-auth/status") {
      payload = { provider_auth_required: false, reconnect_required: false };
    } else if (path === "/api/library/v2/summary") {
      payload = summaryPayload(url.searchParams.get("source") || "all");
    } else if (path === "/api/library" || path === "/api/library/search") {
      throw new Error(`v2 on fixture received unexpected v1 request: ${url.pathname}${url.search}`);
    } else if (/^\/api\/library\/item\/\d+$/.test(path)) {
      const itemId = Number(path.split("/").at(-1));
      const item = items.find((entry) => entry.id === itemId);
      payload = {
        ...item,
        parsed_title: {
          display_title: item.title,
          base_title: item.title,
          edition_identity: "standard",
          parsed_year: item.year,
          title_source: "title",
          parse_confidence: "high",
          warnings: [],
          parser_version: "test",
          suspicious_output: false,
        },
        original_filename: null,
        source_label: item.source_kind === "cloud" ? "Cloud" : "DGX",
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
        resume_position_seconds: item.progress_seconds,
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


async function openDeepCard(page, instanceKey, itemId) {
  const card = page.locator(`[data-library-card-instance-key="${instanceKey}"]`);
  await card.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy({ top: -180, behavior: "instant" }));
  await page.waitForTimeout(50);
  const top = await card.evaluate((node) => node.getBoundingClientRect().top);
  await card.locator(".media-card__poster-link").click();
  await expect(page).toHaveURL(new RegExp(`/library/${itemId}$`));
  return top;
}


async function expectReturnWithinTolerance(page, listUrl, instanceKey, itemId) {
  await page.goto("/");
  await expect(page.locator(".page-section--library")).toBeVisible();
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, listUrl);
  await expect(page).toHaveURL(new RegExp(`${listUrl.replace("?", "\\?")}$`));
  const beforeTop = await openDeepCard(page, instanceKey, itemId);
  await page.getByRole("link", { name: "Back to Library" }).click();
  const card = page.locator(`[data-library-card-instance-key="${instanceKey}"]`);
  await expect(card).toBeVisible();
  await page.waitForTimeout(260);
  const afterTop = await card.evaluate((node) => node.getBoundingClientRect().top);
  expect(Math.abs(afterTop - beforeTop)).toBeLessThanOrEqual(8);
}


test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Phase 4 return fixture is desktop-only");
  test.skip(
    process.env.VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE !== "on",
    "Run with VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE=on",
  );
  await installApiFixture(page);
});


test("v2 root keeps duplicate section instances and restores the exact deep card", async ({ page }) => {
  await page.goto("/library?category=movies");
  await expect(page.locator("[data-library-card-instance-key='continue-watching:72']")).toBeVisible();
  await expect(page.locator("[data-library-card-instance-key='other-movies:72']")).toBeAttached();
  await expectReturnWithinTolerance(
    page,
    "/library?category=movies",
    "other-movies:72",
    72,
  );
});


test("v2 Local source restores its exact deep card", async ({ page }) => {
  await expectReturnWithinTolerance(page, "/library/local", "local:other-movies:71", 71);
});


test("v2 Cloud source restores its exact deep card", async ({ page }) => {
  await expectReturnWithinTolerance(page, "/library/cloud", "cloud:other-movies:72", 72);
});
