import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ApiNetworkError, apiRequest } from "./api";
import { clearProtectedQueryCache, queryClient } from "./queryClient";
import {
  publishConnectivityRecovery,
  registerConnectivityFailure,
  resetConnectivityRecoveryStoreForTests,
} from "./connectivityRecoveryStore";
import {
  buildUserSettingsQueryKey,
  resolveUserSettings,
  setUserSettingsQueryData,
  USER_SETTINGS_QUERY_GC_TIME_MS,
  USER_SETTINGS_QUERY_STALE_TIME_MS,
  useUserSettingsQuery,
} from "./userSettingsQueries";


vi.mock("./api", async () => {
  const actual = await vi.importActual("./api");
  return { ...actual, apiRequest: vi.fn() };
});


function SettingsProbe({ label, user }) {
  const query = useUserSettingsQuery(user);
  const settings = resolveUserSettings(query.data);
  return <p>{label}:{settings.poster_card_display_max_width}</p>;
}


function renderProbes(children) {
  return render(
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>,
  );
}


describe("user settings query", () => {
  beforeEach(() => {
    queryClient.clear();
    resetConnectivityRecoveryStoreForTests();
    apiRequest.mockReset();
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
  });

  test("uses fixed in-memory cache lifetimes and normalized user identity", () => {
    expect(USER_SETTINGS_QUERY_STALE_TIME_MS).toBe(5 * 60 * 1000);
    expect(USER_SETTINGS_QUERY_GC_TIME_MS).toBe(4 * 60 * 60 * 1000);
    expect(buildUserSettingsQueryKey({ userId: " 7 ", role: " Admin " })).toEqual([
      "user-settings",
      "v1",
      { userId: "7", role: "admin" },
    ]);
  });

  test("deduplicates simultaneous observers for the same user", async () => {
    apiRequest.mockResolvedValue({ poster_card_display_max_width: "800" });
    const user = { id: 7, role: "standard_user" };
    renderProbes(
      <>
        <SettingsProbe label="shell" user={user} />
        <SettingsProbe label="library" user={user} />
        <SettingsProbe label="detail" user={user} />
      </>,
    );

    expect(await screen.findByText("shell:800")).toBeInTheDocument();
    expect(screen.getByText("library:800")).toBeInTheDocument();
    expect(screen.getByText("detail:800")).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledTimes(1);
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/user-settings",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  test("keeps different users isolated and applies PATCH payloads immediately", async () => {
    apiRequest.mockImplementation((_path, { signal }) => {
      expect(signal).toBeInstanceOf(AbortSignal);
      return Promise.resolve({ poster_card_display_max_width: "1400" });
    });
    const userA = { id: 7, role: "standard_user" };
    const userB = { id: 8, role: "standard_user" };
    renderProbes(
      <>
        <SettingsProbe label="a" user={userA} />
        <SettingsProbe label="b" user={userB} />
      </>,
    );
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(2));

    setUserSettingsQueryData(userA, { poster_card_display_max_width: "2200" });
    expect(await screen.findByText("a:2200")).toBeInTheDocument();
    expect(screen.getByText("b:1400")).toBeInTheDocument();
  });

  test("preserves cached settings when a transient refetch fails", async () => {
    apiRequest.mockResolvedValueOnce({ poster_card_display_max_width: "800" });
    const user = { id: 7, role: "standard_user" };
    renderProbes(<SettingsProbe label="s" user={user} />);
    expect(await screen.findByText("s:800")).toBeInTheDocument();

    apiRequest.mockRejectedValueOnce(new ApiNetworkError());
    await queryClient.refetchQueries({
      queryKey: buildUserSettingsQueryKey({ userId: user.id, role: user.role }),
    });

    // The transient refetch error must not drop the cached settings.
    expect(screen.getByText("s:800")).toBeInTheDocument();
  });

  test("recovers a transient-failed settings query once per connectivity generation", async () => {
    const failure = registerConnectivityFailure();
    apiRequest
      .mockRejectedValueOnce(new ApiNetworkError(undefined, failure))
      .mockResolvedValue({ poster_card_display_max_width: "1600" });
    const user = { id: 7, role: "standard_user" };
    const settingsKey = buildUserSettingsQueryKey({ userId: user.id, role: user.role });
    renderProbes(<SettingsProbe label="s" user={user} />);

    // Initial transient failure keeps defaults usable; wait until the query has
    // actually settled into the error state (defaults render during loading too).
    await waitFor(() => expect(queryClient.getQueryState(settingsKey)?.status).toBe("error"));
    expect(screen.getByText("s:1400")).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledTimes(1);

    publishConnectivityRecovery({
      generation: 3,
      recoveredThroughFailureId: failure.failureId,
    });
    expect(await screen.findByText("s:1600")).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("recovers once when health recovery is published before the query error reaches React", async () => {
    let rejectInitial;
    const pendingInitial = new Promise((_resolve, reject) => {
      rejectInitial = reject;
    });
    const failure = registerConnectivityFailure();
    apiRequest
      .mockReturnValueOnce(pendingInitial)
      .mockResolvedValue({ poster_card_display_max_width: "1800" });
    const user = { id: 7, role: "standard_user" };
    const settingsKey = buildUserSettingsQueryKey({ userId: user.id, role: user.role });
    renderProbes(
      <>
        <SettingsProbe label="shell" user={user} />
        <SettingsProbe label="library" user={user} />
        <SettingsProbe label="detail" user={user} />
      </>,
    );
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(1));

    publishConnectivityRecovery({
      generation: 4,
      recoveredThroughFailureId: failure.failureId,
    });
    rejectInitial(new ApiNetworkError(undefined, failure));

    await waitFor(() => expect(queryClient.getQueryState(settingsKey)?.status).toBe("success"));
    expect(await screen.findByText("shell:1800")).toBeInTheDocument();
    expect(screen.getByText("library:1800")).toBeInTheDocument();
    expect(screen.getByText("detail:1800")).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("does not refetch a settings query failed by an HTTP business error on connectivity recovery", async () => {
    const httpError = new Error("Forbidden");
    httpError.status = 403;
    apiRequest.mockRejectedValue(httpError);
    const user = { id: 7, role: "standard_user" };
    renderProbes(<SettingsProbe label="s" user={user} />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(1));

    const failure = registerConnectivityFailure();
    publishConnectivityRecovery({
      generation: 9,
      recoveredThroughFailureId: failure.failureId,
    });
    await Promise.resolve();
    await Promise.resolve();

    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  test("protected cache clearing removes library and user settings data", () => {
    const user = { id: 7, role: "standard_user" };
    const settingsKey = buildUserSettingsQueryKey({ userId: user.id, role: user.role });
    queryClient.setQueryData(settingsKey, { poster_card_display_max_width: "800" });
    queryClient.setQueryData(["library", "v1", { userId: "7" }], { items: [{ id: 42 }] });
    queryClient.setQueryData(["library", "v2", { userId: "7" }], { items_by_id: { "42": { id: 42 } } });
    queryClient.setQueryData(["library", "shadow-v2", { userId: "7" }], { items_by_id: { "42": { id: 42 } } });

    clearProtectedQueryCache();

    expect(queryClient.getQueryData(settingsKey)).toBeUndefined();
    expect(queryClient.getQueryData(["library", "v1", { userId: "7" }])).toBeUndefined();
    expect(queryClient.getQueryData(["library", "v2", { userId: "7" }])).toBeUndefined();
    expect(queryClient.getQueryData(["library", "shadow-v2", { userId: "7" }])).toBeUndefined();
  });
});
