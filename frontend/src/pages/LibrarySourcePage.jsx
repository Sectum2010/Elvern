import { startTransition, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { EmptyState } from "../components/EmptyState";
import { LoadingView } from "../components/LoadingView";
import { MediaCard } from "../components/MediaCard";
import { SeriesRail } from "../components/SeriesRail";
import { apiRequest } from "../lib/api";
import { useActiveBrowserPlaybackItemId } from "../lib/browserPlayback";
import {
  clearLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation";
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


function MediaGrid({
  items,
  activeBrowserPlaybackItemId = null,
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
  const { refreshAuth } = useAuth();
  const location = useLocation();
  const activeBrowserPlaybackItemId = useActiveBrowserPlaybackItemId();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [items, setItems] = useState([]);
  const [seriesRails, setSeriesRails] = useState([]);
  const libraryReturnRestoreKeyRef = useRef("");
  const useIpadPortraitSeriesPacking = useIpadPortraitLibraryLayout();
  const copy = SOURCE_PAGE_COPY[sourceKind] || SOURCE_PAGE_COPY.local;
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
  const libraryDevice = detectClientPlatform() === "ipad" ? "ipad" : undefined;
  const libraryDeviceClass = detectClientDeviceClass() === "phone" ? "phone" : undefined;

  useEffect(() => {
    const controller = new AbortController();

    async function loadLibrary() {
      startTransition(() => {
        setLoading(true);
      });
      setError("");
      try {
        const payload = await apiRequest("/api/library", { signal: controller.signal });
        const visibleItems = (payload.items || []).filter(
          (item) => (item.source_kind || "local") === sourceKind,
        );
        const focusedRails = (sourceKind === "cloud"
          ? (payload.cloud_series_rails || [])
          : (payload.series_rails || []))
          .map((rail) => ({
            ...rail,
            items: (rail.items || []).filter((item) => (item.source_kind || "local") === sourceKind),
          }))
          .filter((rail) => (rail.items || []).length >= 2)
          .map((rail) => ({
            ...rail,
            film_count: rail.items.length,
          }));
        setItems(visibleItems);
        setSeriesRails(focusedRails);
      } catch (requestError) {
        if (requestError.name === "AbortError") {
          return;
        }
        if (requestError.status === 401) {
          await refreshAuth();
          return;
        }
        setError(requestError.message || "Failed to load library section");
      } finally {
        setLoading(false);
      }
    }

    loadLibrary();
    return () => {
      controller.abort();
    };
  }, [refreshAuth, sourceKind]);

  useEffect(() => {
    if (loading || typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    const rememberedTarget = readLibraryReturnTarget();
    const shouldRestore = Boolean(location.state?.restoreLibraryReturn) || Boolean(rememberedTarget?.pendingRestore);
    if (!shouldRestore || !rememberedTarget || rememberedTarget.listPath !== location.pathname) {
      return undefined;
    }
    const restoreKey = [
      location.pathname,
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
  }, [items, loading, location.pathname, location.state, seriesRails]);

  return (
    <section
      className="page-section page-section--library-source"
      data-device-class={libraryDeviceClass}
      data-library-device={libraryDevice}
    >
      <div className={`library-focus-hero library-focus-hero--${sourceKind}`}>
        <div className="library-focus-hero__row">
          <div className="library-focus-hero__copy">
            <div className="library-focus-hero__segments" aria-label="Focused library switch">
              <Link
                className={
                  sourceKind === "local"
                    ? "library-focus-hero__segment library-focus-hero__segment--active"
                    : "library-focus-hero__segment"
                }
                to="/library/local"
              >
                Local
              </Link>
              <Link
                className={
                  sourceKind === "cloud"
                    ? "library-focus-hero__segment library-focus-hero__segment--active"
                    : "library-focus-hero__segment"
                }
                to="/library/cloud"
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
            type="search"
            value={query}
          />
        </label>
      </div>

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
                  sectionKey={`${sourceKind}:other-movies`}
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
