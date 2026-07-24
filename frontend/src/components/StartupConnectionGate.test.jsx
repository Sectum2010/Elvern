import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { StartupConnectionGate } from "./StartupConnectionGate.jsx";
import {
  CONNECTION_OFFLINE_OOPS_COPY,
  CONNECTION_GENERIC_OOPS_COPY,
  CONNECTION_SERVER_OOPS_COPY,
  CONNECTION_VPN_OOPS_COPY,
  CONNECTION_FAMILIAR_ROTATION_MS,
  createStartupConnectionController,
  NO_INTERNET_REAPPEAR_MS,
  STARTUP_APPLICATION_READY_EVENT,
  STARTUP_CONNECTIVITY_FAILURE_EVENT,
  STARTUP_SHELL_REVEAL_DELAY_MS,
  STARTUP_UNREACHABLE_DELAY_MS,
} from "../lib/startupConnection.js";


function installShellFixture() {
  document.body.innerHTML = `
    <div id="elvern-connection-shell" data-familiar="raven" data-state="connecting" role="status" aria-live="polite">
      <span data-connection-waiting-word>Flibbertigibbeting...</span>
      <div class="elvern-connection-shell__oops">
        <h1>Oops!</h1>
        <p data-connection-oops-copy>${CONNECTION_VPN_OOPS_COPY}</p>
        <button class="elvern-connection-shell__retry" data-connection-retry type="button">Retry</button>
      </div>
    </div>
    <div id="test-root"></div>
  `;
  return document.getElementById("test-root");
}


function backendHealthResponse(status = 200) {
  return new Response("ok", {
    status,
    headers: { "X-Elvern-Backend-Health": "1" },
  });
}


function frontendHealthResponse(status = 204) {
  return new Response(null, {
    status,
    headers: { "X-Elvern-Frontend-Health": "1" },
  });
}


function healthyServiceFetch(path) {
  if (path === "/_elvern/frontend-health") {
    return Promise.resolve(frontendHealthResponse());
  }
  if (path === "/health") {
    return Promise.resolve(backendHealthResponse());
  }
  return Promise.resolve(new Response(null, { status: 204 }));
}


