import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { DesktopControlCenterLayout } from "./DesktopControlCenterLayout.jsx";

const authState = vi.hoisted(() => ({ user: { id: 7, role: "standard_user" } }));
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
    authState.user = { id: 7, role: "standard_user" };
    railState.renders = 0;
  });

  test("does not mount the admin System Status rail for a standard user", () => {
    renderLayout();

    expect(screen.queryByText("System status rail mounted")).not.toBeInTheDocument();
    expect(railState.renders).toBe(0);
  });

  test("mounts the shared System Status rail for an administrator", () => {
    authState.user = { id: 1, role: "admin" };
    renderLayout();

    expect(screen.getByText("System status rail mounted")).toBeInTheDocument();
    expect(railState.renders).toBe(1);
  });
});
