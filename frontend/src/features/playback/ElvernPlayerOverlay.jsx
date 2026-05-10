import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { formatDuration } from "../../lib/format.js";
import {
  appendPlayedSample,
  computePlayedNotCachedRanges,
  mergeRanges,
  readBufferedAbsoluteRanges,
  rangesToAbsolute,
} from "../../lib/playbackTimelineRanges.js";
import { toBrowserPlaybackAbsoluteSeconds } from "../../lib/browserPlaybackTimeline.js";

import ElvernTimeline from "./ElvernTimeline.jsx";

const IDLE_HIDE_DELAY_MS = 2600;
const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const SUPPORTS_DOCUMENT = typeof document !== "undefined";

function PlayIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M7 5.25v13.5a.75.75 0 0 0 1.16.63l11-6.75a.75.75 0 0 0 0-1.26l-11-6.75A.75.75 0 0 0 7 5.25Z" fill="currentColor" />
    </svg>
  );
}

function PauseIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <rect height="14" rx="1.2" width="4" x="6.5" y="5" fill="currentColor" />
      <rect height="14" rx="1.2" width="4" x="13.5" y="5" fill="currentColor" />
    </svg>
  );
}

function VolumeOnIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M4 9.5v5a.75.75 0 0 0 .75.75H8l4.5 3.5a.75.75 0 0 0 1.2-.6V5.85a.75.75 0 0 0-1.2-.6L8 8.75H4.75A.75.75 0 0 0 4 9.5Z" fill="currentColor" />
      <path d="M16.5 8.5a5 5 0 0 1 0 7" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
      <path d="M19 6a8 8 0 0 1 0 12" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
    </svg>
  );
}

function VolumeMuteIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M4 9.5v5a.75.75 0 0 0 .75.75H8l4.5 3.5a.75.75 0 0 0 1.2-.6V5.85a.75.75 0 0 0-1.2-.6L8 8.75H4.75A.75.75 0 0 0 4 9.5Z" fill="currentColor" />
      <path d="M16 9l5 6M21 9l-5 6" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
    </svg>
  );
}

function FullscreenEnterIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M5 9V5h4M19 9V5h-4M5 15v4h4M19 15v4h-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

function FullscreenExitIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M9 5v4H5M15 5v4h4M9 19v-4H5M15 19v-4h4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

function PipIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <rect height="13" rx="1.6" width="17" x="3.5" y="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <rect height="5" rx="0.6" width="7" x="12.5" y="12" fill="currentColor" />
    </svg>
  );
}

function CaptionsIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <rect height="13" rx="2" width="18" x="3" y="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8.5 11.5a1.5 1.5 0 1 0-1.5 1.5M16 11.5a1.5 1.5 0 1 0-1.5 1.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
    </svg>
  );
}

function SpeedIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M12 4a8 8 0 0 0-7.45 11" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
      <path d="M12 4a8 8 0 0 1 7.45 11" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.6" />
      <path d="M12 12l3.5-3" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" />
    </svg>
  );
}

function AudioTrackIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M5 14V10M9 17V7M13 19V5M17 14V10M21 12v0" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

function isFullscreenApiAvailable(element) {
  if (!element) {
    return false;
  }
  return Boolean(
    element.requestFullscreen
    || element.webkitRequestFullscreen
    || element.mozRequestFullScreen
    || element.msRequestFullscreen,
  );
}

function getActiveFullscreenElement() {
  if (!SUPPORTS_DOCUMENT) {
    return null;
  }
  return (
    document.fullscreenElement
    || document.webkitFullscreenElement
    || document.mozFullScreenElement
    || document.msFullscreenElement
    || null
  );
}

async function requestElementFullscreen(element) {
  if (element.requestFullscreen) {
    return element.requestFullscreen();
  }
  if (element.webkitRequestFullscreen) {
    return element.webkitRequestFullscreen();
  }
  if (element.mozRequestFullScreen) {
    return element.mozRequestFullScreen();
  }
  if (element.msRequestFullscreen) {
    return element.msRequestFullscreen();
  }
  throw new Error("Fullscreen is not available in this browser.");
}

