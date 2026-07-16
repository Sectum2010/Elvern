import { useState } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AuthProvider, useAuth } from "./AuthContext";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { apiRequest, MAINTENANCE_MODE_MESSAGE } from "../lib/api";
import { buildLibraryQueryKey } from "../lib/libraryQueries";
import { queryClient } from "../lib/queryClient";
import { LoginPage } from "../pages/LoginPage";


const standardUser = {
  id: 2,
  username: "viewer",
  role: "user",
  assistant_beta_enabled: false,
};

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function ProtectedApiProbe({ protectedPath = "/api/protected" }) {
  const { user } = useAuth();
  const [error, setError] = useState("");

  async function handleProtectedRequest() {
    setError("");
    try {
      await apiRequest(protectedPath);
    } catch (requestError) {
      setError(requestError.message || "Request failed");
    }
  }

  return (
    <div>
      <p>Protected content</p>
      <p>Signed in as {user?.username || "unknown"}</p>
      <button onClick={handleProtectedRequest} type="button">Call protected API</button>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}

function renderAuthRoutes({ initialEntry = "/library", protectedPath = "/api/protected" } = {}) {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/library"
            element={(
              <ProtectedRoute>
                <ProtectedApiProbe protectedPath={protectedPath} />
              </ProtectedRoute>
            )}
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("AuthProvider maintenance mode handling", () => {
  beforeEach(() => {
    queryClient.clear();
    window.scrollTo = vi.fn();
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("redirects a logged-in standard user to login when a protected API call returns maintenance 503", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ user: standardUser });
      }
      if (requestPath === "/api/protected") {
        return jsonResponse({ detail: MAINTENANCE_MODE_MESSAGE }, 503);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();

    expect(await screen.findByText("Protected content")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Call protected API" }));

    expect(await screen.findByText(MAINTENANCE_MODE_MESSAGE)).toHaveClass("login-maintenance-notice");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  test("redirects to login with the exact notice when session refresh receives maintenance 503", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ detail: MAINTENANCE_MODE_MESSAGE }, 503);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();

    expect(await screen.findByText(MAINTENANCE_MODE_MESSAGE)).toHaveClass("login-maintenance-notice");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  test("heartbeat maintenance 503 ends the session and shows the login notice", async () => {
    let heartbeatCallback = null;
    vi.spyOn(window, "setInterval").mockImplementation((callback) => {
      heartbeatCallback = callback;
      return 42;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ user: standardUser });
      }
      if (requestPath === "/api/auth/heartbeat") {
        return jsonResponse({ detail: MAINTENANCE_MODE_MESSAGE }, 503);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();

    expect(await screen.findByText("Protected content")).toBeInTheDocument();
    expect(heartbeatCallback).toBeTypeOf("function");

    await heartbeatCallback();

    expect(await screen.findByText(MAINTENANCE_MODE_MESSAGE)).toHaveClass("login-maintenance-notice");
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  test("generic 503 responses do not clear the authenticated user", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ user: standardUser });
      }
      if (requestPath === "/api/protected") {
        return jsonResponse({ detail: "Service unavailable" }, 503);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();

    expect(await screen.findByText("Protected content")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Call protected API" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Service unavailable");
    expect(screen.getByText("Protected content")).toBeInTheDocument();
    expect(screen.queryByText(MAINTENANCE_MODE_MESSAGE)).not.toBeInTheDocument();
  });

  test("clears user A library data before applying user B identity", async () => {
    const secondUser = {
      ...standardUser,
      id: 3,
      username: "second-viewer",
    };
    let authMeCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        authMeCalls += 1;
        return jsonResponse({ user: authMeCalls === 1 ? standardUser : secondUser });
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();
    expect(await screen.findByText("Signed in as viewer")).toBeInTheDocument();

    const userALibraryKey = buildLibraryQueryKey({
      userId: standardUser.id,
      role: standardUser.role,
      category: "movies",
    });
    queryClient.setQueryData(userALibraryKey, { items: [{ id: 42 }] });
    expect(queryClient.getQueryData(userALibraryKey)).toBeDefined();

    fireEvent.focus(window);

    expect(await screen.findByText("Signed in as second-viewer")).toBeInTheDocument();
    expect(queryClient.getQueryData(userALibraryKey)).toBeUndefined();
  });
});
