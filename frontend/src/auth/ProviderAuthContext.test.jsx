import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api.js";
import {
  clearProviderAuthIntent,
  readProviderAuthIntent,
  saveProviderAuthIntent,
  startGoogleDriveReconnect,
} from "../lib/providerAuth.js";
import { PAGE_RESUME_EVENT } from "../lib/pageResume.js";
import { ProviderAuthProvider, useProviderAuth } from "./ProviderAuthContext.jsx";

const authState = vi.hoisted(() => ({ loading: false, user: { id: 7, role: "admin" } }));

vi.mock("./AuthContext.jsx", () => ({ useAuth: () => authState }));
vi.mock("../lib/api.js", async (importOriginal) => ({
  ...(await importOriginal()),
  apiRequest: vi.fn(),
}));
vi.mock("../lib/providerAuth.js", async (importOriginal) => ({
  ...(await importOriginal()),
  startGoogleDriveReconnect: vi.fn(),
}));

function Probe() {
  const controller = useProviderAuth();
  return (
    <div>
      <output data-testid="transaction-state">{controller.providerAuthTransaction.state}</output>
      <output data-testid="transaction-message">{controller.providerAuthTransaction.message}</output>
      <button onClick={() => controller.startProviderReconnect({ allowWithoutRequirement: true })} type="button">Reconnect now</button>
    </div>
  );
}

function renderProvider() {
  return render(<ProviderAuthProvider><Probe /></ProviderAuthProvider>);
}

function saveIntent() {
  saveProviderAuthIntent({
    provider: "google_drive",
    operationId: "operation-provider-auth-test",
    identity: "7:admin",
    returnPath: "/settings/cloud-sharing",
    state: "navigating_external",
  });
}

beforeEach(() => {
  authState.loading = false;
  authState.user = { id: 7, role: "admin" };
  window.history.replaceState({}, "", "/settings/cloud-sharing");
  clearProviderAuthIntent();
  apiRequest.mockImplementation((path) => {
    if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
    if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "pending" });
    if (path === "/api/cloud-libraries/google/operation/cancel") return Promise.resolve({ status: "cancelled" });
    throw new Error(`Unexpected request: ${path}`);
  });
  startGoogleDriveReconnect.mockResolvedValue({ authorization_url: "https://accounts.example.test/oauth" });
});

afterEach(() => {
  clearProviderAuthIntent();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("ProviderAuthProvider transaction owner", () => {
  test("deduplicates rapid reconnect clicks and stores the fixed return operation", async () => {
    renderProvider();
    const button = screen.getByRole("button", { name: "Reconnect now" });

    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => expect(startGoogleDriveReconnect).toHaveBeenCalledTimes(1));
    expect(startGoogleDriveReconnect).toHaveBeenCalledWith(expect.objectContaining({
      returnPath: "/settings/cloud-sharing",
    }));
    expect(readProviderAuthIntent({ identity: "7:admin" })).toEqual(expect.objectContaining({
      returnPath: "/settings/cloud-sharing",
    }));
  });

  test("reconciles a successful callback and removes callback state", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "connected" });
      throw new Error(`Unexpected request: ${path}`);
    });
    window.history.replaceState({}, "", "/settings/cloud-sharing?googleDriveStatus=connected");
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("connected");
    expect(window.location.search).toBe("");
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
  });

  test("turns callback cancellation into a terminal non-pending state", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "cancelled" });
      throw new Error(`Unexpected request: ${path}`);
    });
    window.history.replaceState({}, "", "/settings/cloud-sharing?googleDriveStatus=cancelled");
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("cancelled_or_incomplete");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent("Reconnect was not completed.");
  });

  test("uses one status check and one cancellation when browser Back leaves the operation pending", async () => {
    saveIntent();
    renderProvider();
    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("cancelled_or_incomplete");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent("Reconnect was not completed.");
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/status",
    )).toHaveLength(1);
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/cancel",
    )).toHaveLength(1);
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
  });

  test("preserves a reload intent until the initial auth request resolves", async () => {
    authState.loading = true;
    authState.user = null;
    saveIntent();
    const view = renderProvider();

    expect(readProviderAuthIntent({ identity: "7:admin" })).not.toBeNull();
    expect(apiRequest).not.toHaveBeenCalled();

    authState.loading = false;
    authState.user = { id: 7, role: "admin" };
    view.rerender(<ProviderAuthProvider><Probe /></ProviderAuthProvider>);

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("cancelled_or_incomplete");
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
  });

  test("coalesces BFCache, page-resume, and visibility signals into one reconciliation", async () => {
    renderProvider();
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/cloud-libraries/google/provider-auth-status",
      expect.any(Object),
    ));
    apiRequest.mockClear();
    saveIntent();

    fireEvent(window, new Event("pageshow"));
    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));
    fireEvent(document, new Event("visibilitychange"));

    await waitFor(() => expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/status",
    )).toHaveLength(1));
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/cancel",
    )).toHaveLength(1);
  });

  test("loads the bound candidate only when the operation reports an account mismatch", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") {
        return Promise.resolve({ status: "account_mismatch", candidate_available: true });
      }
      if (path === "/api/cloud-libraries/google/account-candidate/status") {
        return Promise.resolve({
          status: "account_mismatch",
          current_account_label: "Current account",
          candidate_account_label: "Candidate account",
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("account_mismatch");
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/account-candidate/status",
    )).toHaveLength(1);
    expect(readProviderAuthIntent({ identity: "7:admin" })).not.toBeNull();
  });

  test("surfaces an operation error without cloud-library inference", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") {
        return Promise.resolve({ status: "error", message: "Google Drive reconnect failed." });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("error");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent("Google Drive reconnect failed.");
    expect(apiRequest.mock.calls.some(([path]) => path === "/api/cloud-libraries")).toBe(false);
  });

  test("clears the session operation when the authenticated identity disappears", async () => {
    saveIntent();
    const rendered = renderProvider();
    authState.user = null;
    rendered.rerender(<ProviderAuthProvider><Probe /></ProviderAuthProvider>);

    await waitFor(() => expect(readProviderAuthIntent()).toBeNull());
    expect(screen.getByTestId("transaction-state")).toHaveTextContent("idle");
  });
});
