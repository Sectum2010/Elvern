import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { REFRESH_SWEEP_MS, RefreshSweepButton } from "./RefreshSweepButton.jsx";


describe("RefreshSweepButton", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      callback();
      return 1;
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  test("draws a single rounded rectangle sweep for the fixed refresh duration", () => {
    const onClick = vi.fn();
    render(
      <RefreshSweepButton className="ghost-button" onClick={onClick}>
        Refresh status
      </RefreshSweepButton>,
    );

    const button = screen.getByRole("button", { name: "Refresh status" });
    act(() => {
      fireEvent.click(button);
    });

    expect(onClick).toHaveBeenCalledTimes(1);
    expect(button).toHaveClass("refresh-status-sweep-button");
    expect(button).toHaveClass("refresh-status-sweep-button--active");

    const sweep = button.querySelector(".refresh-status-sweep-button__sweep");
    const rect = sweep?.querySelector("rect");
    expect(rect).not.toBeNull();
    expect(rect?.getAttribute("pathLength")).toBe("100");
    expect(rect?.getAttribute("rx")).toBe("17.6");

    act(() => {
      vi.advanceTimersByTime(REFRESH_SWEEP_MS);
    });

    expect(button).not.toHaveClass("refresh-status-sweep-button--active");
  });
});
