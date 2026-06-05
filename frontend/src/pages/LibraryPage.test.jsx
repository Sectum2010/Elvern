import { useEffect } from "react";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { readLibraryReturnTarget } from "../lib/libraryNavigation";
import { LibraryPage } from "./LibraryPage";


const mockPlatformState = vi.hoisted(() => ({
  deviceClass: "desktop",
  platform: "desktop",
}));


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
  detectClientDeviceClass: () => mockPlatformState.deviceClass,
  detectClientPlatform: () => mockPlatformState.platform,
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
  arrange: {
    source: "all",
    genre: null,
    quality: "all",
    sort: "smart",
  },
  available_genres: [],
  total_items: 0,
  scan_in_progress: false,
};


function libraryItem(overrides = {}) {
  const id = overrides.id ?? 1;
  const title = overrides.title ?? `Movie ${id}`;
  return {
    id,
    title,
    parsed_title: {
      display_title: title,
      base_title: title,
      edition_identity: "standard",
      parsed_year: overrides.year ?? 2024,
      title_source: "fallback",
      parse_confidence: "high",
      warnings: [],
      parser_version: "",
      suspicious_output: false,
    },
    original_filename: overrides.original_filename ?? `${title.replace(/\s+/g, ".")}.2024.mkv`,
    source_kind: overrides.source_kind ?? "local",
    source_label: overrides.source_label ?? "DGX",
    poster_url: null,
    file_size: overrides.file_size ?? 1024,
    duration_seconds: overrides.duration_seconds ?? 1200,
    width: overrides.width ?? 1920,
    height: overrides.height ?? 1080,
    video_codec: overrides.video_codec ?? "h264",
    audio_codec: overrides.audio_codec ?? "aac",
    container: overrides.container ?? "mkv",
    year: overrides.year ?? 2024,
    created_at: overrides.created_at ?? "2026-06-01T00:00:00+00:00",
    updated_at: overrides.updated_at ?? "2026-06-01T00:00:00+00:00",
    last_scanned_at: overrides.last_scanned_at ?? "2026-06-01T00:00:00+00:00",
    completed: overrides.completed ?? false,
    progress_seconds: overrides.progress_seconds ?? 0,
    progress_duration_seconds: overrides.progress_duration_seconds ?? 0,
    ...overrides,
  };
}


function libraryPayload(overrides = {}) {
  return {
    ...emptyLibraryPayload,
    ...overrides,
  };
}


function visibleTitleLinkNames(...names) {
  const expectedNames = new Set(names);
  return screen
    .getAllByRole("link")
    .map((link) => link.textContent.trim())
    .filter((name) => expectedNames.has(name));
}


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


function rect(left, right) {
  return {
    bottom: 40,
    height: 40,
    left,
    right,
    top: 0,
    width: right - left,
    x: left,
    y: 0,
    toJSON: () => {},
  };
}


function mockCategorySwitchRects() {
  return vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function getRect() {
    if (this.classList?.contains("library-category-switch")) {
      return rect(0, 400);
    }
    const label = this.textContent?.trim();
    if (label === "Movies") {
      return rect(0, 100);
    }
    if (label === "TV Shows") {
      return rect(100, 200);
    }
    if (label === "Anime") {
      return rect(200, 300);
    }
    if (label === "Cartoon") {
      return rect(300, 400);
    }
    return rect(0, 100);
  });
}


