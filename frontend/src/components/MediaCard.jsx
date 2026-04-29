import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { getMovieCardTitle } from "../lib/movieTitles";
import { getCardPosterUrl } from "../lib/posterUrls";
import { getQualityRank } from "../lib/qualityRank";
import { buildLibraryReturnState, rememberLibraryReturnTarget } from "../lib/libraryNavigation";
import {
  getSmartPosterCardSnapshot,
  isSmartPosterLoadingSupported,
  markSmartPosterCardError,
  markSmartPosterCardLoaded,
  POSTER_MODE_ATTACH,
  registerSmartPosterCard,
  subscribeSmartPosterCard,
  unregisterSmartPosterCard,
} from "../lib/smartPosterLoading";


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
  smartPosterLoadingEnabled = false,
  cardInstanceKey = null,
}) {
  const location = useLocation();
  const displayTitle = getMovieCardTitle(item);
  const progressPercent = getProgressPercent(item);
  const monogram = displayTitle.trim().charAt(0).toUpperCase() || "E";
  const [posterFailed, setPosterFailed] = useState(false);
  const [rankTooltipOpen, setRankTooltipOpen] = useState(false);
  const posterRef = useRef(null);
  const posterInstanceId = useId();
  const smartPosterCardId = useMemo(
    () => `poster-${item.id}-${posterInstanceId}`,
    [item.id, posterInstanceId],
  );
  const mobileCardPosterVariantEnabled = (
    smartPosterLoadingEnabled
    && Boolean(item.poster_url)
    && isSmartPosterLoadingSupported()
  );
  const resolvedPosterUrl = useMemo(
    () => (mobileCardPosterVariantEnabled ? getCardPosterUrl(item.poster_url) : item.poster_url),
    [item.poster_url, mobileCardPosterVariantEnabled],
  );
  const smartPosterSchedulerEnabled = (
    mobileCardPosterVariantEnabled
    && Boolean(resolvedPosterUrl)
    && !posterFailed
  );
  const [smartPosterSnapshot, setSmartPosterSnapshot] = useState(() => (
    smartPosterSchedulerEnabled
      ? getSmartPosterCardSnapshot(smartPosterCardId)
      : null
  ));
  const smartPosterMode = smartPosterSchedulerEnabled
    ? (smartPosterSnapshot?.mode || "defer")
    : POSTER_MODE_ATTACH;
  const showPoster = Boolean(resolvedPosterUrl)
    && !posterFailed
    && (!smartPosterSchedulerEnabled || smartPosterMode === POSTER_MODE_ATTACH);
  const qualityRank = getQualityRank(item);
  const tooltipId = `quality-rank-tooltip-${item.id}`;
  const storageKind = (item.source_kind || "local") === "cloud" ? "cloud" : "local";
  const storageLabel = storageKind === "cloud" ? "Cloud" : "Local";
  const detailPath = `/library/${item.id}`;
  const detailState = buildLibraryReturnState({
    listPath: location.pathname,
    anchorItemId: item.id,
    anchorInstanceKey: cardInstanceKey,
    scrollY: typeof window !== "undefined" ? window.scrollY : 0,
  });

  function handleOpenDetail(event) {
    rememberLibraryReturnTarget(getLibraryReturnTargetFromCardClick({
      event,
      listPath: location.pathname,
      itemId: item.id,
      fallbackInstanceKey: cardInstanceKey,
    }));
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
    if (!smartPosterSchedulerEnabled) {
      setSmartPosterSnapshot(null);
      return undefined;
    }
    setSmartPosterSnapshot(getSmartPosterCardSnapshot(smartPosterCardId));
    return subscribeSmartPosterCard(smartPosterCardId, () => {
      setSmartPosterSnapshot(getSmartPosterCardSnapshot(smartPosterCardId));
    });
  }, [smartPosterCardId, smartPosterSchedulerEnabled]);

  useEffect(() => {
    if (!smartPosterSchedulerEnabled || !posterRef.current) {
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
  }, [resolvedPosterUrl, smartPosterCardId, smartPosterSchedulerEnabled]);

  return (
    <article
      className="media-card"
      data-library-item-id={item.id}
      data-library-card-instance-key={cardInstanceKey || undefined}
    >
      <Link className="media-card__poster-link" onClick={handleOpenDetail} state={detailState} to={detailPath}>
        <div className="media-card__poster" aria-hidden="true" ref={posterRef}>
          {backgroundPlaybackActive ? (
            <div
              className="media-card__background-playback-indicator"
              title="Browser Playback active in background"
            />
          ) : null}
          {showPoster ? (
            <img
              alt=""
              className="media-card__poster-image"
              decoding="async"
              loading={smartPosterSchedulerEnabled ? "eager" : "lazy"}
              onError={() => {
                if (smartPosterSchedulerEnabled) {
                  markSmartPosterCardError(smartPosterCardId);
                }
                setPosterFailed(true);
              }}
              onLoad={() => {
                if (smartPosterSchedulerEnabled) {
                  markSmartPosterCardLoaded(smartPosterCardId);
                }
              }}
              src={resolvedPosterUrl}
            />
          ) : (
            <div
              className={[
                "media-card__poster-fallback",
                smartPosterSchedulerEnabled && !posterFailed
                  ? "media-card__poster-fallback--deferred"
                  : "",
              ].filter(Boolean).join(" ")}
            >
              <span>{monogram}</span>
            </div>
          )}
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
