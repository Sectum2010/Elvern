import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { LegacyInstallRedirect } from "./LegacyInstallRedirect.jsx";
import { ProtectedRoute } from "./ProtectedRoute.jsx";

const mockAuthState = vi.hoisted(() => ({
  user: { id: 1, username: "admin", role: "admin" },
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: mockAuthState.user,
    loading: false,
  }),
}));
function LocationProbe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return (
    <p>
      {`${location.pathname}${location.search}${location.hash}|${navigationType}|${location.state?.marker || ""}`}
    </p>
  );
}


describe("LegacyInstallRedirect", () => {
  beforeEach(() => {
    mockAuthState.user = { id: 1, username: "admin", role: "admin" };
  });

  test.each(["/install", "/desktop"])(
    "replaces %s with the canonical Settings Install section",
    async (legacyPath) => {
      render(
        <MemoryRouter initialEntries={[{
          pathname: legacyPath,
          search: "?source=bookmark&section=old",
          hash: "#help",
          state: { marker: "preserved" },
        }]}>
          <Routes>
            <Route path="/install" element={<LegacyInstallRedirect />} />
            <Route path="/desktop" element={<LegacyInstallRedirect />} />
            <Route path="/settings/*" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>,
      );

      await waitFor(() => expect(screen.getByText(
        "/settings/playback-apps?source=bookmark#help|REPLACE|preserved",
      )).toBeInTheDocument());
    },
  );

  test.each(["/install", "/desktop"])("%s remains protected", async (legacyPath) => {
    mockAuthState.user = null;
    render(
      <MemoryRouter initialEntries={[legacyPath]}>
        <Routes>
          <Route
            path={legacyPath}
            element={(
              <ProtectedRoute>
                <LegacyInstallRedirect />
              </ProtectedRoute>
            )}
          />
          <Route path="/login" element={<p>Login page</p>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Login page")).toBeInTheDocument();
  });

  test("works beneath a dynamic basename without mounting a shell", async () => {
    mockAuthState.user = { id: 1, username: "admin", role: "admin" };
    render(
      <MemoryRouter basename="/abc23456" initialEntries={["/abc23456/install/?from=old#help"]}>
        <Routes>
          <Route
            path="/install/"
            element={(
              <ProtectedRoute>
                <LegacyInstallRedirect />
              </ProtectedRoute>
            )}
          />
          <Route path="/settings/*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText(
      "/settings/playback-apps?from=old#help|REPLACE|",
    )).toBeInTheDocument());
    expect(screen.queryByText("Shell marker")).not.toBeInTheDocument();
  });
});