describe("StartupConnectionGate", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
    delete window.__elvernRuntimeReady;
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    delete window.__elvernRuntimeReady;
    document.body.innerHTML = "";
  });

  test("shows connecting first, exact Oops at 60 seconds, and a text-only Retry button", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1));
    expect(document.getElementById("elvern-connection-shell").dataset.state).toBe("connecting");
    expect(screen.queryByText("App ready")).not.toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(document.getElementById("elvern-connection-shell").dataset.state).toBe("unreachable");
    expect(screen.getByRole("heading", { name: "Oops!" })).toBeInTheDocument();
    expect(screen.getByText(CONNECTION_GENERIC_OOPS_COPY)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toHaveClass("elvern-connection-shell__retry");

    const callsBeforeRetry = fetchImpl.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetchImpl).toHaveBeenCalledTimes(callsBeforeRetry + 1);
    expect(document.getElementById("elvern-connection-shell").dataset.state).toBe("unreachable");
  });

  test("does not reveal the shell during a fast healthy startup", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn(healthyServiceFetch);
    const controller = createStartupConnectionController({
      fetchImpl,
      requireApplicationReady: true,
    });
    render(<StartupConnectionGate controller={controller}><p>Auth flow mounted</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(STARTUP_SHELL_REVEAL_DELAY_MS - 1));
    const shell = document.getElementById("elvern-connection-shell");
    expect(shell).not.toHaveClass("elvern-connection-shell--visible");

    act(() => window.dispatchEvent(new CustomEvent(STARTUP_APPLICATION_READY_EVENT)));
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(shell).not.toHaveClass("elvern-connection-shell--visible");
    expect(shell).toHaveClass("elvern-connection-shell--ready");
  });

  test("reveals the connecting shell after the 400ms grace period", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    const shell = document.getElementById("elvern-connection-shell");
    await act(() => vi.advanceTimersByTimeAsync(STARTUP_SHELL_REVEAL_DELAY_MS - 1));
    expect(shell).not.toHaveClass("elvern-connection-shell--visible");

    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(shell).toHaveClass("elvern-connection-shell--visible");
  });

  test("health recovery automatically enters the application", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn(healthyServiceFetch);
    const controller = createStartupConnectionController({ fetchImpl });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("App ready")).toBeInTheDocument();
    expect(document.getElementById("elvern-connection-shell")).toHaveClass("elvern-connection-shell--ready");
  });

  test("health mounts auth flow but keeps the shell until an HTTP response arrives", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn(healthyServiceFetch);
    const controller = createStartupConnectionController({
      fetchImpl,
      requireApplicationReady: true,
    });
    render(<StartupConnectionGate controller={controller}><p>Auth flow mounted</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("Auth flow mounted")).toBeInTheDocument();
    expect(document.getElementById("elvern-connection-shell").dataset.state).toBe("connecting");

    act(() => window.dispatchEvent(new CustomEvent(STARTUP_APPLICATION_READY_EVENT)));
    expect(document.getElementById("elvern-connection-shell")).toHaveClass("elvern-connection-shell--ready");
  });

  test("honors an application-ready signal that arrived before the gate listener", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({
      fetchImpl,
      requireApplicationReady: true,
    });
    window.__elvernRuntimeReady = true;

    render(<StartupConnectionGate controller={controller}><p>Auth already ready</p></StartupConnectionGate>, { container });
    await act(() => vi.advanceTimersByTimeAsync(0));

    expect(screen.getByText("Auth already ready")).toBeInTheDocument();
    expect(document.getElementById("elvern-connection-shell")).toHaveClass("elvern-connection-shell--ready");
  });

  test("keeps the mounted application alive and overlays the server copy after a runtime outage", async () => {
    const container = installShellFixture();
    let backendHealthy = true;
    const fetchImpl = vi.fn((path) => {
      if (String(path).startsWith("https://")) {
        return Promise.resolve({ status: 200, ok: true });
      }
      if (path === "/_elvern/frontend-health") {
        return Promise.resolve(frontendHealthResponse());
      }
      return backendHealthy
        ? Promise.resolve(backendHealthResponse())
        : Promise.reject(new TypeError("backend down"));
    });
    const controller = createStartupConnectionController({ fetchImpl, requireApplicationReady: true });
    render(<StartupConnectionGate controller={controller}><p>Persistent detail state</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    act(() => window.dispatchEvent(new CustomEvent(STARTUP_APPLICATION_READY_EVENT)));
    expect(screen.getByText("Persistent detail state")).toBeInTheDocument();

    backendHealthy = false;
    act(() => window.dispatchEvent(new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT)));
    await act(() => vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS));

    expect(screen.getByText("Persistent detail state")).toBeInTheDocument();
    expect(within(document.querySelector(".runtime-connectivity-oops")).getByText(
      CONNECTION_SERVER_OOPS_COPY,
    )).toBeInTheDocument();
  });

  test("shows a local swipe-only No Internet notice and reappears after 10 seconds", async () => {
    const container = installShellFixture();
    const navigatorObject = { onLine: true };
    const fetchImpl = vi.fn(healthyServiceFetch);
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject });
    render(<StartupConnectionGate controller={controller}><p>Library state</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    navigatorObject.onLine = false;
    act(() => window.dispatchEvent(new Event("offline")));
    const notice = screen.getByText("No Internet");
    fireEvent(notice, new MouseEvent("pointerdown", { bubbles: true, clientY: 100 }));
    fireEvent(notice, new MouseEvent("pointerup", { bubbles: true, clientY: 60 }));
    expect(screen.queryByText("No Internet")).not.toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(NO_INTERNET_REAPPEAR_MS));
    expect(screen.getByText("No Internet")).toBeInTheDocument();
    expect(screen.getByText("Library state")).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS * 2));
    expect(screen.getByText("No Internet")).toBeInTheDocument();
    expect(screen.getByText("Library state")).toBeInTheDocument();
    expect(document.querySelector(".runtime-connectivity-oops")).not.toBeInTheDocument();

    navigatorObject.onLine = true;
    act(() => window.dispatchEvent(new Event("online")));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.queryByText("No Internet")).not.toBeInTheDocument();
    expect(screen.getByText("Library state")).toBeInTheDocument();
  });

  test("pointer cancellation resets drag state without dismissing No Internet", async () => {
    const container = installShellFixture();
    const navigatorObject = { onLine: true };
    const fetchImpl = vi.fn(healthyServiceFetch);
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject });
    render(<StartupConnectionGate controller={controller}><p>Library state</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    navigatorObject.onLine = false;
    act(() => window.dispatchEvent(new Event("offline")));
    const notice = screen.getByText("No Internet");
    fireEvent(notice, new MouseEvent("pointerdown", { bubbles: true, clientY: 100 }));
    fireEvent(notice, new MouseEvent("pointermove", { bubbles: true, clientY: 40 }));
    fireEvent(notice, new MouseEvent("pointercancel", { bubbles: true, clientY: 40 }));

    expect(screen.getByText("No Internet")).toBeInTheDocument();
    expect(screen.getByText("No Internet")).toHaveStyle({ transform: "translate(-50%, 0px)" });
  });

  test("a swipe stays dismissed through 9999ms and creates only one reappearance deadline", async () => {
    const container = installShellFixture();
    const navigatorObject = { onLine: true };
    const fetchImpl = vi.fn(healthyServiceFetch);
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject });
    render(<StartupConnectionGate controller={controller}><p>Library state</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    navigatorObject.onLine = false;
    act(() => window.dispatchEvent(new Event("offline")));
    const notice = screen.getByText("No Internet");
    fireEvent(notice, new MouseEvent("pointerdown", { bubbles: true, clientY: 100 }));
    fireEvent(notice, new MouseEvent("pointerup", { bubbles: true, clientY: 50 }));

    await act(() => vi.advanceTimersByTimeAsync(NO_INTERNET_REAPPEAR_MS - 1));
    expect(screen.queryByText("No Internet")).not.toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(screen.getByText("No Internet")).toBeInTheDocument();
  });

  test("evidence-insufficient runtime outages use the generic Oops copy", async () => {
    const container = installShellFixture();
    const controller = createStartupConnectionController({
      fetchImpl: vi.fn().mockRejectedValue(new TypeError("unreachable")),
      publicConnectivityProbeUrl: "https://probe.operator.example/connectivity",
      publicProbeConfirmationDelayMs: 0,
    });
    render(<StartupConnectionGate controller={controller}><p>Persistent app</p></StartupConnectionGate>, { container });

    controller.reportApplicationReady();
    await act(() => vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS));

    expect(screen.getByText("Persistent app")).toBeInTheDocument();
    expect(within(document.querySelector(".runtime-connectivity-oops")).getByText(
      CONNECTION_GENERIC_OOPS_COPY,
    )).toBeInTheDocument();
  });

  test("an explicit offline login attempt shows the offline Oops shell immediately", async () => {
    const container = installShellFixture();
    const navigatorObject = { onLine: true };
    const fetchImpl = vi.fn(healthyServiceFetch);
    const controller = createStartupConnectionController({ fetchImpl, navigatorObject });
    render(<StartupConnectionGate controller={controller}><p>Login state</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    navigatorObject.onLine = false;
    act(() => window.dispatchEvent(new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT, {
      detail: { requestClass: "auth_login" },
    })));

    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(document.getElementById("elvern-connection-shell")).toHaveAttribute("data-state", "unreachable");
    expect(document.querySelector("[data-connection-oops-copy]")).toHaveTextContent(CONNECTION_OFFLINE_OOPS_COPY);
    expect(screen.getByText("Login state")).toBeInTheDocument();
  });

  test("rotates familiar and waiting word in discrete seven second steps", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl, publicConnectivityProbes: [] });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(CONNECTION_FAMILIAR_ROTATION_MS));
    expect(document.getElementById("elvern-connection-shell").dataset.familiar).toBe("wisp");
    expect(document.querySelector("[data-connection-waiting-word]")).toHaveAttribute("aria-label", "Ruminating...");
  });

  test("reduced motion keeps the first familiar and waiting word static", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(21_000));
    expect(document.getElementById("elvern-connection-shell").dataset.familiar).toBe("raven");
    expect(document.querySelector("[data-connection-waiting-word]")).toHaveAttribute("aria-label", "Flibbertigibbeting...");
  });
});
