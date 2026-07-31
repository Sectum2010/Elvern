import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "./api";
import { buildLibraryV2QueryKey } from "./libraryQueries";
import { queryClient } from "./queryClient";
import { useLibraryViewQuery } from "./useLibraryViewQuery";


vi.mock("./api", () => ({
  apiRequest: vi.fn(),
}));

const identity = {
  userId: 7,
  role: "standard_user",
  category: "movies",
  source: "all",
  genres: [],
  qualities: [],
  sort: "smart",
  query: "",
};

function v1Payload() {
  return {
    items: [],
    series_rails: [],
    cloud_series_rails: [],
    continue_watching: [],
    recently_added: [],
    arrange: {
      source: "all",
      genres: [],
      qualities: [],
      genre: null,
      quality: "all",
      sort: "smart",
    },
    available_genres: [],
    total_items: 0,
    scan_in_progress: false,
  };
}

function v2Payload() {
  return {
    schema_version: "library-summary-v2",
    revision: "a".repeat(64),
    view: {
      category: "movies",
      source: "all",
      genres: [],
      qualities: [],
      genre: null,
      quality: "all",
      sort: "smart",
    },
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

function renderQuery(options = {}) {
  return renderHook(() => useLibraryViewQuery({
    enabled: true,
    identity,
    mode: "off",
    searchActive: false,
    v1RequestPath: "/api/library?category=movies",
    v2RequestPath: "/api/library/v2/summary?category=movies",
    viewIdentity: { category: "movies" },
    ...options,
  }), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}


describe("useLibraryViewQuery", () => {
  afterEach(() => {
    queryClient.clear();
    vi.clearAllMocks();
  });

  test("off requests and renders only v1", async () => {
    apiRequest.mockResolvedValue(v1Payload());
    const { result } = renderQuery({ mode: "off" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.activeVersion).toBe("v1");
    expect(apiRequest).toHaveBeenCalledTimes(1);
    expect(apiRequest.mock.calls[0][0]).toContain("/api/library?");
  });

  test("shadow renders v1 while v2 compares in the background", async () => {
    const consoleSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    apiRequest.mockImplementation(async (path) => (
      path.includes("/v2/") ? v2Payload() : v1Payload()
    ));
    const { result } = renderQuery({ mode: "shadow" });

    await waitFor(() => expect(result.current.shadowComparison?.matches).toBe(true));
    expect(result.current.activeVersion).toBe("v1");
    expect(result.current.data).toEqual(v1Payload());
    expect(apiRequest).toHaveBeenCalledTimes(2);
    expect(consoleSpy).not.toHaveBeenCalled();
  });

  test("on keeps raw normalized data in cache and exposes adapted v2 view", async () => {
    const raw = v2Payload();
    apiRequest.mockResolvedValue(raw);
    const { result } = renderQuery({ mode: "on" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.activeVersion).toBe("v2");
    expect(result.current.data.items).toEqual([]);
    expect(queryClient.getQueryData(buildLibraryV2QueryKey(identity))).toBe(raw);
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  test("root search stays on v1 even in on mode", async () => {
    apiRequest.mockResolvedValue(v1Payload());
    const { result } = renderQuery({
      mode: "on",
      searchActive: true,
      v1RequestPath: "/api/library/search?q=synthetic&category=movies",
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.activeVersion).toBe("v1");
    expect(apiRequest).toHaveBeenCalledTimes(1);
    expect(apiRequest.mock.calls[0][0]).toContain("/api/library/search");
  });

  test("explicit capability failure falls back to v1 but server 500 does not", async () => {
    apiRequest.mockImplementation(async (path) => {
      if (path.includes("/v2/")) {
        const error = new Error("disabled");
        error.status = 503;
        error.detail = { code: "library_summary_v2_disabled" };
        throw error;
      }
      return v1Payload();
    });
    const { result } = renderQuery({ mode: "on" });

    await waitFor(() => expect(result.current.capabilityFallback).toBe(true));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.activeVersion).toBe("v1");
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });
});
