import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigationType,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { buildLibraryQueryKey, LIBRARY_QUERY_STALE_TIME_MS } from "../lib/libraryQueries";
import { readLibraryReturnTarget } from "../lib/libraryNavigation";
import { queryClient } from "../lib/queryClient";
import { LibrarySourcePage } from "./LibrarySourcePage";


const currentUser = { id: 2, role: "standard_user" };


vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: currentUser, refreshAuth: vi.fn() }),
}));

vi.mock("../lib/api", () => ({
  apiRequest: vi.fn(),
}));


function sourcePayload({ source = "local", title = "Akira" } = {}) {
  const item = {
    id: 42,
    title,
    source_kind: source,
    poster_url: "/api/library/item/42/poster?v=cache-token#poster",
  };
  return {
    items: [item],
    series_rails: source === "local" ? [] : [],
    cloud_series_rails: source === "cloud" ? [] : [],
    total_items: 1,
    scan_in_progress: false,
  };
}


function sourceSummaryV2Payload({ source = "local", title = "Akira" } = {}) {
  return {
    schema_version: "library-summary-v2",
    revision: "b".repeat(64),
    view: { category: "movies", source, genre: null, quality: "all", sort: "smart" },
    items_by_id: {
      "42": {
        id: 42,
        title,
        year: null,
        poster_url: "/api/library/item/42/poster?v=cache-token#poster",
        source_kind: source,
        quality_rank: {
          key: "wood",
          label: "Wood",
          score: 0,
          description: "Basic fallback copy.",
          detected: [],
          tooltip: "Basic fallback copy.",
        },
        duration_seconds: null,
        progress_seconds: null,
        progress_duration_seconds: null,
        completed: false,
      },
    },
    sections: {
      item_ids: [42],
      series_rails: [],
      cloud_series_rails: [],
      continue_watching_item_ids: [],
      recently_added_item_ids: [],
    },
    available_genres: [],
    total_items: 1,
    scan_in_progress: false,
  };
}


function mockSourceApi(payload = sourcePayload()) {
  apiRequest.mockImplementation((path) => {
    if (path === "/api/user-settings") {
      return Promise.resolve({
        floating_library_search_enabled: true,
        poster_card_display_max_width: "1400",
      });
    }
    if (path === "/api/library?category=movies&source=local") {
      return Promise.resolve(payload);
    }
    if (path === "/api/library?category=movies&source=cloud") {
      return Promise.resolve(payload);
    }
    return Promise.reject(new Error(`Unexpected request: ${path}`));
  });
}


function LocationProbe({ locations, navigationTypes }) {
  const location = useLocation();
  const navigationType = useNavigationType();
  locations.push(`${location.pathname}${location.search}`);
  navigationTypes.push(navigationType);
  return null;
}


function renderSource({
  initialEntry = "/library/local",
  payload = sourcePayload(),
  sourceKind = "local",
} = {}) {
  const locations = [];
  const navigationTypes = [];
  mockSourceApi(payload);
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <LocationProbe locations={locations} navigationTypes={navigationTypes} />
        <LibrarySourcePage sourceKind={sourceKind} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { locations, navigationTypes };
}


function DetailStub() {
  const location = useLocation();
  return (
    <Link state={{ restoreLibraryReturn: true }} to={location.state?.libraryReturn?.listPath || "/library/local"}>
      Return to source
    </Link>
  );
}


