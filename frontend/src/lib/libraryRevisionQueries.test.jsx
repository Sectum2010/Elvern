import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "./api.js";
import {
  applyLibraryRevisionChange,
  buildLibraryRevisionQueryKey,
  LibraryRevisionSynchronizer,
  LIBRARY_REVISION_VISIBLE_INTERVAL_MS,
  resolveLibraryRevisionMode,
} from "./libraryRevisionQueries.js";
import { buildLibraryV2QueryKey } from "./libraryQueries.js";
import { queryClient } from "./queryClient.js";


const mockAuth = {
  user: { id: 7, role: "standard_user", age_credential: 16, assistant_beta_enabled: false },
  refreshAuth: vi.fn(),
};


vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => mockAuth,
}));

vi.mock("./api.js", () => ({
  apiRequest: vi.fn(),
}));


function revision(overrides = {}) {
  return {
    schema_version: "library-revision-v1",
    catalog: "catalog-1",
    presentation: "presentation-1",
    permission: "permission-1",
    user_overlay: "overlay-1",
    progress: "progress-1",
    combined_library: "combined-1",
    ...overrides,
  };
}


describe("Library revision synchronization", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubEnv("VITE_ELVERN_LIBRARY_REVISION_MODE", "on");
    queryClient.clear();
    apiRequest.mockReset();
    mockAuth.refreshAuth.mockReset();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  test("mode defaults on while explicit off and invalid values stay safely off", () => {
    vi.stubEnv("VITE_ELVERN_LIBRARY_REVISION_MODE", "");
    expect(resolveLibraryRevisionMode()).toBe("on");
    expect(resolveLibraryRevisionMode("")).toBe("on");
    expect(resolveLibraryRevisionMode("   ")).toBe("on");
    expect(resolveLibraryRevisionMode("off")).toBe("off");
    expect(resolveLibraryRevisionMode("unexpected")).toBe("off");
    expect(resolveLibraryRevisionMode("on")).toBe("on");
  });

  test("off mode mounts without polling on timers or lifecycle events", async () => {
    vi.stubEnv("VITE_ELVERN_LIBRARY_REVISION_MODE", "off");
    render(<LibraryRevisionSynchronizer />);
    await act(() => Promise.resolve());

    act(() => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("pageshow"));
      window.dispatchEvent(new Event("online"));
    });
    await act(() => vi.advanceTimersByTimeAsync(LIBRARY_REVISION_VISIBLE_INTERVAL_MS * 2));

    expect(apiRequest).not.toHaveBeenCalled();
  });

  test("query identity includes user, role, and permission identity", () => {
    expect(buildLibraryRevisionQueryKey(mockAuth.user)).toEqual([
      "library-revision",
      "v1",
      expect.objectContaining({
        userId: "7",
        role: "standard_user",
        permissionIdentity: expect.stringContaining("ageCredential"),
      }),
    ]);
  });

  test("first fetch establishes a baseline and visible polling is non-overlapping", async () => {
    let resolveFirst;
    apiRequest.mockReturnValueOnce(new Promise((resolve) => {
      resolveFirst = resolve;
    }));
    render(<LibraryRevisionSynchronizer />);
    await act(() => Promise.resolve());
    expect(apiRequest).toHaveBeenCalledTimes(1);

    act(() => window.dispatchEvent(new Event("focus")));
    await act(() => Promise.resolve());
    expect(apiRequest).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst(revision());
      await Promise.resolve();
    });
    apiRequest.mockResolvedValueOnce(revision());
    await act(() => vi.advanceTimersByTimeAsync(LIBRARY_REVISION_VISIBLE_INTERVAL_MS));
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("catalog changes silently stale Library while progress changes patch existing entities", async () => {
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const key = buildLibraryV2QueryKey({
      userId: 7,
      role: "standard_user",
      category: "movies",
    });
    queryClient.setQueryData(key, {
      schema_version: "library-summary-v2",
      items_by_id: {
        "42": { id: 42, title: "Movie", progress_seconds: 10, completed: false },
      },
      sections: { item_ids: [42] },
    });
    apiRequest.mockResolvedValueOnce({
      schema_version: "library-progress-state-v1",
      progress_revision: "progress-2",
      items: [{ id: 42, progress_seconds: 25, progress_duration_seconds: 100, completed: false }],
    });

    const result = await applyLibraryRevisionChange({
      previous: revision(),
      current: revision({ catalog: "catalog-2", progress: "progress-2", combined_library: "combined-2" }),
      refreshAuth: mockAuth.refreshAuth,
    });

    expect(result.changedLayers).toEqual(expect.arrayContaining(["catalog", "progress", "combined_library"]));
    expect(apiRequest).toHaveBeenCalledWith("/api/library/v2/progress-state", { cache: "no-store" });
    expect(queryClient.getQueryData(key).items_by_id["42"]).toMatchObject({
      progress_seconds: 25,
      progress_duration_seconds: 100,
      completed: false,
    });
    expect(invalidateSpy).toHaveBeenCalled();
  });
});
