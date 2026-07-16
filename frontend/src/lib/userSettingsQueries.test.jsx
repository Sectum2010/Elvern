import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "./api";
import { clearProtectedQueryCache, queryClient } from "./queryClient";
import {
  buildUserSettingsQueryKey,
  resolveUserSettings,
  setUserSettingsQueryData,
  USER_SETTINGS_QUERY_GC_TIME_MS,
  USER_SETTINGS_QUERY_STALE_TIME_MS,
  useUserSettingsQuery,
} from "./userSettingsQueries";


vi.mock("./api", () => ({
  apiRequest: vi.fn(),
}));


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
      </>,
    );

    expect(await screen.findByText("shell:800")).toBeInTheDocument();
    expect(screen.getByText("library:800")).toBeInTheDocument();
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

  test("protected cache clearing removes library and user settings data", () => {
    const user = { id: 7, role: "standard_user" };
    const settingsKey = buildUserSettingsQueryKey({ userId: user.id, role: user.role });
    queryClient.setQueryData(settingsKey, { poster_card_display_max_width: "800" });
    queryClient.setQueryData(["library", "v1", { userId: "7" }], { items: [{ id: 42 }] });

    clearProtectedQueryCache();

    expect(queryClient.getQueryData(settingsKey)).toBeUndefined();
    expect(queryClient.getQueryData(["library", "v1", { userId: "7" }])).toBeUndefined();
  });
});
