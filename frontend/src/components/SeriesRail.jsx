import { useEffect, useMemo, useRef, useState } from "react";
import { MediaCard } from "./MediaCard";

const DESKTOP_DRAG_THRESHOLD_PX = 8;
const DESKTOP_DRAG_RESPONSE = 0.84;
const MOUSE_LONG_PRESS_MS = 180;
const EDGE_VISIBILITY_THRESHOLD = 14;
const EDGE_CUE_LEAD_DESKTOP = 18;
const EDGE_CUE_LEAD_MOBILE = 26;
const TOUCH_HORIZONTAL_RELEASE_THRESHOLD = 12;
const TOUCH_AXIS_RELEASE_BIAS = 4;
const TOUCH_VERTICAL_RECLAIM_THRESHOLD = 9;
const TOUCH_MOMENTUM_SETTLE_MS = 96;
const TOUCH_EDGE_RELEASE_SKIP_THRESHOLD = 18;
const INACTIVE_TOUCH_GESTURE_STATE = {
  active: false,
  startX: 0,
  startY: 0,
  horizontalIntent: false,
  momentumGuard: false,
  startScrollLeft: 0,
};

function detectViewportKind() {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return "desktop";
  }
  if (document.documentElement.dataset.deviceShell !== "iphone") {
    return "desktop";
  }
  return window.matchMedia("(orientation: landscape)").matches ? "phone-landscape" : "phone-portrait";
}


