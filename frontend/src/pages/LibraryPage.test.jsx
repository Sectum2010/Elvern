import { readFileSync } from "node:fs";
import { useEffect } from "react";

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useNavigationType,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { readLibraryReturnTarget, rememberLibraryReturnTarget } from "../lib/libraryNavigation";
import {
  buildLibraryQueryKey,
  LIBRARY_QUERY_STALE_TIME_MS,
} from "../lib/libraryQueries";
import { queryClient } from "../lib/queryClient";
import { LibraryPage } from "./LibraryPage";

const MAINTENANCE_MODE_MESSAGE = "The server is currently under construction, please try again later";

const mockPlatformState = vi.hoisted(() => ({
  deviceClass: "desktop",
  platform: "desktop",
}));
const mockAuthState = vi.hoisted(() => ({
  id: 2,
  role: "standard_user",
}));


vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    refreshAuth: vi.fn(),
    user: {
      id: mockAuthState.id,
      role: mockAuthState.role,
    },
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
  isMaintenanceModeError: (error) => (
    error?.status === 503
    && error?.message === "The server is currently under construction, please try again later"
  ),
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


function summaryV2Payload(v1Payload = emptyLibraryPayload) {
  const itemsById = {};
  const addItems = (items = []) => items.forEach((item) => {
    itemsById[String(item.id)] ||= {
      id: item.id,
      title: item.title,
      year: item.year ?? null,
      poster_url: item.poster_url ?? null,
      source_kind: item.source_kind || "local",
      quality_rank: {
        key: "wood",
        label: "Wood",
        score: 0,
        description: "Basic fallback copy.",
        detected: [],
        tooltip: "Basic fallback copy.",
      },
      duration_seconds: item.duration_seconds ?? null,
      progress_seconds: item.progress_seconds ?? null,
      progress_duration_seconds: item.progress_duration_seconds ?? null,
      completed: Boolean(item.completed),
    };
  });
  addItems(v1Payload.items);
  (v1Payload.series_rails || []).forEach((rail) => addItems(rail.items));
  (v1Payload.cloud_series_rails || []).forEach((rail) => addItems(rail.items));
  addItems(v1Payload.continue_watching);
  addItems(v1Payload.recently_added);
  return {
    schema_version: "library-summary-v2",
    revision: "a".repeat(64),
    view: {
      category: "movies",
      source: v1Payload.arrange?.source || "all",
      genre: v1Payload.arrange?.genre ?? null,
      quality: v1Payload.arrange?.quality || "all",
      sort: v1Payload.arrange?.sort || "smart",
    },
    items_by_id: itemsById,
    sections: {
      item_ids: (v1Payload.items || []).map((item) => item.id),
      series_rails: (v1Payload.series_rails || []).map((rail) => ({
        key: rail.key,
        title: rail.title,
        film_count: rail.film_count,
        item_ids: (rail.items || []).map((item) => item.id),
      })),
      cloud_series_rails: (v1Payload.cloud_series_rails || []).map((rail) => ({
        key: rail.key,
        title: rail.title,
        film_count: rail.film_count,
        item_ids: (rail.items || []).map((item) => item.id),
      })),
      continue_watching_item_ids: (v1Payload.continue_watching || []).map((item) => item.id),
      recently_added_item_ids: (v1Payload.recently_added || []).map((item) => item.id),
    },
    available_genres: v1Payload.available_genres || [],
    total_items: v1Payload.total_items || 0,
    scan_in_progress: Boolean(v1Payload.scan_in_progress),
  };
}


function visibleTitleLinkNames(...names) {
  const expectedNames = new Set(names);
  return screen
    .getAllByRole("link")
    .map((link) => link.textContent.trim())
    .filter((name) => expectedNames.has(name));
}


function mockApi(payload = emptyLibraryPayload, { maintenanceModeEnabled = false } = {}) {
  apiRequest.mockImplementation((requestPath) => {
    if (requestPath === "/api/user-settings") {
      return Promise.resolve(defaultSettings);
    }
    if (requestPath === "/api/admin/maintenance-mode") {
      return Promise.resolve({ enabled: maintenanceModeEnabled });
    }
    if (requestPath.startsWith("/api/library")) {
      return Promise.resolve(payload);
    }
    return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
  });
}


