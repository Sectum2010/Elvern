import { readFileSync } from "node:fs";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { ShellLayout } from "./ShellLayout";
import { queryClient } from "../lib/queryClient";


const mockPlatformState = vi.hoisted(() => ({
  deviceClass: "desktop",
  platform: "linux",
}));
const mockAuthState = vi.hoisted(() => ({
  role: "standard_user",
  assistantEnabled: false,
}));


vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: 2,
      username: "viewer",
      role: mockAuthState.role,
      assistant_beta_enabled: mockAuthState.assistantEnabled,
    },
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


function renderShell({ initialEntry = "/library", children = null } = {}) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <ShellLayout>
          {children || (
            <>
              <label>
                Editable value
                <input aria-label="Editable value" />
              </label>
              <p data-allow-text-selection="true">Copy this text</p>
            </>
          )}
        </ShellLayout>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}


function LocationProbe() {
  const location = useLocation();
  return <p data-testid="shell-location">{`${location.pathname}${location.search}${location.hash}`}</p>;
}


describe("ShellLayout fixed island and mobile selection guard", () => {
  beforeEach(() => {
    queryClient.clear();
    window.sessionStorage.clear();
    mockPlatformState.deviceClass = "tablet";
    mockPlatformState.platform = "ipad";
    mockAuthState.role = "standard_user";
    mockAuthState.assistantEnabled = false;
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
    queryClient.clear();
    vi.restoreAllMocks();
  });

  test("keeps the floating island at the bottom even for a legacy top setting", async () => {
    renderShell();

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/user-settings",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
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
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "linux";
    renderShell();

    expect(document.querySelector(".app-shell")).not.toHaveClass("app-shell--selection-guard");
  });

  test.each([
    ["/library", true],
    ["/library/42", true],
    ["/settings", false],
    ["/admin", false],
    ["/admin/assistant", false],
    ["/assistant", false],
    ["/attachments/42/view", false],
  ])("shows the complete desktop Library Island only on approved route %s", (route, expected) => {
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "linux";
    renderShell({ initialEntry: route });

    expect(Boolean(screen.queryByTestId("desktop-library-island"))).toBe(expected);
    expect(document.querySelector(".floating-island")).toBeNull();
  });

  test("uses the per-user top or bottom desktop Island setting without changing tablet placement", async () => {
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "linux";
    apiRequest.mockResolvedValue({
      desktop_floating_island_position: "bottom",
      poster_card_appearance: "classic",
      background_mode: "preset",
      background_preset: "neon",
    });
    renderShell();

    await waitFor(() => expect(screen.getByTestId("desktop-library-island")).toHaveClass(
      "desktop-library-island-wrap--bottom",
    ));
    expect(document.querySelector(".app-shell")).toHaveClass(
      "app-shell--desktop-library-island-bottom",
    );
  });

  test.each([
    ["standard", "standard_user", false, ["Library", "Settings"]],
    ["assistant", "standard_user", true, ["Library", "Settings", "Assistant"]],
    ["admin", "admin", false, ["Library", "Settings", "Assistant", "Admin"]],
    ["assistant admin", "admin", true, ["Library", "Settings", "Assistant", "Admin"]],
  ])("uses the approved %s navigation without Install", (_name, role, assistantEnabled, expected) => {
    mockAuthState.role = role;
    mockAuthState.assistantEnabled = assistantEnabled;
    renderShell({ initialEntry: "/settings" });

    const navigation = screen.getByRole("navigation", { name: "Primary" });
    expect(within(navigation).getAllByRole("link").map((link) => link.textContent)).toEqual(expected);
    expect(within(navigation).queryByRole("link", { name: "Install" })).not.toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "Settings" })).toHaveClass(
      "floating-island__link--active",
    );
  });

  test.each([
    ["/admin/assistant", "Assistant"],
    ["/admin/assistant/42", "Assistant"],
    ["/admin/security", "Admin"],
  ])("uses explicit active matching for %s", (initialEntry, activeLabel) => {
    mockAuthState.role = "admin";
    mockAuthState.assistantEnabled = false;
    renderShell({ initialEntry });

    const navigation = screen.getByRole("navigation", { name: "Primary" });
    expect(within(navigation).getByRole("link", { name: activeLabel })).toHaveClass(
      "floating-island__link--active",
    );
    const inactiveLabel = activeLabel === "Assistant" ? "Admin" : "Assistant";
    expect(within(navigation).getByRole("link", { name: inactiveLabel })).not.toHaveClass(
      "floating-island__link--active",
    );
  });

  test.each([
    ["standard_user", true],
    ["admin", false],
  ])("marks attachment viewing as Assistant for an authorized %s", (role, assistantEnabled) => {
    mockAuthState.role = role;
    mockAuthState.assistantEnabled = assistantEnabled;
    renderShell({ initialEntry: "/attachments/42/view?name=report.txt" });

    const navigation = screen.getByRole("navigation", { name: "Primary" });
    expect(within(navigation).getByRole("link", { name: "Assistant" })).toHaveClass(
      "floating-island__link--active",
    );
    expect(within(navigation).getByRole("link", { name: "Library" })).not.toHaveClass(
      "floating-island__link--active",
    );
  });

  test.each([
    ["/attachments/42/view", "standard_user", false],
    ["/utility/unknown", "standard_user", true],
  ])("leaves %s without a false active item", (initialEntry, role, assistantEnabled) => {
    mockAuthState.role = role;
    mockAuthState.assistantEnabled = assistantEnabled;
    renderShell({ initialEntry });

    const navigation = screen.getByRole("navigation", { name: "Primary" });
    expect(
      within(navigation).getAllByRole("link").every(
        (link) => !link.classList.contains("floating-island__link--active"),
      ),
    ).toBe(true);
    expect(document.querySelector(".floating-island__nav-indicator")).toBeNull();
    expect(document.querySelector(".floating-island__link--current")).toBeNull();
  });

  test("Floating Library uses the remembered source/search return target from Detail", async () => {
    window.sessionStorage.setItem("elvern:library-return-target", JSON.stringify({
      listPath: "/library?category=movies&q=phase&source=cloud",
      anchorItemId: 42,
      anchorInstanceKey: "other-movies:42",
      pendingRestore: false,
      userId: "2",
      role: "standard_user",
    }));
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "linux";
    renderShell({
      initialEntry: "/library/42",
      children: <LocationProbe />,
    });

    screen.getByRole("tab", { name: "Movies" }).click();

    await waitFor(() => expect(screen.getByTestId("shell-location")).toHaveTextContent(
      "/library?category=movies&q=phase&source=cloud",
    ));
    expect(JSON.parse(
      window.sessionStorage.getItem("elvern:library-return-target"),
    ).pendingRestore).toBe(true);
  });

  test("a trailing-slash Library root does not render a duplicate Elvern header", () => {
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "linux";
    renderShell({
      initialEntry: "/library/",
      children: <div className="topbar library-desktop-hero">Elvern</div>,
    });

    expect(document.querySelectorAll(".topbar")).toHaveLength(1);
    expect(document.querySelector(".app-shell")).toHaveClass("app-shell--library-root");
  });

  test("selection guard CSS restores selection for editable and explicit copy regions", () => {
    const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");

    expect(styles).toMatch(/\.app-shell--selection-guard\s*\{[^}]*-webkit-user-select:\s*none;[^}]*user-select:\s*none;/s);
    expect(styles).toMatch(/\.app-shell--selection-guard\s+:is\([^)]*input[^)]*textarea[^)]*select[^)]*contenteditable[^)]*data-allow-text-selection[^)]*\)\s*\{[^}]*-webkit-user-select:\s*text;[^}]*user-select:\s*text;/s);
    expect(styles).not.toMatch(/\.app-shell--selection-guard[^}]*touch-action:\s*none/s);
  });
});
