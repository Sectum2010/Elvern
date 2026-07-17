import { describe, expect, test, vi } from "vitest";

import {
  DETAIL_PERFORMANCE_MARKS,
  markDetailPerformance,
  startDetailPerformanceTrace,
} from "./detailPerformanceTiming.js";


describe("anonymous Detail performance timing", () => {
  test("is disabled unless explicitly enabled", () => {
    const performanceObject = { mark: vi.fn() };

    expect(markDetailPerformance(DETAIL_PERFORMANCE_MARKS.shellVisible, {
      performanceObject,
    })).toBe(false);
    expect(performanceObject.mark).not.toHaveBeenCalled();
  });

  test("records only fixed anonymous mark names", () => {
    const performanceObject = { clearMarks: vi.fn(), mark: vi.fn() };

    expect(startDetailPerformanceTrace({ enabled: true, performanceObject })).toBe(true);
    expect(markDetailPerformance("private title", { enabled: true, performanceObject })).toBe(false);
    expect(performanceObject.mark).toHaveBeenCalledWith(DETAIL_PERFORMANCE_MARKS.cardClick);
    expect(performanceObject.mark).not.toHaveBeenCalledWith("private title");
  });
});
