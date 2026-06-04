import { useEffect } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { readLibraryReturnTarget } from "../lib/libraryNavigation";
import { LibraryPage } from "./LibraryPage";


vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    refreshAuth: vi.fn(),
  }),
}));

vi.mock("../auth/ProviderAuthContext", () => ({
  useProviderAuth: () => ({
    providerAuthRequirement: null,
    providerAuthDismissedThisSession: false,
    providerAuthReconnectPending: false,
    refreshProviderAuthStatus: vi.fn(),
    startProviderReconnect: vi.fn(),
  }),
}));

vi.mock("../lib/api", () => ({
  apiRequest: vi.fn(),
}));

vi.mock("../lib/browserPlayback", () => ({
  useActiveBrowserPlaybackItemId: () => null,
}));

vi.mock("../lib/platformDetection", () => ({
  detectClientDeviceClass: () => "desktop",
  detectClientPlatform: () => "desktop",
}));


const defaultSettings = {
  hide_duplicate_movies: true,
  hide_recently_added: false,
  floating_library_search_enabled: false,
};

const emptyLibraryPayload = {
  items: [],
  series_rails: [],
  cloud_series_rails: [],
  continue_watching: [],
  recently_added: [],
  total_items: 0,
  scan_in_progress: false,
};


function mockApi(payload = emptyLibraryPayload) {
  apiRequest.mockImplementation((requestPath) => {
    if (requestPath === "/api/user-settings") {
      return Promise.resolve(defaultSettings);
    }
    if (requestPath.startsWith("/api/library")) {
      return Promise.resolve(payload);
    }
    return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
  });
}


function LocationProbe({ locations }) {
  const location = useLocation();
  useEffect(() => {
    locations.push(`${location.pathname}${location.search}`);
  }, [location.pathname, location.search, locations]);
  return null;
}


function renderLibrary(initialEntry = "/library", payload = emptyLibraryPayload) {
  const locations = [];
  mockApi(payload);
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <LocationProbe locations={locations} />
      <LibraryPage />
    </MemoryRouter>,
  );
  return { locations };
}


describe("LibraryPage category switching", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    window.scrollTo = vi.fn();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("defaults to Movies and requests the movies category", async () => {
    renderLibrary("/library");

    const moviesTab = await screen.findByRole("tab", { name: "Movies" });

    expect(moviesTab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies", expect.any(Object));
    });
  });

  test("reads active category from URL and updates URL when a category is clicked", async () => {
    const user = userEvent.setup();
    const { locations } = renderLibrary("/library?category=tv");

    expect(await screen.findByRole("tab", { name: "TV Shows" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("tab", { name: "Anime" }));

    await waitFor(() => {
      expect(locations).toContain("/library?category=anime");
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=anime", expect.any(Object));
    });
  });

  test("search requests include the active category", async () => {
    renderLibrary("/library?category=cartoon");

    expect(await screen.findByRole("tab", { name: "Cartoon" })).toHaveAttribute("aria-selected", "true");
    fireEvent.change(screen.getAllByRole("searchbox", { name: "Search library" })[0], {
      target: { value: "akira" },
    });

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=akira&category=cartoon",
        expect.any(Object),
      );
    });
  });

  test("detail return target preserves the category query", async () => {
    renderLibrary(
      "/library?category=anime",
      {
        ...emptyLibraryPayload,
        items: [
          {
            id: 42,
            title: "Akira",
            parsed_title: {
              display_title: "Akira",
              base_title: "Akira",
              edition_identity: "standard",
              parsed_year: 1988,
              title_source: "fallback",
              parse_confidence: "high",
              warnings: [],
              parser_version: "",
              suspicious_output: false,
            },
            original_filename: "Akira.1988.mkv",
            source_kind: "local",
            source_label: "DGX",
            poster_url: null,
            file_size: 1024,
            duration_seconds: 1200,
            width: 1920,
            height: 1080,
            video_codec: "h264",
            audio_codec: "aac",
            container: "mkv",
            year: 1988,
            created_at: "2026-06-01T00:00:00+00:00",
            updated_at: "2026-06-01T00:00:00+00:00",
            last_scanned_at: "2026-06-01T00:00:00+00:00",
            completed: false,
          },
        ],
        total_items: 1,
      },
    );

    const titleLink = await screen.findByRole("link", { name: "Akira" });
    await userEvent.click(titleLink);

    expect(readLibraryReturnTarget()?.listPath).toBe("/library?category=anime");
  });
});
