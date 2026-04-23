import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { getMovieCardTitle } from "../lib/movieTitles";
import { getQualityRank } from "../lib/qualityRank";
import { buildLibraryReturnState, rememberLibraryReturnTarget } from "../lib/libraryNavigation";


function getProgressPercent(item) {
  if (!item.progress_seconds || !item.progress_duration_seconds) {
    return 0;
  }
  return Math.max(
    0,
    Math.min(100, (item.progress_seconds / item.progress_duration_seconds) * 100),
  );
}


export function MediaCard({ item, backgroundPlaybackActive = false }) {
  const location = useLocation();
  const displayTitle = getMovieCardTitle(item);
  const progressPercent = getProgressPercent(item);
  const monogram = displayTitle.trim().charAt(0).toUpperCase() || "E";
  const [posterFailed, setPosterFailed] = useState(false);
  const [rankTooltipOpen, setRankTooltipOpen] = useState(false);
  const showPoster = Boolean(item.poster_url) && !posterFailed;
  const qualityRank = getQualityRank(item);
  const tooltipId = `quality-rank-tooltip-${item.id}`;
  const storageKind = (item.source_kind || "local") === "cloud" ? "cloud" : "local";
  const storageLabel = storageKind === "cloud" ? "Cloud" : "Local";
  const detailPath = `/library/${item.id}`;
  const detailState = buildLibraryReturnState({
    listPath: location.pathname,
    anchorItemId: item.id,
    scrollY: typeof window !== "undefined" ? window.scrollY : 0,
  });

  function handleOpenDetail() {
    rememberLibraryReturnTarget({
      listPath: location.pathname,
      anchorItemId: item.id,
      scrollY: typeof window !== "undefined" ? window.scrollY : 0,
      pendingRestore: false,
    });
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

  return (
    <article className="media-card" data-library-item-id={item.id}>
      <Link className="media-card__poster-link" onClick={handleOpenDetail} state={detailState} to={detailPath}>
        <div className="media-card__poster" aria-hidden="true">
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
              loading="lazy"
              onError={() => setPosterFailed(true)}
              src={item.poster_url}
            />
          ) : (
            <div className="media-card__poster-fallback">
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
