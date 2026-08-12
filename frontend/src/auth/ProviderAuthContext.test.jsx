import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api.js";
import {
  clearProviderAuthIntent,
  readProviderAuthIntent,
  saveProviderAuthIntent,
  navigateToProviderAuthorization,
  startGoogleDriveReconnect,
} from "../lib/providerAuth.js";
import { PAGE_RESUME_EVENT } from "../lib/pageResume.js";
import { CONNECTIVITY_RECOVERED_EVENT } from "../lib/connectivityRecoveryStore.js";
import { resetExternalNavigationCoordinatorForTests } from "../lib/externalNavigationCoordinator.js";
import { ProviderAuthProvider, useProviderAuth } from "./ProviderAuthContext.jsx";

const authState = vi.hoisted(() => ({ loading: false, user: { id: 7, role: "admin" } }));

vi.mock("./AuthContext.jsx", () => ({ useAuth: () => authState }));
vi.mock("../lib/api.js", async (importOriginal) => ({
  ...(await importOriginal()),
  apiRequest: vi.fn(),
}));
vi.mock("../lib/providerAuth.js", async (importOriginal) => ({
  ...(await importOriginal()),
  navigateToProviderAuthorization: vi.fn(),
  startGoogleDriveReconnect: vi.fn(),
}));

function Probe() {
  const controller = useProviderAuth();
  return (
    <div>
      <output data-testid="transaction-state">{controller.providerAuthTransaction.state}</output>
      <output data-testid="transaction-message">{controller.providerAuthTransaction.message}</output>
      <output data-testid="transaction-outcome">{controller.providerAuthTransaction.outcomeId}</output>
      <button onClick={() => controller.startProviderReconnect({ allowWithoutRequirement: true })} type="button">Reconnect now</button>
      <button onClick={() => controller.acknowledgeProviderAuthOutcome(controller.providerAuthTransaction.outcomeId)} type="button">Acknowledge</button>
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
  resetExternalNavigationCoordinatorForTests();
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
  navigateToProviderAuthorization.mockImplementation(() => undefined);
});

