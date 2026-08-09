import { describe, expect, test } from "vitest";

import {
  classifyControlCenterPath,
  desktopAdminTabToLegacySection,
  desktopSettingsTabToLegacySection,
  isDesktopControlCenterDevice,
  resolveControlCenterLocation,
} from "./controlCenterRoutes.js";

function storageWith(entries = {}) {
  return {
    getItem(key) {
      return entries[key] ?? null;
    },
  };
}

describe("Control Center routes", () => {
  test.each(["windows", "mac", "linux"])("enables the desktop shell on %s", (platform) => {
    expect(isDesktopControlCenterDevice("desktop", platform)).toBe(true);
  });

  test.each([
    ["phone", "mac"],
    ["tablet", "windows"],
    ["desktop", "unknown"],
    ["unknown", "linux"],
  ])("does not mount for %s/%s", (deviceClass, platform) => {
    expect(isDesktopControlCenterDevice(deviceClass, platform)).toBe(false);
  });

  test("classifies valid nested settings and admin routes", () => {
    expect(classifyControlCenterPath("/settings/cloud-sharing")).toEqual({
      area: "settings",
      tab: "cloud-sharing",
      valid: true,
    });
    expect(classifyControlCenterPath("/admin/users-invites")).toEqual({
      area: "admin",
      tab: "users-invites",
      valid: true,
    });
    expect(classifyControlCenterPath("/admin/assistant").area).toBe("");
    expect(classifyControlCenterPath("/admin/assistant/42").area).toBe("");
  });

  test("classifies multi-segment Control Center paths as invalid within their protected area", () => {
    expect(classifyControlCenterPath("/settings/unknown/nested")).toEqual({
      area: "settings",
      tab: "",
      valid: false,
    });
    expect(classifyControlCenterPath("/admin/unknown/nested")).toEqual({
      area: "admin",
      tab: "",
      valid: false,
    });
    expect(resolveControlCenterLocation({ pathname: "/settings/unknown/nested" }))
      .toBe("/settings/appearance");
    expect(resolveControlCenterLocation({ pathname: "/admin/unknown/nested", role: "admin" }))
      .toBe("/admin/overview");
  });

  test.each([
    ["preferences", "/settings/appearance"],
    ["display", "/settings/appearance"],
    ["libraries", "/settings/cloud-sharing"],
    ["hidden", "/settings/hidden-titles"],
    ["install", "/settings/playback-apps"],
  ])("maps legacy Settings section %s", (section, expected) => {
    expect(resolveControlCenterLocation({
      pathname: "/settings",
      search: `?section=${section}&keep=1`,
      hash: "#anchor",
      role: "admin",
      storage: storageWith(),
    })).toBe(`${expected}?keep=1#anchor`);
  });

  test("maps hidden-list and OAuth callback destinations exactly", () => {
    expect(resolveControlCenterLocation({
      pathname: "/settings",
      search: "?section=libraries",
      hash: "#hidden-list",
      storage: storageWith(),
    })).toBe("/settings/hidden-titles#hidden-list");
    expect(resolveControlCenterLocation({
      pathname: "/settings",
      search: "?googleDriveStatus=connected",
      storage: storageWith(),
    })).toBe("/settings/cloud-sharing?googleDriveStatus=connected");
  });

  test("advanced is admin-only and roots use session-scoped memories", () => {
    expect(resolveControlCenterLocation({
      pathname: "/settings",
      search: "?section=advanced",
      role: "standard_user",
      storage: storageWith(),
    })).toBe("/settings/appearance");
    expect(resolveControlCenterLocation({
      pathname: "/settings",
      role: "admin",
      storage: storageWith({ "elvern:control-center:settings-tab": "hidden-titles" }),
    })).toBe("/settings/hidden-titles");
    expect(resolveControlCenterLocation({
      pathname: "/admin",
      role: "admin",
      storage: storageWith({ "elvern:control-center:admin-tab": "logs" }),
    })).toBe("/admin/logs");
  });

  test.each([
    ["panel", "/admin/users-invites"],
    ["security", "/admin/security"],
    ["logs", "/admin/logs"],
    ["recovery", "/admin/recovery"],
  ])("maps legacy Admin section %s", (section, expected) => {
    expect(resolveControlCenterLocation({
      pathname: "/admin",
      search: `?section=${section}`,
      role: "admin",
      storage: storageWith(),
    })).toBe(expected);
  });

  test("nested routes map to the existing mobile contracts", () => {
    expect(desktopSettingsTabToLegacySection("cloud-sharing", "admin")).toBe("libraries");
    expect(desktopSettingsTabToLegacySection("server-storage", "standard_user")).toBe("preferences");
    expect(desktopAdminTabToLegacySection("overview")).toBe("security");
    expect(desktopAdminTabToLegacySection("users-invites")).toBe("panel");
  });
});
