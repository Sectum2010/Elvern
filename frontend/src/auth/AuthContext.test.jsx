import { useState } from "react";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AuthProvider, useAuth } from "./AuthContext";
import { ProtectedRoute } from "../components/ProtectedRoute";
import { apiRequest, MAINTENANCE_MODE_MESSAGE } from "../lib/api";
import { buildLibraryQueryKey } from "../lib/libraryQueries";
import { queryClient } from "../lib/queryClient";
import { PAGE_RESUME_EVENT } from "../lib/pageResume";
import { buildUserSettingsQueryKey } from "../lib/userSettingsQueries";
import {
  readControlCenterTab,
  readControlCenterTheme,
  writeControlCenterTab,
  writeControlCenterTheme,
} from "../lib/controlCenterSession";
import { LoginPage } from "../pages/LoginPage";
import {
  PENDING_LOGOUT_STORAGE_KEY,
  writePendingLogoutMarker,
} from "../lib/pendingLogout.js";


const standardUser = {
  id: 2,
  username: "viewer",
  role: "standard_user",
  assistant_beta_enabled: false,
  age_credential: 18,
  session_id: 22,
};

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function ProtectedApiProbe({ protectedPath = "/api/protected" }) {
  const { logout, user } = useAuth();
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
      <button onClick={logout} type="button">Log out</button>
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
    window.sessionStorage.clear();
    window.localStorage.clear();
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
    const userASettingsKey = buildUserSettingsQueryKey({
      userId: standardUser.id,
      role: standardUser.role,
    });
    queryClient.setQueryData(userASettingsKey, { poster_card_display_max_width: "800" });
    expect(queryClient.getQueryData(userALibraryKey)).toBeDefined();
    writeControlCenterTheme("dark");
    writeControlCenterTab("settings", "cloud-sharing");
    writeControlCenterTab("admin", "logs");

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));

    expect(await screen.findByText("Signed in as second-viewer")).toBeInTheDocument();
    expect(queryClient.getQueryData(userALibraryKey)).toBeUndefined();
    expect(queryClient.getQueryData(userASettingsKey)).toBeUndefined();
    expect(readControlCenterTheme()).toBe("light");
    expect(readControlCenterTab("settings")).toBe("appearance");
    expect(readControlCenterTab("admin")).toBe("overview");
  });

  test("logout clears Control Center session UI state before returning to login", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ user: standardUser });
      }
      if (requestPath === "/api/auth/logout") {
        return jsonResponse({ ok: true });
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();
    expect(await screen.findByText("Signed in as viewer")).toBeInTheDocument();
    writeControlCenterTheme("mixed");
    writeControlCenterTab("settings", "library");
    writeControlCenterTab("admin", "security");

    fireEvent.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(readControlCenterTheme()).toBe("light");
    expect(readControlCenterTab("settings")).toBe("appearance");
    expect(readControlCenterTab("admin")).toBe("overview");
  });

  test("finishes a persisted logout before auth me can restore protected content", async () => {
    writePendingLogoutMarker({
      version: 1,
      userId: String(standardUser.id),
      sessionId: String(standardUser.session_id),
      createdAt: new Date().toISOString(),
    });
    const fetchMock = vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/logout") {
        return jsonResponse({ detail: "Session already ended" }, 401);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderAuthRoutes();

    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/logout",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/auth/me")).toBe(false);
    expect(window.localStorage.getItem(PENDING_LOGOUT_STORAGE_KEY)).toBeNull();
  });

  test("failed server logout keeps protected content obscured and exposes Retry", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ user: standardUser });
      }
      if (requestPath === "/api/auth/logout") {
        return jsonResponse({ detail: "Server error" }, 500);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();
    expect(await screen.findByText("Protected content")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(PENDING_LOGOUT_STORAGE_KEY)).not.toBeNull();
  });

  test("a business 403 revalidates the same identity without clearing library cache", async () => {
    let authMeCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        authMeCalls += 1;
        return jsonResponse({ user: standardUser });
      }
      if (requestPath === "/api/protected") {
        return jsonResponse({ detail: "This action is not allowed" }, 403);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();
    expect(await screen.findByText("Signed in as viewer")).toBeInTheDocument();
    const libraryKey = buildLibraryQueryKey({
      userId: standardUser.id,
      role: standardUser.role,
      category: "movies",
    });
    queryClient.setQueryData(libraryKey, { items: [{ id: 42 }] });
    const settingsKey = buildUserSettingsQueryKey({
      userId: standardUser.id,
      role: standardUser.role,
    });
    queryClient.setQueryData(settingsKey, { poster_card_display_max_width: "800" });

    fireEvent.click(screen.getByRole("button", { name: "Call protected API" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This action is not allowed");
    await waitFor(() => expect(authMeCalls).toBe(2));
    expect(screen.getByText("Signed in as viewer")).toBeInTheDocument();
    expect(queryClient.getQueryData(libraryKey)).toEqual({ items: [{ id: 42 }] });
    expect(queryClient.getQueryData(settingsKey)).toEqual({ poster_card_display_max_width: "800" });
  });

  test("PWA-style visibility and focus revalidation keeps cache for the same identity", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ user: { ...standardUser } });
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();
    expect(await screen.findByText("Signed in as viewer")).toBeInTheDocument();
    const libraryKey = buildLibraryQueryKey({
      userId: standardUser.id,
      role: standardUser.role,
      category: "movies",
    });
    queryClient.setQueryData(libraryKey, { items: [{ id: 42 }] });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(queryClient.getQueryData(libraryKey)).toEqual({ items: [{ id: 42 }] });
  });

  test("clears cache when the same user receives a different permission role", async () => {
    let authMeCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        authMeCalls += 1;
        return jsonResponse({
          user: authMeCalls === 1
            ? standardUser
            : { ...standardUser, role: "admin" },
        });
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();
    expect(await screen.findByText("Signed in as viewer")).toBeInTheDocument();
    const libraryKey = buildLibraryQueryKey({
      userId: standardUser.id,
      role: standardUser.role,
      category: "movies",
    });
    queryClient.setQueryData(libraryKey, { items: [{ id: 42 }] });

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));

    await waitFor(() => expect(authMeCalls).toBe(2));
    expect(queryClient.getQueryData(libraryKey)).toBeUndefined();
  });

  test("clears cache when the same user's age permission changes", async () => {
    let authMeCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        authMeCalls += 1;
        return jsonResponse({
          user: authMeCalls === 1
            ? standardUser
            : { ...standardUser, age_credential: 13 },
        });
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderAuthRoutes();
    expect(await screen.findByText("Signed in as viewer")).toBeInTheDocument();
    const libraryKey = buildLibraryQueryKey({
      userId: standardUser.id,
      role: standardUser.role,
      category: "movies",
    });
    queryClient.setQueryData(libraryKey, { items: [{ id: 42 }] });

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));

    await waitFor(() => expect(authMeCalls).toBe(2));
    expect(queryClient.getQueryData(libraryKey)).toBeUndefined();
  });
});
