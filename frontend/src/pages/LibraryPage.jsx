import { startTransition, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useProviderAuth } from "../auth/ProviderAuthContext";
import { EmptyState } from "../components/EmptyState";
import { FloatingLibrarySearch } from "../components/FloatingLibrarySearch";
import { LoadingView } from "../components/LoadingView";
import { MediaCard } from "../components/MediaCard";
import { RefreshSweepButton } from "../components/RefreshSweepButton";
import { SeriesRail } from "../components/SeriesRail";
import { apiRequest } from "../lib/api";
import {
  getProviderAuthPassiveNoticeMessage,
  shouldUseProviderAuthPassiveNotice,
} from "../lib/providerAuth";
import { useActiveBrowserPlaybackItemId } from "../lib/browserPlayback";
import {
  formatCompletedRescanWarning,
  formatRescanBannerText,
  hasCloudSyncWarning,
} from "../lib/cloudSyncStatus";
import {
  clearLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation";
import { detectClientDeviceClass, detectClientPlatform } from "../lib/platformDetection";
import {
  packIpadPortraitSeriesRailRows,
  packSeriesRailRows,
} from "../lib/seriesRails";
import { getSmartPosterOrientation } from "../lib/smartPosterLoading";
import {
  canUpdateStableViewportAnchor,
  captureCenterMovieAnchor,
  captureViewportAnchorCandidates,
  computeAnchorRestoreScrollTop,
  computeRestoreVerificationCorrection,
  formatViewportAnchorDebug,
  formatViewportAnchorCandidateListDebug,
  getLayoutViewportMeasurement,
  getOrientationRestoreRefinementDelayMs,
  getRestoreViewportMeasurement,
  isLibraryOrientationRestorePlatform,
  isRestoreAttemptStale,
  isUserRestoreCancellationEvent,
  isVisualViewportZoomed,
  MAX_ORIENTATION_RESTORE_CORRECTIONS,
  requestTemporaryViewportScaleReset,
  resolveStableOrientationAnchor,
  restoreHorizontalRailPosition,
  selectLibraryReturnRestoreTarget,
  selectPreferredOrientationRestoreTarget,
  shouldRecoverZoomedLibraryRotation,
  shouldLogViewportAnchorDebug,
} from "../lib/viewportAnchor";


export const LIBRARY_CATEGORY_OPTIONS = [
  { key: "movies", label: "Movies", otherHeading: "Other Movies" },
  { key: "tv", label: "TV Shows", otherHeading: "Other TV Shows" },
  { key: "anime", label: "Anime", otherHeading: "Other Anime" },
  { key: "cartoon", label: "Cartoon", otherHeading: "Other Cartoon" },
];

const DEFAULT_LIBRARY_CATEGORY = "movies";
const LIBRARY_CATEGORY_KEYS = new Set(LIBRARY_CATEGORY_OPTIONS.map((category) => category.key));
const LIBRARY_SOURCE_OPTIONS = [
  { key: "all", label: "All" },
  { key: "local", label: "Local" },
  { key: "cloud", label: "Cloud" },
];
const LIBRARY_QUALITY_OPTIONS = [
  { key: "all", label: "All quality", activeLabel: "" },
  { key: "diamond", label: "Diamond", activeLabel: "Diamond" },
  { key: "gold", label: "Gold", activeLabel: "Gold" },
  { key: "silver", label: "Silver", activeLabel: "Silver" },
  { key: "iron", label: "Iron", activeLabel: "Iron" },
  { key: "bronze", label: "Bronze", activeLabel: "Bronze" },
  { key: "wood", label: "Wood", activeLabel: "Wood" },
];
const LIBRARY_SORT_OPTIONS = [
  { key: "smart", label: "Smart Default", activeLabel: "" },
  { key: "az", label: "A → Z", activeLabel: "A → Z" },
  { key: "za", label: "Z → A", activeLabel: "Z → A" },
  { key: "recent_desc", label: "Recently added: newest first", activeLabel: "Recently added" },
  { key: "recent_asc", label: "Recently added: oldest first", activeLabel: "Oldest added" },
  { key: "year_desc", label: "Release year: newest first", activeLabel: "Newest year" },
  { key: "year_asc", label: "Release year: oldest first", activeLabel: "Oldest year" },
  { key: "size_desc", label: "File size: largest first", activeLabel: "Largest" },
  { key: "size_asc", label: "File size: smallest first", activeLabel: "Smallest" },
];
const LIBRARY_SOURCE_KEYS = new Set(LIBRARY_SOURCE_OPTIONS.map((option) => option.key));
const LIBRARY_QUALITY_KEYS = new Set(LIBRARY_QUALITY_OPTIONS.map((option) => option.key));
const LIBRARY_SORT_KEYS = new Set(LIBRARY_SORT_OPTIONS.map((option) => option.key));
const DEFAULT_LIBRARY_ARRANGE = {
  source: "all",
  genre: "",
  quality: "all",
  sort: "smart",
};
const SCROLLABLE_ARRANGE_DEVICE_CLASSES = new Set(["phone", "tablet"]);


export function resolveLibraryCategoryFromSearch(search = "") {
  const params = new URLSearchParams(search);
  const category = String(params.get("category") || "").trim().toLowerCase();
  return LIBRARY_CATEGORY_KEYS.has(category) ? category : DEFAULT_LIBRARY_CATEGORY;
}


export function resolveLibraryArrangeFromSearch(search = "") {
  const params = new URLSearchParams(search);
  const source = String(params.get("source") || "").trim().toLowerCase();
  const quality = String(params.get("quality") || "").trim().toLowerCase();
  const sort = String(params.get("sort") || "").trim().toLowerCase();
  const genre = String(params.get("genre") || "").trim();
  return {
    source: LIBRARY_SOURCE_KEYS.has(source) ? source : DEFAULT_LIBRARY_ARRANGE.source,
    genre,
    quality: LIBRARY_QUALITY_KEYS.has(quality) ? quality : DEFAULT_LIBRARY_ARRANGE.quality,
    sort: LIBRARY_SORT_KEYS.has(sort) ? sort : DEFAULT_LIBRARY_ARRANGE.sort,
  };
}


function applyLibraryArrangeParams(params, arrange = DEFAULT_LIBRARY_ARRANGE) {
  if (arrange.source && arrange.source !== DEFAULT_LIBRARY_ARRANGE.source) {
    params.set("source", arrange.source);
  } else {
    params.delete("source");
  }
  if (arrange.genre) {
    params.set("genre", arrange.genre);
  } else {
    params.delete("genre");
  }
  if (arrange.quality && arrange.quality !== DEFAULT_LIBRARY_ARRANGE.quality) {
    params.set("quality", arrange.quality);
  } else {
    params.delete("quality");
  }
  if (arrange.sort && arrange.sort !== DEFAULT_LIBRARY_ARRANGE.sort) {
    params.set("sort", arrange.sort);
  } else {
    params.delete("sort");
  }
}


export function buildLibraryCategorySearch(currentSearch = "", category = DEFAULT_LIBRARY_CATEGORY) {
  const params = new URLSearchParams(currentSearch);
  params.set("category", LIBRARY_CATEGORY_KEYS.has(category) ? category : DEFAULT_LIBRARY_CATEGORY);
  const nextSearch = params.toString();
  return nextSearch ? `?${nextSearch}` : "";
}


export function buildLibraryArrangeSearch(currentSearch = "", arrange = DEFAULT_LIBRARY_ARRANGE) {
  const params = new URLSearchParams(currentSearch);
  applyLibraryArrangeParams(params, arrange);
  const nextSearch = params.toString();
  return nextSearch ? `?${nextSearch}` : "";
}


export function buildLibraryRequestPath({ category = DEFAULT_LIBRARY_CATEGORY, query = "", arrange = DEFAULT_LIBRARY_ARRANGE } = {}) {
  const normalizedCategory = LIBRARY_CATEGORY_KEYS.has(category) ? category : DEFAULT_LIBRARY_CATEGORY;
  const trimmedQuery = query.trim();
  const params = new URLSearchParams();
  if (trimmedQuery) {
    params.set("q", trimmedQuery);
  }
  params.set("category", normalizedCategory);
  applyLibraryArrangeParams(params, arrange);
  return trimmedQuery
    ? `/api/library/search?${params.toString()}`
    : `/api/library?${params.toString()}`;
}


function LibraryCategorySwitch({ activeCategory, dragEnabled = false, onChange }) {
  const controlRef = useRef(null);
  const draggingRef = useRef(false);
  const ignoreNextClickRef = useRef(false);
  const dragBoundsRef = useRef({ clientX: 0, min: 0, max: 0 });
  const [dragging, setDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const [dragPreviewCategory, setDragPreviewCategory] = useState(null);

  function getCategoryFromPoint(clientX) {
    const rect = controlRef.current?.getBoundingClientRect();
    if (!rect) {
      return activeCategory;
    }
    const ratio = Math.max(0, Math.min(0.999, (clientX - rect.left) / (rect.width || 1)));
    const index = Math.max(0, Math.min(LIBRARY_CATEGORY_OPTIONS.length - 1, Math.floor(ratio * LIBRARY_CATEGORY_OPTIONS.length)));
    return LIBRARY_CATEGORY_OPTIONS[index]?.key || activeCategory;
  }

  function handleActivePointerDown(event) {
    if (!dragEnabled) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const controlRect = controlRef.current?.getBoundingClientRect();
    const buttonRect = event.currentTarget.getBoundingClientRect();
    dragBoundsRef.current = {
      clientX: event.clientX,
      min: controlRect ? controlRect.left - buttonRect.left : 0,
      max: controlRect ? controlRect.right - buttonRect.right : 0,
    };
    draggingRef.current = true;
    ignoreNextClickRef.current = true;
    setDragOffset(0);
    setDragPreviewCategory(activeCategory);
    setDragging(true);
  }

  function handleActivePointerMove(event) {
    if (!draggingRef.current) {
      return;
    }
    const bounds = dragBoundsRef.current;
    const nextOffset = Math.max(bounds.min, Math.min(bounds.max, event.clientX - bounds.clientX));
    setDragOffset(nextOffset);
    setDragPreviewCategory(getCategoryFromPoint(event.clientX));
  }

  function handleActivePointerUp(event) {
    if (!draggingRef.current) {
      return;
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    draggingRef.current = false;
    setDragging(false);
    setDragOffset(0);
    const nextCategory = getCategoryFromPoint(event.clientX);
    setDragPreviewCategory(null);
    onChange(nextCategory);
    window.setTimeout(() => {
      ignoreNextClickRef.current = false;
    }, 120);
  }

  function handleActivePointerCancel(event) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    draggingRef.current = false;
    ignoreNextClickRef.current = false;
    setDragging(false);
    setDragOffset(0);
    setDragPreviewCategory(null);
  }

  const activeIndex = Math.max(0, LIBRARY_CATEGORY_OPTIONS.findIndex((category) => category.key === activeCategory));
  const visualCategory = dragging && dragPreviewCategory ? dragPreviewCategory : activeCategory;
  const visualIndex = Math.max(0, LIBRARY_CATEGORY_OPTIONS.findIndex((category) => category.key === visualCategory));
  const indicatorIndex = dragging ? activeIndex : visualIndex;
  const controlStyle = {
    "--library-category-count": LIBRARY_CATEGORY_OPTIONS.length,
    "--library-category-index": indicatorIndex,
    "--library-category-drag-x": dragging ? `${dragOffset}px` : "0px",
  };

  return (
    <div
      aria-label="Library category"
      className={[
        "library-category-switch",
        dragEnabled ? "library-category-switch--drag-enabled" : "",
        dragging ? "library-category-switch--dragging" : "",
      ].filter(Boolean).join(" ")}
      ref={controlRef}
      role="tablist"
      style={controlStyle}
    >
      <span
        aria-hidden="true"
        className={[
          "library-category-switch__indicator",
          dragging ? "library-category-switch__indicator--dragging" : "",
        ].filter(Boolean).join(" ")}
      />
      {LIBRARY_CATEGORY_OPTIONS.map((category) => {
        const active = category.key === activeCategory;
        const visuallyActive = dragging ? category.key === visualCategory : active;
        return (
          <button
            aria-selected={active}
            className={[
              "library-category-switch__button",
              active ? "library-category-switch__button--current" : "",
              visuallyActive ? "library-category-switch__button--active" : "",
              active && dragging ? "library-category-switch__button--dragging" : "",
            ].filter(Boolean).join(" ")}
            key={category.key}
            onClick={(event) => {
              if (ignoreNextClickRef.current) {
                event.preventDefault();
                ignoreNextClickRef.current = false;
                return;
              }
              onChange(category.key);
            }}
            onPointerCancel={active && dragEnabled ? handleActivePointerCancel : undefined}
            onPointerDown={active && dragEnabled ? handleActivePointerDown : undefined}
            onPointerMove={active && dragEnabled ? handleActivePointerMove : undefined}
            onPointerUp={active && dragEnabled ? handleActivePointerUp : undefined}
            role="tab"
            type="button"
          >
            {category.label}
          </button>
        );
      })}
    </div>
  );
}


function getOptionLabel(options, key, fallback = "") {
  return options.find((option) => option.key === key)?.label || fallback;
}


function getOptionActiveLabel(options, key, fallback = "") {
  const option = options.find((entry) => entry.key === key);
  return option?.activeLabel || option?.label || fallback;
}


function getArrangeActiveLabel(arrange) {
  if (arrange.sort !== DEFAULT_LIBRARY_ARRANGE.sort) {
    return getOptionActiveLabel(LIBRARY_SORT_OPTIONS, arrange.sort);
  }
  if (arrange.genre) {
    return arrange.genre;
  }
  if (arrange.quality !== DEFAULT_LIBRARY_ARRANGE.quality) {
    return getOptionActiveLabel(LIBRARY_QUALITY_OPTIONS, arrange.quality);
  }
  if (arrange.source !== DEFAULT_LIBRARY_ARRANGE.source) {
    return getOptionLabel(LIBRARY_SOURCE_OPTIONS, arrange.source);
  }
  return "";
}


function getFloatingSearchViewportOrientation() {
  if (typeof window === "undefined") {
    return "portrait";
  }
  const visualViewport = window.visualViewport || null;
  const width = visualViewport?.width
    || window.innerWidth
    || (typeof document !== "undefined" ? document.documentElement.clientWidth : 0);
  const height = visualViewport?.height
    || window.innerHeight
    || (typeof document !== "undefined" ? document.documentElement.clientHeight : 0);
  return width > height ? "landscape" : "portrait";
}


function getFloatingSearchHeroRightGutter(heroElement) {
  if (typeof window === "undefined" || !heroElement) {
    return null;
  }
  const visualViewport = window.visualViewport || null;
  const viewportWidth = visualViewport?.width
    || window.innerWidth
    || (typeof document !== "undefined" ? document.documentElement.clientWidth : 0);
  if (!viewportWidth) {
    return null;
  }
  return `${Math.max(0, Math.round(viewportWidth - heroElement.getBoundingClientRect().right))}px`;
}


function ArrangeSection({ title, children }) {
  return (
    <section className="library-arrange__section">
      <h3>{title}</h3>
      <div className="library-arrange__option-grid">
        {children}
      </div>
    </section>
  );
}


function ArrangeOption({ active = false, label, onClick }) {
  return (
    <button
      aria-pressed={active}
      className={[
        "library-arrange__option",
        active ? "library-arrange__option--active" : "",
      ].filter(Boolean).join(" ")}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}


function LibraryArrangeControl({
  arrange,
  availableGenres = [],
  panelMode = "desktop",
  panelSize = "",
  onChange,
}) {
  const [open, setOpen] = useState(false);
  const controlRef = useRef(null);
  const activeLabel = getArrangeActiveLabel(arrange);
  const scrollablePanel = panelMode === "scrollable";

  useEffect(() => {
    if (!open || typeof document === "undefined") {
      return undefined;
    }
    function handlePointerDown(event) {
      if (!controlRef.current?.contains(event.target)) {
        setOpen(false);
      }
    }
    function handleKeyDown(event) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  function updateArrange(nextValues) {
    onChange({
      ...arrange,
      ...nextValues,
    });
  }

  return (
    <div className="library-arrange" ref={controlRef}>
      <button
        aria-expanded={open}
        aria-label="Arrange library"
        className={[
          "library-arrange__trigger",
          activeLabel ? "library-arrange__trigger--active" : "",
        ].filter(Boolean).join(" ")}
        onClick={() => setOpen((current) => !current)}
        type="button"
      >
        <SlidersHorizontal aria-hidden="true" className="library-arrange__icon" />
        {activeLabel ? <span className="library-arrange__active-label">{activeLabel}</span> : null}
      </button>
      {open ? (
        <div
          aria-label="Arrange library"
          className={[
            "library-arrange__panel",
            scrollablePanel ? "library-arrange__panel--scrollable" : "library-arrange__panel--desktop",
            scrollablePanel && panelSize ? `library-arrange__panel--${panelSize}` : "",
          ].filter(Boolean).join(" ")}
          role="dialog"
        >
          {scrollablePanel ? (
            <span aria-hidden="true" className="library-arrange__mobile-handle" />
          ) : null}
          <ArrangeSection title="Source">
            {LIBRARY_SOURCE_OPTIONS.map((option) => (
              <ArrangeOption
                active={arrange.source === option.key}
                key={option.key}
                label={option.label}
                onClick={() => updateArrange({ source: option.key })}
              />
            ))}
          </ArrangeSection>
          <ArrangeSection title="Genre">
            <ArrangeOption
              active={!arrange.genre}
              label="All genres"
              onClick={() => updateArrange({ genre: "" })}
            />
            {availableGenres.map((genre) => (
              <ArrangeOption
                active={arrange.genre.toLowerCase() === genre.toLowerCase()}
                key={genre}
                label={genre}
                onClick={() => updateArrange({ genre })}
              />
            ))}
          </ArrangeSection>
          <ArrangeSection title="Quality">
            {LIBRARY_QUALITY_OPTIONS.map((option) => (
              <ArrangeOption
                active={arrange.quality === option.key}
                key={option.key}
                label={option.label}
                onClick={() => updateArrange({ quality: option.key })}
              />
            ))}
          </ArrangeSection>
          <ArrangeSection title="Sort">
            {LIBRARY_SORT_OPTIONS.map((option) => (
              <ArrangeOption
                active={arrange.sort === option.key}
                key={option.key}
                label={option.label}
                onClick={() => updateArrange({ sort: option.key })}
              />
            ))}
          </ArrangeSection>
        </div>
      ) : null}
    </div>
  );
}


function MediaGrid({
  items,
  activeBrowserPlaybackItemId = null,
  smartPosterLoadingEnabled = false,
  sectionKey = "library",
}) {
  return (
    <div className="media-grid">
      {items.map((item) => (
        <MediaCard
          backgroundPlaybackActive={activeBrowserPlaybackItemId === item.id}
          cardInstanceKey={`${sectionKey}:${item.id}`}
          item={item}
          key={item.id}
          smartPosterLoadingEnabled={smartPosterLoadingEnabled}
        />
      ))}
    </div>
  );
}

function isIpadPortraitLibraryViewport() {
  if (typeof window === "undefined") {
    return false;
  }
  return detectClientPlatform() === "ipad"
    && window.matchMedia("(min-width: 740px)").matches;
}

function useIpadPortraitLibraryLayout() {
  const [enabled, setEnabled] = useState(() => isIpadPortraitLibraryViewport());

  useEffect(() => {
    function updateLayoutMode() {
      setEnabled(isIpadPortraitLibraryViewport());
    }

    updateLayoutMode();
    window.addEventListener("resize", updateLayoutMode);
    window.addEventListener("orientationchange", updateLayoutMode);
    return () => {
      window.removeEventListener("resize", updateLayoutMode);
      window.removeEventListener("orientationchange", updateLayoutMode);
    };
  }, []);

  return enabled;
}

export function LibraryPage() {
  const { refreshAuth } = useAuth();
  const {
    providerAuthRequirement,
    providerAuthDismissedThisSession,
    providerAuthReconnectPending,
    refreshProviderAuthStatus,
    startProviderReconnect,
  } = useProviderAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const activeLibraryCategory = useMemo(
    () => resolveLibraryCategoryFromSearch(location.search),
    [location.search],
  );
  const activeLibraryArrange = useMemo(
    () => resolveLibraryArrangeFromSearch(location.search),
    [location.search],
  );
  const activeLibraryCategoryConfig = useMemo(
    () => LIBRARY_CATEGORY_OPTIONS.find((category) => category.key === activeLibraryCategory) || LIBRARY_CATEGORY_OPTIONS[0],
    [activeLibraryCategory],
  );
  const currentLibraryListPath = useMemo(
    () => `${location.pathname}${location.search || ""}`,
    [location.pathname, location.search],
  );
  const activeBrowserPlaybackItemId = useActiveBrowserPlaybackItemId();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [settings, setSettings] = useState({
    hide_duplicate_movies: true,
    hide_recently_added: false,
    floating_library_search_enabled: true,
  });
  const [loading, setLoading] = useState(true);
  const [rescanPending, setRescanPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [library, setLibrary] = useState({
    items: [],
    series_rails: [],
    cloud_series_rails: [],
    continue_watching: [],
    recently_added: [],
    arrange: DEFAULT_LIBRARY_ARRANGE,
    available_genres: [],
    total_items: 0,
    scan_in_progress: false,
  });
  const cloudSyncWarningRef = useRef("");
  const scanRunningRef = useRef(false);
  const orientationAnchorsRef = useRef([]);
  const latestCenterMovieAnchorRef = useRef(null);
  const lastStableLibraryAnchorRef = useRef(null);
  const pendingOrientationAnchorRef = useRef(null);
  const orientationRef = useRef(null);
  const orientationLastMeasurementRef = useRef(null);
  const orientationRestoreTimerRef = useRef(0);
  const orientationRestoreFrameOneRef = useRef(0);
  const orientationRestoreFrameTwoRef = useRef(0);
  const orientationRestoreRefineTimerRef = useRef(0);
  const orientationSampleFrameRef = useRef(0);
  const orientationRestoreLockRef = useRef(false);
  const orientationViewportChangeActiveRef = useRef(false);
  const orientationRestoreTokenRef = useRef(0);
  const orientationRestoreCorrectionCountRef = useRef(0);
  const orientationUserIntentVersionRef = useRef(0);
  const orientationSamplerRef = useRef(() => {});
  const orientationDebugLogAtRef = useRef(0);
  const librarySectionRef = useRef(null);
  const libraryHeroRef = useRef(null);
  const desktopSearchInputRef = useRef(null);
  const mobileSearchInputRef = useRef(null);
  const floatingSearchScrollYRef = useRef(null);
  const pendingFloatingSearchRestoreYRef = useRef(null);
  const libraryReturnRestoreKeyRef = useRef("");
  const [floatingSearchViewportOrientation, setFloatingSearchViewportOrientation] = useState(() => getFloatingSearchViewportOrientation());
  const useIpadPortraitSeriesPacking = useIpadPortraitLibraryLayout();
  const clientPlatform = detectClientPlatform();
  const clientDeviceClass = detectClientDeviceClass();
  const libraryDevice = clientPlatform === "ipad" ? "ipad" : undefined;
  const libraryDeviceClass = SCROLLABLE_ARRANGE_DEVICE_CLASSES.has(clientDeviceClass)
    ? clientDeviceClass
    : undefined;
  const arrangePanelMode = SCROLLABLE_ARRANGE_DEVICE_CLASSES.has(clientDeviceClass) ? "scrollable" : "desktop";
  const arrangePanelSize = SCROLLABLE_ARRANGE_DEVICE_CLASSES.has(clientDeviceClass) ? clientDeviceClass : "";
  const heroAlignedFloatingSearch = clientDeviceClass === "tablet"
    || (clientDeviceClass === "phone" && floatingSearchViewportOrientation === "portrait");
  const floatingSearchDesktopMode = clientDeviceClass === "desktop" && clientPlatform !== "ipad";
  const categorySwitchDragEnabled = clientDeviceClass === "desktop";
  const floatingSearchScrollRestoreEnabled = ["desktop", "phone", "tablet"].includes(clientDeviceClass);
  const isPhoneClient = useMemo(() => {
    if (typeof navigator === "undefined") {
      return false;
    }
    const userAgent = navigator.userAgent || "";
    return /iphone|ipod|android.+mobile|windows phone/i.test(userAgent);
  }, []);
  const continueWatchingLimit = 6;
  const continueWatchingItems = useMemo(
    () => library.continue_watching.map((item) => {
      if (
        !isPhoneClient
        || (item.source_kind || "local") !== "cloud"
        || !item.progress_seconds
        || item.progress_duration_seconds
        || !item.duration_seconds
      ) {
        return item;
      }
      return {
        ...item,
        progress_duration_seconds: item.duration_seconds,
      };
    }),
    [isPhoneClient, library.continue_watching],
  );
  const visibleContinueWatchingItems = useMemo(
    () => continueWatchingItems.slice(0, continueWatchingLimit),
    [continueWatchingItems, continueWatchingLimit],
  );
  const showContinueWatchingSection = visibleContinueWatchingItems.length > 0;
  const visibleSeriesRails = useMemo(
    () => [
      ...(library.series_rails || []),
      ...(library.cloud_series_rails || []),
    ],
    [library.cloud_series_rails, library.series_rails],
  );
  const seriesRailItemIds = useMemo(
    () => new Set(
      visibleSeriesRails.flatMap((rail) => (rail.items || []).map((item) => item.id)),
    ),
    [visibleSeriesRails],
  );
  const visibleLibraryGridItems = useMemo(
    () => library.items.filter((item) => !seriesRailItemIds.has(item.id)),
    [library.items, seriesRailItemIds],
  );
  const packedSeriesRailRows = useMemo(
    () => (useIpadPortraitSeriesPacking
      ? packIpadPortraitSeriesRailRows(visibleSeriesRails)
      : packSeriesRailRows(visibleSeriesRails)),
    [useIpadPortraitSeriesPacking, visibleSeriesRails],
  );
  const cloudReconnectPrompt = useMemo(() => {
    if (!providerAuthRequirement) {
      return null;
    }
    if (shouldUseProviderAuthPassiveNotice(providerAuthRequirement)) {
      return null;
    }
    return {
      title: providerAuthRequirement.title,
      message: providerAuthRequirement.message,
      allowReconnect: providerAuthRequirement.allowReconnect !== false,
      requiresAdmin: providerAuthRequirement.requiresAdmin === true,
    };
  }, [providerAuthRequirement]);
  const providerAuthPassiveNotice = useMemo(
    () => getProviderAuthPassiveNoticeMessage(providerAuthRequirement),
    [providerAuthRequirement],
  );

  function scheduleFloatingSearchScrollRestore() {
    if (
      typeof window === "undefined"
      || !floatingSearchScrollRestoreEnabled
      || pendingFloatingSearchRestoreYRef.current === null
    ) {
      return;
    }
    const restoreY = pendingFloatingSearchRestoreYRef.current;
    pendingFloatingSearchRestoreYRef.current = null;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: restoreY, behavior: "auto" });
      });
    });
  }

  async function loadLibrary({ signal, silent = false } = {}) {
    if (!silent) {
      startTransition(() => {
        setLoading(true);
      });
    }
    setError("");
    try {
      const target = buildLibraryRequestPath({
        category: activeLibraryCategory,
        query: deferredQuery,
        arrange: activeLibraryArrange,
      });
      const payload = await apiRequest(target, { signal });
      if (scanRunningRef.current && !payload.scan_in_progress) {
        if (cloudSyncWarningRef.current) {
          setError(formatCompletedRescanWarning(cloudSyncWarningRef.current));
          setNotice("");
        } else {
          setNotice("Library scan completed.");
        }
      }
      scanRunningRef.current = Boolean(payload.scan_in_progress);
      setLibrary(payload);
      if (!deferredQuery.trim()) {
        scheduleFloatingSearchScrollRestore();
      }
    } catch (requestError) {
      if (requestError.name === "AbortError") {
        return;
      }
      if (requestError.status === 401) {
        await refreshAuth();
        return;
      }
      setError(requestError.message || "Failed to load library");
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }

  async function loadLibrarySettings({ signal } = {}) {
    try {
      const payload = await apiRequest("/api/user-settings", { signal });
      setSettings(payload);
    } catch (requestError) {
      if (requestError.name === "AbortError") {
        return;
      }
      if (requestError.status === 401) {
        await refreshAuth();
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    loadLibrarySettings({ signal: controller.signal });
    loadLibrary({ signal: controller.signal });
    return () => {
      controller.abort();
    };
  }, [activeLibraryArrange, activeLibraryCategory, deferredQuery]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }
    const sectionElement = librarySectionRef.current;
    const heroElement = libraryHeroRef.current;
    const visualViewport = window.visualViewport || null;

    function updateFloatingSearchAlignment() {
      setFloatingSearchViewportOrientation((current) => {
        const nextOrientation = getFloatingSearchViewportOrientation();
        return current === nextOrientation ? current : nextOrientation;
      });
      const heroRightGutter = getFloatingSearchHeroRightGutter(heroElement);
      if (sectionElement && heroRightGutter) {
        sectionElement.style.setProperty("--library-hero-right-gutter", heroRightGutter);
      }
    }

    updateFloatingSearchAlignment();
    const resizeObserver = typeof ResizeObserver !== "undefined" && heroElement
      ? new ResizeObserver(updateFloatingSearchAlignment)
      : null;
    resizeObserver?.observe(heroElement);
    window.addEventListener("resize", updateFloatingSearchAlignment);
    window.addEventListener("orientationchange", updateFloatingSearchAlignment);
    visualViewport?.addEventListener("resize", updateFloatingSearchAlignment);
    visualViewport?.addEventListener("scroll", updateFloatingSearchAlignment);
    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener("resize", updateFloatingSearchAlignment);
      window.removeEventListener("orientationchange", updateFloatingSearchAlignment);
      visualViewport?.removeEventListener("resize", updateFloatingSearchAlignment);
      visualViewport?.removeEventListener("scroll", updateFloatingSearchAlignment);
    };
  }, [clientDeviceClass]);

  useEffect(() => {
    if (loading || deferredQuery.trim() || !activeLibraryArrange.genre) {
      return;
    }
    const availableGenreKeys = new Set(
      (library.available_genres || []).map((genre) => String(genre).toLowerCase()),
    );
    if (availableGenreKeys.has(activeLibraryArrange.genre.toLowerCase())) {
      return;
    }
    const nextArrange = {
      ...activeLibraryArrange,
      genre: "",
    };
    navigate(
      {
        pathname: location.pathname,
        search: buildLibraryArrangeSearch(location.search, nextArrange),
        hash: location.hash,
      },
      { replace: true },
    );
  }, [activeLibraryArrange, deferredQuery, library.available_genres, loading, location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const statusValue = params.get("googleDriveStatus");
    const statusMessage = params.get("googleDriveMessage");
    if (!statusValue && !statusMessage) {
      return;
    }
    if (statusValue === "connected") {
      setNotice(statusMessage || "Google Drive connected.");
      setError("");
      void refreshProviderAuthStatus();
    } else {
      setError(statusMessage || "Google Drive reconnect failed.");
      setNotice("");
    }
    const nextParams = new URLSearchParams(location.search);
    nextParams.delete("googleDriveStatus");
    nextParams.delete("googleDriveMessage");
    navigate(
      {
        pathname: location.pathname,
        search: nextParams.toString() ? `?${nextParams.toString()}` : "",
        hash: location.hash,
      },
      { replace: true },
    );
  }, [location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (!library.scan_in_progress) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      loadLibrary({ silent: true });
    }, 2500);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [activeLibraryArrange, activeLibraryCategory, library.scan_in_progress, deferredQuery]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    const orientationPlatform = detectClientPlatform();
    if (!isLibraryOrientationRestorePlatform(orientationPlatform)) {
      return undefined;
    }
    const visualViewport = window.visualViewport || null;
    const MAJOR_VIEWPORT_CHANGE_PX = 140;

    function logOrientationAnchorDebug(message, details = {}) {
      if (!shouldLogViewportAnchorDebug()) {
        return;
      }
      const now = typeof performance !== "undefined" && typeof performance.now === "function"
        ? performance.now()
        : Date.now();
      if ((now - orientationDebugLogAtRef.current) < 1000) {
        return;
      }
      orientationDebugLogAtRef.current = now;
      console.info("[orientation-anchor]", {
        message,
        ...details,
      });
    }

    function readMeasurement() {
      return getLayoutViewportMeasurement({ viewportWindow: window });
    }

    function readOrientation(measurement = readMeasurement()) {
      return getSmartPosterOrientation({
        width: measurement.width,
        height: measurement.height,
      });
    }

    function clearPendingOrientationRestore(includeSampleFrame = false) {
      if (orientationRestoreTimerRef.current) {
        window.clearTimeout(orientationRestoreTimerRef.current);
        orientationRestoreTimerRef.current = 0;
      }
      if (orientationRestoreFrameOneRef.current) {
        window.cancelAnimationFrame(orientationRestoreFrameOneRef.current);
        orientationRestoreFrameOneRef.current = 0;
      }
      if (orientationRestoreFrameTwoRef.current) {
        window.cancelAnimationFrame(orientationRestoreFrameTwoRef.current);
        orientationRestoreFrameTwoRef.current = 0;
      }
      if (orientationRestoreRefineTimerRef.current) {
        window.clearTimeout(orientationRestoreRefineTimerRef.current);
        orientationRestoreRefineTimerRef.current = 0;
      }
      if (includeSampleFrame && orientationSampleFrameRef.current) {
        window.cancelAnimationFrame(orientationSampleFrameRef.current);
        orientationSampleFrameRef.current = 0;
      }
    }

    function isMajorViewportChange(nextMeasurement) {
      const previousMeasurement = orientationLastMeasurementRef.current;
      if (!previousMeasurement) {
        return false;
      }
      return (
        Math.abs(nextMeasurement.width - previousMeasurement.width) >= MAJOR_VIEWPORT_CHANGE_PX
        || Math.abs(nextMeasurement.height - previousMeasurement.height) >= MAJOR_VIEWPORT_CHANGE_PX
      );
    }

    function sampleLatestCenterMovieAnchor(reason = "sample") {
      if (orientationRestoreLockRef.current || orientationViewportChangeActiveRef.current) {
        return latestCenterMovieAnchorRef.current;
      }
      const measurement = readMeasurement();
      orientationLastMeasurementRef.current = measurement;
      if (!canUpdateStableViewportAnchor({
        platform: orientationPlatform,
        restoreInProgress: orientationRestoreLockRef.current || orientationViewportChangeActiveRef.current,
        viewportWindow: window,
      })) {
        return latestCenterMovieAnchorRef.current;
      }
      const nextAnchor = captureCenterMovieAnchor({
        doc: document,
        viewportWindow: window,
        orientation: readOrientation(measurement),
      });
      if (nextAnchor?.itemId) {
        latestCenterMovieAnchorRef.current = nextAnchor;
        lastStableLibraryAnchorRef.current = nextAnchor;
        logOrientationAnchorDebug("latest center movie anchor updated", {
          reason,
          latestCenterMovieItemId: nextAnchor.itemId,
        });
      }
      return nextAnchor || latestCenterMovieAnchorRef.current;
    }

    function scheduleCenterMovieAnchorSample({ reason = "sample", immediate = false } = {}) {
      if (immediate) {
        clearPendingOrientationRestore(true);
        return sampleLatestCenterMovieAnchor(reason);
      }
      if (
        orientationSampleFrameRef.current
        || orientationRestoreLockRef.current
        || orientationViewportChangeActiveRef.current
      ) {
        return latestCenterMovieAnchorRef.current;
      }
      orientationSampleFrameRef.current = window.requestAnimationFrame(() => {
        orientationSampleFrameRef.current = 0;
        sampleLatestCenterMovieAnchor(reason);
      });
      return latestCenterMovieAnchorRef.current;
    }

    function freezePendingOrientationAnchor(reason = "orientation_start") {
      if (pendingOrientationAnchorRef.current?.itemId) {
        return pendingOrientationAnchorRef.current;
      }
      const capturedAnchor = isVisualViewportZoomed({ viewportWindow: window })
        ? null
        : captureCenterMovieAnchor({
          doc: document,
          viewportWindow: window,
          orientation: readOrientation(),
        });
      const stableAnchor = resolveStableOrientationAnchor({
        lastStableAnchor: lastStableLibraryAnchorRef.current,
        latestAnchor: latestCenterMovieAnchorRef.current,
        isZoomed: isVisualViewportZoomed({ viewportWindow: window }),
        capturedAnchor,
      });
      pendingOrientationAnchorRef.current = stableAnchor?.itemId ? stableAnchor : null;
      logOrientationAnchorDebug("frozen orientation anchor", {
        reason,
        latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
        frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
      });
      return pendingOrientationAnchorRef.current;
    }

    function captureFallbackOrientationAnchors({ allowSeriesQueryFallback = false } = {}) {
      if (isVisualViewportZoomed({ viewportWindow: window })) {
        return orientationAnchorsRef.current;
      }
      const nextAnchors = captureViewportAnchorCandidates({
        doc: document,
        viewportWindow: window,
        allowSeriesQueryFallback,
        orientation: readOrientation(),
      });
      if (nextAnchors.length > 0) {
        orientationAnchorsRef.current = nextAnchors;
      }
      return nextAnchors;
    }

    function completeOrientationRestore() {
      clearPendingOrientationRestore();
      orientationRestoreLockRef.current = false;
      orientationViewportChangeActiveRef.current = false;
      pendingOrientationAnchorRef.current = null;
      orientationAnchorsRef.current = [];
      orientationRestoreCorrectionCountRef.current = 0;
      scheduleCenterMovieAnchorSample({ reason: "restore_complete" });
    }

    function cancelOrientationRestore(reason, details = {}) {
      const latestCenterMovieItemId = latestCenterMovieAnchorRef.current?.itemId || null;
      const frozenOrientationAnchorItemId = pendingOrientationAnchorRef.current?.itemId || null;
      clearPendingOrientationRestore(true);
      orientationRestoreTokenRef.current += 1;
      orientationRestoreLockRef.current = false;
      orientationViewportChangeActiveRef.current = false;
      pendingOrientationAnchorRef.current = null;
      orientationAnchorsRef.current = [];
      orientationRestoreCorrectionCountRef.current = 0;
      if (reason) {
        logOrientationAnchorDebug("cancelled", {
          reason,
          canceledByUserInteraction: reason === "user_interaction",
          latestCenterMovieItemId,
          frozenOrientationAnchorItemId,
          ...details,
        });
      }
    }

    function scheduleOrientationRestoreVerification(scheduledToken, scheduledUserIntentVersion) {
      orientationRestoreFrameOneRef.current = window.requestAnimationFrame(() => {
        orientationRestoreFrameOneRef.current = 0;
        orientationRestoreFrameTwoRef.current = window.requestAnimationFrame(() => {
          orientationRestoreFrameTwoRef.current = 0;
          orientationRestoreRefineTimerRef.current = window.setTimeout(() => {
            orientationRestoreRefineTimerRef.current = 0;
            verifyOrientationRestore(scheduledToken, scheduledUserIntentVersion);
          }, getOrientationRestoreRefinementDelayMs());
        });
      });
    }

    function resolveOrientationRestoreTarget() {
      const frozenAnchor = pendingOrientationAnchorRef.current;
      if (!frozenAnchor?.itemId && !orientationAnchorsRef.current.length) {
        captureFallbackOrientationAnchors({ allowSeriesQueryFallback: true });
      }
      return selectPreferredOrientationRestoreTarget({
        frozenAnchor,
        fallbackAnchors: frozenAnchor?.itemId ? [] : orientationAnchorsRef.current,
        doc: document,
      });
    }

    function verifyOrientationRestore(scheduledToken, scheduledUserIntentVersion) {
      if (isRestoreAttemptStale({
        scheduledToken,
        activeToken: orientationRestoreTokenRef.current,
        scheduledUserIntentVersion,
        currentUserIntentVersion: orientationUserIntentVersionRef.current,
      })) {
        completeOrientationRestore();
        return;
      }
      const { anchor, targetNode } = resolveOrientationRestoreTarget();
      if (!targetNode) {
        completeOrientationRestore();
        return;
      }
      const measurement = getRestoreViewportMeasurement({ viewportWindow: window });
      const correctionTop = computeRestoreVerificationCorrection({
        anchor,
        currentScrollY: window.scrollY,
        targetRectTop: targetNode.getBoundingClientRect().top,
        targetRectHeight: targetNode.getBoundingClientRect().height,
        viewportMeasurement: measurement,
        correctionCount: orientationRestoreCorrectionCountRef.current,
        maxCorrections: MAX_ORIENTATION_RESTORE_CORRECTIONS,
      });
      if (!Number.isFinite(correctionTop)) {
        completeOrientationRestore();
        return;
      }
      orientationRestoreCorrectionCountRef.current += 1;
      logOrientationAnchorDebug("restore verification correction", {
        orientation: readOrientation(),
        latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
        frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
        selectedRestoreAnchor: formatViewportAnchorDebug(anchor),
        restoreTargetItemId: anchor?.itemId || null,
        restoreTargetScrollY: correctionTop,
        correctionCount: orientationRestoreCorrectionCountRef.current,
      });
      window.scrollTo({
        top: correctionTop,
        behavior: "auto",
      });
      if (orientationRestoreCorrectionCountRef.current >= MAX_ORIENTATION_RESTORE_CORRECTIONS) {
        completeOrientationRestore();
        return;
      }
      scheduleOrientationRestoreVerification(scheduledToken, scheduledUserIntentVersion);
    }

    function attemptOrientationRestore(scheduledToken, scheduledUserIntentVersion) {
      if (isRestoreAttemptStale({
        scheduledToken,
        activeToken: orientationRestoreTokenRef.current,
        scheduledUserIntentVersion,
        currentUserIntentVersion: orientationUserIntentVersionRef.current,
      })) {
        completeOrientationRestore();
        return;
      }
      const { anchor, targetNode, source } = resolveOrientationRestoreTarget();
      if (!targetNode) {
        logOrientationAnchorDebug("restore skipped missing target", {
          source,
          latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
          frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
          candidates: formatViewportAnchorCandidateListDebug(orientationAnchorsRef.current),
        });
        completeOrientationRestore();
        return;
      }
      const measurement = getRestoreViewportMeasurement({ viewportWindow: window });
      const nextTop = computeAnchorRestoreScrollTop({
        anchor,
        currentScrollY: window.scrollY,
        targetRectTop: targetNode.getBoundingClientRect().top,
        viewportMeasurement: measurement,
      });
      if (!Number.isFinite(nextTop)) {
        completeOrientationRestore();
        return;
      }
      logOrientationAnchorDebug("restore attempt", {
        source,
        orientation: readOrientation(measurement),
        latestCenterMovieItemId: latestCenterMovieAnchorRef.current?.itemId || null,
        frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
        selectedRestoreAnchor: formatViewportAnchorDebug(anchor),
        restoreTargetItemId: anchor?.itemId || null,
        restoreTargetScrollY: nextTop,
        correctionCount: orientationRestoreCorrectionCountRef.current,
      });
      window.scrollTo({
        top: nextTop,
        behavior: "auto",
      });
      scheduleOrientationRestoreVerification(scheduledToken, scheduledUserIntentVersion);
    }

    function scheduleOrientationRestore({ zoomedRotationRecovery = false } = {}) {
      clearPendingOrientationRestore(true);
      orientationRestoreLockRef.current = true;
      orientationRestoreTokenRef.current += 1;
      orientationRestoreCorrectionCountRef.current = 0;
      const scheduledToken = orientationRestoreTokenRef.current;
      const scheduledUserIntentVersion = orientationUserIntentVersionRef.current;
      if (zoomedRotationRecovery) {
        const resetRequested = requestTemporaryViewportScaleReset({
          doc: document,
          viewportWindow: window,
        });
        logOrientationAnchorDebug("zoomed rotation recovery requested", {
          resetRequested,
          frozenOrientationAnchorItemId: pendingOrientationAnchorRef.current?.itemId || null,
        });
      }
      orientationRestoreTimerRef.current = window.setTimeout(() => {
        orientationRestoreTimerRef.current = 0;
        orientationRestoreFrameOneRef.current = window.requestAnimationFrame(() => {
          orientationRestoreFrameOneRef.current = 0;
          orientationRestoreFrameTwoRef.current = window.requestAnimationFrame(() => {
            orientationRestoreFrameTwoRef.current = 0;
            attemptOrientationRestore(scheduledToken, scheduledUserIntentVersion);
          });
        });
      }, zoomedRotationRecovery ? 260 : 70);
    }

    function handleViewportShift(event) {
      const measurement = readMeasurement();
      const nextOrientation = readOrientation(measurement);
      const orientationChanged = orientationRef.current !== null && nextOrientation !== orientationRef.current;
      const majorViewportChange = isMajorViewportChange(measurement);
      const zoomedRotationRecovery = shouldRecoverZoomedLibraryRotation({
        platform: orientationPlatform,
        viewportWindow: window,
        orientationChanged,
        majorViewportChange,
        eventType: event?.type || "",
      });
      if (orientationRef.current === null) {
        orientationRef.current = nextOrientation;
        orientationLastMeasurementRef.current = measurement;
        scheduleCenterMovieAnchorSample({ reason: "initial_measurement", immediate: true });
        return;
      }
      orientationLastMeasurementRef.current = measurement;
      if (!orientationChanged && !majorViewportChange && event?.type !== "orientationchange") {
        scheduleCenterMovieAnchorSample({ reason: "stable_resize" });
        return;
      }
      if (!orientationViewportChangeActiveRef.current) {
        freezePendingOrientationAnchor(event?.type || "viewport_change");
        orientationAnchorsRef.current = [];
      }
      orientationViewportChangeActiveRef.current = true;
      orientationRef.current = nextOrientation;
      scheduleOrientationRestore({ zoomedRotationRecovery });
    }

    function handleUserOrientationInteraction(event) {
      if (!isUserRestoreCancellationEvent({ type: event.type, key: event.key })) {
        return;
      }
      if (
        !orientationRestoreLockRef.current
        && !orientationRestoreTimerRef.current
        && !orientationRestoreFrameOneRef.current
        && !orientationRestoreFrameTwoRef.current
        && !orientationRestoreRefineTimerRef.current
      ) {
        return;
      }
      orientationUserIntentVersionRef.current += 1;
      cancelOrientationRestore("user_interaction", {
        eventType: event.type,
        key: event.key || null,
      });
    }

    orientationSamplerRef.current = scheduleCenterMovieAnchorSample;
    const initialMeasurement = readMeasurement();
    orientationRef.current = readOrientation(initialMeasurement);
    orientationLastMeasurementRef.current = initialMeasurement;
    scheduleCenterMovieAnchorSample({ reason: "mount", immediate: true });
    window.addEventListener("scroll", scheduleCenterMovieAnchorSample, { passive: true });
    window.addEventListener("resize", handleViewportShift);
    window.addEventListener("orientationchange", handleViewportShift);
    window.addEventListener("touchstart", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("touchmove", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("wheel", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("pointerdown", handleUserOrientationInteraction, { passive: true });
    window.addEventListener("keydown", handleUserOrientationInteraction);
    visualViewport?.addEventListener("resize", handleViewportShift);
    return () => {
      orientationSamplerRef.current = () => {};
      window.removeEventListener("scroll", scheduleCenterMovieAnchorSample);
      window.removeEventListener("resize", handleViewportShift);
      window.removeEventListener("orientationchange", handleViewportShift);
      window.removeEventListener("touchstart", handleUserOrientationInteraction);
      window.removeEventListener("touchmove", handleUserOrientationInteraction);
      window.removeEventListener("wheel", handleUserOrientationInteraction);
      window.removeEventListener("pointerdown", handleUserOrientationInteraction);
      window.removeEventListener("keydown", handleUserOrientationInteraction);
      visualViewport?.removeEventListener("resize", handleViewportShift);
      clearPendingOrientationRestore(true);
      orientationRestoreLockRef.current = false;
      orientationViewportChangeActiveRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (loading || typeof window === "undefined" || typeof document === "undefined") {
      return;
    }
    if (!isLibraryOrientationRestorePlatform(detectClientPlatform())) {
      return;
    }
    orientationSamplerRef.current?.({
      reason: "library_content_loaded",
      immediate: false,
    });
  }, [
    loading,
    library.total_items,
    visibleContinueWatchingItems.length,
    visibleLibraryGridItems.length,
    packedSeriesRailRows.length,
  ]);

  useEffect(() => {
    if (loading || typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    const rememberedTarget = readLibraryReturnTarget();
    const shouldRestore = Boolean(location.state?.restoreLibraryReturn) || Boolean(rememberedTarget?.pendingRestore);
    if (!shouldRestore || !rememberedTarget || rememberedTarget.listPath !== currentLibraryListPath) {
      return undefined;
    }
    const restoreKey = [
      currentLibraryListPath,
      rememberedTarget.anchorInstanceKey || rememberedTarget.anchorItemId || "none",
      rememberedTarget.anchorViewportRatioY ?? "none",
      rememberedTarget.scrollY,
    ].join(":");
    if (libraryReturnRestoreKeyRef.current === restoreKey) {
      return undefined;
    }
    libraryReturnRestoreKeyRef.current = restoreKey;
    const timerId = window.setTimeout(() => {
      window.requestAnimationFrame(() => {
        const { anchor, targetNode } = selectLibraryReturnRestoreTarget(rememberedTarget, {
          doc: document,
        });
        if (targetNode) {
          restoreHorizontalRailPosition({
            targetNode,
            railKey: rememberedTarget.railKey,
            railScrollLeft: rememberedTarget.railScrollLeft,
          });
          const nextTop = computeAnchorRestoreScrollTop({
            anchor,
            currentScrollY: window.scrollY,
            targetRectTop: targetNode.getBoundingClientRect().top,
            viewportMeasurement: getRestoreViewportMeasurement({ viewportWindow: window }),
          });
          window.scrollTo({
            top: Number.isFinite(nextTop) ? nextTop : rememberedTarget.scrollY,
            behavior: "auto",
          });
        } else if (rememberedTarget.scrollY > 0) {
          window.scrollTo({ top: rememberedTarget.scrollY, behavior: "auto" });
        } else {
          window.scrollTo({ top: 0, behavior: "auto" });
        }
        clearLibraryReturnPending();
      });
    }, 0);
    return () => {
      window.clearTimeout(timerId);
    };
  }, [currentLibraryListPath, library.items, loading, location.state]);

  async function handleRescan() {
    setRescanPending(true);
    setError("");
    setNotice("");
    try {
      const payload = await apiRequest("/api/library/rescan", { method: "POST" });
      const nextCloudSyncWarning = hasCloudSyncWarning(payload.cloud_sync)
        ? String(payload.cloud_sync?.message || "").trim()
        : "";
      cloudSyncWarningRef.current = nextCloudSyncWarning;
      if (payload?.cloud_sync?.reconnect_required) {
        void refreshProviderAuthStatus();
      }
      if (nextCloudSyncWarning) {
        setError(formatRescanBannerText(payload));
        setNotice("");
      } else {
        setNotice(formatRescanBannerText(payload));
      }
      setLibrary((current) => ({ ...current, scan_in_progress: payload.running }));
      scanRunningRef.current = Boolean(payload.running);
      await loadLibrary({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "Unable to start scan");
    } finally {
      setRescanPending(false);
    }
  }

  function handleFloatingSearchChange(nextValue, details = {}) {
    const previousValue = typeof details.previousValue === "string" ? details.previousValue : query;
    const nextSearchValue = typeof nextValue === "string" ? nextValue : "";

    if (floatingSearchScrollRestoreEnabled && typeof window !== "undefined") {
      const previousWasEmpty = previousValue.trim().length === 0;
      const nextIsEmpty = nextSearchValue.trim().length === 0;
      if (previousWasEmpty && !nextIsEmpty && floatingSearchScrollYRef.current === null) {
        floatingSearchScrollYRef.current = window.scrollY;
      }
      if (!previousWasEmpty && nextIsEmpty) {
        if (Number.isFinite(floatingSearchScrollYRef.current)) {
          pendingFloatingSearchRestoreYRef.current = floatingSearchScrollYRef.current;
        }
        floatingSearchScrollYRef.current = null;
      }
    }

    setQuery(nextSearchValue);
  }

  function handleCategoryChange(category) {
    if (category === activeLibraryCategory) {
      return;
    }
    const nextSearch = buildLibraryCategorySearch(location.search, category);
    navigate(
      {
        pathname: location.pathname,
        search: nextSearch,
        hash: location.hash,
      },
      { replace: false },
    );
  }

  function handleArrangeChange(nextArrange) {
    const params = new URLSearchParams(location.search);
    params.set("category", activeLibraryCategory);
    applyLibraryArrangeParams(params, nextArrange);
    const nextParams = params.toString();
    navigate(
      {
        pathname: location.pathname,
        search: nextParams ? `?${nextParams}` : "",
        hash: location.hash,
      },
      { replace: false },
    );
  }

  const isSearching = deferredQuery.trim().length > 0;
  const isFlatSortedView = activeLibraryArrange.sort !== DEFAULT_LIBRARY_ARRANGE.sort;

  return (
    <section
      className="page-section page-section--library"
      data-floating-search-align={heroAlignedFloatingSearch ? "hero-right" : undefined}
      data-device-class={libraryDeviceClass}
      data-library-device={libraryDevice}
      ref={librarySectionRef}
    >
      <div className="topbar library-desktop-hero" aria-label="Library overview" ref={libraryHeroRef}>
        <p className="eyebrow library-desktop-hero__eyebrow">Private Media Library</p>
        <div className="library-desktop-hero__row">
          <div className="library-desktop-hero__brand">
            <Link className="brand" to="/library">
              Elvern
            </Link>
            <span className="status-pill">{library.total_items} indexed</span>
          </div>
          <label className="search-field library-desktop-hero__search library-desktop-hero__search--desktop">
            <span className="sr-only">Search library</span>
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title or filename"
              ref={desktopSearchInputRef}
              type="search"
              value={query}
            />
          </label>
          <RefreshSweepButton
            className="ghost-button"
            disabled={rescanPending}
            onClick={handleRescan}
            type="button"
          >
            {rescanPending ? "Starting scan..." : "Rescan library"}
          </RefreshSweepButton>
        </div>
        <div className="library-desktop-hero__category-row">
          <LibraryCategorySwitch
            activeCategory={activeLibraryCategory}
            dragEnabled={categorySwitchDragEnabled}
            onChange={handleCategoryChange}
          />
          <LibraryArrangeControl
            arrange={activeLibraryArrange}
            availableGenres={library.available_genres || []}
            panelMode={arrangePanelMode}
            panelSize={arrangePanelSize}
            onChange={handleArrangeChange}
          />
        </div>
      </div>

      <div className="library-mobile-search-card">
        <label className="search-field library-desktop-hero__search library-desktop-hero__search--mobile">
          <span className="sr-only">Search library</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title or filename"
            ref={mobileSearchInputRef}
            type="search"
            value={query}
          />
        </label>
      </div>

      <FloatingLibrarySearch
        desktopInteractionMode={floatingSearchDesktopMode}
        enabled={settings.floating_library_search_enabled !== false}
        label="Search library"
        mainInputRefs={[desktopSearchInputRef, mobileSearchInputRef]}
        onChange={handleFloatingSearchChange}
        placeholder="Search title or filename"
        value={query}
      />

      {cloudReconnectPrompt && providerAuthDismissedThisSession ? (
        <section className="content-section cloud-auth-warning">
          <div className="section-header section-header--compact">
            <h2>{cloudReconnectPrompt.title}</h2>
          </div>
          <p className="form-error">{cloudReconnectPrompt.message}</p>
          {cloudReconnectPrompt.allowReconnect ? (
            <div className="player-actions">
              <button
                className="ghost-button"
                disabled={providerAuthReconnectPending}
                onClick={startProviderReconnect}
                type="button"
              >
                {providerAuthReconnectPending ? "Connecting..." : "Reconnect Google Drive"}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {providerAuthPassiveNotice ? (
        <p className="cloud-auth-passive-notice">{providerAuthPassiveNotice}</p>
      ) : null}

      {notice ? <p className="page-note">{notice}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}

      {loading ? <LoadingView label="Loading library..." /> : null}

      {!loading && isSearching ? (
        library.items.length > 0 ? (
          <div className="content-stack">
            <div className="section-header section-header--compact">
              <h2>Search results</h2>
            </div>
            <MediaGrid
              activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
              items={library.items}
              sectionKey="search-results"
              smartPosterLoadingEnabled
            />
          </div>
        ) : (
          <EmptyState
            title="No matches yet"
            description="Try a different title fragment, filename, or clear the search field."
          />
        )
      ) : null}

      {!loading && !isSearching && isFlatSortedView ? (
        library.items.length > 0 ? (
          <div className="content-stack">
            <MediaGrid
              activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
              items={library.items}
              sectionKey="sorted-library"
              smartPosterLoadingEnabled
            />
          </div>
        ) : (
          <EmptyState
            title="No media indexed yet"
            description="Point ELVERN_MEDIA_ROOT at your movies folder, then run a rescan."
          />
        )
      ) : null}

      {!loading && !isSearching && !isFlatSortedView ? (
        <div className="content-stack">
          {showContinueWatchingSection ? (
            <section className="content-section">
              <div className="section-header section-header--compact">
                <h2>Continue watching</h2>
              </div>
              <MediaGrid
                activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                items={visibleContinueWatchingItems}
                sectionKey="continue-watching"
                smartPosterLoadingEnabled
              />
            </section>
          ) : null}

          {packedSeriesRailRows.map((row) => (
            <div
              className={[
                "series-rail-pack-row",
                row.layout ? `series-rail-pack-row--${row.layout}` : "",
              ].filter(Boolean).join(" ")}
              key={row.key}
            >
              {row.blocks.map((block) => (
                <div
                  className="series-rail-pack-block"
                  key={block.key}
                  style={{ "--series-rail-pack-span": String(block.slots) }}
                >
                  <SeriesRail
                    activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                    desktopSlots={block.slots < 6 ? block.slots : null}
                    enableTouchReleaseAssist
                    rail={block.rail}
                    sectionKey={`series:${block.rail.key}`}
                    smartPosterLoadingEnabled
                  />
                </div>
              ))}
            </div>
          ))}

          {!settings.hide_recently_added && library.recently_added.length > 0 ? (
            <section className="content-section">
              <div className="section-header section-header--compact">
                <h2>Recently added</h2>
              </div>
              <MediaGrid
                activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                items={library.recently_added}
                sectionKey="recently-added"
                smartPosterLoadingEnabled
              />
            </section>
          ) : null}

          <section className="content-section">
            <div className="section-header section-header--compact">
              <h2>{activeLibraryCategoryConfig.otherHeading}</h2>
            </div>
            {visibleLibraryGridItems.length > 0 ? (
            <MediaGrid
              activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
              items={visibleLibraryGridItems}
              sectionKey="other-movies"
              smartPosterLoadingEnabled
            />
            ) : (
              <EmptyState
                title="No media indexed yet"
                description="Point ELVERN_MEDIA_ROOT at your movies folder, then run a rescan."
              />
            )}
          </section>
        </div>
      ) : null}
    </section>
  );
}
