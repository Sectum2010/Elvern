import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { StartupConnectionGate } from "./StartupConnectionGate.jsx";
import {
  CONNECTION_FAMILIAR_ROTATION_MS,
  createStartupConnectionController,
  STARTUP_APPLICATION_READY_EVENT,
  STARTUP_UNREACHABLE_DELAY_MS,
} from "../lib/startupConnection.js";


function installShellFixture() {
  document.body.innerHTML = `
    <div id="elvern-connection-shell" data-familiar="raven" data-state="connecting" role="status" aria-live="polite">
      <span data-connection-waiting-word>Flibbertigibbeting...</span>
      <div class="elvern-connection-shell__oops">
        <h1>Oops!</h1>
        <p>Elvern could not be reached. Check your connection and try again.</p>
        <button class="elvern-connection-shell__retry" data-connection-retry type="button">Retry</button>
      </div>
    </div>
    <div id="test-root"></div>
  `;
  return document.getElementById("test-root");
}


describe("StartupConnectionGate", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  test("shows connecting first, exact Oops at 60 seconds, and a text-only Retry button", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(STARTUP_UNREACHABLE_DELAY_MS - 1));
    expect(document.getElementById("elvern-connection-shell").dataset.state).toBe("connecting");
    expect(screen.queryByText("App ready")).not.toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(document.getElementById("elvern-connection-shell").dataset.state).toBe("unreachable");
    expect(screen.getByRole("heading", { name: "Oops!" })).toBeInTheDocument();
    expect(screen.getByText("Elvern could not be reached. Check your connection and try again.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toHaveClass("elvern-connection-shell__retry");

    const callsBeforeRetry = fetchImpl.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetchImpl).toHaveBeenCalledTimes(callsBeforeRetry + 1);
    expect(document.getElementById("elvern-connection-shell").dataset.state).toBe("unreachable");
  });

  test("health recovery automatically enters the application", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
    const controller = createStartupConnectionController({ fetchImpl });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("App ready")).toBeInTheDocument();
    expect(document.getElementById("elvern-connection-shell")).toHaveClass("elvern-connection-shell--ready");
  });

  test("health mounts auth flow but keeps the shell until an HTTP response arrives", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok", { status: 200 }));
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

  test("rotates familiar and waiting word in discrete seven second steps", async () => {
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(CONNECTION_FAMILIAR_ROTATION_MS));
    expect(document.getElementById("elvern-connection-shell").dataset.familiar).toBe("wisp");
    expect(screen.getByText("Ruminating...")).toBeInTheDocument();
  });

  test("reduced motion keeps the first familiar and waiting word static", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
    const container = installShellFixture();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("offline"));
    const controller = createStartupConnectionController({ fetchImpl });
    render(<StartupConnectionGate controller={controller}><p>App ready</p></StartupConnectionGate>, { container });

    await act(() => vi.advanceTimersByTimeAsync(21_000));
    expect(document.getElementById("elvern-connection-shell").dataset.familiar).toBe("raven");
    expect(screen.getByText("Flibbertigibbeting...")).toBeInTheDocument();
  });
});
