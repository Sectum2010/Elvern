import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "./api.js";
import {
  applyLibraryRevisionChange,
  buildLibraryRevisionQueryKey,
  isLibraryRevisionCapabilityUnavailableError,
  LibraryRevisionSynchronizer,
  LibraryProgressRevisionRaceError,
  LibraryProgressStateContractError,
  LIBRARY_PROGRESS_REVISION_IMMEDIATE_RETRY_MAX,
  LIBRARY_REVISION_VISIBLE_INTERVAL_MS,
  resolveLibraryRevisionMode,
  validateLibraryProgressStatePayload,
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


function token(character) {
  return String(character).repeat(64);
}


function revision(overrides = {}) {
  return {
    schema_version: "library-revision-v1",
    catalog: token("a"),
    presentation: token("b"),
    permission: token("c"),
    user_overlay: token("d"),
    progress: token("e"),
    combined_library: token("f"),
    ...overrides,
  };
}


function apiError(status, payload = null) {
  const error = new Error("Request failed");
  error.status = status;
  error.payload = payload;
  error.detail = payload?.detail ?? null;
  return error;
}


async function flushAsyncWork() {
  await act(async () => {
    for (let index = 0; index < 12; index += 1) {
      await Promise.resolve();
    }
  });
}


describe("Library revision synchronization", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubEnv("VITE_ELVERN_LIBRARY_REVISION_MODE", "on");
    queryClient.clear();
    apiRequest.mockReset();
    mockAuth.user = { id: 7, role: "standard_user", age_credential: 16, assistant_beta_enabled: false };
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
      progress_revision: token("9"),
      items: [{ id: 42, progress_seconds: 25, progress_duration_seconds: 100, completed: false }],
    });

    const result = await applyLibraryRevisionChange({
      previous: revision(),
      current: revision({ catalog: token("8"), progress: token("9"), combined_library: token("7") }),
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

  test("strict progress-state validator accepts only the minimal authoritative contract", () => {
    const valid = {
      schema_version: "library-progress-state-v1",
      progress_revision: token("a"),
      items: [
        { id: 1, progress_seconds: 0, progress_duration_seconds: null, completed: false },
        { id: 2, progress_seconds: 12.5, progress_duration_seconds: 100, completed: true },
      ],
    };
    expect(validateLibraryProgressStatePayload(valid)).toBe(valid);

    const invalidPayloads = [
      null,
      { ...valid, schema_version: "wrong" },
      { ...valid, progress_revision: "short" },
      { ...valid, items: "not-an-array" },
      { ...valid, title: "private" },
      { ...valid, items: [{ id: 0, progress_seconds: 0, progress_duration_seconds: null, completed: false }] },
      { ...valid, items: [{ id: 1, progress_seconds: -1, progress_duration_seconds: null, completed: false }] },
      { ...valid, items: [{ id: 1, progress_seconds: "1", progress_duration_seconds: null, completed: false }] },
      { ...valid, items: [{ id: 1, progress_seconds: 1, progress_duration_seconds: Infinity, completed: false }] },
      { ...valid, items: [{ id: 1, progress_seconds: 1, progress_duration_seconds: null, completed: 0 }] },
      { ...valid, items: [{ id: 1, progress_seconds: 1, progress_duration_seconds: null, completed: false, path: "/private" }] },
      { ...valid, items: [
        { id: 1, progress_seconds: 1, progress_duration_seconds: null, completed: false },
        { id: 1, progress_seconds: 2, progress_duration_seconds: null, completed: false },
      ] },
    ];
    invalidPayloads.forEach((payload) => {
      expect(() => validateLibraryProgressStatePayload(payload)).toThrow(LibraryProgressStateContractError);
    });
  });

  test("malformed progress payload preserves progress baseline while successful catalog advances", async () => {
    const key = buildLibraryV2QueryKey({ userId: 7, role: "standard_user", category: "movies" });
    queryClient.setQueryData(key, {
      schema_version: "library-summary-v2",
      items_by_id: { "42": { id: 42, progress_seconds: 10, completed: false } },
      sections: { item_ids: [42] },
    });
    apiRequest.mockResolvedValueOnce({
      schema_version: "library-progress-state-v1",
      progress_revision: token("9"),
      items: [{ id: 42, progress_seconds: 0, progress_duration_seconds: 100, completed: false, title: "private" }],
    });
    const previous = revision();
    const current = revision({ catalog: token("8"), progress: token("9"), combined_library: token("7") });

    const result = await applyLibraryRevisionChange({ previous, current, refreshAuth: mockAuth.refreshAuth });

    expect(result.progressError).toBeInstanceOf(LibraryProgressStateContractError);
    expect(result.immediateRetryRequired).toBe(false);
    expect(result.nextBaseline.catalog).toBe(current.catalog);
    expect(result.nextBaseline.combined_library).toBe(current.combined_library);
    expect(result.nextBaseline.progress).toBe(previous.progress);
    expect(queryClient.getQueryData(key).items_by_id["42"].progress_seconds).toBe(10);
  });

  test("progress token race does not patch or advance progress and requests an immediate retry", async () => {
    const key = buildLibraryV2QueryKey({ userId: 7, role: "standard_user", category: "movies" });
    queryClient.setQueryData(key, {
      schema_version: "library-summary-v2",
      items_by_id: { "42": { id: 42, progress_seconds: 10, completed: false } },
      sections: { item_ids: [42] },
    });
    apiRequest.mockResolvedValueOnce({
      schema_version: "library-progress-state-v1",
      progress_revision: token("8"),
      items: [{ id: 42, progress_seconds: 0, progress_duration_seconds: 100, completed: false }],
    });
    const previous = revision();
    const current = revision({ catalog: token("7"), progress: token("9"), combined_library: token("6") });

    const result = await applyLibraryRevisionChange({ previous, current, refreshAuth: mockAuth.refreshAuth });

    expect(result.progressError).toBeInstanceOf(LibraryProgressRevisionRaceError);
    expect(result.immediateRetryRequired).toBe(true);
    expect(result.nextBaseline.catalog).toBe(current.catalog);
    expect(result.nextBaseline.progress).toBe(previous.progress);
    expect(queryClient.getQueryData(key).items_by_id["42"].progress_seconds).toBe(10);
  });

  test("capability helper matches only endpoint missing and explicit disabled payload", () => {
    expect(isLibraryRevisionCapabilityUnavailableError(apiError(404))).toBe(true);
    expect(isLibraryRevisionCapabilityUnavailableError(apiError(503, {
      detail: { code: "library_revision_disabled" },
    }))).toBe(true);
    expect(isLibraryRevisionCapabilityUnavailableError(apiError(503, {
      detail: { code: "maintenance_mode" },
    }))).toBe(false);
    expect(isLibraryRevisionCapabilityUnavailableError(apiError(500))).toBe(false);
    expect(isLibraryRevisionCapabilityUnavailableError(new TypeError("network"))).toBe(false);
  });

  test.each([
    ["explicit disabled", apiError(503, { detail: { code: "library_revision_disabled" } })],
    ["missing endpoint", apiError(404)],
  ])("%s capability stops timers and lifecycle polling for the current identity", async (_label, error) => {
    const libraryKey = buildLibraryV2QueryKey({ userId: 7, role: "standard_user", category: "movies" });
    const cachedLibrary = { schema_version: "library-summary-v2", items_by_id: {}, sections: {} };
    queryClient.setQueryData(libraryKey, cachedLibrary);
    apiRequest.mockRejectedValue(error);
    render(<LibraryRevisionSynchronizer />);
    await flushAsyncWork();
    expect(apiRequest).toHaveBeenCalledTimes(1);

    act(() => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("pageshow"));
      window.dispatchEvent(new Event("online"));
      window.dispatchEvent(new Event("elvern:library-revision-check"));
    });
    await act(() => vi.advanceTimersByTimeAsync(LIBRARY_REVISION_VISIBLE_INTERVAL_MS * 2));

    expect(apiRequest).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(libraryKey)).toBe(cachedLibrary);
  });

  test.each([
    ["server error", apiError(500)],
    ["network error", new TypeError("network")],
    ["unauthorized", apiError(401)],
    ["forbidden", apiError(403)],
  ])("%s keeps the normal revision retry cadence", async (_label, error) => {
    apiRequest.mockRejectedValueOnce(error).mockResolvedValueOnce(revision());
    render(<LibraryRevisionSynchronizer />);
    await flushAsyncWork();
    expect(apiRequest).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(LIBRARY_REVISION_VISIBLE_INTERVAL_MS));
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("identity change probes again after capability was unavailable", async () => {
    apiRequest.mockRejectedValueOnce(apiError(404)).mockResolvedValueOnce(revision());
    const view = render(<LibraryRevisionSynchronizer />);
    await flushAsyncWork();
    expect(apiRequest).toHaveBeenCalledTimes(1);

    mockAuth.user = { ...mockAuth.user, id: 8 };
    view.rerender(<LibraryRevisionSynchronizer />);
    await flushAsyncWork();

    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("identity change does not inherit an unresolved request lock", async () => {
    let resolveFirst;
    apiRequest
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveFirst = resolve;
      }))
      .mockResolvedValueOnce(revision());
    const view = render(<LibraryRevisionSynchronizer />);
    await act(() => Promise.resolve());
    expect(apiRequest).toHaveBeenCalledTimes(1);

    mockAuth.user = { ...mockAuth.user, id: 8 };
    view.rerender(<LibraryRevisionSynchronizer />);
    await flushAsyncWork();

    expect(apiRequest).toHaveBeenCalledTimes(2);
    await act(async () => {
      resolveFirst(revision());
      await Promise.resolve();
    });
  });

  test("progress revision races retry immediately at most twice without overlap", async () => {
    let revisionCalls = 0;
    let progressCalls = 0;
    apiRequest.mockImplementation((path) => {
      if (path === "/api/library/v2/revision") {
        revisionCalls += 1;
        return Promise.resolve(revision({ progress: token(String(revisionCalls % 10)) }));
      }
      if (path === "/api/library/v2/progress-state") {
        progressCalls += 1;
        return Promise.resolve({
          schema_version: "library-progress-state-v1",
          progress_revision: token(String((revisionCalls + 1) % 10)),
          items: [],
        });
      }
      return Promise.reject(new Error(`Unexpected path: ${path}`));
    });
    render(<LibraryRevisionSynchronizer />);
    await flushAsyncWork();

    act(() => window.dispatchEvent(new Event("focus")));
    await flushAsyncWork();

    expect(revisionCalls).toBe(2 + LIBRARY_PROGRESS_REVISION_IMMEDIATE_RETRY_MAX);
    expect(progressCalls).toBe(1 + LIBRARY_PROGRESS_REVISION_IMMEDIATE_RETRY_MAX);
  });
});