describe("LibrarySourcePage cached source views", () => {
  beforeEach(() => {
    queryClient.clear();
    vi.stubEnv("VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE", "off");
    window.sessionStorage.clear();
    window.scrollTo = vi.fn();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    apiRequest.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  test("local source requests only the server-filtered payload and uses card width identity", async () => {
    renderSource();

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/library?category=movies&source=local",
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
    expect(apiRequest).not.toHaveBeenCalledWith("/api/library", expect.anything());
    await waitFor(() => expect(document.querySelector(".media-card__poster-image")).toHaveAttribute(
      "src",
      "/api/library/item/42/poster?v=cache-token&variant=card&display_width=1400#poster",
    ));
  });

  test("cloud source requests only source=cloud", async () => {
    renderSource({
      initialEntry: "/library/cloud",
      payload: sourcePayload({ source: "cloud" }),
      sourceKind: "cloud",
    });

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/library?category=movies&source=cloud",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
  });

  test("on mode loads the normalized source payload while q remains local", async () => {
    vi.stubEnv("VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE", "on");
    const payload = sourceSummaryV2Payload({ source: "local", title: "Akira" });
    apiRequest.mockImplementation((path) => {
      if (path === "/api/user-settings") {
        return Promise.resolve({ floating_library_search_enabled: true });
      }
      if (path === "/api/library/v2/summary?category=movies&source=local") {
        return Promise.resolve(payload);
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/library/local?q=akira"]}>
          <LibrarySourcePage sourceKind="local" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("link", { name: "Akira" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search Local Library" })).toHaveValue("akira");
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/library/v2/summary?category=movies&source=local",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(apiRequest.mock.calls.some(([path]) => path.includes("q="))).toBe(false);
    expect(apiRequest.mock.calls.some(([path]) => path === "/api/library?category=movies&source=local")).toBe(false);
  });

  test("fresh exact cache renders immediately without another source request", async () => {
    const payload = sourcePayload();
    const cacheKey = buildLibraryQueryKey({
      userId: currentUser.id,
      role: currentUser.role,
      category: "movies",
      source: "local",
      genre: "",
      quality: "all",
      sort: "smart",
      query: "",
    });
    queryClient.setQueryData(cacheKey, payload);
    renderSource({ payload });

    expect(await screen.findByRole("link", { name: "Akira" })).toBeInTheDocument();
    expect(screen.queryByText("Loading local library...")).not.toBeInTheDocument();
    expect(apiRequest.mock.calls.filter(([path]) => path.includes("/api/library?"))).toHaveLength(0);
  });

  test("stale exact cache stays visible while one background refresh runs", async () => {
    const payload = sourcePayload();
    const cacheKey = buildLibraryQueryKey({
      userId: currentUser.id,
      role: currentUser.role,
      category: "movies",
      source: "local",
      genre: "",
      quality: "all",
      sort: "smart",
      query: "",
    });
    queryClient.setQueryData(cacheKey, payload, {
      updatedAt: Date.now() - LIBRARY_QUERY_STALE_TIME_MS - 1,
    });
    let resolveRefresh;
    const refreshPromise = new Promise((resolve) => {
      resolveRefresh = resolve;
    });
    mockSourceApi(payload);
    apiRequest.mockImplementation((path) => {
      if (path === "/api/user-settings") {
        return Promise.resolve({ floating_library_search_enabled: true });
      }
      if (path === "/api/library?category=movies&source=local") {
        return refreshPromise;
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/library/local"]}>
          <LibrarySourcePage sourceKind="local" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("link", { name: "Akira" })).toBeInTheDocument();
    expect(screen.queryByText("Loading local library...")).not.toBeInTheDocument();
    expect(apiRequest.mock.calls.filter(([path]) => path.includes("source=local"))).toHaveLength(1);
    resolveRefresh(payload);
    await refreshPromise;
  });

  test("stale refresh failure keeps cached content without showing a background error", async () => {
    const payload = sourcePayload();
    const cacheKey = buildLibraryQueryKey({
      userId: currentUser.id,
      role: currentUser.role,
      category: "movies",
      source: "local",
      genre: "",
      quality: "all",
      sort: "smart",
      query: "",
    });
    queryClient.setQueryData(cacheKey, payload, {
      updatedAt: Date.now() - LIBRARY_QUERY_STALE_TIME_MS - 1,
    });
    apiRequest.mockImplementation((path) => {
      if (path === "/api/user-settings") {
        return Promise.resolve({ floating_library_search_enabled: true });
      }
      if (path === "/api/library?category=movies&source=local") {
        return Promise.reject(new Error("Background refresh failed"));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/library/local"]}>
          <LibrarySourcePage sourceKind="local" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("link", { name: "Akira" })).toBeInTheDocument();
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/library?category=movies&source=local",
      expect.anything(),
    ));
    expect(screen.queryByText("Loading local library...")).not.toBeInTheDocument();
    expect(screen.queryByText("Background refresh failed")).not.toBeInTheDocument();
  });

  test("q initializes from URL, commits on Enter, clears, and never refetches the base payload", async () => {
    const { locations, navigationTypes } = renderSource({ initialEntry: "/library/local?q=akira" });
    const input = await screen.findByRole("searchbox", { name: "Search Local Library" });
    expect(input).toHaveValue("akira");
    expect(input.closest("form")).toHaveClass("library-focus-search-card");
    expect(screen.queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "arrival" } });
    expect(locations.at(-1)).toBe("/library/local?q=akira");
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    expect(locations.at(-1)).toBe("/library/local?q=akira");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(locations.at(-1)).toBe("/library/local?q=arrival"));
    expect(navigationTypes.at(-1)).toBe("REPLACE");

    fireEvent.change(input, { target: { value: "" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(locations.at(-1)).toBe("/library/local"));
    expect(apiRequest.mock.calls.filter(([path]) => path.includes("/api/library?"))).toHaveLength(1);
  });

  test.each(["local", "cloud"])(
    "%s source search detail return preserves q, card instance, and exact cached payload",
    async (sourceKind) => {
    const payload = sourcePayload({ source: sourceKind });
    mockSourceApi(payload);
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/library/${sourceKind}?q=akira`]}>
          <Routes>
            <Route path={`/library/${sourceKind}`} element={<LibrarySourcePage sourceKind={sourceKind} />} />
            <Route path="/library/:itemId" element={<DetailStub />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("link", { name: "Akira" }));
    expect(readLibraryReturnTarget()).toMatchObject({
      listPath: `/library/${sourceKind}?q=akira`,
      anchorInstanceKey: `${sourceKind}:other-movies:42`,
    });
    fireEvent.click(await screen.findByRole("link", { name: "Return to source" }));

    const sourceLabel = sourceKind === "cloud" ? "Cloud" : "Local";
    expect(await screen.findByRole("searchbox", { name: `Search ${sourceLabel} Library` })).toHaveValue("akira");
    expect(screen.queryByText(`Loading ${sourceKind} library...`)).not.toBeInTheDocument();
    expect(apiRequest.mock.calls.filter(([path]) => path.includes(`source=${sourceKind}`))).toHaveLength(1);
  },
  );
});
