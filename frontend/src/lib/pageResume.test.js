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

  test("the initial non-persisted pageshow produces no resume generation", async () => {
    const events = [];
    const handleResume = (event) => events.push(event.detail);
    window.addEventListener(PAGE_RESUME_EVENT, handleResume);
    installPageResumeCoordinator();

    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false }));
    await vi.advanceTimersByTimeAsync(PAGE_RESUME_COALESCE_MS);

    expect(events).toEqual([]);
    window.removeEventListener(PAGE_RESUME_EVENT, handleResume);
  });

  test("the document's first normal focus at load produces no resume generation", async () => {
    const events = [];
    const handleResume = (event) => events.push(event.detail);
    window.addEventListener(PAGE_RESUME_EVENT, handleResume);
    installPageResumeCoordinator();

    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(PAGE_RESUME_COALESCE_MS);

    expect(events).toEqual([]);
    window.removeEventListener(PAGE_RESUME_EVENT, handleResume);
  });

  test("a genuine focus return after a blur produces exactly one resume generation", async () => {
    const events = [];
    const handleResume = (event) => events.push(event.detail);
    window.addEventListener(PAGE_RESUME_EVENT, handleResume);
    installPageResumeCoordinator();

    window.dispatchEvent(new Event("blur"));
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(PAGE_RESUME_COALESCE_MS);

    expect(events).toHaveLength(1);
    expect(events[0].generation).toBe(1);
    window.removeEventListener(PAGE_RESUME_EVENT, handleResume);
  });

  test("a hidden visibility transition pauses without producing a resume event, and the following visible return coalesces to one generation", async () => {
    const events = [];
    const handleResume = (event) => events.push(event.detail);
    window.addEventListener(PAGE_RESUME_EVENT, handleResume);
    installPageResumeCoordinator();

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(PAGE_RESUME_COALESCE_MS);
    expect(events).toEqual([]);

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(PAGE_RESUME_COALESCE_MS);

    expect(events).toHaveLength(1);
    window.removeEventListener(PAGE_RESUME_EVENT, handleResume);
  });
});
