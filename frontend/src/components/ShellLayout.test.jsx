import { readFileSync } from "node:fs";

import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { ShellLayout } from "./ShellLayout";


const mockPlatformState = vi.hoisted(() => ({
  deviceClass: "desktop",
  platform: "linux",
}));


vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 2, username: "viewer", role: "standard_user" },
    logout: vi.fn(),
  }),
}));

vi.mock("../lib/api", () => ({
  apiRequest: vi.fn(),
}));

vi.mock("../lib/platformDetection", () => ({
  detectClientDeviceClass: () => mockPlatformState.deviceClass,
  detectClientPlatform: () => mockPlatformState.platform,
  isDesktopClientPlatform: (platform) => ["windows", "mac", "linux"].includes(platform),
}));

vi.mock("../features/playback/usePlaybackReadyNotice", () => ({
  usePlaybackReadyNotice: () => ({
    playbackReadyNotice: null,
    dismissPlaybackReadyNotice: vi.fn(),
    openPlaybackReadyNotice: vi.fn(),
  }),
}));


function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/library"]}>
      <ShellLayout>
        <label>
          Editable value
          <input aria-label="Editable value" />
        </label>
        <p data-allow-text-selection="true">Copy this text</p>
      </ShellLayout>
    </MemoryRouter>,
  );
}


describe("ShellLayout fixed island and mobile selection guard", () => {
  beforeEach(() => {
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "linux";
    apiRequest.mockReset();
    apiRequest.mockResolvedValue({
      floating_controls_position: "top",
      poster_card_appearance: "classic",
      background_mode: "preset",
      background_preset: "neon",
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  test("keeps the floating island at the bottom even for a legacy top setting", async () => {
    renderShell();

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/user-settings"));
    expect(document.querySelector(".app-shell")).toHaveClass("app-shell--floating-island-bottom");
    expect(document.querySelector(".floating-island")).toHaveClass("floating-island--bottom");
    expect(document.querySelector(".floating-island")).not.toHaveClass("floating-island--top");
  });

  test.each([
    ["phone", "iphone"],
    ["tablet", "ipad"],
    ["phone", "android"],
    ["tablet", "android"],
  ])("adds selection guard for %s %s", (deviceClass, platform) => {
    mockPlatformState.deviceClass = deviceClass;
    mockPlatformState.platform = platform;
    renderShell();

    expect(document.querySelector(".app-shell")).toHaveClass("app-shell--selection-guard");
  });

  test("does not add selection guard on desktop", () => {
    renderShell();

    expect(document.querySelector(".app-shell")).not.toHaveClass("app-shell--selection-guard");
  });

  test("selection guard CSS restores selection for editable and explicit copy regions", () => {
    const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");

    expect(styles).toMatch(/\.app-shell--selection-guard\s*\{[^}]*-webkit-user-select:\s*none;[^}]*user-select:\s*none;/s);
    expect(styles).toMatch(/\.app-shell--selection-guard\s+:is\([^)]*input[^)]*textarea[^)]*select[^)]*contenteditable[^)]*data-allow-text-selection[^)]*\)\s*\{[^}]*-webkit-user-select:\s*text;[^}]*user-select:\s*text;/s);
    expect(styles).not.toMatch(/\.app-shell--selection-guard[^}]*touch-action:\s*none/s);
  });
});
