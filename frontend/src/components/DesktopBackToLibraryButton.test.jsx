import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  MemoryRouter,
  useLocation,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { rememberLibraryReturnTarget, readLibraryReturnTarget } from "../lib/libraryNavigation.js";
import { DesktopBackToLibraryButton } from "./DesktopBackToLibraryButton.jsx";


const mockPlatform = vi.hoisted(() => ({
  deviceClass: "desktop",
  platform: "linux",
}));


vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => ({
    user: { id: 2, role: "standard_user" },
  }),
}));


vi.mock("../lib/platformDetection.js", () => ({
  detectClientDeviceClass: () => mockPlatform.deviceClass,
  detectClientPlatform: () => mockPlatform.platform,
  isDesktopClientPlatform: (platform) => ["windows", "mac", "linux"].includes(platform),
}));


function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {`${location.pathname}${location.search}|${Boolean(location.state?.restoreLibraryReturn)}`}
    </output>
  );
}


function renderButton(initialEntry = "/settings") {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <DesktopBackToLibraryButton />
      <LocationProbe />
    </MemoryRouter>,
  );
}


describe("DesktopBackToLibraryButton", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    mockPlatform.deviceClass = "desktop";
    mockPlatform.platform = "linux";
  });

  afterEach(() => {
    cleanup();
  });

  test("returns to the protected exact Library view and marks its anchor pending", () => {
    rememberLibraryReturnTarget({
      listPath: "/library?category=anime&source=cloud&genre=Action&q=akira",
      anchorItemId: 42,
      anchorInstanceKey: "other-movies:42",
      userId: 2,
      role: "standard_user",
    });
    renderButton();

    fireEvent.click(screen.getByRole("link", { name: "Back to Library" }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=anime&source=cloud&genre=Action&q=akira|true",
    );
    expect(readLibraryReturnTarget({
      userId: 2,
      role: "standard_user",
    })?.pendingRestore).toBe(true);
  });

  test("uses the safe Movies fallback when no protected return target exists", () => {
    renderButton("/admin");

    fireEvent.click(screen.getByRole("link", { name: "Back to Library" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies|false",
    );
  });

  test.each([
    ["phone", "iphone"],
    ["tablet", "ipad"],
    ["tablet", "android"],
  ])("does not change the existing %s %s surface", (deviceClass, platform) => {
    mockPlatform.deviceClass = deviceClass;
    mockPlatform.platform = platform;
    renderButton();

    expect(screen.queryByRole("link", { name: "Back to Library" })).not.toBeInTheDocument();
  });
});
