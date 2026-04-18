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
import { packSeriesRailRows } from "../lib/seriesRails";


function MediaGrid({ items, activeBrowserPlaybackItemId = null }) {
  return (
    <div className="media-grid">
      {items.map((item) => (
        <MediaCard
          backgroundPlaybackActive={activeBrowserPlaybackItemId === item.id}
          item={item}
          key={item.id}
        />
      ))}
    </div>
  );
}

function formatMovieCount(count) {
  return `${count} ${count === 1 ? "movie" : "movies"}`;
}

export function LibraryPage() {
  const { refreshAuth } = useAuth();
  const location = useLocation();
  const activeBrowserPlaybackItemId = useActiveBrowserPlaybackItemId();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [settings, setSettings] = useState({
    hide_duplicate_movies: true,
    hide_recently_added: false,
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
    total_items: 0,
    scan_in_progress: false,
  });
  const [sourceCounts, setSourceCounts] = useState({ local: 0, cloud: 0 });
  const scanRunningRef = useRef(false);
  const seriesAnchorRef = useRef({ key: "", offsetTop: 0 });
  const orientationRef = useRef(null);
  const orientationRestoreTimerRef = useRef(0);
  const orientationRestoreLockRef = useRef(false);
  const libraryReturnRestoreKeyRef = useRef("");
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
    () => packSeriesRailRows(visibleSeriesRails),
    [visibleSeriesRails],
  );

  async function loadLibrary({ signal, silent = false } = {}) {
    if (!silent) {
      startTransition(() => {
        setLoading(true);
      });
    }
    setError("");
    try {
      const target = deferredQuery.trim()
        ? `/api/library/search?q=${encodeURIComponent(deferredQuery.trim())}`
        : "/api/library";
      const payload = await apiRequest(target, { signal });
      if (!deferredQuery.trim()) {
        const nextSourceCounts = (payload.items || []).reduce(
          (counts, item) => {
            if ((item.source_kind || "local") === "cloud") {
              counts.cloud += 1;
            } else {
              counts.local += 1;
            }
            return counts;
          },
          { local: 0, cloud: 0 },
        );
        setSourceCounts(nextSourceCounts);
      }
      if (scanRunningRef.current && !payload.scan_in_progress) {
        setNotice("Library scan completed.");
      }
      scanRunningRef.current = Boolean(payload.scan_in_progress);
      setLibrary(payload);
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
  }, [deferredQuery]);

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
  }, [library.scan_in_progress, deferredQuery]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    if (document.documentElement.dataset.deviceShell !== "iphone") {
      return undefined;
    }

    const readOrientation = () =>
      window.matchMedia("(orientation: landscape)").matches ? "landscape" : "portrait";

    function captureSeriesAnchor() {
      if (orientationRestoreLockRef.current) {
        return;
      }
      const seriesNodes = Array.from(document.querySelectorAll("[data-series-rail-key]"));
      const viewportFloor = 96;
      const viewportCeiling = window.innerHeight * 0.82;
      const candidate = seriesNodes.find((node) => {
        const rect = node.getBoundingClientRect();
        return rect.bottom > viewportFloor && rect.top < viewportCeiling;
      });
      if (!candidate) {
        return;
      }
      seriesAnchorRef.current = {
        key: candidate.getAttribute("data-series-rail-key") || "",
        offsetTop: candidate.getBoundingClientRect().top,
      };
    }

    function restoreSeriesAnchor() {
      const anchor = seriesAnchorRef.current;
      if (!anchor?.key) {
        return;
      }
      const escapedKey = anchor.key.replaceAll('"', '\\"');
      const target = document.querySelector(`[data-series-rail-key="${escapedKey}"]`);
      if (!target) {
        return;
      }
      const nextTop = window.scrollY + target.getBoundingClientRect().top - anchor.offsetTop;
      window.scrollTo({
        top: Math.max(0, nextTop),
        behavior: "auto",
      });
    }

    function handleViewportShift() {
      const nextOrientation = readOrientation();
      if (orientationRef.current === null) {
        orientationRef.current = nextOrientation;
        captureSeriesAnchor();
        return;
      }
      if (nextOrientation === orientationRef.current) {
        captureSeriesAnchor();
        return;
      }
      orientationRef.current = nextOrientation;
      orientationRestoreLockRef.current = true;
      if (orientationRestoreTimerRef.current) {
        window.clearTimeout(orientationRestoreTimerRef.current);
      }
      orientationRestoreTimerRef.current = window.setTimeout(() => {
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            restoreSeriesAnchor();
            orientationRestoreLockRef.current = false;
            captureSeriesAnchor();
          });
        });
        orientationRestoreTimerRef.current = 0;
      }, 90);
    }

    orientationRef.current = readOrientation();
    captureSeriesAnchor();
    window.addEventListener("scroll", captureSeriesAnchor, { passive: true });
    window.addEventListener("resize", handleViewportShift);
    window.addEventListener("orientationchange", handleViewportShift);
    return () => {
      window.removeEventListener("scroll", captureSeriesAnchor);
      window.removeEventListener("resize", handleViewportShift);
      window.removeEventListener("orientationchange", handleViewportShift);
      if (orientationRestoreTimerRef.current) {
        window.clearTimeout(orientationRestoreTimerRef.current);
        orientationRestoreTimerRef.current = 0;
      }
      orientationRestoreLockRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (loading || typeof window === "undefined" || typeof document === "undefined") {
      return undefined;
    }
    const rememberedTarget = readLibraryReturnTarget();
    const shouldRestore = Boolean(location.state?.restoreLibraryReturn) || Boolean(rememberedTarget?.pendingRestore);
    if (!shouldRestore || !rememberedTarget || rememberedTarget.listPath !== location.pathname) {
      return undefined;
    }
    const restoreKey = `${location.pathname}:${rememberedTarget.anchorItemId || "none"}:${rememberedTarget.scrollY}`;
    if (libraryReturnRestoreKeyRef.current === restoreKey) {
      return undefined;
    }
    libraryReturnRestoreKeyRef.current = restoreKey;
    const timerId = window.setTimeout(() => {
      window.requestAnimationFrame(() => {
        const targetNode = rememberedTarget.anchorItemId
          ? document.querySelector(`[data-library-item-id="${rememberedTarget.anchorItemId}"]`)
          : null;
        if (targetNode) {
          const nextTop = window.scrollY + targetNode.getBoundingClientRect().top - 96;
          window.scrollTo({ top: Math.max(0, nextTop), behavior: "auto" });
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
  }, [library.items, loading, location.pathname, location.state]);

  async function handleRescan() {
    setRescanPending(true);
    setError("");
    setNotice("");
    try {
      const payload = await apiRequest("/api/library/rescan", { method: "POST" });
      setNotice(payload.message);
      setLibrary((current) => ({ ...current, scan_in_progress: payload.running }));
      scanRunningRef.current = Boolean(payload.running);
      await loadLibrary({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "Unable to start scan");
    } finally {
      setRescanPending(false);
    }
  }

  const isSearching = deferredQuery.trim().length > 0;

  return (
    <section className="page-section page-section--library">
      <div className="topbar library-desktop-hero" aria-label="Library overview">
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
              type="search"
              value={query}
            />
          </label>
          <button
            className="ghost-button"
            disabled={rescanPending}
            onClick={handleRescan}
            type="button"
          >
            {rescanPending ? "Starting scan..." : "Rescan library"}
          </button>
        </div>
      </div>

      <div className="library-mobile-search-card">
        <label className="search-field library-desktop-hero__search library-desktop-hero__search--mobile">
          <span className="sr-only">Search library</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search title or filename"
            type="search"
            value={query}
          />
        </label>
      </div>

      <div className="library-focus-entry">
        <Link className="library-focus-entry__link" to="/library/local">
          <span className="library-focus-entry__label">Local</span>
          <span className="library-focus-entry__meta">{formatMovieCount(sourceCounts.local)}</span>
        </Link>
        <Link className="library-focus-entry__link" to="/library/cloud">
          <span className="library-focus-entry__label">Cloud</span>
          <span className="library-focus-entry__meta">{formatMovieCount(sourceCounts.cloud)}</span>
        </Link>
      </div>

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
            />
          </div>
        ) : (
          <EmptyState
            title="No matches yet"
            description="Try a different title fragment, filename, or clear the search field."
          />
        )
      ) : null}

      {!loading && !isSearching ? (
        <div className="content-stack">
          {showContinueWatchingSection ? (
            <section className="content-section">
              <div className="section-header section-header--compact">
                <h2>Continue watching</h2>
              </div>
              <MediaGrid
                activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                items={visibleContinueWatchingItems}
              />
            </section>
          ) : null}

          {packedSeriesRailRows.map((row) => (
            <div className="series-rail-pack-row" key={row.key}>
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
              />
            </section>
          ) : null}

          <section className="content-section">
            <div className="section-header section-header--compact">
              <h2>Other Movies</h2>
            </div>
            {visibleLibraryGridItems.length > 0 ? (
              <MediaGrid
                activeBrowserPlaybackItemId={activeBrowserPlaybackItemId}
                items={visibleLibraryGridItems}
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