async function exitDocumentFullscreen() {
  if (!SUPPORTS_DOCUMENT) {
    return;
  }
  if (document.exitFullscreen) {
    return document.exitFullscreen();
  }
  if (document.webkitExitFullscreen) {
    return document.webkitExitFullscreen();
  }
  if (document.mozCancelFullScreen) {
    return document.mozCancelFullScreen();
  }
  if (document.msExitFullscreen) {
    return document.msExitFullscreen();
  }
  return undefined;
}

export default function ElvernPlayerOverlay({
  videoRef,
  shellRef,
  durationSeconds,
  sessionPayload,
  onSeekCommit,
  preparing = false,
  preparingTargetSeconds = null,
  preparingMessage = "",
  errorMessage = "",
  title = "",
  fallbackFullscreenButtonLabel = null,
  cinemaModeActive = false,
  onToggleFullscreen = null,
}) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTimeSeconds, setCurrentTimeSeconds] = useState(0);
  const [bufferedAbsoluteRanges, setBufferedAbsoluteRanges] = useState([]);
  const [playedAbsoluteRanges, setPlayedAbsoluteRanges] = useState([]);
  const [isMuted, setIsMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [showCaptionsMenu, setShowCaptionsMenu] = useState(false);
  const [showAudioMenu, setShowAudioMenu] = useState(false);
  const [textTracks, setTextTracks] = useState([]);
  const [audioTracks, setAudioTracks] = useState([]);
  const [pipActive, setPipActive] = useState(false);
  const [fullscreenActive, setFullscreenActive] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [isPointerInside, setIsPointerInside] = useState(false);
  const [isDraggingTimeline, setIsDraggingTimeline] = useState(false);

  const lastSampledAbsoluteRef = useRef(null);
  const idleTimerRef = useRef(null);
  const overlayRootRef = useRef(null);

  const safeDuration = Number.isFinite(durationSeconds) && durationSeconds > 0 ? durationSeconds : 0;

  const playedNotCachedAbsoluteRanges = useMemo(() => (
    computePlayedNotCachedRanges(playedAbsoluteRanges, bufferedAbsoluteRanges, safeDuration)
  ), [playedAbsoluteRanges, bufferedAbsoluteRanges, safeDuration]);

  const fullscreenApiAvailable = isFullscreenApiAvailable(shellRef?.current || null);

  const refreshControlsTimer = useCallback(() => {
    if (!controlsVisible) {
      setControlsVisible(true);
    }
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    if (!isPlaying || preparing || isDraggingTimeline || showSpeedMenu || showCaptionsMenu || showAudioMenu) {
      return;
    }
    idleTimerRef.current = setTimeout(() => {
      setControlsVisible(false);
      idleTimerRef.current = null;
    }, IDLE_HIDE_DELAY_MS);
  }, [
    controlsVisible,
    isDraggingTimeline,
    isPlaying,
    preparing,
    showAudioMenu,
    showCaptionsMenu,
    showSpeedMenu,
  ]);

  useEffect(() => {
    refreshControlsTimer();
    return () => {
      if (idleTimerRef.current) {
        clearTimeout(idleTimerRef.current);
        idleTimerRef.current = null;
      }
    };
  }, [refreshControlsTimer]);

  useEffect(() => {
    const video = videoRef?.current;
    if (!video) {
      return undefined;
    }
    const updatePlaybackState = () => {
      setIsPlaying(!video.paused && !video.ended);
    };
    const updateVolumeState = () => {
      setIsMuted(Boolean(video.muted));
      setVolume(Number.isFinite(video.volume) ? video.volume : 1);
    };
    const updateRateState = () => {
      setPlaybackRate(Number.isFinite(video.playbackRate) ? video.playbackRate : 1);
    };
    const handleTimeUpdate = () => {
      const localTime = Number.isFinite(video.currentTime) ? video.currentTime : 0;
      const absolute = toBrowserPlaybackAbsoluteSeconds(sessionPayload, localTime);
      setCurrentTimeSeconds(absolute);
      setPlayedAbsoluteRanges((previous) => (
        appendPlayedSample(previous, lastSampledAbsoluteRef.current, absolute)
      ));
      lastSampledAbsoluteRef.current = absolute;
    };
    const handleProgress = () => {
      setBufferedAbsoluteRanges(readBufferedAbsoluteRanges(video, sessionPayload));
    };
    const handleSeeking = () => {
      lastSampledAbsoluteRef.current = null;
    };
    const handleSeeked = () => {
      const localTime = Number.isFinite(video.currentTime) ? video.currentTime : 0;
      lastSampledAbsoluteRef.current = toBrowserPlaybackAbsoluteSeconds(sessionPayload, localTime);
      handleTimeUpdate();
      handleProgress();
    };
    const refreshTextTracks = () => {
      if (!video.textTracks) {
        setTextTracks([]);
        return;
      }
      const collected = [];
      for (let index = 0; index < video.textTracks.length; index += 1) {
        const track = video.textTracks[index];
        if (track.kind === "subtitles" || track.kind === "captions") {
          collected.push({
            id: track.id || `track-${index}`,
            label: track.label || track.language || `Track ${index + 1}`,
            language: track.language || "",
            mode: track.mode,
            index,
          });
        }
      }
      setTextTracks(collected);
    };
    const refreshAudioTracks = () => {
      const native = video.audioTracks;
      if (!native || typeof native.length !== "number") {
        setAudioTracks([]);
        return;
      }
      const collected = [];
      for (let index = 0; index < native.length; index += 1) {
        const track = native[index];
        collected.push({
          id: track.id || `audio-${index}`,
          label: track.label || track.language || `Audio ${index + 1}`,
          language: track.language || "",
          enabled: Boolean(track.enabled),
          index,
        });
      }
      setAudioTracks(collected);
    };

    updatePlaybackState();
    updateVolumeState();
    updateRateState();
    handleTimeUpdate();
    handleProgress();
    refreshTextTracks();
    refreshAudioTracks();

    video.addEventListener("play", updatePlaybackState);
    video.addEventListener("pause", updatePlaybackState);
    video.addEventListener("ended", updatePlaybackState);
    video.addEventListener("volumechange", updateVolumeState);
    video.addEventListener("ratechange", updateRateState);
    video.addEventListener("timeupdate", handleTimeUpdate);
    video.addEventListener("progress", handleProgress);
    video.addEventListener("seeking", handleSeeking);
    video.addEventListener("seeked", handleSeeked);
    video.addEventListener("loadedmetadata", handleProgress);

    if (video.textTracks?.addEventListener) {
      video.textTracks.addEventListener("change", refreshTextTracks);
      video.textTracks.addEventListener("addtrack", refreshTextTracks);
      video.textTracks.addEventListener("removetrack", refreshTextTracks);
    }
    if (video.audioTracks?.addEventListener) {
      video.audioTracks.addEventListener("change", refreshAudioTracks);
      video.audioTracks.addEventListener("addtrack", refreshAudioTracks);
      video.audioTracks.addEventListener("removetrack", refreshAudioTracks);
    }

    return () => {
      video.removeEventListener("play", updatePlaybackState);
      video.removeEventListener("pause", updatePlaybackState);
      video.removeEventListener("ended", updatePlaybackState);
      video.removeEventListener("volumechange", updateVolumeState);
      video.removeEventListener("ratechange", updateRateState);
      video.removeEventListener("timeupdate", handleTimeUpdate);
      video.removeEventListener("progress", handleProgress);
      video.removeEventListener("seeking", handleSeeking);
      video.removeEventListener("seeked", handleSeeked);
      video.removeEventListener("loadedmetadata", handleProgress);
      if (video.textTracks?.removeEventListener) {
        video.textTracks.removeEventListener("change", refreshTextTracks);
        video.textTracks.removeEventListener("addtrack", refreshTextTracks);
        video.textTracks.removeEventListener("removetrack", refreshTextTracks);
      }
      if (video.audioTracks?.removeEventListener) {
        video.audioTracks.removeEventListener("change", refreshAudioTracks);
        video.audioTracks.removeEventListener("addtrack", refreshAudioTracks);
        video.audioTracks.removeEventListener("removetrack", refreshAudioTracks);
      }
    };
  }, [sessionPayload, videoRef]);

  useEffect(() => {
    if (!sessionPayload) {
      return;
    }
    const cacheRanges = sessionPayload?.cache_ranges;
    if (!Array.isArray(cacheRanges) || cacheRanges.length === 0) {
      return;
    }
    const absolute = rangesToAbsolute(sessionPayload, cacheRanges);
    setBufferedAbsoluteRanges((previous) => mergeRanges([...previous, ...absolute]));
  }, [sessionPayload]);

  useEffect(() => {
    if (!SUPPORTS_DOCUMENT) {
      return undefined;
    }
    const handleFullscreenChange = () => {
      const active = getActiveFullscreenElement();
      setFullscreenActive(Boolean(active && shellRef?.current && active === shellRef.current));
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    document.addEventListener("webkitfullscreenchange", handleFullscreenChange);
    handleFullscreenChange();
    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
      document.removeEventListener("webkitfullscreenchange", handleFullscreenChange);
    };
  }, [shellRef]);

  useEffect(() => {
    const video = videoRef?.current;
    if (!video) {
      return undefined;
    }
    const handleEnter = () => setPipActive(true);
    const handleLeave = () => setPipActive(false);
    video.addEventListener("enterpictureinpicture", handleEnter);
    video.addEventListener("leavepictureinpicture", handleLeave);
    return () => {
      video.removeEventListener("enterpictureinpicture", handleEnter);
      video.removeEventListener("leavepictureinpicture", handleLeave);
    };
  }, [videoRef]);

  const togglePlay = useCallback(() => {
    const video = videoRef?.current;
    if (!video) {
      return;
    }
    if (video.paused || video.ended) {
      const playPromise = video.play();
      if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(() => {
          // user gesture may be required; surface remains usable
        });
      }
    } else {
      video.pause();
    }
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const toggleMute = useCallback(() => {
    const video = videoRef?.current;
    if (!video) {
      return;
    }
    video.muted = !video.muted;
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const handleVolumeChange = useCallback((event) => {
    const video = videoRef?.current;
    if (!video) {
      return;
    }
    const next = Number(event.target.value);
    if (!Number.isFinite(next)) {
      return;
    }
    video.volume = Math.max(0, Math.min(1, next));
    if (next > 0 && video.muted) {
      video.muted = false;
    }
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const handleRateSelect = useCallback((rate) => {
    const video = videoRef?.current;
    if (video) {
      video.playbackRate = rate;
    }
    setShowSpeedMenu(false);
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const handleTextTrackSelect = useCallback((trackIndex) => {
    const video = videoRef?.current;
    if (!video || !video.textTracks) {
      return;
    }
    for (let index = 0; index < video.textTracks.length; index += 1) {
      const track = video.textTracks[index];
      if (index === trackIndex) {
        track.mode = track.mode === "showing" ? "disabled" : "showing";
      } else if (track.mode === "showing") {
        track.mode = "disabled";
      }
    }
    setShowCaptionsMenu(false);
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const handleTextTrackOff = useCallback(() => {
    const video = videoRef?.current;
    if (!video || !video.textTracks) {
      setShowCaptionsMenu(false);
      return;
    }
    for (let index = 0; index < video.textTracks.length; index += 1) {
      video.textTracks[index].mode = "disabled";
    }
    setShowCaptionsMenu(false);
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const handleAudioTrackSelect = useCallback((trackIndex) => {
    const video = videoRef?.current;
    const native = video?.audioTracks;
    if (!native) {
      setShowAudioMenu(false);
      return;
    }
    for (let index = 0; index < native.length; index += 1) {
      native[index].enabled = index === trackIndex;
    }
    setShowAudioMenu(false);
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const togglePip = useCallback(async () => {
    const video = videoRef?.current;
    if (!video || !SUPPORTS_DOCUMENT) {
      return;
    }
    if (!document.pictureInPictureEnabled || video.disablePictureInPicture) {
      return;
    }
    try {
      if (document.pictureInPictureElement === video) {
        await document.exitPictureInPicture();
      } else if (video.requestPictureInPicture) {
        await video.requestPictureInPicture();
      }
    } catch (pipError) {
      // Surface unavailable; silently keep current state.
    }
    refreshControlsTimer();
  }, [refreshControlsTimer, videoRef]);

  const toggleFullscreen = useCallback(async () => {
    if (typeof onToggleFullscreen === "function") {
      onToggleFullscreen();
      return;
    }
    const shell = shellRef?.current;
    if (!shell) {
      return;
    }
    try {
      const active = getActiveFullscreenElement();
      if (active === shell) {
        await exitDocumentFullscreen();
        return;
      }
      if (isFullscreenApiAvailable(shell)) {
        await requestElementFullscreen(shell);
      }
    } catch (fullscreenError) {
      // Fullscreen may be denied; the caller may surface a notice.
    }
  }, [onToggleFullscreen, shellRef]);

  const handleSurfaceClick = useCallback(() => {
    togglePlay();
  }, [togglePlay]);

  const handleSurfaceTouch = useCallback(() => {
    if (controlsVisible) {
      setControlsVisible(false);
    } else {
      refreshControlsTimer();
    }
  }, [controlsVisible, refreshControlsTimer]);

  const handlePointerEnter = useCallback(() => {
    setIsPointerInside(true);
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  const handlePointerLeave = useCallback(() => {
    setIsPointerInside(false);
    if (isPlaying && !preparing && !isDraggingTimeline && !showSpeedMenu && !showCaptionsMenu && !showAudioMenu) {
      setControlsVisible(false);
    }
  }, [isDraggingTimeline, isPlaying, preparing, showAudioMenu, showCaptionsMenu, showSpeedMenu]);

  const handlePointerMove = useCallback(() => {
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  const handleTimelinePreview = useCallback(() => {
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  const handleTimelineCommit = useCallback((targetSeconds) => {
    onSeekCommit?.(targetSeconds);
  }, [onSeekCommit]);

  const handleTimelineDragStart = useCallback(() => {
    setIsDraggingTimeline(true);
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  const handleTimelineDragEnd = useCallback(() => {
    setIsDraggingTimeline(false);
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  useEffect(() => {
    if (!isPlaying) {
      setControlsVisible(true);
    }
  }, [isPlaying]);

  useEffect(() => {
    if (preparing || errorMessage) {
      setControlsVisible(true);
    }
  }, [errorMessage, preparing]);

  const visibilityClass = controlsVisible || !isPlaying || preparing || isPointerInside
    ? " elvern-overlay--visible"
    : " elvern-overlay--idle";

  const captionActiveCount = textTracks.filter((track) => track.mode === "showing").length;
  const audioMenuAvailable = audioTracks.length > 1;
  const captionsMenuAvailable = textTracks.length > 0;

  const pipAvailable = SUPPORTS_DOCUMENT
    && Boolean(document.pictureInPictureEnabled)
    && Boolean(videoRef?.current?.requestPictureInPicture)
    && !videoRef?.current?.disablePictureInPicture;

  const fullscreenLikeActive = fullscreenActive || cinemaModeActive;
  const fullscreenButtonLabel = fullscreenLikeActive ? "Exit fullscreen" : (fallbackFullscreenButtonLabel || "Fullscreen");
  const fullscreenButtonRendered = fullscreenApiAvailable || typeof onToggleFullscreen === "function";

  return (
    <div
      ref={overlayRootRef}
      className={`elvern-overlay${visibilityClass}`}
      data-preparing={preparing ? "true" : "false"}
      onPointerEnter={handlePointerEnter}
      onPointerLeave={handlePointerLeave}
      onPointerMove={handlePointerMove}
    >
      <button
        aria-label={isPlaying ? "Pause" : "Play"}
        className="elvern-overlay__surface"
        onClick={handleSurfaceClick}
        onTouchEnd={(event) => {
          event.preventDefault();
          handleSurfaceTouch();
        }}
        type="button"
      >
        <span className="elvern-overlay__surface-hint" aria-hidden="true">
          {isPlaying ? <PauseIcon className="elvern-overlay__surface-icon" /> : <PlayIcon className="elvern-overlay__surface-icon" />}
        </span>
      </button>

      {title || preparing || errorMessage ? (
        <div className="elvern-overlay__top-bar" aria-hidden={controlsVisible ? undefined : true}>
          {title ? <span className="elvern-overlay__title">{title}</span> : null}
          {errorMessage ? <span className="elvern-overlay__error">{errorMessage}</span> : null}
        </div>
      ) : null}

      {preparing ? (
        <div className="elvern-overlay__preparing" role="status" aria-live="polite">
          <span className="elvern-overlay__preparing-spinner" aria-hidden="true" />
          <span className="elvern-overlay__preparing-text">
            {preparingMessage || (preparingTargetSeconds != null
              ? `Preparing ${formatDuration(preparingTargetSeconds)}`
              : "Preparing selected position")}
          </span>
        </div>
      ) : null}

      <div className="elvern-overlay__bottom-bar">
        <div className="elvern-overlay__time-row">
          <span className="elvern-overlay__time-current" aria-label="Current time">
            {formatDuration(currentTimeSeconds)}
          </span>
          <span className="elvern-overlay__time-separator" aria-hidden="true">/</span>
          <span className="elvern-overlay__time-duration" aria-label="Movie duration">
            {safeDuration > 0 ? formatDuration(safeDuration) : "--:--"}
          </span>
        </div>

        <ElvernTimeline
          ariaLabel="Movie timeline"
          bufferedAbsoluteRanges={bufferedAbsoluteRanges}
          currentTimeSeconds={currentTimeSeconds}
          disabled={safeDuration <= 0}
          durationSeconds={safeDuration}
          onDragEnd={handleTimelineDragEnd}
          onDragStart={handleTimelineDragStart}
          onSeekCommit={handleTimelineCommit}
          onSeekPreview={handleTimelinePreview}
          playedNotCachedAbsoluteRanges={playedNotCachedAbsoluteRanges}
          preparingTargetSeconds={preparing ? preparingTargetSeconds : null}
        />

        <div className="elvern-overlay__controls-row">
          <button
            aria-label={isPlaying ? "Pause" : "Play"}
            className="elvern-overlay__icon-button"
            onClick={togglePlay}
            type="button"
          >
            {isPlaying ? <PauseIcon className="elvern-overlay__icon" /> : <PlayIcon className="elvern-overlay__icon" />}
          </button>

          <div className="elvern-overlay__volume-group">
            <button
              aria-label={isMuted || volume === 0 ? "Unmute" : "Mute"}
              className="elvern-overlay__icon-button"
              onClick={toggleMute}
              type="button"
            >
              {isMuted || volume === 0 ? <VolumeMuteIcon className="elvern-overlay__icon" /> : <VolumeOnIcon className="elvern-overlay__icon" />}
            </button>
            <input
              aria-label="Volume"
              className="elvern-overlay__volume-slider"
              max="1"
              min="0"
              onChange={handleVolumeChange}
              step="0.05"
              type="range"
              value={isMuted ? 0 : volume}
            />
          </div>

          <div className="elvern-overlay__spacer" aria-hidden="true" />

          {captionsMenuAvailable ? (
            <div className="elvern-overlay__menu-host">
              <button
                aria-expanded={showCaptionsMenu}
                aria-label="Subtitles"
                className={`elvern-overlay__icon-button${captionActiveCount > 0 ? " elvern-overlay__icon-button--active" : ""}`}
                onClick={() => {
                  setShowCaptionsMenu((value) => !value);
                  setShowSpeedMenu(false);
                  setShowAudioMenu(false);
                  refreshControlsTimer();
                }}
                type="button"
              >
                <CaptionsIcon className="elvern-overlay__icon" />
              </button>
              {showCaptionsMenu ? (
                <div className="elvern-overlay__menu" role="menu">
                  <button
                    className="elvern-overlay__menu-item"
                    onClick={handleTextTrackOff}
                    role="menuitemradio"
                    aria-checked={captionActiveCount === 0}
                    type="button"
                  >
                    Off
                  </button>
                  {textTracks.map((track) => (
                    <button
                      aria-checked={track.mode === "showing"}
                      className="elvern-overlay__menu-item"
                      key={track.id}
                      onClick={() => handleTextTrackSelect(track.index)}
                      role="menuitemradio"
                      type="button"
                    >
                      {track.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {audioMenuAvailable ? (
            <div className="elvern-overlay__menu-host">
              <button
                aria-expanded={showAudioMenu}
                aria-label="Audio track"
                className="elvern-overlay__icon-button"
                onClick={() => {
                  setShowAudioMenu((value) => !value);
                  setShowSpeedMenu(false);
                  setShowCaptionsMenu(false);
                  refreshControlsTimer();
                }}
                type="button"
              >
                <AudioTrackIcon className="elvern-overlay__icon" />
              </button>
              {showAudioMenu ? (
                <div className="elvern-overlay__menu" role="menu">
                  {audioTracks.map((track) => (
                    <button
                      aria-checked={track.enabled}
                      className="elvern-overlay__menu-item"
                      key={track.id}
                      onClick={() => handleAudioTrackSelect(track.index)}
                      role="menuitemradio"
                      type="button"
                    >
                      {track.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="elvern-overlay__menu-host">
            <button
              aria-expanded={showSpeedMenu}
              aria-label={`Playback speed ${playbackRate.toFixed(2)}x`}
              className={`elvern-overlay__icon-button${playbackRate !== 1 ? " elvern-overlay__icon-button--active" : ""}`}
              onClick={() => {
                setShowSpeedMenu((value) => !value);
                setShowCaptionsMenu(false);
                setShowAudioMenu(false);
                refreshControlsTimer();
              }}
              type="button"
            >
              <SpeedIcon className="elvern-overlay__icon" />
              {playbackRate !== 1 ? <span className="elvern-overlay__icon-badge">{`${playbackRate}x`}</span> : null}
            </button>
            {showSpeedMenu ? (
              <div className="elvern-overlay__menu" role="menu">
                {PLAYBACK_RATES.map((rate) => (
                  <button
                    aria-checked={Math.abs(rate - playbackRate) < 0.01}
                    className="elvern-overlay__menu-item"
                    key={rate}
                    onClick={() => handleRateSelect(rate)}
                    role="menuitemradio"
                    type="button"
                  >
                    {`${rate}x`}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {pipAvailable ? (
            <button
              aria-label={pipActive ? "Exit picture in picture" : "Picture in picture"}
              aria-pressed={pipActive}
              className={`elvern-overlay__icon-button${pipActive ? " elvern-overlay__icon-button--active" : ""}`}
              onClick={togglePip}
              type="button"
            >
              <PipIcon className="elvern-overlay__icon" />
            </button>
          ) : null}

          {fullscreenButtonRendered ? (
            <button
              aria-label={fullscreenButtonLabel}
              aria-pressed={fullscreenLikeActive}
              className="elvern-overlay__icon-button"
              onClick={toggleFullscreen}
              type="button"
            >
              {fullscreenLikeActive ? <FullscreenExitIcon className="elvern-overlay__icon" /> : <FullscreenEnterIcon className="elvern-overlay__icon" />}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
