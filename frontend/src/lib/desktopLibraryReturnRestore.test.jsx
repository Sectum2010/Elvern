import { act, cleanup, render } from "@testing-library/react";
import { useEffect, useRef } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  createDesktopLibraryReturnRestoreTransaction,
  DESKTOP_LIBRARY_RETURN_MAX_CORRECTIONS,
  DESKTOP_LIBRARY_RETURN_TOLERANCE_PX,
  isDesktopLibraryReturnPlatform,
  logDesktopLibraryReturnCapture,
  useDesktopLibraryReturnRestore,
} from "./desktopLibraryReturnRestore";
import { rememberLibraryReturnTarget } from "./libraryNavigation";


function installViewport({ initialScrollY = 0, viewportHeight = 900, scrollHeight = 6000 } = {}) {
  let scrollY = initialScrollY;
  Object.defineProperty(window, "scrollY", {
    configurable: true,
    get: () => scrollY,
  });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: viewportHeight });
  Object.defineProperty(document.documentElement, "clientHeight", {
    configurable: true,
    value: viewportHeight,
  });
  Object.defineProperty(document.documentElement, "scrollHeight", {
    configurable: true,
    value: scrollHeight,
  });
  window.scrollTo = vi.fn(({ top }) => {
    scrollY = Number(top) || 0;
    window.dispatchEvent(new Event("scroll"));
  });
  window.requestAnimationFrame = vi.fn((callback) => window.setTimeout(() => callback(performance.now()), 0));
  window.cancelAnimationFrame = vi.fn((frameId) => window.clearTimeout(frameId));
  return {
    getScrollY: () => scrollY,
    setScrollY: (value) => {
      scrollY = value;
    },
  };
}


function createCard({ itemId, instanceKey, documentTop, height = 360, railKey = null } = {}) {
  const card = document.createElement("article");
  card.dataset.libraryItemId = String(itemId);
  card.dataset.libraryCardInstanceKey = instanceKey;
  let currentDocumentTop = documentTop;
  card.getBoundingClientRect = () => ({
    top: currentDocumentTop - window.scrollY,
    bottom: currentDocumentTop - window.scrollY + height,
    left: 100,
    right: 340,
    width: 240,
    height,
  });
  if (railKey) {
    const rail = document.createElement("section");
    rail.dataset.seriesRailKey = railKey;
    const viewport = document.createElement("div");
    viewport.className = "series-rail__viewport";
    viewport.scrollLeft = 0;
    rail.append(viewport, card);
    return {
      card,
      container: rail,
      viewport,
      setDocumentTop: (value) => {
        currentDocumentTop = value;
      },
    };
  }
  return {
    card,
    container: card,
    viewport: null,
    setDocumentTop: (value) => {
      currentDocumentTop = value;
    },
  };
}


function returnTarget(overrides = {}) {
  return {
    listPath: "/library",
    anchorItemId: 42,
    anchorInstanceKey: "other-movies:42",
    anchorViewportRatioY: 0.4,
    anchorViewportRatioX: 0.2,
    viewportWidth: 1440,
    viewportHeight: 900,
    scrollY: 2400,
    railKey: null,
    railScrollLeft: null,
    pendingRestore: true,
    ...overrides,
  };
}


