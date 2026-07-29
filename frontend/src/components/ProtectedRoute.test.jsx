import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { ProtectedRoute } from "./ProtectedRoute.jsx";


const authState = vi.hoisted(() => ({
  loading: false,
  user: null,
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => authState,
}));


function renderAssistantRoute(user) {
  authState.user = user;
  return render(
    <MemoryRouter initialEntries={["/assistant"]}>
      <Routes>
        <Route path="/library" element={<p>Library fallback</p>} />
        <Route
          path="/assistant"
          element={(
            <ProtectedRoute requireAssistant>
              <p>Assistant content</p>
            </ProtectedRoute>
          )}
        />
      </Routes>
    </MemoryRouter>,
  );
}


describe("ProtectedRoute Assistant access", () => {
  beforeEach(() => {
    authState.loading = false;
    authState.user = null;
  });

  test.each([
    { role: "admin", assistant_beta_enabled: false },
    { role: "admin", assistant_beta_enabled: true },
    { role: "standard_user", assistant_beta_enabled: true },
  ])("allows $role with flag $assistant_beta_enabled", (user) => {
    renderAssistantRoute(user);
    expect(screen.getByText("Assistant content")).toBeInTheDocument();
  });

  test("redirects a standard user without access", () => {
    renderAssistantRoute({ role: "standard_user", assistant_beta_enabled: false });
    expect(screen.getByText("Library fallback")).toBeInTheDocument();
  });
});
