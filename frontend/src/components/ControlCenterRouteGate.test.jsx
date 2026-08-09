import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { ControlCenterRouteGate } from "./ControlCenterRouteGate.jsx";

const authState = vi.hoisted(() => ({ role: "standard_user" }));
const layoutState = vi.hoisted(() => ({ renders: 0 }));

vi.mock("../auth/AuthContext.jsx", () => ({
  useAuth: () => ({ user: { id: 7, role: authState.role } }),
}));

vi.mock("../lib/platformDetection.js", () => ({
  detectClientDeviceClass: () => "desktop",
  detectClientPlatform: () => "linux",
}));

vi.mock("./ControlCenterSessionContext.jsx", () => ({
  ControlCenterSessionProvider: ({ children }) => children,
}));

vi.mock("./DesktopControlCenterLayout.jsx", () => ({
  DesktopControlCenterLayout: () => {
    layoutState.renders += 1;
    return <div>Desktop Control Center mounted</div>;
  },
}));

function LocationProbe() {
  const location = useLocation();
  return <div>{location.pathname}</div>;
}

function renderGate(initialEntry) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route element={<ControlCenterRouteGate />}>
          <Route path="/settings/*" element={<div>Settings child</div>} />
          <Route path="/admin/*" element={<div>Admin child</div>} />
        </Route>
        <Route path="/library" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ControlCenterRouteGate", () => {
  beforeEach(() => {
    authState.role = "standard_user";
    layoutState.renders = 0;
  });

  test("redirects a standard user before mounting the desktop Admin shell", async () => {
    renderGate("/admin/overview");

    expect(await screen.findByText("/library")).toBeInTheDocument();
    expect(screen.queryByText("Desktop Control Center mounted")).not.toBeInTheDocument();
    expect(screen.queryByText("Admin child")).not.toBeInTheDocument();
    expect(layoutState.renders).toBe(0);
  });

  test("still mounts the desktop shell for an authorized administrator", () => {
    authState.role = "admin";
    renderGate("/admin/overview");

    expect(screen.getByText("Desktop Control Center mounted")).toBeInTheDocument();
    expect(layoutState.renders).toBe(1);
  });
});