afterEach(() => {
  resetExternalNavigationCoordinatorForTests();
  clearProviderAuthIntent();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("ProviderAuthProvider transaction owner", () => {
  test("removes malformed stored intents instead of retrying corrupt navigation state", () => {
    window.sessionStorage.setItem("elvern:provider-auth-intent", "{broken-json");

    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
    expect(window.sessionStorage.getItem("elvern:provider-auth-intent")).toBeNull();
  });

  test("removes stale and unreadable stored intents without entering loading", () => {
    window.sessionStorage.setItem("elvern:provider-auth-intent", JSON.stringify({
      provider: "google_drive",
      operationId: "stale-operation",
      identity: "7:admin",
      returnPath: "/settings/cloud-sharing",
      state: "navigating_external",
      savedAt: Date.now() - (16 * 60 * 1000),
    }));
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();

    window.sessionStorage.setItem("elvern:provider-auth-intent", "still-present");
    vi.spyOn(Storage.prototype, "getItem").mockImplementationOnce(() => {
      throw new DOMException("Storage unavailable", "SecurityError");
    });
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
  });

  test("fails reconnect safely when the browser cannot persist the operation", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => {
      throw new DOMException("Storage unavailable", "QuotaExceededError");
    });
    renderProvider();

    fireEvent.click(screen.getByRole("button", { name: "Reconnect now" }));

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("error");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent(
      "this browser could not save the operation",
    );
    expect(startGoogleDriveReconnect).not.toHaveBeenCalled();
    expect(navigateToProviderAuthorization).not.toHaveBeenCalled();
  });

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
    expect(navigateToProviderAuthorization).toHaveBeenCalledWith("https://accounts.example.test/oauth");
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
    expect(screen.getByTestId("transaction-outcome")).toHaveTextContent("7:admin:operation-provider-auth-test");
    expect(window.location.search).toBe("");
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
  });

  test("acknowledges a terminal outcome exactly once across rerenders", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "connected" });
      throw new Error(`Unexpected request: ${path}`);
    });
    const rendered = renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("connected");
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(screen.getByTestId("transaction-state")).toHaveTextContent("idle");

    rendered.rerender(<ProviderAuthProvider><Probe /></ProviderAuthProvider>);
    expect(screen.getByTestId("transaction-state")).toHaveTextContent("idle");
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/status",
    )).toHaveLength(1);
  });

  test("keeps a network-unknown operation reconcilable instead of calling it cancelled", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.reject(new TypeError("network unavailable"));
      throw new Error(`Unexpected request: ${path}`);
    });
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("unknown");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent("temporarily unknown");
    expect(screen.getByTestId("transaction-outcome")).toHaveTextContent("");
    expect(readProviderAuthIntent({ identity: "7:admin" })).not.toBeNull();
  });

  test("turns callback cancellation into a terminal non-pending state", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "cancelled" });
      throw new Error(`Unexpected request: ${path}`);
    });
    window.history.replaceState(
      {},
      "",
      "/settings/cloud-sharing?googleDriveStatus=cancelled&googleDriveMessage=Google%20Drive%20sign-in%20was%20cancelled%20or%20denied.",
    );
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("cancelled_or_incomplete");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent("Reconnect was not completed.");
    expect(screen.getByTestId("transaction-message")).not.toHaveTextContent("cancelled or denied");
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

  test("keeps the operation pending when cancellation cannot be confirmed", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "pending" });
      if (path === "/api/cloud-libraries/google/operation/cancel") return Promise.reject(new TypeError("network unavailable"));
      throw new Error(`Unexpected request: ${path}`);
    });
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("pending_confirmation");
    expect(screen.getByTestId("transaction-outcome")).toHaveTextContent("");
    expect(readProviderAuthIntent({ identity: "7:admin" })).toEqual(expect.objectContaining({
      operationId: "operation-provider-auth-test",
    }));
  });

  test("does not clear the intent when cancellation returns a non-terminal status", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "pending" });
      if (path === "/api/cloud-libraries/google/operation/cancel") return Promise.resolve({ status: "pending" });
      throw new Error(`Unexpected request: ${path}`);
    });
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("pending_confirmation");
    expect(readProviderAuthIntent({ identity: "7:admin" })).not.toBeNull();
  });

  test("reconciles a retained cancellation exactly once after confirmed connectivity recovery", async () => {
    saveIntent();
    let cancellationAttempts = 0;
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "pending" });
      if (path === "/api/cloud-libraries/google/operation/cancel") {
        cancellationAttempts += 1;
        return cancellationAttempts === 1
          ? Promise.reject(new TypeError("network unavailable"))
          : Promise.resolve({ status: "cancelled" });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("pending_confirmation");
    fireEvent(window, new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, { detail: { generation: 11 } }));
    fireEvent(window, new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, { detail: { generation: 11 } }));

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("cancelled_or_incomplete");
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/status",
    )).toHaveLength(2);
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/cancel",
    )).toHaveLength(2);
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
  });

  test("keeps an unknown backend operation status pending and retains its intent", async () => {
    saveIntent();
    apiRequest.mockImplementation((path) => {
      if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
      if (path === "/api/cloud-libraries/google/operation/status") return Promise.resolve({ status: "future_status" });
      throw new Error(`Unexpected request: ${path}`);
    });
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("pending_confirmation");
    expect(readProviderAuthIntent({ identity: "7:admin" })).toEqual(expect.objectContaining({
      operationId: "operation-provider-auth-test",
    }));
  });

  test("turns a failed browser navigation into one terminal error only after backend cancellation", async () => {
    navigateToProviderAuthorization.mockImplementationOnce(() => {
      throw new Error("Browser navigation was blocked.");
    });
    renderProvider();
    fireEvent.click(screen.getByRole("button", { name: "Reconnect now" }));

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("error");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent("Browser navigation was blocked.");
    expect(screen.getByTestId("transaction-outcome")).not.toHaveTextContent("");
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

  test("consumes only one unified resume generation per operation", async () => {
    renderProvider();
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/cloud-libraries/google/provider-auth-status",
      expect.any(Object),
    ));
    apiRequest.mockClear();
    saveIntent();

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT, { detail: { generation: 7 } }));
    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT, { detail: { generation: 7 } }));

    await waitFor(() => expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/status",
    )).toHaveLength(1));
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/cancel",
    )).toHaveLength(1);
  });

  test("raw pageshow and visibility events do not own ProviderAuth reconciliation", async () => {
    renderProvider();
    await waitFor(() => expect(apiRequest).toHaveBeenCalled());
    apiRequest.mockClear();
    saveIntent();

    fireEvent(window, new Event("pageshow"));
    fireEvent(document, new Event("visibilitychange"));
    await Promise.resolve();

    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/cloud-libraries/google/operation/status",
    )).toHaveLength(0);
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

  test("clears an old-role operation before the new role can reconcile it", async () => {
    authState.loading = true;
    saveIntent();
    const rendered = renderProvider();

    authState.loading = false;
    authState.user = { id: 7, role: "standard_user" };
    rendered.rerender(<ProviderAuthProvider><Probe /></ProviderAuthProvider>);

    await waitFor(() => expect(readProviderAuthIntent()).toBeNull());
    expect(screen.getByTestId("transaction-state")).toHaveTextContent("idle");
    expect(apiRequest.mock.calls.some(
      ([path]) => path === "/api/cloud-libraries/google/operation/status",
    )).toBe(false);
  });
});
