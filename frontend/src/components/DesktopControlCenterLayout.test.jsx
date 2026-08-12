import { fireEvent, render, screen, within } from "@testing-library/react";
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
    authState.user = { id: 1, username: "admin", role: "admin", assistant_beta_enabled: true };
    renderLayout();

    expect(screen.getByText("System status rail mounted")).toBeInTheDocument();
    expect(railState.renders).toBe(1);
    expect(screen.getByRole("button", { name: "Switch to Admin Panel" })).toBeInTheDocument();
  });

  test("shows the approved Recovery subtitle", () => {
    authState.user = { id: 1, username: "admin", role: "admin", assistant_beta_enabled: true };
    renderLayout("/admin/recovery");

    expect(screen.getByRole("heading", { name: "Recovery" })).toBeInTheDocument();
    expect(screen.getByText("Encrypted checkpoints, verification, and off-host protection.")).toBeInTheDocument();
  });

  test("account popover contains only the permitted actions and Escape is a no-op", () => {
    authState.user = { id: 1, username: "admin", role: "admin", assistant_beta_enabled: true };
    renderLayout();

    fireEvent.click(screen.getByRole("button", { name: /admin administrator/i }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Assistant",
      "Sign out",
    ]);
    expect(within(menu).queryByText("Settings")).not.toBeInTheDocument();
    expect(within(menu).queryByText("Admin Panel")).not.toBeInTheDocument();
    expect(within(menu).queryByText("Profile")).not.toBeInTheDocument();
    expect(within(menu).queryByText("Account")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  test("account popover keeps the approved Meridian SVG path contract", () => {
    authState.user = { id: 1, username: "admin", role: "admin", assistant_beta_enabled: true };
    renderLayout();
    fireEvent.click(screen.getByRole("button", { name: /admin administrator/i }));

    const items = within(screen.getByRole("menu")).getAllByRole("menuitem");
    expect(Array.from(items[0].querySelectorAll("path"), (path) => path.getAttribute("d"))).toEqual([
      "M12 4l1.8 5.2L19 11l-5.2 1.8L12 18l-1.8-5.2L5 11l5.2-1.8z",
    ]);
    expect(Array.from(items[1].querySelectorAll("path"), (path) => path.getAttribute("d"))).toEqual([
      "M9.5 4.5H6A1.5 1.5 0 004.5 6v12A1.5 1.5 0 006 19.5h3.5",
      "M15.5 8l4 4-4 4",
      "M19.5 12h-10",
    ]);
  });

  test("standard user without Assistant permission sees only Sign out", () => {
    renderLayout();
    fireEvent.click(screen.getByRole("button", { name: /display-user standard user/i }));

    expect(within(screen.getByRole("menu")).getAllByRole("menuitem")).toHaveLength(1);
    expect(within(screen.getByRole("menu")).getByText("Sign out")).toBeInTheDocument();
  });

  test("uses the Meridian page title and exact subtitle for the active route", () => {
    renderLayout("/settings/cloud-sharing");

    expect(screen.getByRole("heading", { name: "Cloud & Sharing" })).toBeInTheDocument();
    expect(screen.getByText("Google Drive libraries — yours and the ones shared with everyone."))
      .toBeInTheDocument();
  });
});
