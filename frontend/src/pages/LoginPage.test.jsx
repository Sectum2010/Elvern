import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import { MAINTENANCE_MODE_MESSAGE } from "../lib/api";
import { LoginPage } from "./LoginPage";


function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderLoginPage() {
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/library" element={<p>Library route</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function submitLogin() {
  fireEvent.change(await screen.findByLabelText("Username"), {
    target: { value: "viewer" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginPage maintenance notices", () => {
  beforeEach(() => {
    window.scrollTo = vi.fn();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });

  test("renders the maintenance mode message below the login card", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ detail: MAINTENANCE_MODE_MESSAGE }, 503);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderLoginPage();

    const notice = await screen.findByText(MAINTENANCE_MODE_MESSAGE);
    const card = document.querySelector(".login-card");

    expect(notice).toHaveClass("login-maintenance-notice");
    expect(card).not.toContainElement(notice);
    expect(within(card).queryByText(MAINTENANCE_MODE_MESSAGE)).not.toBeInTheDocument();
  });

  test("login blocked by Maintenance Mode shows the exact message below the card only", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ detail: "Not authenticated" }, 401);
      }
      if (requestPath === "/api/auth/login") {
        return jsonResponse({ detail: MAINTENANCE_MODE_MESSAGE }, 503);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderLoginPage();
    await submitLogin();

    const notice = await screen.findByText(MAINTENANCE_MODE_MESSAGE);
    const card = document.querySelector(".login-card");

    expect(notice).toHaveClass("login-maintenance-notice");
    expect(card).not.toContainElement(notice);
    expect(within(card).queryByText(MAINTENANCE_MODE_MESSAGE)).not.toBeInTheDocument();
    expect(card.querySelector(".form-error")).toBeNull();
  });

  test("ordinary invalid-password errors stay inside the login form", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ detail: "Not authenticated" }, 401);
      }
      if (requestPath === "/api/auth/login") {
        return jsonResponse({ detail: "Invalid username or password" }, 401);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderLoginPage();
    await submitLogin();

    const card = document.querySelector(".login-card");
    const error = await within(card).findByText("Invalid username or password");

    expect(error).toHaveClass("form-error");
    expect(screen.queryByText(MAINTENANCE_MODE_MESSAGE)).not.toBeInTheDocument();
  });

  test("disabled account errors stay inside the login form", async () => {
    vi.stubGlobal("fetch", vi.fn(async (requestPath) => {
      if (requestPath === "/api/auth/me") {
        return jsonResponse({ detail: "Not authenticated" }, 401);
      }
      if (requestPath === "/api/auth/login") {
        return jsonResponse({ detail: "This account has been disabled" }, 403);
      }
      throw new Error(`Unexpected request: ${requestPath}`);
    }));

    renderLoginPage();
    await submitLogin();

    const card = document.querySelector(".login-card");
    const error = await within(card).findByText("This account has been disabled");

    expect(error).toHaveClass("form-error");
    expect(screen.queryByText(MAINTENANCE_MODE_MESSAGE)).not.toBeInTheDocument();
  });
});