export function SeriesRail({
  rail,
  desktopSlots = null,
  enableTouchReleaseAssist = false,
  activeBrowserPlaybackItemId = null,
  smartPosterLoadingEnabled = false,
  sectionKey = null,
}) {
  const viewportRef = useRef(null);
  const dragStateRef = useRef(null);
  const suppressClickRef = useRef(false);
  const longPressReadyRef = useRef(false);
  const mousePressTimerRef = useRef(0);
  const scrollSuppressTimerRef = useRef(0);
  const touchReleaseTimerRef = useRef(0);
  const touchReleaseFrameRef = useRef(0);
  const momentumSettleTimerRef = useRef(0);
  const momentumActiveRef = useRef(false);
  const touchGestureStateRef = useRef({
    ...INACTIVE_TOUCH_GESTURE_STATE,
  });
  const [isDragging, setIsDragging] = useState(false);
  const [isTouchReleasing, setIsTouchReleasing] = useState(false);
  const [isMomentumActive, setIsMomentumActive] = useState(false);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const [showLeadingCue, setShowLeadingCue] = useState(false);
  const [showTrailingCue, setShowTrailingCue] = useState(false);
  const [viewportKind, setViewportKind] = useState(() => detectViewportKind());
  const visibleSlots = useMemo(() => {
    if (viewportKind === "phone-landscape") {
      return 2;
    }
    if (viewportKind === "phone-portrait") {
      return 1;
    }
    if (Number.isInteger(desktopSlots) && desktopSlots > 1) {
      return desktopSlots;
    }
    return 5;
  }, [desktopSlots, viewportKind]);
  const staticPhoneLandscape = viewportKind === "phone-landscape" && rail.items.length <= 3;
  const peekEnabled = !staticPhoneLandscape && rail.items.length > visibleSlots;

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }
    function handleViewportChange() {
      setViewportKind(detectViewportKind());
    }
    handleViewportChange();
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("orientationchange", handleViewportChange);
    return () => {
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("orientationchange", handleViewportChange);
    };
  }, []);

  useEffect(() => () => {
    if (typeof window !== "undefined" && mousePressTimerRef.current) {
      window.clearTimeout(mousePressTimerRef.current);
    }
    if (typeof window !== "undefined" && scrollSuppressTimerRef.current) {
      window.clearTimeout(scrollSuppressTimerRef.current);
    }
    if (typeof window !== "undefined" && touchReleaseTimerRef.current) {
      window.clearTimeout(touchReleaseTimerRef.current);
    }
    if (typeof window !== "undefined" && touchReleaseFrameRef.current) {
      window.cancelAnimationFrame(touchReleaseFrameRef.current);
    }
    if (typeof window !== "undefined" && momentumSettleTimerRef.current) {
      window.clearTimeout(momentumSettleTimerRef.current);
    }
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return undefined;
    }
    clearMousePressTimer();
    clearTouchReleaseAssist();
    clearMomentumActiveState();
    dragStateRef.current = null;
    longPressReadyRef.current = false;
    suppressClickRef.current = false;
    touchGestureStateRef.current = { ...INACTIVE_TOUCH_GESTURE_STATE };
    setIsDragging(false);
    if (viewportKind !== "desktop") {
      const resetScroll = () => {
        if (!viewportRef.current) {
          return;
        }
        viewportRef.current.scrollLeft = 0;
      };
      resetScroll();
      if (typeof window !== "undefined") {
        const frameOne = window.requestAnimationFrame(() => {
          resetScroll();
          const frameTwo = window.requestAnimationFrame(() => {
            resetScroll();
          });
          touchReleaseFrameRef.current = frameTwo;
        });
        return () => {
          window.cancelAnimationFrame(frameOne);
        };
      }
    }
    return undefined;
  }, [staticPhoneLandscape, viewportKind]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return undefined;
    }

    const dragBlockedNodes = viewport.querySelectorAll("a, img");
    dragBlockedNodes.forEach((node) => {
      node.setAttribute("draggable", "false");
    });

    function syncScrollState() {
      const leftOverflow = viewport.scrollLeft;
      const rightOverflow = Math.max(0, viewport.scrollWidth - viewport.clientWidth - viewport.scrollLeft);
      const nextCanScrollLeft = leftOverflow > EDGE_VISIBILITY_THRESHOLD;
      const nextCanScrollRight = rightOverflow > EDGE_VISIBILITY_THRESHOLD;
      const cueThreshold = viewportKind === "desktop" ? EDGE_CUE_LEAD_DESKTOP : EDGE_CUE_LEAD_MOBILE;
      const nearLeadingEdge = leftOverflow <= cueThreshold;
      const nearTrailingEdge = rightOverflow <= cueThreshold;
      setCanScrollLeft(nextCanScrollLeft);
      setCanScrollRight(nextCanScrollRight);
      setShowLeadingCue(peekEnabled && !nearLeadingEdge && nearTrailingEdge);
      setShowTrailingCue(peekEnabled && nearLeadingEdge && !nearTrailingEdge);
      suppressClickRef.current = true;
      if (typeof window !== "undefined") {
        if (scrollSuppressTimerRef.current) {
          window.clearTimeout(scrollSuppressTimerRef.current);
        }
        scrollSuppressTimerRef.current = window.setTimeout(() => {
          suppressClickRef.current = false;
          scrollSuppressTimerRef.current = 0;
        }, 140);
        if (
          enableTouchReleaseAssist
          && viewportKind !== "desktop"
          && !touchGestureStateRef.current.active
          && !isTouchReleasing
        ) {
          momentumActiveRef.current = true;
          setIsMomentumActive(true);
          if (momentumSettleTimerRef.current) {
            window.clearTimeout(momentumSettleTimerRef.current);
          }
          momentumSettleTimerRef.current = window.setTimeout(() => {
            momentumActiveRef.current = false;
            setIsMomentumActive(false);
            momentumSettleTimerRef.current = 0;
          }, TOUCH_MOMENTUM_SETTLE_MS);
        }
      }
    }

    syncScrollState();
    viewport.addEventListener("scroll", syncScrollState, { passive: true });
    const observer = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => {
          syncScrollState();
        })
      : null;
    observer?.observe(viewport);
    return () => {
      viewport.removeEventListener("scroll", syncScrollState);
      observer?.disconnect();
    };
  }, [enableTouchReleaseAssist, isTouchReleasing, peekEnabled, rail.items.length, viewportKind]);

  function clearMousePressTimer() {
    if (typeof window !== "undefined" && mousePressTimerRef.current) {
      window.clearTimeout(mousePressTimerRef.current);
      mousePressTimerRef.current = 0;
    }
  }

  function queueClickReset() {
    if (typeof window === "undefined") {
      suppressClickRef.current = false;
      return;
    }
    window.setTimeout(() => {
      suppressClickRef.current = false;
    }, 0);
  }

  function endMouseDrag(pointerId) {
    const viewport = viewportRef.current;
    if (viewport && pointerId !== undefined && viewport.hasPointerCapture?.(pointerId)) {
      viewport.releasePointerCapture(pointerId);
    }
    clearMousePressTimer();
    dragStateRef.current = null;
    longPressReadyRef.current = false;
    queueClickReset();
    setIsDragging(false);
  }

  function activateMouseDrag(viewport, pointerId) {
    longPressReadyRef.current = true;
    suppressClickRef.current = true;
    setIsDragging(true);
    if (pointerId !== undefined && !viewport.hasPointerCapture?.(pointerId)) {
      viewport.setPointerCapture?.(pointerId);
    }
  }

  function handlePointerDown(event) {
    if (staticPhoneLandscape) {
      return;
    }
    const viewport = viewportRef.current;
    if (!viewport) {
      return;
    }
    if (event.pointerType !== "mouse" || event.button !== 0) {
      return;
    }
    dragStateRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: viewport.scrollLeft,
    };
    suppressClickRef.current = false;
    longPressReadyRef.current = false;
    clearMousePressTimer();
    mousePressTimerRef.current = window.setTimeout(() => {
      const activeDragState = dragStateRef.current;
      if (!activeDragState || activeDragState.pointerId !== event.pointerId) {
        mousePressTimerRef.current = 0;
        return;
      }
      activateMouseDrag(viewport, event.pointerId);
      mousePressTimerRef.current = 0;
    }, MOUSE_LONG_PRESS_MS);
  }

  function handleDragStartCapture(event) {
    event.preventDefault();
  }

  function handlePointerMove(event) {
    if (staticPhoneLandscape) {
      return;
    }
    const dragState = dragStateRef.current;
    const viewport = viewportRef.current;
    if (!dragState || !viewport || dragState.pointerId !== event.pointerId) {
      return;
    }
    if (event.pointerType !== "mouse") {
      return;
    }
    const deltaX = event.clientX - dragState.startX;
    if (Math.abs(deltaX) > DESKTOP_DRAG_THRESHOLD_PX) {
      activateMouseDrag(viewport, event.pointerId);
      clearMousePressTimer();
    }
    if (longPressReadyRef.current) {
      setIsDragging(true);
      viewport.scrollLeft = dragState.startScrollLeft - (deltaX * DESKTOP_DRAG_RESPONSE);
      event.preventDefault();
    }
  }

  function handlePointerUp(event) {
    if (staticPhoneLandscape) {
      return;
    }
    if (event.pointerType !== "mouse" || dragStateRef.current?.pointerId !== event.pointerId) {
      return;
    }
    endMouseDrag(event.pointerId);
  }

  function handlePointerCancel(event) {
    if (staticPhoneLandscape) {
      return;
    }
    if (event.pointerType !== "mouse" || dragStateRef.current?.pointerId !== event.pointerId) {
      return;
    }
    endMouseDrag(event.pointerId);
  }

  function handleClickCapture(event) {
    if (staticPhoneLandscape) {
      return;
    }
    if (!suppressClickRef.current) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
  }

  function clearTouchReleaseAssist() {
    if (typeof window !== "undefined" && touchReleaseTimerRef.current) {
      window.clearTimeout(touchReleaseTimerRef.current);
      touchReleaseTimerRef.current = 0;
    }
    if (typeof window !== "undefined" && touchReleaseFrameRef.current) {
      window.cancelAnimationFrame(touchReleaseFrameRef.current);
      touchReleaseFrameRef.current = 0;
    }
    setIsTouchReleasing(false);
  }

  function clearMomentumActiveState() {
    if (typeof window !== "undefined" && momentumSettleTimerRef.current) {
      window.clearTimeout(momentumSettleTimerRef.current);
      momentumSettleTimerRef.current = 0;
    }
    momentumActiveRef.current = false;
    setIsMomentumActive(false);
  }

  function scheduleTouchReleaseAssist() {
    const viewport = viewportRef.current;
    if (!enableTouchReleaseAssist || !viewport || typeof window === "undefined") {
      return;
    }
    const rightOverflow = Math.max(0, viewport.scrollWidth - viewport.clientWidth - viewport.scrollLeft);
    const nearTouchEdge =
      viewport.scrollLeft <= TOUCH_EDGE_RELEASE_SKIP_THRESHOLD
      || rightOverflow <= TOUCH_EDGE_RELEASE_SKIP_THRESHOLD;
    if (nearTouchEdge) {
      clearTouchReleaseAssist();
      clearMomentumActiveState();
      return;
    }
    clearTouchReleaseAssist();
    setIsTouchReleasing(true);
    touchReleaseFrameRef.current = window.requestAnimationFrame(() => {
      viewport.scrollLeft = viewport.scrollLeft;
      touchReleaseFrameRef.current = 0;
      touchReleaseTimerRef.current = window.setTimeout(() => {
        setIsTouchReleasing(false);
        touchReleaseTimerRef.current = 0;
      }, 72);
    });
  }

  function handleTouchStart(event) {
    if (!enableTouchReleaseAssist || staticPhoneLandscape) {
      return;
    }
    clearTouchReleaseAssist();
    const touch = event.touches?.[0];
    if (!touch) {
      return;
    }
    const momentumGuard = momentumActiveRef.current;
    touchGestureStateRef.current = {
      active: true,
      startX: touch.clientX,
      startY: touch.clientY,
      horizontalIntent: false,
      momentumGuard,
      startScrollLeft: viewportRef.current?.scrollLeft || 0,
    };
  }

  function handleTouchMove(event) {
    if (!enableTouchReleaseAssist || staticPhoneLandscape) {
      return;
    }
    const gesture = touchGestureStateRef.current;
    if (!gesture.active || gesture.horizontalIntent) {
      return;
    }
    const touch = event.touches?.[0];
    if (!touch) {
      return;
    }
    const deltaX = touch.clientX - gesture.startX;
    const deltaY = touch.clientY - gesture.startY;
    if (
      gesture.momentumGuard
      && Math.abs(deltaY) >= TOUCH_VERTICAL_RECLAIM_THRESHOLD
      && Math.abs(deltaY) > Math.abs(deltaX) + TOUCH_AXIS_RELEASE_BIAS
    ) {
      clearMomentumActiveState();
      touchGestureStateRef.current = { ...INACTIVE_TOUCH_GESTURE_STATE };
      return;
    }
    if (
      Math.abs(deltaX) >= TOUCH_HORIZONTAL_RELEASE_THRESHOLD
      && Math.abs(deltaX) > Math.abs(deltaY) + TOUCH_AXIS_RELEASE_BIAS
    ) {
      gesture.horizontalIntent = true;
      if (gesture.momentumGuard) {
        clearMomentumActiveState();
      }
    }
    if (gesture.horizontalIntent && gesture.momentumGuard) {
      const viewport = viewportRef.current;
      if (!viewport) {
        return;
      }
      suppressClickRef.current = true;
      viewport.scrollLeft = gesture.startScrollLeft - deltaX;
      event.preventDefault();
    }
  }

  function handleTouchEnd() {
    if (!enableTouchReleaseAssist || staticPhoneLandscape) {
      return;
    }
    const gesture = touchGestureStateRef.current;
    if (gesture.active && gesture.horizontalIntent) {
      scheduleTouchReleaseAssist();
    }
    touchGestureStateRef.current = { ...INACTIVE_TOUCH_GESTURE_STATE };
  }

  return (
    <section className="content-section series-rail" data-series-rail-key={rail.key}>
      <div className="section-header section-header--compact series-rail__header">
        <div>
          <h2>{rail.title}</h2>
          <p className="series-rail__count">{rail.film_count} films</p>
        </div>
      </div>
      <div
        ref={viewportRef}
        className={[
          "series-rail__viewport",
          `series-rail__viewport--${viewportKind}`,
          staticPhoneLandscape ? "series-rail__viewport--static" : "",
          peekEnabled ? "series-rail__viewport--peek" : "",
          Number.isInteger(desktopSlots) && desktopSlots > 1 ? "series-rail__viewport--packed" : "",
          isDragging ? "series-rail__viewport--dragging" : "",
          isMomentumActive ? "series-rail__viewport--momentum-active" : "",
          isTouchReleasing ? "series-rail__viewport--touch-releasing" : "",
        ].filter(Boolean).join(" ")}
        data-can-scroll-left={canScrollLeft}
        data-can-scroll-right={canScrollRight}
        data-show-leading-cue={showLeadingCue}
        data-show-trailing-cue={showTrailingCue}
        onClickCapture={handleClickCapture}
        onDragStartCapture={handleDragStartCapture}
        onPointerCancel={handlePointerCancel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onTouchCancel={handleTouchEnd}
        onTouchEnd={handleTouchEnd}
        onTouchMove={handleTouchMove}
        onTouchStart={handleTouchStart}
        style={{
          "--series-rail-fade-left": showLeadingCue ? (viewportKind === "desktop" ? "2.1rem" : "1.65rem") : "0px",
          "--series-rail-fade-right": showTrailingCue ? (viewportKind === "desktop" ? "2.1rem" : "1.65rem") : "0px",
          ...(Number.isInteger(desktopSlots) && desktopSlots > 1
            ? {
                "--series-rail-desktop-static-columns": String(desktopSlots),
              }
            : {}),
          ...(staticPhoneLandscape
            ? {
                "--series-rail-static-columns": String(Math.max(1, rail.items.length)),
              }
            : {}),
        }}
      >
        <div className="series-rail__track">
          {rail.items.map((item) => (
            <div className="series-rail__slide" key={item.id}>
              <MediaCard
                backgroundPlaybackActive={activeBrowserPlaybackItemId === item.id}
                cardInstanceKey={`${sectionKey || `series:${rail.key}`}:${item.id}`}
                item={item}
                smartPosterLoadingEnabled={smartPosterLoadingEnabled}
              />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
