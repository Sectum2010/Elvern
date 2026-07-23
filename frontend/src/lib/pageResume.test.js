import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  installPageResumeCoordinator,
  PAGE_RESUME_COALESCE_MS,
  PAGE_RESUME_EVENT,
  resetPageResumeCoordinatorForTests,
} from "./pageResume.js";


describe("page resume coordinator", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetPageResumeCoordinatorForTests();
  });

  afterEach(() => {
    resetPageResumeCoordinatorForTests();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test("coalesces pageshow, focus, and visibility into one sanitized generation", async () => {
    const events = [];
    const handleResume = (event) => events.push(event.detail);
    window.addEventListener(PAGE_RESUME_EVENT, handleResume);
    vi.spyOn(performance, "getEntriesByType").mockReturnValue([{ type: "back_forward" }]);
    installPageResumeCoordinator();

    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(PAGE_RESUME_COALESCE_MS);

    expect(events).toEqual([{
      generation: 1,
      navigationType: "back_forward",
      pageshowPersisted: true,
    }]);
    expect(events[0]).not.toHaveProperty("url");
    expect(events[0]).not.toHaveProperty("query");
    window.removeEventListener(PAGE_RESUME_EVENT, handleResume);
  });
});
