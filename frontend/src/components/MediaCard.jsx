import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useLocation } from "react-router-dom";
import { getMovieCardTitle } from "../lib/movieTitles";
import { getCardPosterUrl } from "../lib/posterUrls";
import { resolveLibraryQualityRank } from "../lib/qualityRank";
import { buildLibraryReturnState, rememberLibraryReturnTarget } from "../lib/libraryNavigation";
import { startDetailPerformanceTrace } from "../lib/detailPerformanceTiming";
import { logDesktopLibraryReturnCapture } from "../lib/desktopLibraryReturnRestore";
import { usePosterContextMenu } from "./PosterContextMenu";
import {
  getSmartPosterCardSnapshot,
  isSmartPosterLoadingSupported,
  markSmartPosterCardError,
  markSmartPosterCardLoaded,
  POSTER_MODE_ATTACH,
  registerSmartPosterCard,
  retrySmartPosterCard,
  subscribeSmartPosterCard,
  unregisterSmartPosterCard,
} from "../lib/smartPosterLoading";
import {
  getPosterRecoveryAttachContext,
  hasPosterRecoveryAttempt,
  isPosterAttachContextRecovered,
  markPosterRecoveryAttempt,
  POSTER_RECOVERY_COOLDOWN_MS,
  resolvePosterRecoveryErrorContext,
  subscribePosterRecoveryEvents,
} from "../lib/posterRecovery";


function toFiniteNumber(value, fallback = null) {
  const parsedValue = Number(value);
  return Number.isFinite(parsedValue) ? parsedValue : fallback;
}

function getProgressPercent(item) {
  if (!item.progress_seconds || !item.progress_duration_seconds) {
    return 0;
  }
  return Math.max(
    0,
    Math.min(100, (item.progress_seconds / item.progress_duration_seconds) * 100),
  );
}

function getLibraryReturnTargetFromCardClick({
  event,
  itemId,
  listPath,
  fallbackInstanceKey = null,
}) {
  const scrollY = typeof window !== "undefined" ? window.scrollY : 0;
  const baseTarget = {
    listPath,
    anchorItemId: itemId,
    anchorInstanceKey: fallbackInstanceKey,
    scrollY,
    pendingRestore: false,
  };
  if (typeof window === "undefined") {
    return baseTarget;
  }
  const cardNode = event?.currentTarget?.closest?.(".media-card")
    || event?.target?.closest?.(".media-card")
    || null;
  if (!cardNode) {
    return baseTarget;
  }
  const rect = cardNode.getBoundingClientRect();
  const visualViewport = window.visualViewport || null;
  const viewportScale = toFiniteNumber(visualViewport?.scale, 1);
  const useVisualViewport = Math.abs(viewportScale - 1) <= 0.01;
  const layoutViewportWidth = window.document?.documentElement?.clientWidth || window.innerWidth || 0;
  const layoutViewportHeight = window.document?.documentElement?.clientHeight || window.innerHeight || 0;
  const viewportWidth = useVisualViewport
    ? toFiniteNumber(visualViewport?.width, layoutViewportWidth)
    : layoutViewportWidth;
  const viewportHeight = useVisualViewport
    ? toFiniteNumber(visualViewport?.height, layoutViewportHeight)
    : layoutViewportHeight;
  const viewportOffsetTop = useVisualViewport ? toFiniteNumber(visualViewport?.offsetTop, 0) : 0;
  const viewportOffsetLeft = useVisualViewport ? toFiniteNumber(visualViewport?.offsetLeft, 0) : 0;
  const railNode = cardNode.closest?.("[data-series-rail-key]") || null;
  const railViewportNode = railNode?.querySelector?.(".series-rail__viewport") || null;
  return {
    ...baseTarget,
    anchorItemId: toFiniteNumber(cardNode.getAttribute("data-library-item-id"), itemId),
    anchorInstanceKey: cardNode.getAttribute("data-library-card-instance-key") || fallbackInstanceKey,
    anchorViewportRatioY: viewportHeight
      ? (rect.top - viewportOffsetTop) / viewportHeight
      : null,
    anchorViewportRatioX: viewportWidth
      ? (rect.left - viewportOffsetLeft) / viewportWidth
      : null,
    viewportWidth,
    viewportHeight,
    railKey: railNode?.getAttribute?.("data-series-rail-key") || null,
    railScrollLeft: Number.isFinite(railViewportNode?.scrollLeft)
      ? railViewportNode.scrollLeft
      : null,
  };
}


