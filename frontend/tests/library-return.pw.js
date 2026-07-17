import { expect, test } from "@playwright/test";


function libraryItem(id) {
  return {
    id,
    title: `Synthetic Film ${id}`,
    parsed_title: {
      display_title: `Synthetic Film ${id}`,
      base_title: `Synthetic Film ${id}`,
      edition_identity: "standard",
      parsed_year: 2000 + (id % 20),
      title_source: "title",
      parse_confidence: "high",
      warnings: [],
      parser_version: "test",
      suspicious_output: false,
    },
    original_filename: null,
    source_kind: "local",
    source_label: "DGX",
    poster_url: null,
    quality_tier: "silver",
    quality_label: "Silver",
    genres: [],
    genre_display: "Unknown",
    hidden_for_user: false,
    hidden_globally: false,
    file_size: 1024,
    duration_seconds: 7200,
    width: 1920,
    height: 1080,
    video_codec: "h264",
    audio_codec: "aac",
    container: "mkv",
    year: 2000 + (id % 20),
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    last_scanned_at: "2026-01-01T00:00:00Z",
    progress_seconds: 0,
    progress_duration_seconds: 7200,
    completed: false,
    download_access_allowed: true,
  };
}


const items = Array.from({ length: 90 }, (_, index) => libraryItem(index + 1));
const libraryPayload = {
  items,
  series_rails: [],
  cloud_series_rails: [],
  continue_watching: [],
  recently_added: [],
  arrange: { source: "all", genre: null, quality: "all", sort: "smart" },
  available_genres: [],
  scan_in_progress: false,
  total_items: items.length,
};


async function installApiFixture(page) {
  await page.route("**/_elvern/frontend-health", async (route) => {
    await route.fulfill({ status: 200, body: "" });
  });
  await page.route("**/health", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let payload = {};
    if (path === "/api/auth/me") {
      payload = {
        user: {
          id: 2,
          username: "browser-fixture",
          display_name: "Browser Fixture",
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
    } else if (path === "/api/admin/maintenance-mode") {
      payload = { enabled: false };
    } else if (path === "/api/provider-auth/status") {
      payload = { provider_auth_required: false, reconnect_required: false };
    } else if (path === "/api/library") {
      payload = libraryPayload;
    } else if (/^\/api\/library\/item\/\d+$/.test(path)) {
      const itemId = Number(path.split("/").at(-1));
      payload = {
        ...items.find((item) => item.id === itemId),
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


async function openDeepCard(page, itemId) {
  const card = page.locator(`[data-library-card-instance-key="other-movies:${itemId}"]`);
  await card.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy({ top: -180, behavior: "instant" }));
  await page.waitForTimeout(50);
  const top = await card.evaluate((node) => node.getBoundingClientRect().top);
  await card.locator(".media-card__poster-link").click();
  await expect(page).toHaveURL(new RegExp(`/library/${itemId}$`));
  return top;
}


test.beforeEach(async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Desktop return fixture only");
  if (process.env.ELVERN_RETURN_DEBUG === "1") {
    await page.addInitScript(() => {
      window.localStorage.setItem("elvern_library_return_debug", "1");
    });
    page.on("console", async (message) => {
      if (message.text().includes("[Elvern library return]")) {
        const values = await Promise.all(message.args().map(async (argument) => {
          try {
            return await argument.jsonValue();
          } catch {
            return null;
          }
        }));
        console.log(JSON.stringify(values));
      }
    });
  }
  await installApiFixture(page);
});


test("desktop detail return restores the exact deep card before settling", async ({ page }) => {
  await page.goto("/library?category=movies");
  await expect(page.locator("[data-library-card-instance-key='other-movies:72']")).toBeVisible();
  const beforeTop = await openDeepCard(page, 72);

  await page.getByRole("link", { name: "Back to Library" }).click();
  await expect(page).toHaveURL(/\/library\?category=movies$/);
  const card = page.locator("[data-library-card-instance-key='other-movies:72']");
  await expect(card).toBeVisible();
  await page.waitForTimeout(260);
  const afterTop = await card.evaluate((node) => node.getBoundingClientRect().top);

  expect(Math.abs(afterTop - beforeTop)).toBeLessThanOrEqual(8);
});


test("desktop return applies one bounded correction after an upper layout shift", async ({ page }) => {
  await page.goto("/library?category=movies");
  await expect(page.locator("[data-library-card-instance-key='other-movies:66']")).toBeVisible();
  const beforeTop = await openDeepCard(page, 66);

  await page.getByRole("link", { name: "Back to Library" }).click();
  await expect(page).toHaveURL(/\/library\?category=movies$/);
  await page.evaluate(() => {
    const root = document.querySelector(".page-section--library");
    const spacer = document.createElement("div");
    spacer.dataset.testDelayedUpperSection = "true";
    spacer.style.height = "180px";
    root?.insertBefore(spacer, root.firstChild);
  });
  const card = page.locator("[data-library-card-instance-key='other-movies:66']");
  await page.waitForTimeout(300);
  const afterTop = await card.evaluate((node) => node.getBoundingClientRect().top);

  expect(Math.abs(afterTop - beforeTop)).toBeLessThanOrEqual(8);
  await expect(page.locator(".page-section--library")).not.toHaveAttribute(
    "data-library-return-restoring",
    "true",
  );
});
