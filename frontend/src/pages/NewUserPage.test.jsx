import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { NewUserPage } from "./NewUserPage";


const mockAuthState = vi.hoisted(() => ({
  refreshAuth: vi.fn(),
  user: null,
  loading: false,
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => mockAuthState,
}));

vi.mock("../lib/api", () => ({
  apiRequest: vi.fn(),
}));


function renderNewUserPage(initialEntry = "/new-user") {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/new-user" element={<NewUserPage />} />
        <Route path="/library" element={<p>Library route</p>} />
        <Route path="/login" element={<p>Login route</p>} />
      </Routes>
    </MemoryRouter>,
  );
}


describe("NewUserPage invite code privacy", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    mockAuthState.refreshAuth = vi.fn();
    mockAuthState.user = null;
    mockAuthState.loading = false;
  });

  test("does not prefill invite code from URL query parameters", () => {
    renderNewUserPage("/new-user?invite_code=SECRET-CODE&invite=OTHER-CODE");

    expect(screen.getByLabelText("One-time invite code")).toHaveValue("");
  });

  test("failed signup clears passwords but keeps the invite code in component state", async () => {
    apiRequest.mockRejectedValueOnce(new Error("Invalid invite code"));

    renderNewUserPage();

    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "viewer" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "primary-secret" },
    });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "primary-secret" },
    });
    fireEvent.change(screen.getByLabelText("One-time invite code"), {
      target: { value: "INVITE-KEEP" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create account / sign in" }));

    await waitFor(() => expect(screen.getByText("Invalid invite code")).toBeInTheDocument());
    expect(apiRequest).toHaveBeenCalledWith("/api/auth/signup", {
      method: "POST",
      data: {
        username: "viewer",
        password: "primary-secret",
        confirm_password: "primary-secret",
        invite_code: "INVITE-KEEP",
      },
    });
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Confirm password")).toHaveValue("");
    expect(screen.getByLabelText("One-time invite code")).toHaveValue("INVITE-KEEP");
  });
});
