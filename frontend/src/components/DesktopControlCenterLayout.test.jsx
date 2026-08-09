import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { DesktopControlCenterLayout } from "./DesktopControlCenterLayout.jsx";

const authState = vi.hoisted(() => ({ user: { id: 7, username: "display-user", role: "standard_user" } }));
const railState = vi.hoisted(() => ({ renders: 0 }));

vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => authState,
}));

vi.mock("../lib/libraryNavigation.js", () => ({
  markLibraryReturnPending: vi.fn(),
  readLibraryReturnTarget: vi.fn(() => null),
}));

vi.mock("./ControlCenterSessionContext.jsx", () => ({
  useControlCenterSession: () => ({
    adminTab: "overview",
    settingsTab: "appearance",
    statusRailOpen: false,
    setStatusRailOpen: vi.fn(),
    theme: "light",
    setTheme: vi.fn(),
  }),
}));

vi.mock("./SystemStatusRail.jsx", () => ({
  SystemStatusRail: () => {
    railState.renders += 1;
    return <div>System status rail mounted</div>;
  },
}));

function renderLayout(pathname = "/settings/appearance") {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <DesktopControlCenterLayout />
    </MemoryRouter>,
  );
}

describe("DesktopControlCenterLayout privilege boundaries", () => {
  beforeEach(() => {
    authState.user = { id: 7, username: "display-user", role: "standard_user" };
    railState.renders = 0;
  });

  test("does not mount the admin System Status rail for a standard user", () => {
    renderLayout();

    expect(screen.queryByText("System status rail mounted")).not.toBeInTheDocument();
    expect(railState.renders).toBe(0);
    expect(screen.queryByRole("button", { name: "Switch to Admin Panel" })).not.toBeInTheDocument();
    expect(screen.getByText("display-user")).toBeInTheDocument();
    expect(screen.getByText("Standard user")).toBeInTheDocument();
    expect(screen.getByText(`Elvern · ${window.location.hostname}`)).toBeInTheDocument();
  });

  test("mounts the shared System Status rail for an administrator", () => {
    authState.user = { id: 1, username: "admin", role: "admin" };
    renderLayout();

    expect(screen.getByText("System status rail mounted")).toBeInTheDocument();
    expect(railState.renders).toBe(1);
    expect(screen.getByRole("button", { name: "Switch to Admin Panel" })).toBeInTheDocument();
  });

  test("uses the Meridian page title and exact subtitle for the active route", () => {
    renderLayout("/settings/cloud-sharing");

    expect(screen.getByRole("heading", { name: "Cloud & Sharing" })).toBeInTheDocument();
    expect(screen.getByText("Google Drive libraries — yours and the ones shared with everyone."))
      .toBeInTheDocument();
  });
});
