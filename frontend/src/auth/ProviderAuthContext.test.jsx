import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const authState = vi.hoisted(() => ({ user: { id: 7, role: "admin" } }));

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
  authState.user = { id: 7, role: "admin" };
  window.history.replaceState({}, "", "/settings/cloud-sharing");
  clearProviderAuthIntent();
  apiRequest.mockImplementation((path) => {
    if (path === "/api/cloud-libraries/google/provider-auth-status") return Promise.resolve({ provider_auth_required: false });
    if (path === "/api/cloud-libraries") return Promise.resolve({ google: { connected: false, reconnect_required: false } });
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
    window.history.replaceState({}, "", "/settings/cloud-sharing?googleDriveStatus=connected");
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("connected");
    expect(window.location.search).toBe("");
    expect(readProviderAuthIntent({ identity: "7:admin" })).toBeNull();
  });

  test("turns callback cancellation into a terminal non-pending state", async () => {
    saveIntent();
    window.history.replaceState({}, "", "/settings/cloud-sharing?googleDriveStatus=cancelled");
    renderProvider();

    expect(await screen.findByTestId("transaction-state")).toHaveTextContent("cancelled_or_incomplete");
    expect(screen.getByTestId("transaction-message")).toHaveTextContent("Reconnect was not completed.");
  });

  test("uses one bounded follow-up and never leaves a resumed transaction pending", async () => {
    vi.useFakeTimers();
    saveIntent();
    renderProvider();
    await act(async () => Promise.resolve());
    expect(screen.getByTestId("transaction-state")).toHaveTextContent("reconciling");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(screen.getByTestId("transaction-state")).toHaveTextContent("cancelled_or_incomplete");
    expect(apiRequest.mock.calls.filter(([path]) => path === "/api/cloud-libraries")).toHaveLength(2);
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

    await waitFor(() => expect(apiRequest.mock.calls.filter(([path]) => path === "/api/cloud-libraries")).toHaveLength(1));
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
