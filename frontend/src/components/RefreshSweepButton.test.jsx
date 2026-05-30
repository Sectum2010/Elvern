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
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      bottom: 44,
      height: 44,
      left: 0,
      right: 180,
      top: 0,
      width: 180,
      x: 0,
      y: 0,
      toJSON: () => {},
    });
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
    const path = sweep?.querySelector("path");
    expect(sweep?.getAttribute("viewBox")).toBe("0 0 180 44");
    expect(path).not.toBeNull();
    expect(path?.getAttribute("d")).toContain("A ");
    expect(path?.style.getPropertyValue("--refresh-sweep-length")).not.toBe("100");

    act(() => {
      vi.advanceTimersByTime(REFRESH_SWEEP_MS);
    });

    expect(button).not.toHaveClass("refresh-status-sweep-button--active");
    rectSpy.mockRestore();
  });
});