function LocationProbe({ locations, navigationTypes }) {
  const location = useLocation();
  const navigationType = useNavigationType();
  useEffect(() => {
    locations.push(`${location.pathname}${location.search}`);
    navigationTypes.push(navigationType);
  }, [location.pathname, location.search, locations, navigationType, navigationTypes]);
  return null;
}


function HistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(-1)} type="button">History back</button>
      <button onClick={() => navigate(1)} type="button">History forward</button>
    </>
  );
}


function renderLibrary(initialEntry = "/library", payload = emptyLibraryPayload, options = {}) {
  const locations = [];
  const navigationTypes = [];
  const {
    initialEntries = [initialEntry],
    initialIndex,
    withHistoryControls = false,
    ...apiOptions
  } = options;
  mockApi(payload, apiOptions);
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries} initialIndex={initialIndex}>
        <LocationProbe locations={locations} navigationTypes={navigationTypes} />
        {withHistoryControls ? <HistoryControls /> : null}
        <LibraryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { locations, navigationTypes };
}


function DetailStub() {
  const location = useLocation();
  const returnTarget = location.state?.libraryReturn;
  return (
    <Link state={{ restoreLibraryReturn: true }} to={returnTarget?.listPath || "/library"}>
      Return to library
    </Link>
  );
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


function setViewportSize(width, height) {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    writable: true,
    value: height,
  });
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
    queryClient.clear();
    apiRequest.mockReset();
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "desktop";
    mockAuthState.id = 2;
    mockAuthState.role = "standard_user";
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
    setViewportSize(1024, 768);
    window.scrollTo = vi.fn();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  test("defaults to Movies and requests the movies category", async () => {
    renderLibrary("/library");

    const moviesTab = await screen.findByRole("tab", { name: "Movies" });

    expect(moviesTab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library?category=movies",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  test("on mode renders a non-search root view from normalized v2 only", async () => {
    vi.stubEnv("VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE", "on");
    const v1 = libraryPayload({ items: [libraryItem({ id: 71, title: "V2 Root" })], total_items: 1 });
    const v2 = summaryV2Payload(v1);
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/user-settings") return Promise.resolve(defaultSettings);
      if (requestPath === "/api/library/v2/summary?category=movies") return Promise.resolve(v2);
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/library"]}>
          <LibraryPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("link", { name: "V2 Root" })).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/library/v2/summary?category=movies",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(apiRequest.mock.calls.some(([path]) => path === "/api/library?category=movies")).toBe(false);
  });

  test("on mode keeps formal root search on the v1 search endpoint", async () => {
    vi.stubEnv("VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE", "on");
    renderLibrary("/library?q=akira", libraryPayload({
      items: [libraryItem({ title: "Akira" })],
      total_items: 1,
    }));

    expect(await screen.findByRole("link", { name: "Akira" })).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/library/search?q=akira&category=movies",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(apiRequest.mock.calls.some(([path]) => path.includes("/api/library/v2/summary"))).toBe(false);
  });

  test("shows the maintenance mode warning line when Maintenance Mode is on", async () => {
    mockAuthState.role = "admin";
    renderLibrary("/library", emptyLibraryPayload, { maintenanceModeEnabled: true });

    const warning = await screen.findByText("Maintenance mode is still turned on");

    expect(warning).toHaveClass("library-maintenance-warning-line");
    const hero = document.querySelector(".library-desktop-hero");
    expect(hero?.nextElementSibling).toBe(warning);
  });

  test("hides the maintenance mode warning line when Maintenance Mode is off", async () => {
    mockAuthState.role = "admin";
    renderLibrary("/library", emptyLibraryPayload, { maintenanceModeEnabled: false });

    await screen.findByRole("tab", { name: "Movies" });

    expect(screen.queryByText("Maintenance mode is still turned on")).not.toBeInTheDocument();
  });

  test("standard users never request the admin maintenance endpoint", async () => {
    renderLibrary("/library", emptyLibraryPayload);

    await screen.findByRole("tab", { name: "Movies" });

    expect(apiRequest.mock.calls.some(([path]) => path === "/api/admin/maintenance-mode")).toBe(false);
    expect(screen.queryByText("Maintenance mode is still turned on")).not.toBeInTheDocument();
  });

  test("does not render the regular-user maintenance block message as a Library error", async () => {
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultSettings);
      }
      if (requestPath === "/api/admin/maintenance-mode") {
        return Promise.resolve({ enabled: false });
      }
      if (requestPath.startsWith("/api/library")) {
        return Promise.reject(Object.assign(new Error(MAINTENANCE_MODE_MESSAGE), { status: 503 }));
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/library"]}>
          <LibraryPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole("tab", { name: "Movies" });

    expect(screen.queryByText(MAINTENANCE_MODE_MESSAGE)).not.toBeInTheDocument();
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

  test("initializes the search input and request from q in the URL", async () => {
    renderLibrary("/library?category=anime&q=akira");

    expect((await screen.findAllByRole("searchbox", { name: "Search library" }))[0]).toHaveValue("akira");
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=akira&category=anime",
        expect.any(Object),
      );
    });
  });

  test("debounces search requests and replaces the current history entry", async () => {
    const { locations, navigationTypes } = renderLibrary("/library?category=movies");
    await screen.findByRole("tab", { name: "Movies" });
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies", expect.any(Object));
    });
    apiRequest.mockClear();

    fireEvent.change(screen.getAllByRole("searchbox", { name: "Search library" })[0], {
      target: { value: "matrix" },
    });

    expect(apiRequest).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/library/search"),
      expect.any(Object),
    );
    expect(locations.at(-1)).toBe("/library?category=movies");

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=matrix&category=movies",
        expect.any(Object),
      );
    });
    expect(locations.at(-1)).toBe("/library?category=movies&q=matrix");
    expect(navigationTypes.at(-1)).toBe("REPLACE");
  });

  test("clearing search removes q while preserving the other view parameters", async () => {
    const { locations } = renderLibrary("/library?category=anime&source=local&q=akira");
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=akira&category=anime&source=local",
        expect.any(Object),
      );
    });
    apiRequest.mockClear();

    fireEvent.change(screen.getAllByRole("searchbox", { name: "Search library" })[0], {
      target: { value: "" },
    });

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library?category=anime&source=local",
        expect.any(Object),
      );
    });
    expect(locations.at(-1)).toBe("/library?category=anime&source=local");
  });

  test("browser history synchronizes the input and exact library query", async () => {
    renderLibrary("/library?q=matrix", emptyLibraryPayload, {
      initialEntries: ["/library?category=anime&q=akira", "/library?category=movies&q=matrix"],
      initialIndex: 1,
      withHistoryControls: true,
    });
    expect((await screen.findAllByRole("searchbox", { name: "Search library" }))[0]).toHaveValue("matrix");

    fireEvent.click(screen.getByRole("button", { name: "History back" }));
    await waitFor(() => {
      expect(screen.getAllByRole("searchbox", { name: "Search library" })[0]).toHaveValue("akira");
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=akira&category=anime",
        expect.any(Object),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "History forward" }));
    await waitFor(() => {
      expect(screen.getAllByRole("searchbox", { name: "Search library" })[0]).toHaveValue("matrix");
    });
  });

  test("view changes do not reload user settings or maintenance status", async () => {
    mockAuthState.role = "admin";
    const user = userEvent.setup();
    renderLibrary("/library?category=movies");
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies", expect.any(Object));
    });

    await user.click(screen.getByRole("tab", { name: "Anime" }));
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=anime", expect.any(Object));
    });
    fireEvent.change(screen.getAllByRole("searchbox", { name: "Search library" })[0], {
      target: { value: "akira" },
    });
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=akira&category=anime",
        expect.any(Object),
      );
    });

    await user.click(screen.getByRole("button", { name: "Arrange library" }));
    await user.click(within(screen.getByRole("dialog", { name: "Arrange library" })).getByRole("button", { name: "Local" }));
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library/search?q=akira&category=anime&source=local",
        expect.any(Object),
      );
    });

    expect(apiRequest.mock.calls.filter(([path]) => path === "/api/user-settings")).toHaveLength(1);
    expect(apiRequest.mock.calls.filter(([path]) => path === "/api/admin/maintenance-mode")).toHaveLength(1);
  });

  test("an older view response cannot replace the current exact query", async () => {
    const user = userEvent.setup();
    let resolveMovies;
    const moviesRequest = new Promise((resolve) => {
      resolveMovies = resolve;
    });
    apiRequest.mockImplementation((path) => {
      if (path === "/api/user-settings") {
        return Promise.resolve(defaultSettings);
      }
      if (path === "/api/admin/maintenance-mode") {
        return Promise.resolve({ enabled: false });
      }
      if (path === "/api/library?category=movies") {
        return moviesRequest;
      }
      if (path === "/api/library?category=anime") {
        return Promise.resolve(libraryPayload({
          items: [libraryItem({ id: 42, title: "Akira" })],
          total_items: 1,
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/library?category=movies"]}>
          <LibraryPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("tab", { name: "Anime" }));
    expect(await screen.findByRole("link", { name: "Akira" })).toBeInTheDocument();

    await act(async () => {
      resolveMovies(libraryPayload({
        items: [libraryItem({ id: 7, title: "The Matrix" })],
        total_items: 1,
      }));
      await moviesRequest;
    });

    expect(screen.getByRole("link", { name: "Akira" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "The Matrix" })).not.toBeInTheDocument();
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

  test("cloud source option updates the URL and request path", async () => {
    const user = userEvent.setup();
    const { locations } = renderLibrary("/library?category=movies");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));
    await user.click(within(screen.getByRole("dialog", { name: "Arrange library" })).getByRole("button", { name: "Cloud" }));

    await waitFor(() => {
      expect(locations).toContain("/library?category=movies&source=cloud");
      expect(apiRequest).toHaveBeenCalledWith("/api/library?category=movies&source=cloud", expect.any(Object));
    });
    expect(screen.getByRole("button", { name: "Arrange library" })).toHaveTextContent("Cloud");
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

  test("root Local and Cloud cards are removed while source arrange controls remain", async () => {
    const user = userEvent.setup();
    renderLibrary("/library");

    await screen.findByRole("button", { name: "Arrange library" });

    expect(screen.queryByRole("link", { name: /Local/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Cloud/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Arrange library" }));
    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    expect(within(panel).getByRole("button", { name: "All" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Local" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Cloud" })).toBeInTheDocument();
  });

  test("phone arrange panel uses the scrollable phone panel and keeps controls available", async () => {
    mockPlatformState.deviceClass = "phone";
    mockPlatformState.platform = "iphone";
    const user = userEvent.setup();
    renderLibrary("/library");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));

    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    expect(panel).toHaveClass("library-arrange__panel--scrollable");
    expect(panel).toHaveClass("library-arrange__panel--phone");
    expect(panel).not.toHaveClass("library-arrange__panel--desktop");
    expect(panel.querySelector(".library-arrange__mobile-handle")).toBeNull();
    expect(panel.querySelector(".library-arrange__side-scroll-indicator")).toBeNull();
    expect(panel.querySelector(".library-arrange__panel-body--scrollable")).not.toBeNull();
    expect(screen.getByRole("tablist", { name: "Library category" })).toBeInTheDocument();
    expect(within(panel).getByText("Source")).toBeInTheDocument();
    expect(within(panel).getByText("Genre")).toBeInTheDocument();
    expect(within(panel).getByText("Quality")).toBeInTheDocument();
    expect(within(panel).getByText("Sort")).toBeInTheDocument();
  });

  test("tablet arrange panel uses the taller scrollable tablet panel", async () => {
    mockPlatformState.deviceClass = "tablet";
    mockPlatformState.platform = "ipad";
    const user = userEvent.setup();
    renderLibrary("/library");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));

    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    expect(panel).toHaveClass("library-arrange__panel--scrollable");
    expect(panel).toHaveClass("library-arrange__panel--tablet");
    expect(panel).not.toHaveClass("library-arrange__panel--phone");
    expect(panel).not.toHaveClass("library-arrange__panel--desktop");
    expect(panel.querySelector(".library-arrange__mobile-handle")).toBeNull();
    expect(panel.querySelector(".library-arrange__side-scroll-indicator")).toBeNull();
    expect(panel.querySelector(".library-arrange__panel-body--scrollable")).not.toBeNull();
  });

  test("ipad platform uses the tablet scrollable arrange path even with a desktop device class", async () => {
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "ipad";
    const user = userEvent.setup();
    renderLibrary("/library");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));

    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    expect(panel).toHaveClass("library-arrange__panel--scrollable");
    expect(panel).toHaveClass("library-arrange__panel--tablet");
    expect(panel).not.toHaveClass("library-arrange__panel--desktop");
    expect(panel.querySelector(".library-arrange__side-scroll-indicator")).toBeNull();
    expect(panel.querySelector(".library-arrange__panel-body--scrollable")).not.toBeNull();
    expect(document.querySelector(".page-section--library")).toHaveAttribute("data-device-class", "tablet");
  });

  test("desktop arrange panel keeps the desktop dropdown", async () => {
    const user = userEvent.setup();
    renderLibrary("/library");

    await user.click(await screen.findByRole("button", { name: "Arrange library" }));

    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    expect(panel).toHaveClass("library-arrange__panel--desktop");
    expect(panel).not.toHaveClass("library-arrange__panel--scrollable");
    expect(panel.querySelector(".library-arrange__mobile-handle")).toBeNull();
    expect(panel.querySelector(".library-arrange__side-scroll-indicator")).toBeNull();
    expect(panel.querySelector(".library-arrange__panel-body--scrollable")).toBeNull();
  });

  test("phone portrait aligns floating search to the hero right edge", async () => {
    mockPlatformState.deviceClass = "phone";
    mockPlatformState.platform = "iphone";
    setViewportSize(390, 844);
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function getRect() {
      if (this.classList?.contains("library-desktop-hero")) {
        return rect(12, 360);
      }
      return rect(0, 100);
    });
    renderLibrary("/library");

    await screen.findByRole("button", { name: "Arrange library" });
    const section = document.querySelector(".page-section--library");

    expect(section).toHaveAttribute("data-device-class", "phone");
    expect(section).toHaveAttribute("data-floating-search-align", "hero-right");
    await waitFor(() => {
      expect(section.style.getPropertyValue("--library-hero-right-gutter")).toBe("30px");
    });
    rectSpy.mockRestore();
  });

  test("phone landscape leaves the floating search on the normal offset path", async () => {
    mockPlatformState.deviceClass = "phone";
    mockPlatformState.platform = "iphone";
    setViewportSize(844, 390);
    renderLibrary("/library");

    await screen.findByRole("button", { name: "Arrange library" });
    const section = document.querySelector(".page-section--library");

    expect(section).toHaveAttribute("data-device-class", "phone");
    expect(section).not.toHaveAttribute("data-floating-search-align");
  });

  test("detail return target preserves q and the search-result card instance", async () => {
    renderLibrary(
      "/library?category=anime&q=akira",
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

    expect(readLibraryReturnTarget()).toMatchObject({
      listPath: "/library?category=anime&q=akira",
      anchorInstanceKey: "search-results:42",
    });
  });

  test("library detail return renders a fresh exact-key cache without full-page loading", async () => {
    const payload = libraryPayload({
      items: [libraryItem({ id: 42, title: "Akira" })],
      total_items: 1,
    });
    mockApi(payload);
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/library?category=anime"]}>
          <Routes>
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/library/:itemId" element={<DetailStub />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await userEvent.click(await screen.findByRole("link", { name: "Akira" }));
    await userEvent.click(await screen.findByRole("link", { name: "Return to library" }));

    expect(screen.queryByText("Loading library...")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Akira" })).toBeInTheDocument();
    expect(apiRequest.mock.calls.filter(([path]) => path === "/api/library?category=anime")).toHaveLength(1);
  });

  test("stale cache restores before background refetch and does not jump twice", async () => {
    const payload = libraryPayload({
      items: [libraryItem({ id: 42, title: "Akira" })],
      total_items: 1,
    });
    const cacheKey = buildLibraryQueryKey({
      userId: 2,
      role: "standard_user",
      category: "movies",
      source: "all",
      genre: "",
      quality: "all",
      sort: "smart",
      query: "",
    });
    queryClient.setQueryData(cacheKey, payload, {
      updatedAt: Date.now() - LIBRARY_QUERY_STALE_TIME_MS - 1,
    });
    rememberLibraryReturnTarget({
      listPath: "/library?category=movies",
      anchorItemId: 42,
      anchorInstanceKey: "other-movies:42",
      anchorViewportRatioY: 0.4,
      scrollY: 500,
      pendingRestore: true,
    });
    let resolveRefresh;
    const refreshPromise = new Promise((resolve) => {
      resolveRefresh = resolve;
    });
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultSettings);
      }
      if (requestPath === "/api/admin/maintenance-mode") {
        return Promise.resolve({ enabled: false });
      }
      if (requestPath === "/api/library?category=movies") {
        return refreshPromise;
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      bottom: 500,
      height: 300,
      left: 0,
      right: 200,
      top: 200,
      width: 200,
      x: 0,
      y: 200,
      toJSON: () => {},
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[{
          pathname: "/library",
          search: "?category=movies",
          state: { restoreLibraryReturn: true },
        }]}
        >
          <LibraryPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("link", { name: "Akira" })).toBeInTheDocument();
    expect(screen.queryByText("Loading library...")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(window.scrollTo).toHaveBeenCalled();
    });
    const restoreCallCount = window.scrollTo.mock.calls.length;

    await act(async () => {
      resolveRefresh(payload);
      await refreshPromise;
    });
    await act(async () => Promise.resolve());

    expect(window.scrollTo).toHaveBeenCalledTimes(restoreCallCount);
    rectSpy.mockRestore();
  });
});