describe("LibraryPage category switching", () => {
  beforeEach(() => {
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "desktop";
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      writable: true,
      value: MouseEvent,
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

  test("desktop drag changes the active category URL", async () => {
    const rectSpy = mockCategorySwitchRects();
    const { locations } = renderLibrary("/library?category=movies");
    const moviesTab = await screen.findByRole("tab", { name: "Movies" });
    const switchControl = screen.getByRole("tablist", { name: "Library category" });

    expect(switchControl).toHaveClass("library-category-switch--drag-enabled");

    fireEvent.pointerDown(moviesTab, { clientX: 50, pointerId: 1 });
    fireEvent.pointerMove(moviesTab, { clientX: 250, pointerId: 1 });
    expect(switchControl.style.getPropertyValue("--library-category-index")).toBe("0");
    expect(switchControl.style.getPropertyValue("--library-category-drag-x")).toBe("200px");
    fireEvent.pointerUp(moviesTab, { clientX: 250, pointerId: 1 });

    await waitFor(() => {
      expect(locations).toContain("/library?category=anime");
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=anime", expect.any(Object));
    });
    rectSpy.mockRestore();
  });

  test("phone category switch is click-only and does not use the drag path", async () => {
    mockPlatformState.deviceClass = "phone";
    mockPlatformState.platform = "iphone";
    const rectSpy = mockCategorySwitchRects();
    const user = userEvent.setup();
    const { locations } = renderLibrary("/library?category=movies");
    const moviesTab = await screen.findByRole("tab", { name: "Movies" });
    const switchControl = screen.getByRole("tablist", { name: "Library category" });

    expect(switchControl).not.toHaveClass("library-category-switch--drag-enabled");

    fireEvent.pointerDown(moviesTab, { clientX: 50, pointerId: 1 });
    fireEvent.pointerMove(moviesTab, { clientX: 250, pointerId: 1 });
    fireEvent.pointerUp(moviesTab, { clientX: 250, pointerId: 1 });
    expect(locations).not.toContain("/library?category=anime");

    await user.click(screen.getByRole("tab", { name: "Anime" }));
    await waitFor(() => {
      expect(locations).toContain("/library?category=anime");
    });
    rectSpy.mockRestore();
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

  test("arrange icon renders in the hero category row and is icon-only by default", async () => {
    renderLibrary("/library");

    const arrangeButton = await screen.findByRole("button", { name: "Arrange library" });

    expect(arrangeButton.closest(".library-desktop-hero__category-row")).not.toBeNull();
    expect(arrangeButton).not.toHaveTextContent(/\S/);
  });

  test("clicking arrange icon opens the arrange panel", async () => {
    const user = userEvent.setup();
    renderLibrary("/library");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));

    expect(screen.getByRole("dialog", { name: "Arrange library" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All genres" })).toBeInTheDocument();
  });

  test("source option updates the URL and request path", async () => {
    const user = userEvent.setup();
    const { locations } = renderLibrary("/library?category=movies");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));
    await user.click(within(screen.getByRole("dialog", { name: "Arrange library" })).getByRole("button", { name: "Local" }));

    await waitFor(() => {
      expect(locations).toContain("/library?category=movies&source=local");
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies&source=local", expect.any(Object));
    });
    expect(screen.getByRole("button", { name: "Arrange library" })).toHaveTextContent("Local");
  });

  test("genre option is single-select and updates the request path", async () => {
    const user = userEvent.setup();
    const { locations } = renderLibrary(
      "/library?category=movies",
      {
        ...emptyLibraryPayload,
        available_genres: ["Adventure", "Family"],
      },
    );

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));
    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    await user.click(within(panel).getByRole("button", { name: "Adventure" }));
    await user.click(within(panel).getByRole("button", { name: "Family" }));

    await waitFor(() => {
      expect(locations).toContain("/library?category=movies&genre=Family");
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies&genre=Family", expect.any(Object));
    });
    expect(screen.getByRole("button", { name: "Arrange library" })).toHaveTextContent("Family");
  });

  test("quality and sort options update URL and compact active label", async () => {
    const user = userEvent.setup();
    const { locations } = renderLibrary("/library?category=movies");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));
    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    await user.click(within(panel).getByRole("button", { name: "Gold" }));
    await user.click(within(panel).getByRole("button", { name: "A → Z" }));

    await waitFor(() => {
      expect(locations).toContain("/library?category=movies&quality=gold&sort=az");
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies&quality=gold&sort=az", expect.any(Object));
    });
    expect(screen.getByRole("button", { name: "Arrange library" })).toHaveTextContent("A → Z");
  });

  test("quality option uses an exact compact active label", async () => {
    const user = userEvent.setup();
    const { locations } = renderLibrary("/library?category=movies");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));
    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    await user.click(within(panel).getByRole("button", { name: "Gold" }));

    await waitFor(() => {
      expect(locations).toContain("/library?category=movies&quality=gold");
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies&quality=gold", expect.any(Object));
    });
    expect(screen.getByRole("button", { name: "Arrange library" })).toHaveTextContent("Gold");
    expect(screen.getByRole("button", { name: "Arrange library" })).not.toHaveTextContent("Gold+");
  });

  test("search request includes active category and arrange filters", async () => {
    renderLibrary(
      "/library?category=anime&source=local&genre=Adventure&quality=gold&sort=az",
      {
        ...emptyLibraryPayload,
        available_genres: ["Adventure"],
      },
    );

    await screen.findByRole("tab", { name: "Anime" });
    fireEvent.change(screen.getAllByRole("searchbox", { name: "Search library" })[0], {
      target: { value: "akira" },
    });

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=akira&category=anime&source=local&genre=Adventure&quality=gold&sort=az",
        expect.any(Object),
      );
    });
  });

  test("smart default keeps continue watching, rails, recently added, and other sections", async () => {
    const alpha = libraryItem({ id: 1, title: "Alpha" });
    const beta = libraryItem({ id: 2, title: "Beta", progress_seconds: 120, progress_duration_seconds: 1200 });
    renderLibrary(
      "/library?category=movies",
      libraryPayload({
        items: [alpha, beta],
        continue_watching: [beta],
        series_rails: [
          {
            key: "saga",
            title: "Saga",
            film_count: 1,
            items: [alpha],
          },
        ],
        recently_added: [alpha, beta],
        total_items: 2,
      }),
    );

    expect(await screen.findByRole("heading", { name: "Continue watching" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Saga" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recently added" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Other Movies" })).toBeInTheDocument();
  });

  test("A to Z sort renders one flat grid and hides smart default sections", async () => {
    renderLibrary(
      "/library?category=movies&sort=az",
      libraryPayload({
        items: [
          libraryItem({ id: 1, title: "Alpha" }),
          libraryItem({ id: 2, title: "Beta", progress_seconds: 120, progress_duration_seconds: 1200 }),
        ],
        continue_watching: [libraryItem({ id: 2, title: "Beta", progress_seconds: 120, progress_duration_seconds: 1200 })],
        series_rails: [
          {
            key: "saga",
            title: "Saga",
            film_count: 1,
            items: [libraryItem({ id: 1, title: "Alpha" })],
          },
        ],
        recently_added: [libraryItem({ id: 2, title: "Beta" }), libraryItem({ id: 1, title: "Alpha" })],
        arrange: {
          source: "all",
          genre: null,
          quality: "all",
          sort: "az",
        },
        total_items: 2,
      }),
    );

    await screen.findByRole("link", { name: "Alpha" });

    expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies&sort=az", expect.any(Object));
    expect(screen.queryByRole("heading", { name: "Continue watching" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Saga" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recently added" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Other Movies" })).not.toBeInTheDocument();
    expect(visibleTitleLinkNames("Alpha", "Beta")).toEqual(["Alpha", "Beta"]);
  });

  test("file size sort also renders the flat sorted grid", async () => {
    renderLibrary(
      "/library?category=movies&sort=size_desc",
      libraryPayload({
        items: [
          libraryItem({ id: 2, title: "Large Copy", file_size: 2000 }),
          libraryItem({ id: 1, title: "Small Copy", file_size: 1000 }),
        ],
        continue_watching: [libraryItem({ id: 1, title: "Small Copy", progress_seconds: 120, progress_duration_seconds: 1200 })],
        series_rails: [
          {
            key: "copies",
            title: "Copies",
            film_count: 1,
            items: [libraryItem({ id: 2, title: "Large Copy" })],
          },
        ],
        recently_added: [libraryItem({ id: 1, title: "Small Copy" })],
        arrange: {
          source: "all",
          genre: null,
          quality: "all",
          sort: "size_desc",
        },
        total_items: 2,
      }),
    );

    await screen.findByRole("link", { name: "Large Copy" });

    expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies&sort=size_desc", expect.any(Object));
    expect(screen.queryByRole("heading", { name: "Continue watching" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Copies" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recently added" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Other Movies" })).not.toBeInTheDocument();
    expect(visibleTitleLinkNames("Large Copy", "Small Copy")).toEqual(["Large Copy", "Small Copy"]);
  });

  test("existing Local and Cloud cards still render", async () => {
    renderLibrary("/library");

    expect(await screen.findByRole("link", { name: /Local/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Cloud/ })).toBeInTheDocument();
  });

  test("phone arrange panel uses the mobile panel class", async () => {
    mockPlatformState.deviceClass = "phone";
    mockPlatformState.platform = "iphone";
    const user = userEvent.setup();
    renderLibrary("/library");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));

    expect(screen.getByRole("dialog", { name: "Arrange library" })).toHaveClass("library-arrange__panel--mobile");
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