describe("desktop library return restore transaction", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.sessionStorage.clear();
    document.body.innerHTML = "";
  });

  afterEach(() => {
    cleanup();
    document.body.innerHTML = "";
    window.sessionStorage.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  test("is limited to Windows, macOS, and Linux desktop/laptop", () => {
    expect(isDesktopLibraryReturnPlatform({ platform: "windows", deviceClass: "desktop" })).toBe(true);
    expect(isDesktopLibraryReturnPlatform({ platform: "macos", deviceClass: "desktop" })).toBe(true);
    expect(isDesktopLibraryReturnPlatform({ platform: "linux", deviceClass: "desktop" })).toBe(true);
    expect(isDesktopLibraryReturnPlatform({ platform: "iphone", deviceClass: "phone" })).toBe(false);
    expect(isDesktopLibraryReturnPlatform({ platform: "ipad", deviceClass: "tablet" })).toBe(false);
    expect(isDesktopLibraryReturnPlatform({ platform: "android", deviceClass: "tablet" })).toBe(false);
  });

  test("capture diagnostics are default-off and exclude media titles and paths", () => {
    installViewport();
    const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3200 });
    logDesktopLibraryReturnCapture({ target: returnTarget(), cardNode: exact.card });
    expect(debugSpy).not.toHaveBeenCalled();

    window.localStorage.setItem("elvern_library_return_debug", "1");
    logDesktopLibraryReturnCapture({ target: returnTarget(), cardNode: exact.card });
    expect(debugSpy).toHaveBeenCalledWith(
      "[Elvern library return] library card captured",
      expect.objectContaining({
        anchorItemId: 42,
        anchorInstanceKey: "other-movies:42",
        documentCardTop: 3200,
      }),
    );
    const loggedValues = debugSpy.mock.calls.at(-1)[1];
    expect(loggedValues).not.toHaveProperty("title");
    expect(loggedValues).not.toHaveProperty("posterPath");
    expect(loggedValues).not.toHaveProperty("filePath");
  });

  test("restores a deep exact instance synchronously with the saved viewport ratio", () => {
    const viewport = installViewport({ initialScrollY: 0 });
    const duplicate = createCard({ itemId: 42, instanceKey: "continue-watching:42", documentTop: 1200 });
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3200 });
    document.body.append(duplicate.container, exact.container);
    const root = document.createElement("section");
    document.body.append(root);

    const transaction = createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget(),
      rootNode: root,
      doc: document,
      viewportWindow: window,
    });
    transaction.start();

    expect(viewport.getScrollY()).toBe(2840);
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 2840, behavior: "auto" });
    expect(root).toHaveAttribute("data-library-return-restoring", "true");
    expect(transaction.getSnapshot().targetSource).toBe("exact_instance");
  });

  test("handles a browser-provided initial scroll value without double counting it", () => {
    const viewport = installViewport({ initialScrollY: 1500 });
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3200 });
    document.body.append(exact.container);

    createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget(),
      doc: document,
      viewportWindow: window,
    }).start();

    expect(viewport.getScrollY()).toBe(2840);
  });

  test("restores rail scrollLeft before vertical measurement", () => {
    installViewport();
    const exact = createCard({
      itemId: 42,
      instanceKey: "series:dragon:42",
      documentTop: 2800,
      railKey: "dragon",
    });
    document.body.append(exact.container);

    createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget({
        anchorInstanceKey: "series:dragon:42",
        railKey: "dragon",
        railScrollLeft: 288,
      }),
      doc: document,
      viewportWindow: window,
    }).start();

    expect(exact.viewport.scrollLeft).toBe(288);
  });

  test("corrects a delayed upper-section layout change once and then settles", () => {
    const viewport = installViewport();
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3200 });
    document.body.append(exact.container);
    const completed = vi.fn();
    const transaction = createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget(),
      doc: document,
      viewportWindow: window,
      onComplete: completed,
    });
    transaction.start();
    exact.setDocumentTop(3560);

    act(() => {
      vi.runAllTimers();
    });

    expect(viewport.getScrollY()).toBe(3200);
    expect(transaction.getSnapshot().correctionCount).toBe(1);
    expect(completed).toHaveBeenCalledWith(expect.objectContaining({ reason: "stable" }));
  });

  test("waits for a stale background refresh and corrects after the refreshed layout commits", () => {
    const viewport = installViewport();
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3200 });
    document.body.append(exact.container);
    const transaction = createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget(),
      doc: document,
      viewportWindow: window,
      backgroundFetching: true,
    });
    transaction.start();
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(transaction.getSnapshot().finished).toBe(false);

    exact.setDocumentTop(3440);
    transaction.setExternalPending({ backgroundFetching: false, settingsPending: false });
    transaction.notifyLayoutChange("background_refetch_committed");
    act(() => {
      vi.runAllTimers();
    });

    expect(viewport.getScrollY()).toBe(3080);
    expect(transaction.getSnapshot().correctionCount).toBe(1);
    expect(transaction.getSnapshot().finished).toBe(true);
  });

  test("user wheel cancels pending correction and clears the temporary root marker", () => {
    const viewport = installViewport();
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3200 });
    const root = document.createElement("section");
    root.append(exact.container);
    document.body.append(root);
    const completed = vi.fn();
    const transaction = createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget(),
      rootNode: root,
      doc: document,
      viewportWindow: window,
      onComplete: completed,
    });
    transaction.start();
    exact.setDocumentTop(3600);
    window.dispatchEvent(new WheelEvent("wheel"));
    act(() => {
      vi.runAllTimers();
    });

    expect(viewport.getScrollY()).toBe(2840);
    expect(transaction.getSnapshot().cancelled).toBe(true);
    expect(root).not.toHaveAttribute("data-library-return-restoring");
    expect(completed).toHaveBeenCalledWith(expect.objectContaining({ reason: "user_interaction" }));
  });

  test("a delayed bare user scroll cancels after the native restoration window", () => {
    const viewport = installViewport();
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3200 });
    document.body.append(exact.container);
    const transaction = createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget(),
      doc: document,
      viewportWindow: window,
      backgroundFetching: true,
    });
    transaction.start();
    act(() => {
      vi.advanceTimersByTime(300);
    });
    viewport.setScrollY(2900);
    window.dispatchEvent(new Event("scroll"));

    expect(transaction.getSnapshot().cancelled).toBe(true);
    expect(transaction.getSnapshot().finishReason).toBe("user_interaction");
  });

  test("hard-limits corrections and uses a stable near-bottom clamp", () => {
    installViewport({ scrollHeight: 3600 });
    const exact = createCard({ itemId: 42, instanceKey: "other-movies:42", documentTop: 3500 });
    document.body.append(exact.container);
    const transaction = createDesktopLibraryReturnRestoreTransaction({
      target: returnTarget(),
      doc: document,
      viewportWindow: window,
    });
    transaction.start();
    act(() => {
      vi.runAllTimers();
    });

    expect(window.scrollY).toBe(2700);
    expect(transaction.getSnapshot().correctionCount).toBeLessThanOrEqual(
      DESKTOP_LIBRARY_RETURN_MAX_CORRECTIONS,
    );
    expect(transaction.getSnapshot().finishReason).toBe("near_bottom_clamp");
    expect(DESKTOP_LIBRARY_RETURN_TOLERANCE_PX).toBeLessThanOrEqual(8);
  });

  test("the hook performs cached restore in layout effect before passive effects observe scroll", () => {
    const viewport = installViewport();
    rememberLibraryReturnTarget(returnTarget());
    let passiveEffectScrollY = null;

    function Harness() {
      const rootRef = useRef(null);
      useDesktopLibraryReturnRestore({
        enabled: true,
        currentListPath: "/library",
        locationState: { restoreLibraryReturn: true },
        loading: false,
        rootRef,
        platform: "linux",
        deviceClass: "desktop",
        navigationType: "POP",
        queryState: { hasExactData: true, isFresh: true, isFetching: false, dataUpdatedAt: 1 },
        settingsState: { hasData: true, isPending: false },
      });
      useEffect(() => {
        passiveEffectScrollY = window.scrollY;
      }, []);
      return (
        <section ref={rootRef}>
          <article
            data-library-card-instance-key="other-movies:42"
            data-library-item-id="42"
            ref={(node) => {
              if (node) {
                node.getBoundingClientRect = () => ({
                  top: 3200 - window.scrollY,
                  bottom: 3560 - window.scrollY,
                  left: 100,
                  right: 340,
                  width: 240,
                  height: 360,
                });
              }
            }}
          />
        </section>
      );
    }

    render(<Harness />);

    expect(viewport.getScrollY()).toBe(2840);
    expect(passiveEffectScrollY).toBe(2840);
  });
});
