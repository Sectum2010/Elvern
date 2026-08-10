import { describe, expect, test } from "vitest";

import {
  ADMIN_LIVE_AUDIT_TICKER_LINE,
  desktopAdminResourcesForTab,
  easeMeridianEntryProgress,
  getDeterministicUserAvatarPalette,
  MERIDIAN_ENTRY_PROGRESS_INTERVAL_MS,
  MERIDIAN_ENTRY_PROGRESS_STEP,
  shouldPollDesktopPlaybackWorkers,
  shouldRefreshDesktopRealtimeResource,
} from "./adminControlCenter.js";

describe("desktop Admin Control Center resource contract", () => {
  test("loads only route-owned resources", () => {
    expect(desktopAdminResourcesForTab("overview")).toEqual([
      "system",
      "users",
      "sessions",
      "exposure",
    ]);
    expect(desktopAdminResourcesForTab("users-invites")).toEqual([
      "system",
      "users",
      "invites",
      "passwordHelp",
    ]);
    expect(desktopAdminResourcesForTab("security")).toEqual([
      "system",
      "users",
      "sessions",
      "invites",
      "urlPrefix",
      "ownTotp",
    ]);
    expect(desktopAdminResourcesForTab("logs")).toEqual(["system", "sessions", "audit"]);
    expect(desktopAdminResourcesForTab("recovery")).toEqual(["system", "audit", "backups"]);
  });

  test("workers have one visible Users & Invites polling owner", () => {
    expect(shouldPollDesktopPlaybackWorkers("users-invites", "visible")).toBe(true);
    expect(shouldPollDesktopPlaybackWorkers("users-invites", "hidden")).toBe(false);
    expect(shouldPollDesktopPlaybackWorkers("overview", "visible")).toBe(false);
  });

  test("SSE refreshes only resources used by the active route", () => {
    expect(shouldRefreshDesktopRealtimeResource("users-invites", "users")).toBe(true);
    expect(shouldRefreshDesktopRealtimeResource("users-invites", "sessions")).toBe(false);
    expect(shouldRefreshDesktopRealtimeResource("logs", "sessions")).toBe(true);
    expect(shouldRefreshDesktopRealtimeResource("logs", "users")).toBe(false);
    expect(shouldRefreshDesktopRealtimeResource("recovery", "users")).toBe(false);
  });

  test("keeps the approved design ticker as presentation-only copy", () => {
    expect(ADMIN_LIVE_AUDIT_TICKER_LINE).toContain("admin · auth.login");
    expect(ADMIN_LIVE_AUDIT_TICKER_LINE).toContain("admin.library.rescan");
  });

  test("matches the Meridian 800ms stepped entry motion", () => {
    expect(MERIDIAN_ENTRY_PROGRESS_INTERVAL_MS).toBe(40);
    expect(MERIDIAN_ENTRY_PROGRESS_STEP).toBe(0.05);
    expect((1 / MERIDIAN_ENTRY_PROGRESS_STEP) * MERIDIAN_ENTRY_PROGRESS_INTERVAL_MS).toBe(800);
    expect(easeMeridianEntryProgress(0)).toBe(0);
    expect(easeMeridianEntryProgress(0.5)).toBe(0.875);
    expect(easeMeridianEntryProgress(1)).toBe(1);
  });

  test("assigns stable non-semantic avatar palettes from internal identifiers", () => {
    expect(getDeterministicUserAvatarPalette(42))
      .toBe(getDeterministicUserAvatarPalette(42));
    expect(getDeterministicUserAvatarPalette("42"))
      .toBe(getDeterministicUserAvatarPalette(42));
    expect(getDeterministicUserAvatarPalette(42))
      .toMatch(/^meridian-user-avatar--palette-[0-7]$/);
    expect(new Set(Array.from({ length: 24 }, (_, index) => (
      getDeterministicUserAvatarPalette(index + 1)
    ))).size).toBeGreaterThan(4);
  });
});