describe("LibraryPage CSS guards", () => {
  const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");

  test("phone and tablet arrange dropdowns open downward as scrollable panels", () => {
    expect(styles).toMatch(/\.library-arrange__panel--scrollable\s*\{[^}]*position:\s*absolute;[^}]*inset-block-start:\s*calc\(100% \+ 0\.45rem\);[^}]*overflow:\s*hidden;/s);
    expect(styles).toMatch(/\.library-arrange__panel--phone\s*\{[^}]*--library-arrange-scroll-body-height:\s*min\(33\.8vh,\s*14\.733rem\);/s);
    expect(styles).toMatch(/\.library-arrange__panel--tablet\s*\{[^}]*--library-arrange-scroll-body-height:\s*min\(43\.94vh,\s*19\.153rem\);/s);
    expect(styles).toMatch(/\.library-arrange__panel-body--scrollable\s*\{[^}]*block-size:\s*var\(--library-arrange-scroll-body-height\);[^}]*max-height:\s*var\(--library-arrange-scroll-body-height\);[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;/s);
    expect(styles).not.toContain("library-arrange__mobile-handle");
  });

  test("scrollable arrange dropdown uses only the native scrollable body indicator", () => {
    expect(styles).not.toContain(".library-arrange__panel--scrollable::after");
    expect(styles).not.toContain("library-arrange__side-scroll-indicator");
    expect(styles).toMatch(/\.library-arrange__panel-body--scrollable\s*\{[^}]*scrollbar-color:\s*rgba\(255,\s*255,\s*255,\s*0\.78\) transparent;[^}]*scrollbar-width:\s*thin;/s);
    expect(styles).toMatch(/\.library-arrange__panel-body--scrollable::-webkit-scrollbar-thumb\s*\{[^}]*background:\s*rgba\(255,\s*255,\s*255,\s*0\.78\);/s);
  });

  test("tablet arrange height rule stays after phone height rule", () => {
    const phoneHeightIndex = styles.indexOf(".library-arrange__panel--phone");
    const tabletHeightIndex = styles.indexOf(".library-arrange__panel--tablet");

    expect(phoneHeightIndex).toBeGreaterThan(-1);
    expect(tabletHeightIndex).toBeGreaterThan(phoneHeightIndex);
    expect(styles.slice(tabletHeightIndex)).not.toMatch(/--library-arrange-scroll-body-height:\s*min\(33\.8vh,\s*14\.733rem\);/);
  });

  test("phone and tablet category rows have a stable two-column layout for switch and arrange icon", () => {
    expect(styles).toMatch(/\.page-section--library\[data-device-class="phone"\] \.library-desktop-hero__category-row,\s*\.page-section--library\[data-device-class="tablet"\] \.library-desktop-hero__category-row\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\) auto;[^}]*gap:\s*0\.5rem;/s);
    expect(styles).toMatch(/\.page-section--library\[data-device-class="phone"\] \.library-category-switch,\s*\.page-section--library\[data-device-class="tablet"\] \.library-category-switch\s*\{[^}]*max-width:\s*none;[^}]*min-width:\s*0;/s);
  });

  test("floating search aligns to the measured hero edge without raising above the hero", () => {
    expect(styles).toMatch(/\.library-desktop-hero\s*\{[^}]*z-index:\s*80;/s);
    expect(styles).toMatch(/\.floating-library-search\s*\{[^}]*z-index:\s*17;/s);
    expect(styles).toMatch(/\.page-section--library\[data-floating-search-align="hero-right"\] \.floating-library-search\s*\{[^}]*right:\s*var\(--library-hero-right-gutter,/s);
    expect(styles).not.toMatch(/max\(4\.1rem/);
    expect(styles).not.toMatch(/max\(3\.4rem/);
  });
});