export function MediaCard({
  item,
  backgroundPlaybackActive = false,
  posterDisplayWidth = "1400",
  smartPosterLoadingEnabled = false,
  cardInstanceKey = null,
  protectedIdentity = null,
}) {
  const location = useLocation();
  const { openPosterContextMenu } = usePosterContextMenu();
  const displayTitle = getMovieCardTitle(item);
  const progressPercent = getProgressPercent(item);
  const monogram = displayTitle.trim().charAt(0).toUpperCase() || "E";
  const [posterLoadState, setPosterLoadState] = useState({
    url: null,
    loaded: false,
    failed: false,
  });
  const [rankTooltipOpen, setRankTooltipOpen] = useState(false);
  const posterRef = useRef(null);
  const resolvedPosterUrl = useMemo(
    () => getCardPosterUrl(item.poster_url, posterDisplayWidth),
    [item.poster_url, posterDisplayWidth],
  );
  const posterImageRef = useRef(null);
  const posterLoadStateRef = useRef(posterLoadState);
  const posterRecoveryRef = useRef({
    url: resolvedPosterUrl,
    attachFailureWatermark: 0,
    boundIncidentId: 0,
    boundFailureId: 0,
    attemptedIncidentIds: [],
  });
  const posterRequestAttachedRef = useRef(false);
  const posterRecoveryTimerRef = useRef(0);
  const posterRecoveryUrlRef = useRef(resolvedPosterUrl);
  const posterInstanceId = useId();
  const smartPosterCardId = useMemo(
    () => `poster-${item.id}-${posterInstanceId}`,
    [item.id, posterInstanceId],
  );
  const smartPosterSchedulerEnabled = (
    smartPosterLoadingEnabled
    && Boolean(item.poster_url)
    && isSmartPosterLoadingSupported()
  );
  const posterStateMatchesUrl = posterLoadState.url === resolvedPosterUrl;
  const posterFailed = posterStateMatchesUrl && posterLoadState.failed;
  const posterLoaded = posterStateMatchesUrl && posterLoadState.loaded;
  const smartPosterSchedulerActive = smartPosterSchedulerEnabled
    && Boolean(resolvedPosterUrl)
    && !posterFailed;
  const [smartPosterSnapshot, setSmartPosterSnapshot] = useState(() => (
    smartPosterSchedulerActive
      ? getSmartPosterCardSnapshot(smartPosterCardId)
      : null
  ));
  const smartPosterMode = smartPosterSchedulerActive
    ? (smartPosterSnapshot?.mode || "defer")
    : POSTER_MODE_ATTACH;
  const showPoster = Boolean(resolvedPosterUrl)
    && !posterFailed
    && (!smartPosterSchedulerActive || smartPosterMode === POSTER_MODE_ATTACH);
  const qualityRank = resolveLibraryQualityRank(item);
  const tooltipId = `quality-rank-tooltip-${item.id}`;
  const storageKind = (item.source_kind || "local") === "cloud" ? "cloud" : "local";
  const storageLabel = storageKind === "cloud" ? "Cloud" : "Local";
  const detailPath = `/library/${item.id}`;
  const libraryListPath = `${location.pathname}${location.search || ""}${location.hash || ""}`;
  const detailState = buildLibraryReturnState({
    listPath: libraryListPath,
    anchorItemId: item.id,
    anchorInstanceKey: cardInstanceKey,
    scrollY: typeof window !== "undefined" ? window.scrollY : 0,
    userId: protectedIdentity?.userId,
    role: protectedIdentity?.role,
  });

  function handleOpenDetail(event) {
    startDetailPerformanceTrace();
    const target = getLibraryReturnTargetFromCardClick({
      event,
      listPath: libraryListPath,
      itemId: item.id,
      fallbackInstanceKey: cardInstanceKey,
    });
    rememberLibraryReturnTarget({
      ...target,
      userId: protectedIdentity?.userId,
      role: protectedIdentity?.role,
    });
    const cardNode = event?.currentTarget?.closest?.(".media-card")
      || event?.target?.closest?.(".media-card")
      || null;
    logDesktopLibraryReturnCapture({ target, cardNode });
  }

  function handlePosterContextMenu(event) {
    const opened = openPosterContextMenu(event, {
      id: item.id,
      title: displayTitle,
    });
    if (opened) {
      event.preventDefault();
    }
  }

  function openRankTooltip() {
    setRankTooltipOpen(true);
  }

  function closeRankTooltip() {
    setRankTooltipOpen(false);
  }

  function toggleRankTooltip(event) {
    event.preventDefault();
    event.stopPropagation();
    setRankTooltipOpen((current) => !current);
  }

  useEffect(() => {
    posterLoadStateRef.current = posterLoadState;
  }, [posterLoadState]);

  useLayoutEffect(() => {
    if (posterRecoveryUrlRef.current === resolvedPosterUrl) {
      return;
    }
    posterRecoveryUrlRef.current = resolvedPosterUrl;
    posterRecoveryRef.current = {
      url: resolvedPosterUrl,
      attachFailureWatermark: 0,
      boundIncidentId: 0,
      boundFailureId: 0,
      attemptedIncidentIds: [],
    };
    posterRequestAttachedRef.current = false;
    if (posterRecoveryTimerRef.current) {
      window.clearTimeout(posterRecoveryTimerRef.current);
      posterRecoveryTimerRef.current = 0;
    }
  }, [resolvedPosterUrl]);

  useEffect(() => {
    setPosterLoadState((current) => (
      current.url === resolvedPosterUrl
        ? current
        : { url: resolvedPosterUrl, loaded: false, failed: false }
    ));
  }, [resolvedPosterUrl]);

  const retryRecoveredPoster = useCallback(() => {
    const loadState = posterLoadStateRef.current;
    const recovery = posterRecoveryRef.current;
    const incidentId = Number(recovery.boundIncidentId || 0);
    if (
      recovery.url !== resolvedPosterUrl
      || incidentId <= 0
      || hasPosterRecoveryAttempt(recovery, incidentId)
      || !isPosterAttachContextRecovered(recovery)
      || loadState.url !== resolvedPosterUrl
      || !loadState.failed
    ) {
      return;
    }
    posterRecoveryRef.current = markPosterRecoveryAttempt(
      recovery,
      incidentId,
    );
    window.clearTimeout(posterRecoveryTimerRef.current);
    posterRecoveryTimerRef.current = window.setTimeout(() => {
      const current = posterLoadStateRef.current;
      if (current.url !== resolvedPosterUrl || !current.failed) {
        return;
      }
      posterRequestAttachedRef.current = false;
      retrySmartPosterCard(smartPosterCardId);
      setPosterLoadState({
        url: resolvedPosterUrl,
        loaded: false,
        failed: false,
      });
    }, POSTER_RECOVERY_COOLDOWN_MS);
  }, [resolvedPosterUrl, smartPosterCardId]);

  useEffect(() => subscribePosterRecoveryEvents(({ kind }) => {
    if (kind === "recovered") {
      retryRecoveredPoster();
    }
  }), [retryRecoveredPoster]);

  const recordPosterRequestAttach = useCallback(() => {
    if (!posterImageRef.current || posterRequestAttachedRef.current) {
      return;
    }
    posterRequestAttachedRef.current = true;
    const current = posterRecoveryRef.current;
    posterRecoveryRef.current = {
      url: resolvedPosterUrl,
      ...getPosterRecoveryAttachContext(),
      attemptedIncidentIds: (
        current.url === resolvedPosterUrl
          ? current.attemptedIncidentIds
          : []
      ),
    };
  }, [resolvedPosterUrl]);

  useLayoutEffect(() => {
    recordPosterRequestAttach();
  }, [recordPosterRequestAttach, showPoster]);

  useEffect(() => () => {
    window.clearTimeout(posterRecoveryTimerRef.current);
  }, []);

  useEffect(() => {
    if (!smartPosterSchedulerActive) {
      setSmartPosterSnapshot(null);
      return undefined;
    }
    setSmartPosterSnapshot(getSmartPosterCardSnapshot(smartPosterCardId));
    return subscribeSmartPosterCard(smartPosterCardId, () => {
      setSmartPosterSnapshot(getSmartPosterCardSnapshot(smartPosterCardId));
    });
  }, [smartPosterCardId, smartPosterSchedulerActive]);

  useEffect(() => {
    if (!smartPosterSchedulerActive || !posterRef.current) {
      return undefined;
    }
    registerSmartPosterCard({
      id: smartPosterCardId,
      node: posterRef.current,
      posterUrl: resolvedPosterUrl,
    });
    return () => {
      unregisterSmartPosterCard(smartPosterCardId);
    };
  }, [resolvedPosterUrl, smartPosterCardId, smartPosterSchedulerActive]);

  return (
    <article
      className="media-card"
      data-library-item-id={item.id}
      data-library-card-instance-key={cardInstanceKey || undefined}
    >
      <Link className="media-card__poster-link" onClick={handleOpenDetail} state={detailState} to={detailPath}>
        <div
          className="media-card__poster"
          aria-hidden="true"
          onContextMenu={handlePosterContextMenu}
          ref={posterRef}
        >
          {backgroundPlaybackActive ? (
            <div
              className="media-card__background-playback-indicator"
              title="Browser Playback active in background"
            />
          ) : null}
          <div
            className={[
              "media-card__poster-fallback",
              smartPosterSchedulerActive && !posterFailed
                ? "media-card__poster-fallback--deferred"
                : "",
              showPoster && posterLoaded
                ? "media-card__poster-fallback--hidden"
                : "",
            ].filter(Boolean).join(" ")}
          >
            <span>{monogram}</span>
          </div>
          {showPoster ? (
            <img
              alt=""
              className={[
                "media-card__poster-image",
                posterLoaded ? "media-card__poster-image--loaded" : "",
              ].filter(Boolean).join(" ")}
              decoding="async"
              draggable="false"
              fetchPriority={smartPosterSchedulerActive ? undefined : "low"}
              loading={smartPosterSchedulerActive ? "eager" : "lazy"}
              ref={posterImageRef}
              onError={() => {
                if (smartPosterSchedulerActive) {
                  markSmartPosterCardError(smartPosterCardId);
                }
                posterRecoveryRef.current = {
                  ...resolvePosterRecoveryErrorContext(posterRecoveryRef.current),
                  url: resolvedPosterUrl,
                };
                const failedState = {
                  url: resolvedPosterUrl,
                  loaded: false,
                  failed: true,
                };
                posterLoadStateRef.current = failedState;
                setPosterLoadState(failedState);
                void Promise.resolve().then(retryRecoveredPoster);
              }}
              onLoad={() => {
                if (smartPosterSchedulerActive) {
                  markSmartPosterCardLoaded(smartPosterCardId);
                }
                // A successful load resets incident/attempt state so the poster
                // starts clean for any future incident.
                posterRecoveryRef.current = {
                  url: resolvedPosterUrl,
                  attachFailureWatermark: 0,
                  boundIncidentId: 0,
                  boundFailureId: 0,
                  attemptedIncidentIds: [],
                };
                const loadedState = {
                  url: resolvedPosterUrl,
                  loaded: true,
                  failed: false,
                };
                posterLoadStateRef.current = loadedState;
                setPosterLoadState(loadedState);
              }}
              src={resolvedPosterUrl}
            />
          ) : null}
          {progressPercent > 0 ? (
            <div className="media-card__progress">
              <div style={{ width: `${progressPercent}%` }} />
            </div>
          ) : null}
        </div>
      </Link>
      <div className="media-card__body">
        <div className="media-card__copy">
          <Link className="media-card__title-link" onClick={handleOpenDetail} state={detailState} to={detailPath}>
            <h3 className="media-card__title">{displayTitle}</h3>
          </Link>
        </div>
        <div className="media-card__badges">
          <div
            className="media-card__rank-shell"
            onMouseEnter={openRankTooltip}
            onMouseLeave={closeRankTooltip}
          >
            <button
              aria-label={`${qualityRank.label}: ${qualityRank.description}`}
              aria-describedby={rankTooltipOpen ? tooltipId : undefined}
              aria-expanded={rankTooltipOpen}
              className={`media-card__rank media-card__rank--${qualityRank.key}`}
              onBlur={closeRankTooltip}
              onClick={toggleRankTooltip}
              type="button"
            >
              {qualityRank.label}
            </button>
            <div
              className={`media-card__rank-tooltip${rankTooltipOpen ? " media-card__rank-tooltip--open" : ""}`}
              id={tooltipId}
              role="tooltip"
            >
              {qualityRank.tooltip}
            </div>
          </div>
          <span className={`media-card__storage-badge media-card__storage-badge--${storageKind}`}>
            {storageLabel}
          </span>
        </div>
      </div>
    </article>
  );
}
