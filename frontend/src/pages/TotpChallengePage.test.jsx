import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { TotpChallengePage } from "./TotpChallengePage.jsx";


const mockPlatformState = vi.hoisted(() => ({
  deviceClass: "desktop",
  platform: "linux",
}));


vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    refreshAuth: vi.fn(),
    user: null,
  }),
}));

vi.mock("../lib/authViewportNavigation.js", () => ({
  prepareAuthViewportExit: vi.fn(),
  useAuthViewportRedirectReady: () => false,
}));

vi.mock("../lib/platformDetection.js", () => ({
  detectClientDeviceClass: () => mockPlatformState.deviceClass,
  detectClientPlatform: () => mockPlatformState.platform,
  isDesktopClientPlatform: (platform) => ["windows", "mac", "linux"].includes(platform),
}));


function renderChallenge() {
  return render(
    <MemoryRouter>
      <TotpChallengePage />
    </MemoryRouter>,
  );
}


describe("TotpChallengePage desktop focus", () => {
  beforeEach(() => {
    window.sessionStorage.setItem("elvern_totp_challenge", "challenge-token");
    window.sessionStorage.setItem("elvern_totp_expires", String(Date.now() + 60_000));
    mockPlatformState.deviceClass = "desktop";
    mockPlatformState.platform = "linux";
  });

  afterEach(() => {
    cleanup();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  test.each(["linux", "mac", "windows"])(
    "focuses the authenticator code on %s desktop",
    async (platform) => {
      mockPlatformState.platform = platform;
      renderChallenge();

      await waitFor(() => expect(screen.getByLabelText("Authenticator code")).toHaveFocus());
    },
  );

  test.each([
    ["phone", "iphone"],
    ["tablet", "ipad"],
    ["phone", "android"],
    ["tablet", "android"],
  ])("does not auto-focus on %s %s", (deviceClass, platform) => {
    mockPlatformState.deviceClass = deviceClass;
    mockPlatformState.platform = platform;
    renderChallenge();

    expect(screen.getByLabelText("Authenticator code")).not.toHaveFocus();
  });
});
