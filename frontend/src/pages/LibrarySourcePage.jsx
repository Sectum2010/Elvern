import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { EmptyState } from "../components/EmptyState";
import { FloatingLibrarySearch } from "../components/FloatingLibrarySearch";
import { LoadingView } from "../components/LoadingView";
import { MediaCard } from "../components/MediaCard";
import { SeriesRail } from "../components/SeriesRail";
import { apiRequest } from "../lib/api";
import { useActiveBrowserPlaybackItemId } from "../lib/browserPlayback";
import {
  clearLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation";
import {
  buildLibraryQueryKey,
  LIBRARY_QUERY_GC_TIME_MS,
  LIBRARY_QUERY_STALE_TIME_MS,
} from "../lib/libraryQueries";
import { detectClientDeviceClass, detectClientPlatform } from "../lib/platformDetection";
import {
  packIpadPortraitSeriesRailRows,
  packSeriesRailRows,
} from "../lib/seriesRails";
import {
  computeAnchorRestoreScrollTop,
  getRestoreViewportMeasurement,
  restoreHorizontalRailPosition,
  selectLibraryReturnRestoreTarget,
} from "../lib/viewportAnchor";
import { resolveUserSettings, useUserSettingsQuery } from "../lib/userSettingsQueries";


export const LIBRARY_SOURCE_SEARCH_DEBOUNCE_MS = 300;
const EMPTY_SOURCE_LIBRARY_PAYLOAD = Object.freeze({
  items: [],
  series_rails: [],
  cloud_series_rails: [],
  scan_in_progress: false,
});


export function resolveLibrarySourceQueryFromSearch(search = "") {
  return String(new URLSearchParams(search).get("q") || "").trim();
}


export function buildLibrarySourceQuerySearch(currentSearch = "", query = "") {
  const params = new URLSearchParams(currentSearch);
  const normalizedQuery = String(query || "").trim();
  if (normalizedQuery) {
    params.set("q", normalizedQuery);
  } else {
    params.delete("q");
  }
  const nextSearch = params.toString();
  return nextSearch ? `?${nextSearch}` : "";
}


function MediaGrid({
  items,
  activeBrowserPlaybackItemId = null,
  posterDisplayWidth = "1400",
  smartPosterLoadingEnabled = false,
  sectionKey = "library-source",
}) {
  return (
    <div className="media-grid">
      {items.map((item) => (
        <MediaCard
          backgroundPlaybackActive={activeBrowserPlaybackItemId === item.id}
          cardInstanceKey={`${sectionKey}:${item.id}`}
          item={item}
          key={item.id}
          posterDisplayWidth={posterDisplayWidth}
          smartPosterLoadingEnabled={smartPosterLoadingEnabled}
        />
      ))}
    </div>
  );
}

function formatMovieCount(count) {
  return `${count} ${count === 1 ? "movie" : "movies"}`;
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


const SOURCE_PAGE_COPY = {
  local: {
    eyebrow: "Local",
    title: "Local Library",
    subtitle: "Browse only your DGX movies.",
    sectionTitle: "Other Movies",
    emptyTitle: "No local movies yet",
    emptyDescription: "Your visible DGX library will appear here once local movies are indexed.",
  },
  cloud: {
    eyebrow: "Cloud",
    title: "Cloud Library",
    subtitle: "Browse only your visible Cloud movies.",
    sectionTitle: "Other Movies",
    emptyTitle: "No cloud movies yet",
    emptyDescription: "Your visible Cloud library will appear here once Google Drive movies are indexed.",
  },
};

function matchesFocusedLibraryQuery(item, normalizedQuery) {
  if (!normalizedQuery) {
    return true;
  }
  const haystack = [
    item.title,
    item.filename,
    item.year,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(normalizedQuery);
}


export function LibrarySourcePage({ sourceKind }) {
  const { refreshAuth, user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const activeBrowserPlaybackItemId = useActiveBrowserPlaybackItemId();
  const activeSourceQuery = useMemo(
    () => resolveLibrarySourceQueryFromSearch(location.search),
    [location.search],
  );
  const currentLibraryListPath = useMemo(
    () => `${location.pathname}${location.search || ""}`,
    [location.pathname, location.search],
  );
  const [query, setQuery] = useState(() => activeSourceQuery);
  const deferredQuery = useDeferredValue(query);
  const [error, setError] = useState("");
  const userSettingsQuery = useUserSettingsQuery(user);
  const settings = resolveUserSettings(userSettingsQuery.data);
  const sourceSearchInputRef = useRef(null);
  const floatingSearchScrollYRef = useRef(null);
  const pendingFloatingSearchRestoreYRef = useRef(null);
  const libraryReturnRestoreKeyRef = useRef("");
  const useIpadPortraitSeriesPacking = useIpadPortraitLibraryLayout();
  const resolvedSourceKind = sourceKind === "cloud" ? "cloud" : "local";
  const copy = SOURCE_PAGE_COPY[resolvedSourceKind];
  const clientPlatform = detectClientPlatform();
  const clientDeviceClass = detectClientDeviceClass();
  const libraryDevice = clientPlatform === "ipad" ? "ipad" : undefined;
  const libraryDeviceClass = clientDeviceClass === "phone" ? "phone" : undefined;
  const floatingSearchDesktopMode = clientDeviceClass === "desktop" && clientPlatform !== "ipad";
  const floatingSearchScrollRestoreEnabled = ["desktop", "phone", "tablet"].includes(clientDeviceClass);
  const libraryRequestPath = `/api/library?category=movies&source=${resolvedSourceKind}`;
  const libraryQueryKey = useMemo(
    () => buildLibraryQueryKey({
      userId: user?.id,
      role: user?.role,
      category: "movies",
      source: resolvedSourceKind,
      genre: "",
      quality: "all",
      sort: "smart",
      query: "",
    }),
    [resolvedSourceKind, user?.id, user?.role],
  );
  const libraryQuery = useQuery({
    queryKey: libraryQueryKey,
    queryFn: ({ signal }) => apiRequest(libraryRequestPath, { signal }),
    enabled: Boolean(user?.id),
    staleTime: LIBRARY_QUERY_STALE_TIME_MS,
    gcTime: LIBRARY_QUERY_GC_TIME_MS,
    retry: false,
    refetchInterval: (queryState) => (
      queryState.state.data?.scan_in_progress ? 2500 : false
    ),
  });
  const library = libraryQuery.data || EMPTY_SOURCE_LIBRARY_PAYLOAD;
  const loading = !libraryQuery.data && libraryQuery.isPending;
  const items = library.items || [];
  const seriesRails = resolvedSourceKind === "cloud"
    ? (library.cloud_series_rails || [])
    : (library.series_rails || []);
  const normalizedQuery = deferredQuery.trim().toLowerCase();
  const visibleSeriesRails = useMemo(
    () => seriesRails
      .map((rail) => {
        const matchingItems = (rail.items || []).filter((item) => matchesFocusedLibraryQuery(item, normalizedQuery));
        if (!normalizedQuery) {
          return rail;
        }
        if (matchingItems.length < 2) {
          return null;
        }
        return {
          ...rail,
          film_count: matchingItems.length,
          items: matchingItems,
        };
      })
      .filter(Boolean),
    [normalizedQuery, seriesRails],
  );
  const visibleSeriesRailItemIds = useMemo(
    () => new Set(
      visibleSeriesRails.flatMap((rail) => (rail.items || []).map((item) => item.id)),
    ),
    [visibleSeriesRails],
  );
  const filteredItems = useMemo(
    () => items.filter(
      (item) => matchesFocusedLibraryQuery(item, normalizedQuery) && !visibleSeriesRailItemIds.has(item.id),
    ),
    [items, normalizedQuery, visibleSeriesRailItemIds],
  );
  const packedSeriesRailRows = useMemo(
    () => (useIpadPortraitSeriesPacking
      ? packIpadPortraitSeriesRailRows(visibleSeriesRails)
      : packSeriesRailRows(visibleSeriesRails)),
    [useIpadPortraitSeriesPacking, visibleSeriesRails],
  );
  const sourceVisibleCount = items.length;
  const hasVisibleContent = visibleSeriesRails.length > 0 || filteredItems.length > 0;

  useEffect(() => {
    setQuery(activeSourceQuery);
  }, [activeSourceQuery]);

  useEffect(() => {
    const normalizedQuery = query.trim();
    if (normalizedQuery === activeSourceQuery) {
      return undefined;
    }
    const timerId = window.setTimeout(() => {
      navigate(
        {
          pathname: location.pathname,
          search: buildLibrarySourceQuerySearch(location.search, normalizedQuery),
          hash: location.hash,
        },
        { replace: true },
      );
    }, LIBRARY_SOURCE_SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timerId);
    };
  }, [activeSourceQuery, location.hash, location.pathname, location.search, navigate, query]);

  useEffect(() => {
    setError("");
  }, [libraryQueryKey]);

  useEffect(() => {
    const requestError = libraryQuery.error;
    if (!requestError || requestError.name === "AbortError") {
      return;
    }
    if (requestError.status === 401) {
      void refreshAuth();
      return;
    }
    if (requestError.status === 403) {
      return;
    }
    if (!libraryQuery.data) {
      setError(requestError.message || "Failed to load library section");
    }
  }, [libraryQuery.data, libraryQuery.error, refreshAuth]);

  useEffect(() => {
    if (
      typeof window === "undefined"
      || !floatingSearchScrollRestoreEnabled
      || normalizedQuery
      || loading
      || pendingFloatingSearchRestoreYRef.current === null
    ) {
      return undefined;
    }
    const restoreY = pendingFloatingSearchRestoreYRef.current;
    const frameId = window.requestAnimationFrame(() => {
      window.scrollTo({ top: restoreY, behavior: "auto" });
      pendingFloatingSearchRestoreYRef.current = null;
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [
    filteredItems.length,
    floatingSearchScrollRestoreEnabled,
    loading,
    normalizedQuery,
    visibleSeriesRails.length,
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
  }, [currentLibraryListPath, items, loading, location.state, seriesRails]);

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

  return (
    <section
      className="page-section page-section--library-source"
      data-device-class={libraryDeviceClass}
      data-library-device={libraryDevice}
    >
      <div className={`library-focus-hero library-focus-hero--${resolvedSourceKind}`}>
        <div className="library-focus-hero__row">
          <div className="library-focus-hero__copy">
            <div className="library-focus-hero__segments" aria-label="Focused library switch">
              <Link
                className={
                  resolvedSourceKind === "local"
                    ? "library-focus-hero__segment library-focus-hero__segment--active"
                    : "library-focus-hero__segment"
                }
                to={{ pathname: "/library/local", search: location.search }}
              >
                Local
              </Link>
              <Link
                className={
                  resolvedSourceKind === "cloud"
                    ? "library-focus-hero__segment library-focus-hero__segment--active"
                    : "library-focus-hero__segment"
                }
                to={{ pathname: "/library/cloud", search: location.search }}
              >
                Cloud
              </Link>
            </div>
            <div className="library-focus-hero__headline">
              <h1>{copy.title}</h1>
              <span className="library-focus-hero__count">{formatMovieCount(sourceVisibleCount)}</span>
            </div>
          </div>
          <Link className="ghost-button ghost-button--inline" to="/library">
            Back to Library
          </Link>
        </div>
      </div>

      <div className="library-focus-search-card">
        <label className="search-field">
          <span className="sr-only">Search {copy.title}</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Search ${copy.eyebrow.toLowerCase()} movies`}
            ref={sourceSearchInputRef}
            type="search"
            value={query}
          />
        </label>
      </div>

      <FloatingLibrarySearch
        desktopInteractionMode={floatingSearchDesktopMode}
        enabled={settings.floating_library_search_enabled !== false}
        label={`Search ${copy.title}`}
        mainInputRefs={[sourceSearchInputRef]}
        onChange={handleFloatingSearchChange}
        placeholder={`Search ${copy.eyebrow.toLowerCase()} movies`}
        value={query}
      />

      {error ? <p className="form-error">{error}</p> : null}
      {loading ? <LoadingView label={`Loading ${copy.title.toLowerCase()}...`} /> : null}

      {!loading ? (
        hasVisibleContent ? (
          <>
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
                      posterDisplayWidth={settings.poster_card_display_max_width}
                      rail={block.rail}
                      smartPosterLoadingEnabled
                    />
                  </div>
                ))}
              </div>
            ))}
            {filteredItems.length > 0 ? (
              <section className="content-section">
                <div className="section-header section-header--compact">
                  <h2>{copy.sectionTitle}</h2>
                </div>
                <MediaGrid
                  activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                  items={filteredItems}
                  posterDisplayWidth={settings.poster_card_display_max_width}
                  sectionKey={`${resolvedSourceKind}:other-movies`}
                  smartPosterLoadingEnabled
                />
              </section>
            ) : null}
          </>
        ) : (
          <EmptyState
            title={deferredQuery.trim() ? "No matches yet" : copy.emptyTitle}
            description={deferredQuery.trim()
              ? `Try a different title fragment or filename in ${copy.title.toLowerCase()}.`
              : copy.emptyDescription}
          />
        )
      ) : null}
    </section>
  );
}
