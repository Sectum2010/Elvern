import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  appendPlayedSample,
  computePlayedNotCachedRanges,
  getContiguousBufferedEndFromPosition,
  readBufferedAbsoluteRanges,
} from "../../lib/playbackTimelineRanges.js";
import { toBrowserPlaybackAbsoluteSeconds } from "../../lib/browserPlaybackTimeline.js";
import {
  ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS,
  formatPlaybackDuration,
  formatPlaybackTime,
  resolveOverlayLayoutCapabilities,
  shouldOverlayBeVisible,
} from "../../lib/elvernOverlayLayout.js";
import {
  VIDEO_FIT_STANDARD,
  VIDEO_FIT_ZOOM_FILL,
  normalizeVideoFitMode,
} from "../../lib/playerFitMode.js";

import ElvernTimeline from "./ElvernTimeline.jsx";
import { AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE } from "./useOptimizedPlaybackSession.js";
import { usePlaybackTrackControls } from "./usePlaybackTrackControls.js";

const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const SUPPORTS_DOCUMENT = typeof document !== "undefined";
const TOUCH_OVERLAY_IDLE_HIDE_DELAY_MS = 5000;
const PHONE_OVERLAY_IDLE_HIDE_DELAY_MS = 3000;

function readPointerType(event) {
  return event?.pointerType || event?.nativeEvent?.pointerType || "";
}

function isSpaceKey(event) {
  return event?.key === " " || event?.key === "Spacebar" || event?.code === "Space";
}

function consumeKeyboardEvent(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  event?.nativeEvent?.stopImmediatePropagation?.();
}

function stopTrackMenuSurfaceEvent(event) {
  event?.stopPropagation?.();
  event?.nativeEvent?.stopImmediatePropagation?.();
}

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

export function FullscreenExitIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M9 5v4H5M15 5v4h4M9 19v-4H5M15 19v-4h4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

export function InlineExpandIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <path d="M8.5 15.5 4.5 19.5M4.5 15.5v4h4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.15" />
      <path d="M15.5 8.5 19.5 4.5M15.5 4.5h4v4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.15" />
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
      <path d="M5.5 5.5h13A2.5 2.5 0 0 1 21 8v6.2a2.5 2.5 0 0 1-2.5 2.5H12l-4.1 3.1a.7.7 0 0 1-1.12-.56V16.7H5.5A2.5 2.5 0 0 1 3 14.2V8a2.5 2.5 0 0 1 2.5-2.5Z" fill="none" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.65" />
      <path d="M7.4 10.1h4.2M7.4 13h2.9M13.1 10.1h3.5M12.1 13h4.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.55" />
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
      <rect height="10" rx="3" width="6" x="9" y="4" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M6.8 11.2a5.2 5.2 0 0 0 10.4 0M12 16.4V20M8.8 20h6.4" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
    </svg>
  );
}

function MoreIcon({ className }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 24 24">
      <circle cx="6" cy="12" fill="currentColor" r="1.6" />
      <circle cx="12" cy="12" fill="currentColor" r="1.6" />
      <circle cx="18" cy="12" fill="currentColor" r="1.6" />
    </svg>
  );
}

function isBackendUnsupportedTrack(track) {
  return track?.source === "backend" && track?.browserSupported === false;
}

function shouldShowSubtitleWarning(track) {
  return track?.source === "backend"
    && track?.trackSource === "raw_probe_summary_json"
    && Boolean(track?.imageBased);
}

function resolveAudioTrackUnavailableMessage(scanStatus, scanError) {
  const status = String(scanStatus || "").trim().toLowerCase();
  const error = String(scanError || "").trim().toLowerCase();
  if (status === "scanning" || status === "stale" || status === "never" || status === "not_scanned") {
    return "Scanning audio tracks...";
  }
  if (
    status === "provider_auth_required"
    || status === "provider_reconnect_required"
    || error === "provider_auth_required"
    || error === "provider_reconnect_required"
  ) {
    return "Reconnect cloud source to scan tracks";
  }
  if (status === "failed" || error) {
    return "Track scan unavailable";
  }
  return "No alternate audio tracks found";
}

function coerceAudioSwitchErrorDetail(value) {
  if (!value) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value
      .map((entry) => coerceAudioSwitchErrorDetail(entry))
      .filter(Boolean)
      .join("; ");
  }
  if (typeof value === "object") {
    const primaryMessages = [
      value.audio_switch_error,
      value.audio_switch_replacement_last_error,
      value.payload,
      value.response,
      value.detail,
    ]
      .map((entry) => coerceAudioSwitchErrorDetail(entry))
      .filter(Boolean)
      .map((entry) => entry.trim())
      .filter(Boolean);
    if (primaryMessages.length > 0) {
      return Array.from(new Set(primaryMessages)).join("; ");
    }
    const fallbackMessages = [
      value.message,
      value.error,
      value.reason,
      value.title,
    ]
      .map((entry) => coerceAudioSwitchErrorDetail(entry))
      .filter(Boolean)
      .map((entry) => entry.trim())
      .filter(Boolean);
    return Array.from(new Set(fallbackMessages)).join("; ");
  }
  return "";
}

function resolveAudioSwitchErrorFromPayloadOrError(payloadOrError, fallback = "Could not switch audio track.") {
  const rawError = coerceAudioSwitchErrorDetail(payloadOrError).trim();
  if (!rawError) {
    return fallback;
  }
  const singleLine = rawError.replace(/\s+/g, " ");
  return singleLine.length > 180 ? `${singleLine.slice(0, 177).trim()}...` : singleLine;
}

function TrackMenuItem({
  checked = false,
  disabled = false,
  error = false,
  kind,
  onSelect,
  pending = false,
  track,
}) {
  const unsupported = isBackendUnsupportedTrack(track);
  if (unsupported) {
    return (
      <div className="elvern-overlay__menu-item elvern-overlay__menu-item--disabled elvern-overlay__track-menu-item" role="menuitem">
        {kind === "subtitle" && shouldShowSubtitleWarning(track) ? (
          <span className="elvern-overlay__track-menu-warning" aria-hidden="true">!</span>
        ) : null}
      <span className="elvern-overlay__track-menu-label">{track.label}</span>
    </div>
  );
}
  return (
    <button
      aria-checked={checked}
      aria-busy={pending ? "true" : undefined}
      aria-disabled={disabled || pending ? "true" : undefined}
      aria-invalid={error ? "true" : undefined}
      className={`elvern-overlay__menu-item elvern-overlay__track-menu-item${pending ? " elvern-overlay__track-menu-item--pending" : ""}${error ? " elvern-overlay__track-menu-item--error" : ""}${disabled && !pending && !error ? " elvern-overlay__track-menu-item--locked" : ""}`}
      disabled={pending}
      onClick={() => {
        if (!disabled && !pending) {
          onSelect(track.id);
        }
      }}
      role="menuitemradio"
      type="button"
    >
      {error ? <span className="elvern-overlay__track-menu-error-mark" aria-hidden="true">!</span> : null}
      {pending ? <span className="elvern-overlay__track-menu-spinner" aria-hidden="true" /> : null}
      <span className="elvern-overlay__track-menu-label">{track.label}</span>
    </button>
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
  videoElementKey = 0,
  durationSeconds,
  sessionPayload,
  onSeekCommit,
  preparing = false,
  preparingTargetActive = false,
  preparingTargetSeconds = null,
  preparingMessage = "",
  errorMessage = "",
  title = "",
  fallbackFullscreenButtonLabel = null,
  cinemaModeActive = false,
  onToggleFullscreen = null,
  videoFitMode = VIDEO_FIT_STANDARD,
  onVideoFitModeChange = null,
  deviceClass = "desktop",
  hlsRef = null,
  onBackendAudioTrackSelect = null,
  onBackendSubtitleTrackSelect = null,
  onClientPreparedThruChange = null,
  trackRefreshKey = "",
  backendAudioTracks = [],
  backendSubtitleTracks = [],
  audioTrackScanStatus = "",
  audioTrackScanError = "",
}) {
  const layoutCapabilities = useMemo(() => resolveOverlayLayoutCapabilities(deviceClass), [deviceClass]);

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
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [pipActive, setPipActive] = useState(false);
  const [fullscreenActive, setFullscreenActive] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [controlsManuallyHidden, setControlsManuallyHidden] = useState(false);
  const [controlsFocused, setControlsFocused] = useState(false);
  const [isDraggingTimeline, setIsDraggingTimeline] = useState(false);
  const [activeBackendSubtitle, setActiveBackendSubtitle] = useState(null);
  const [activeSubtitleCueTexts, setActiveSubtitleCueTexts] = useState([]);
  const [pendingSubtitleTrackId, setPendingSubtitleTrackId] = useState("");
  const [subtitleTrackError, setSubtitleTrackError] = useState("");
  const [audioSwitchVisual, setAudioSwitchVisual] = useState(null);
  const [audioTrackError, setAudioTrackError] = useState("");
  const [audioTrackErrorTrackId, setAudioTrackErrorTrackId] = useState("");

  const controlsVisibleRef = useRef(true);
  const controlsManuallyHiddenRef = useRef(false);
  const lastSampledAbsoluteRef = useRef(null);
  const idleTimerRef = useRef(null);
  const overlayRootRef = useRef(null);
  const lastPointerFocusAtRef = useRef(0);
  const lastTouchTapSurfacePointerAtRef = useRef(0);
  const ignoreNextTapSurfaceClickRef = useRef(false);

  const safeDuration = Number.isFinite(durationSeconds) && durationSeconds > 0 ? durationSeconds : 0;
  const fullscreenLikeActive = fullscreenActive || cinemaModeActive;
  const phoneInlineMinimal = layoutCapabilities.variant === "phone" && !fullscreenLikeActive;
  const phoneFullscreenCinema = layoutCapabilities.variant === "phone" && fullscreenLikeActive;
  const currentVideoFitMode = normalizeVideoFitMode(videoFitMode);
  const videoFitToggleAvailable = fullscreenLikeActive && typeof onVideoFitModeChange === "function";
  const touchOptimizedOverlay = layoutCapabilities.variant === "phone" || layoutCapabilities.variant === "tablet";
  const idleHideDelayMs = layoutCapabilities.variant === "phone"
    ? PHONE_OVERLAY_IDLE_HIDE_DELAY_MS
    : (touchOptimizedOverlay
      ? TOUCH_OVERLAY_IDLE_HIDE_DELAY_MS
      : ELVERN_OVERLAY_IDLE_HIDE_DELAY_MS);

  const isAnyMenuOpen = showSpeedMenu || showCaptionsMenu || showAudioMenu || showMoreMenu;
  const {
    audioTracks,
    selectAudioTrack,
    selectSubtitleTrack,
    subtitleTracks: textTracks,
    subtitlesOff,
  } = usePlaybackTrackControls({
    backendAudioTracks,
    backendSubtitleTracks,
    hlsRef,
    onBackendAudioTrackSelect,
    onBackendSubtitleTrackSelect,
    sessionPayload,
    trackRefreshKey,
    videoElementKey,
    videoRef,
  });
  const sessionAudioSwitchState = String(sessionPayload?.audio_switch_state || "").trim().toLowerCase();
  const sessionPendingAudioStreamIndex = Number.isInteger(sessionPayload?.pending_audio_stream_index)
    ? sessionPayload.pending_audio_stream_index
    : null;
  const sessionAttachRevision = Number(sessionPayload?.attach_revision || 0);
  const sessionClientAttachRevision = Number(sessionPayload?.client_attach_revision || 0);
  const visualPendingAudioTrackId = audioSwitchVisual?.trackId || "";
  const backendAudioPending = audioTracks.some((track) => track.pending);
  const backendAudioPreparing = sessionAudioSwitchState === "preparing"
    && sessionPendingAudioStreamIndex != null;
  const backendAudioAttaching = sessionAudioSwitchState === "active"
    && sessionAttachRevision > 0
    && sessionAttachRevision > sessionClientAttachRevision
    && audioSwitchVisual?.phase === "attaching";
  const audioSwitchInProgress = Boolean(
    audioSwitchVisual
    || backendAudioPending
    || backendAudioPreparing
    || backendAudioAttaching,
  );
  const subtitleSwitchInProgress = Boolean(pendingSubtitleTrackId);
  const switchInteractionLocked = audioSwitchInProgress || subtitleSwitchInProgress;
  const closeAllMenus = useCallback(() => {
    setShowSpeedMenu(false);
    setShowCaptionsMenu(false);
    setShowAudioMenu(false);
    setShowMoreMenu(false);
  }, []);

  const playedNotCachedAbsoluteRanges = useMemo(() => (
    computePlayedNotCachedRanges(playedAbsoluteRanges, bufferedAbsoluteRanges, safeDuration)
  ), [playedAbsoluteRanges, bufferedAbsoluteRanges, safeDuration]);

  const fullscreenApiAvailable = isFullscreenApiAvailable(shellRef?.current || null);

  const setControlsVisibleValue = useCallback((nextValue) => {
    controlsVisibleRef.current = nextValue;
    setControlsVisible(nextValue);
    if (!nextValue) {
      closeAllMenus();
    }
  }, [closeAllMenus]);

  const setControlsManuallyHiddenValue = useCallback((nextValue) => {
    controlsManuallyHiddenRef.current = nextValue;
    setControlsManuallyHidden(nextValue);
  }, []);

  const hideControlsNow = useCallback(() => {
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    setControlsVisibleValue(false);
  }, [setControlsVisibleValue]);

  const hidePhoneFullscreenControlsNow = useCallback(() => {
    setControlsManuallyHiddenValue(true);
    hideControlsNow();
  }, [hideControlsNow, setControlsManuallyHiddenValue]);

  const refreshControlsTimer = useCallback(() => {
    if (!phoneFullscreenCinema) {
      setControlsManuallyHiddenValue(false);
    }
    setControlsVisibleValue(true);
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    if (!isPlaying || preparing || isDraggingTimeline || isAnyMenuOpen || controlsFocused) {
      return;
    }
    idleTimerRef.current = setTimeout(() => {
      setControlsVisibleValue(false);
      idleTimerRef.current = null;
    }, idleHideDelayMs);
  }, [
    controlsFocused,
    idleHideDelayMs,
    isAnyMenuOpen,
    isDraggingTimeline,
    isPlaying,
    preparing,
    phoneFullscreenCinema,
    setControlsManuallyHiddenValue,
    setControlsVisibleValue,
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
    const disableNativeControls = () => {
      if (video.controls) {
        video.controls = false;
      }
      video.removeAttribute("controls");
    };
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
      const nextBufferedRanges = readBufferedAbsoluteRanges(video, sessionPayload);
      const localTime = Number.isFinite(video.currentTime) ? video.currentTime : 0;
      const absolutePlayhead = toBrowserPlaybackAbsoluteSeconds(sessionPayload, localTime);
      setBufferedAbsoluteRanges(nextBufferedRanges);
      if (typeof onClientPreparedThruChange === "function") {
        onClientPreparedThruChange(
          getContiguousBufferedEndFromPosition(absolutePlayhead, nextBufferedRanges),
        );
      }
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
    disableNativeControls();
    updatePlaybackState();
    updateVolumeState();
    updateRateState();
    handleTimeUpdate();
    handleProgress();

    video.addEventListener("play", updatePlaybackState);
    video.addEventListener("playing", disableNativeControls);
    video.addEventListener("pause", updatePlaybackState);
    video.addEventListener("ended", updatePlaybackState);
    video.addEventListener("loadeddata", disableNativeControls);
    video.addEventListener("canplay", disableNativeControls);
    video.addEventListener("volumechange", updateVolumeState);
    video.addEventListener("ratechange", updateRateState);
    video.addEventListener("timeupdate", handleTimeUpdate);
    video.addEventListener("progress", handleProgress);
    video.addEventListener("seeking", handleSeeking);
    video.addEventListener("seeked", handleSeeked);
    video.addEventListener("loadedmetadata", handleProgress);

    return () => {
      video.removeEventListener("play", updatePlaybackState);
      video.removeEventListener("playing", disableNativeControls);
      video.removeEventListener("pause", updatePlaybackState);
      video.removeEventListener("ended", updatePlaybackState);
      video.removeEventListener("loadeddata", disableNativeControls);
      video.removeEventListener("canplay", disableNativeControls);
      video.removeEventListener("volumechange", updateVolumeState);
      video.removeEventListener("ratechange", updateRateState);
      video.removeEventListener("timeupdate", handleTimeUpdate);
      video.removeEventListener("progress", handleProgress);
      video.removeEventListener("seeking", handleSeeking);
      video.removeEventListener("seeked", handleSeeked);
      video.removeEventListener("loadedmetadata", handleProgress);
    };
  }, [onClientPreparedThruChange, sessionPayload, videoElementKey, videoRef]);

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
    closeAllMenus();
  }, [cinemaModeActive, fullscreenActive, closeAllMenus]);

  useEffect(() => {
    if (phoneInlineMinimal) {
      closeAllMenus();
      return;
    }
    setControlsVisibleValue(true);
  }, [closeAllMenus, phoneInlineMinimal, setControlsVisibleValue]);

  useEffect(() => {
    if (!phoneFullscreenCinema) {
      setControlsManuallyHiddenValue(false);
    }
  }, [phoneFullscreenCinema, setControlsManuallyHiddenValue]);

  const sessionIdentity = sessionPayload?.session_id || sessionPayload?.session_token || "";
  useEffect(() => {
    closeAllMenus();
    setControlsManuallyHiddenValue(false);
  }, [closeAllMenus, sessionIdentity, setControlsManuallyHiddenValue, videoElementKey]);

  useEffect(() => {
    if (!SUPPORTS_DOCUMENT || !isAnyMenuOpen) {
      return undefined;
    }
    const handleDocumentPointerDown = (event) => {
      const target = event?.target;
      if (!target || typeof target.closest !== "function") {
        return;
      }
      if (target.closest(".elvern-overlay__menu") || target.closest(".elvern-overlay__menu-host")) {
        return;
      }
      closeAllMenus();
    };
    document.addEventListener("pointerdown", handleDocumentPointerDown, true);
    document.addEventListener("mousedown", handleDocumentPointerDown, true);
    document.addEventListener("touchstart", handleDocumentPointerDown, true);
    return () => {
      document.removeEventListener("pointerdown", handleDocumentPointerDown, true);
      document.removeEventListener("mousedown", handleDocumentPointerDown, true);
      document.removeEventListener("touchstart", handleDocumentPointerDown, true);
    };
  }, [closeAllMenus, isAnyMenuOpen]);

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
    setControlsManuallyHiddenValue(false);
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
  }, [refreshControlsTimer, setControlsManuallyHiddenValue, videoRef]);

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
    closeAllMenus();
    refreshControlsTimer();
  }, [closeAllMenus, refreshControlsTimer, videoRef]);

  const handleTextTrackSelect = useCallback(async (trackId) => {
    if (switchInteractionLocked) {
      refreshControlsTimer();
      return;
    }
    const selectedTrack = textTracks.find((track) => track.id === trackId);
    if (!selectedTrack || isBackendUnsupportedTrack(selectedTrack)) {
      return;
    }
    setPendingSubtitleTrackId(trackId);
    setSubtitleTrackError("");
    refreshControlsTimer();
    try {
      const result = await selectSubtitleTrack(trackId);
      const prepared = result?.preparedSubtitle;
      if (prepared?.vtt_url) {
        setActiveBackendSubtitle({
          id: result.id,
          label: prepared.label || result.label,
          src: prepared.vtt_url,
        });
      }
      setPendingSubtitleTrackId("");
      closeAllMenus();
      refreshControlsTimer();
    } catch (trackError) {
      setPendingSubtitleTrackId("");
      setSubtitleTrackError("Could not prepare subtitle.");
      refreshControlsTimer();
    }
  }, [closeAllMenus, refreshControlsTimer, selectSubtitleTrack, switchInteractionLocked, textTracks]);

  const handleTextTrackOff = useCallback(() => {
    if (switchInteractionLocked) {
      refreshControlsTimer();
      return;
    }
    subtitlesOff();
    setActiveBackendSubtitle(null);
    setActiveSubtitleCueTexts([]);
    setPendingSubtitleTrackId("");
    setSubtitleTrackError("");
    closeAllMenus();
    refreshControlsTimer();
  }, [closeAllMenus, refreshControlsTimer, subtitlesOff, switchInteractionLocked]);

  useEffect(() => {
    const video = videoRef?.current || null;
    if (!video || !activeBackendSubtitle?.src || !SUPPORTS_DOCUMENT) {
      setActiveSubtitleCueTexts([]);
      return undefined;
    }
    const trackElement = document.createElement("track");
    trackElement.kind = "subtitles";
    trackElement.label = activeBackendSubtitle.label || "Subtitle";
    trackElement.srclang = "und";
    trackElement.src = activeBackendSubtitle.src;
    trackElement.default = true;
    let textTrack = null;
    let disposed = false;
    const updateCueTexts = () => {
      if (disposed || !textTrack?.activeCues) {
        setActiveSubtitleCueTexts([]);
        return;
      }
      const nextTexts = Array.from(textTrack.activeCues)
        .map((cue) => String(cue?.text || "").trim())
        .filter(Boolean);
      setActiveSubtitleCueTexts(nextTexts);
    };
    const attachCueListener = () => {
      textTrack = trackElement.track;
      if (!textTrack) {
        return;
      }
      textTrack.mode = "hidden";
      textTrack.addEventListener?.("cuechange", updateCueTexts);
      updateCueTexts();
    };
    trackElement.addEventListener("load", attachCueListener);
    video.appendChild(trackElement);
    attachCueListener();
    return () => {
      disposed = true;
      textTrack?.removeEventListener?.("cuechange", updateCueTexts);
      trackElement.removeEventListener("load", attachCueListener);
      try {
        video.removeChild(trackElement);
      } catch {
        // The video may have been remounted by a real attachment change.
      }
      setActiveSubtitleCueTexts([]);
    };
  }, [activeBackendSubtitle, videoElementKey, videoRef]);

  const handleAudioTrackSelect = useCallback(async (trackId) => {
    const selectedTrack = audioTracks.find((track) => track.id === trackId);
    if (!selectedTrack || isBackendUnsupportedTrack(selectedTrack)) {
      return;
    }
    if (switchInteractionLocked) {
      refreshControlsTimer();
      return;
    }
    const selectedStreamIndex = Number.isInteger(selectedTrack.index) ? selectedTrack.index : null;
    if ((selectedTrack.selected || selectedTrack.enabled) && !selectedTrack.pending) {
      setAudioSwitchVisual(null);
      setAudioTrackError("");
      setAudioTrackErrorTrackId("");
      refreshControlsTimer();
      return;
    }
    const activeTrack = audioTracks.find((track) => track.selected || track.enabled);
    setAudioSwitchVisual({
      trackId,
      streamIndex: selectedStreamIndex,
      previousTrackId: activeTrack?.id || "",
      previousStreamIndex: Number.isInteger(activeTrack?.index) ? activeTrack.index : null,
      phase: "requesting",
    });
    setAudioTrackError("");
    setAudioTrackErrorTrackId("");
    refreshControlsTimer();
    try {
      const payload = await selectAudioTrack(trackId);
      const hasSessionPayloadWrapper = Boolean(
        payload && Object.prototype.hasOwnProperty.call(payload, "sessionPayload"),
      );
      const sessionPayloadResponse = hasSessionPayloadWrapper ? payload.sessionPayload : payload;
      if (selectedTrack.source === "backend" && !sessionPayloadResponse) {
        setAudioSwitchVisual(null);
        setAudioTrackError(resolveAudioSwitchErrorFromPayloadOrError(payload));
        setAudioTrackErrorTrackId(trackId);
        refreshControlsTimer();
        return;
      }
      const switchState = String(sessionPayloadResponse?.audio_switch_state || "").trim().toLowerCase();
      const pendingStreamIndex = Number.isInteger(sessionPayloadResponse?.pending_audio_stream_index)
        ? sessionPayloadResponse.pending_audio_stream_index
        : null;
      const activeStreamIndex = Number.isInteger(sessionPayloadResponse?.active_audio_stream_index)
        ? sessionPayloadResponse.active_audio_stream_index
        : null;
      const attachRevision = Number(sessionPayloadResponse?.attach_revision || 0);
      const clientAttachRevision = Number(sessionPayloadResponse?.client_attach_revision || 0);
      const clientAttachPending = attachRevision > 0
        && attachRevision > clientAttachRevision
        && selectedStreamIndex != null
        && activeStreamIndex === selectedStreamIndex;
      const hasSwitchError = Boolean(String(sessionPayloadResponse?.audio_switch_error || "").trim());
      if (switchState === "failed" || hasSwitchError) {
        setAudioSwitchVisual(null);
        setAudioTrackError(resolveAudioSwitchErrorFromPayloadOrError(sessionPayloadResponse));
        setAudioTrackErrorTrackId(trackId);
      } else if (
        switchState === "preparing"
        && selectedStreamIndex != null
        && pendingStreamIndex === selectedStreamIndex
      ) {
        setAudioSwitchVisual((current) => ({
          trackId,
          streamIndex: selectedStreamIndex,
          previousTrackId: current?.previousTrackId || activeTrack?.id || "",
          previousStreamIndex: Number.isInteger(current?.previousStreamIndex)
            ? current.previousStreamIndex
            : Number.isInteger(activeTrack?.index) ? activeTrack.index : null,
          phase: "preparing",
        }));
        setAudioTrackError("");
        setAudioTrackErrorTrackId("");
      } else if (
        switchState === "active"
        && selectedStreamIndex != null
        && activeStreamIndex === selectedStreamIndex
      ) {
        setAudioSwitchVisual((current) => (
          clientAttachPending
            ? {
                trackId,
                streamIndex: selectedStreamIndex,
                previousTrackId: current?.previousTrackId || activeTrack?.id || "",
                previousStreamIndex: Number.isInteger(current?.previousStreamIndex)
                  ? current.previousStreamIndex
                  : Number.isInteger(activeTrack?.index) ? activeTrack.index : null,
                phase: "attaching",
              }
            : null
        ));
        setAudioTrackError("");
        setAudioTrackErrorTrackId("");
      } else if (selectedTrack.source === "backend") {
        setAudioSwitchVisual((current) => ({
          trackId,
          streamIndex: selectedStreamIndex,
          previousTrackId: current?.previousTrackId || activeTrack?.id || "",
          previousStreamIndex: Number.isInteger(current?.previousStreamIndex)
            ? current.previousStreamIndex
            : Number.isInteger(activeTrack?.index) ? activeTrack.index : null,
          phase: "requesting",
        }));
        setAudioTrackError("");
        setAudioTrackErrorTrackId("");
      } else {
        setAudioSwitchVisual(null);
        setAudioTrackError("");
        setAudioTrackErrorTrackId("");
      }
      refreshControlsTimer();
    } catch (trackError) {
      setAudioSwitchVisual(null);
      setAudioTrackError(resolveAudioSwitchErrorFromPayloadOrError(trackError));
      setAudioTrackErrorTrackId(trackId);
      refreshControlsTimer();
    }
  }, [audioTracks, refreshControlsTimer, selectAudioTrack, switchInteractionLocked]);

  useEffect(() => {
    if (errorMessage !== AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE || !audioSwitchVisual) {
      return;
    }
    setAudioTrackError(AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE);
    setAudioTrackErrorTrackId(audioSwitchVisual.trackId || "");
    setAudioSwitchVisual(null);
  }, [audioSwitchVisual, errorMessage]);

  useEffect(() => {
    if (errorMessage !== AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE) {
      return;
    }
    const switchState = String(sessionPayload?.audio_switch_state || "").trim().toLowerCase();
    const activeStreamIndex = Number.isInteger(sessionPayload?.active_audio_stream_index)
      ? sessionPayload.active_audio_stream_index
      : null;
    const selectedStreamIndex = Number.isInteger(sessionPayload?.selected_audio_stream_index)
      ? sessionPayload.selected_audio_stream_index
      : null;
    const attachRevision = Number(sessionPayload?.attach_revision || 0);
    const clientAttachRevision = Number(sessionPayload?.client_attach_revision || 0);
    const backendError = String(sessionPayload?.audio_switch_error || "").trim();
    if (
      switchState === "active"
      && !backendError
      && activeStreamIndex != null
      && activeStreamIndex === selectedStreamIndex
      && attachRevision > 0
      && clientAttachRevision >= attachRevision
    ) {
      setAudioSwitchVisual(null);
      setAudioTrackError("");
      setAudioTrackErrorTrackId("");
    }
  }, [
    errorMessage,
    sessionPayload?.active_audio_stream_index,
    sessionPayload?.audio_switch_error,
    sessionPayload?.audio_switch_state,
    sessionPayload?.attach_revision,
    sessionPayload?.client_attach_revision,
    sessionPayload?.selected_audio_stream_index,
  ]);

  useEffect(() => {
    if (errorMessage === AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE) {
      return;
    }
    const switchState = String(sessionPayload?.audio_switch_state || "").trim().toLowerCase();
    const pendingStreamIndex = Number.isInteger(sessionPayload?.pending_audio_stream_index)
      ? sessionPayload.pending_audio_stream_index
      : null;
    const activeStreamIndex = Number.isInteger(sessionPayload?.active_audio_stream_index)
      ? sessionPayload.active_audio_stream_index
      : null;
    const selectedStreamIndex = Number.isInteger(sessionPayload?.selected_audio_stream_index)
      ? sessionPayload.selected_audio_stream_index
      : null;
    const attachRevision = Number(sessionPayload?.attach_revision || 0);
    const clientAttachRevision = Number(sessionPayload?.client_attach_revision || 0);
    const backendPendingTrack = audioTracks.find((track) => (
      Number.isInteger(track.index)
      && pendingStreamIndex != null
      && track.index === pendingStreamIndex
    ));
    if (audioSwitchVisual) {
      const visualTrackExists = audioTracks.some((track) => track.id === audioSwitchVisual.trackId);
      if (!visualTrackExists) {
        setAudioSwitchVisual(null);
        return;
      }
      const requestStreamIndex = audioSwitchVisual.streamIndex;
      if (
        switchState === "preparing"
        && requestStreamIndex != null
        && pendingStreamIndex === requestStreamIndex
      ) {
        if (audioSwitchVisual.phase !== "preparing") {
          setAudioSwitchVisual({ ...audioSwitchVisual, phase: "preparing" });
        }
        setAudioTrackError("");
      } else if (
        switchState === "active"
        && requestStreamIndex != null
        && activeStreamIndex === requestStreamIndex
      ) {
        if (attachRevision > 0 && attachRevision > clientAttachRevision) {
          if (audioSwitchVisual.phase !== "attaching") {
            setAudioSwitchVisual({ ...audioSwitchVisual, phase: "attaching" });
          }
          setAudioTrackError("");
          setAudioTrackErrorTrackId("");
        } else {
          setAudioSwitchVisual(null);
          setAudioTrackError("");
          setAudioTrackErrorTrackId("");
        }
      } else if (
        (switchState === "failed" || String(sessionPayload?.audio_switch_error || "").trim())
        && (
          audioSwitchVisual.phase === "preparing"
          || (
            requestStreamIndex != null
            && (pendingStreamIndex === requestStreamIndex || selectedStreamIndex === requestStreamIndex)
          )
        )
      ) {
        setAudioSwitchVisual(null);
        setAudioTrackError(resolveAudioSwitchErrorFromPayloadOrError(sessionPayload));
        setAudioTrackErrorTrackId(audioSwitchVisual.trackId);
      }
      return;
    }
    if (switchState === "preparing" && backendPendingTrack) {
      setAudioSwitchVisual({
        trackId: backendPendingTrack.id,
        streamIndex: pendingStreamIndex,
        previousTrackId: "",
        previousStreamIndex: null,
        phase: "preparing",
      });
      setAudioTrackError("");
      setAudioTrackErrorTrackId("");
    } else if (
      audioTrackError === AUDIO_SWITCH_ATTACH_FAILURE_MESSAGE
      && switchState === "active"
      && !String(sessionPayload?.audio_switch_error || "").trim()
      && activeStreamIndex != null
      && activeStreamIndex === selectedStreamIndex
      && attachRevision > 0
      && clientAttachRevision >= attachRevision
    ) {
      setAudioTrackError("");
      setAudioTrackErrorTrackId("");
    }
  }, [
    audioSwitchVisual,
    audioTrackError,
    audioTracks,
    errorMessage,
    sessionPayload?.audio_switch_error,
    sessionPayload?.audio_switch_state,
    sessionPayload?.active_audio_stream_index,
    sessionPayload?.attach_revision,
    sessionPayload?.client_attach_revision,
    sessionPayload?.pending_audio_stream_index,
    sessionPayload?.selected_audio_stream_index,
  ]);

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
    closeAllMenus();
    refreshControlsTimer();
  }, [closeAllMenus, refreshControlsTimer, videoRef]);

  const handleVideoFitModeToggle = useCallback(() => {
    if (typeof onVideoFitModeChange !== "function") {
      return;
    }
    onVideoFitModeChange(currentVideoFitMode === VIDEO_FIT_ZOOM_FILL ? VIDEO_FIT_STANDARD : VIDEO_FIT_ZOOM_FILL);
    closeAllMenus();
    refreshControlsTimer();
  }, [closeAllMenus, currentVideoFitMode, onVideoFitModeChange, refreshControlsTimer]);

  const toggleFullscreen = useCallback(async () => {
    closeAllMenus();
    if (typeof onToggleFullscreen === "function") {
      onToggleFullscreen();
      refreshControlsTimer();
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
    refreshControlsTimer();
  }, [closeAllMenus, onToggleFullscreen, refreshControlsTimer, shellRef]);

  const handleTapSurfaceClick = useCallback((event) => {
    const pointerType = readPointerType(event);
    if (ignoreNextTapSurfaceClickRef.current) {
      ignoreNextTapSurfaceClickRef.current = false;
      event?.preventDefault?.();
      event?.stopPropagation?.();
      return;
    }
    const handledTouchRecently = Date.now() - lastTouchTapSurfacePointerAtRef.current < 700;
    if (isAnyMenuOpen) {
      closeAllMenus();
      refreshControlsTimer();
      return;
    }
    if (pointerType === "touch" || pointerType === "pen" || handledTouchRecently) {
      return;
    }
    if (phoneInlineMinimal) {
      if (controlsVisibleRef.current) {
        hideControlsNow();
        return;
      }
      refreshControlsTimer();
      return;
    }
    if (phoneFullscreenCinema) {
      const controlsAreVisuallyShown = controlsVisibleRef.current && !controlsManuallyHiddenRef.current;
      if (controlsAreVisuallyShown) {
        hidePhoneFullscreenControlsNow();
        return;
      }
      setControlsManuallyHiddenValue(false);
      refreshControlsTimer();
      return;
    }
    if (event?.detail === 0) {
      togglePlay();
      return;
    }
    togglePlay();
  }, [
    closeAllMenus,
    hideControlsNow,
    hidePhoneFullscreenControlsNow,
    isAnyMenuOpen,
    phoneFullscreenCinema,
    phoneInlineMinimal,
    refreshControlsTimer,
    setControlsManuallyHiddenValue,
    togglePlay,
  ]);

  const handleTapSurfacePointerUp = useCallback((event) => {
    const pointerType = readPointerType(event);
    if (pointerType !== "touch" && pointerType !== "pen") {
      return;
    }
    lastTouchTapSurfacePointerAtRef.current = Date.now();
    ignoreNextTapSurfaceClickRef.current = true;
    if (isAnyMenuOpen) {
      closeAllMenus();
      refreshControlsTimer();
      return;
    }
    if (phoneInlineMinimal) {
      if (controlsVisibleRef.current) {
        hideControlsNow();
        return;
      }
      refreshControlsTimer();
      return;
    }
    if (phoneFullscreenCinema) {
      const controlsAreVisuallyShown = controlsVisibleRef.current && !controlsManuallyHiddenRef.current;
      if (controlsAreVisuallyShown) {
        hidePhoneFullscreenControlsNow();
        return;
      }
      setControlsManuallyHiddenValue(false);
      refreshControlsTimer();
      return;
    }
    if (!isPlaying) {
      refreshControlsTimer();
      return;
    }
    if (controlsVisibleRef.current) {
      hideControlsNow();
      return;
    }
    refreshControlsTimer();
  }, [
    closeAllMenus,
    hideControlsNow,
    hidePhoneFullscreenControlsNow,
    isAnyMenuOpen,
    isPlaying,
    phoneFullscreenCinema,
    phoneInlineMinimal,
    refreshControlsTimer,
    setControlsManuallyHiddenValue,
  ]);

  const handleCenterTransportClick = useCallback((event) => {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    closeAllMenus();
    setControlsManuallyHiddenValue(false);
    togglePlay();
  }, [closeAllMenus, setControlsManuallyHiddenValue, togglePlay]);

  const handleCenterTransportPointerUp = useCallback((event) => {
    event?.stopPropagation?.();
  }, []);

  const handleInlineMaximizeClick = useCallback((event) => {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    toggleFullscreen();
  }, [toggleFullscreen]);

  const handleInlineMaximizePointerUp = useCallback((event) => {
    event?.stopPropagation?.();
  }, []);

  const handlePointerEnter = useCallback((event) => {
    if (event?.pointerType && event.pointerType !== "mouse") {
      return;
    }
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  const handlePointerDownCapture = useCallback(() => {
    lastPointerFocusAtRef.current = Date.now();
    setControlsFocused(false);
  }, []);

  const handleKeyDownCapture = useCallback((event) => {
    if (event?.key === "Tab") {
      lastPointerFocusAtRef.current = 0;
      return;
    }
    if (event?.key === "Escape" && isAnyMenuOpen) {
      consumeKeyboardEvent(event);
      closeAllMenus();
      refreshControlsTimer();
      return;
    }
    if (isSpaceKey(event)) {
      consumeKeyboardEvent(event);
      if (!event.repeat) {
        togglePlay();
      }
    }
  }, [closeAllMenus, isAnyMenuOpen, refreshControlsTimer, togglePlay]);

  const handleKeyUpCapture = useCallback((event) => {
    if (isSpaceKey(event)) {
      consumeKeyboardEvent(event);
    }
  }, []);

  const handlePointerMove = useCallback((event) => {
    if (event?.pointerType && event.pointerType !== "mouse") {
      return;
    }
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  const handlePointerLeave = useCallback((event) => {
    if (readPointerType(event) && readPointerType(event) !== "mouse") {
      return;
    }
    if (!isPlaying || preparing || isDraggingTimeline || isAnyMenuOpen || controlsFocused) {
      return;
    }
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    setControlsVisibleValue(false);
  }, [controlsFocused, isAnyMenuOpen, isDraggingTimeline, isPlaying, preparing, setControlsVisibleValue]);

  const handleFocusCapture = useCallback((event) => {
    if (event?.target?.closest?.(".elvern-overlay__tap-surface")) {
      setControlsFocused(false);
      refreshControlsTimer();
      return;
    }
    if (Date.now() - lastPointerFocusAtRef.current < 800) {
      setControlsFocused(false);
      refreshControlsTimer();
      return;
    }
    setControlsFocused(true);
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  const handleBlurCapture = useCallback((event) => {
    const next = event?.relatedTarget;
    if (next && overlayRootRef.current?.contains(next)) {
      return;
    }
    setControlsFocused(false);
  }, []);

  const handleTimelinePreview = useCallback(() => {
    closeAllMenus();
    refreshControlsTimer();
  }, [closeAllMenus, refreshControlsTimer]);

  const handleTimelineCommit = useCallback((targetSeconds) => {
    closeAllMenus();
    onSeekCommit?.(targetSeconds);
  }, [closeAllMenus, onSeekCommit]);

  const handleTimelineDragStart = useCallback(() => {
    closeAllMenus();
    setIsDraggingTimeline(true);
    refreshControlsTimer();
  }, [closeAllMenus, refreshControlsTimer]);

  const handleTimelineDragEnd = useCallback(() => {
    setIsDraggingTimeline(false);
    refreshControlsTimer();
  }, [refreshControlsTimer]);

  useEffect(() => {
    if (!isPlaying) {
      setControlsVisibleValue(true);
    }
  }, [isPlaying, setControlsVisibleValue]);

  useEffect(() => {
    if (preparing || errorMessage) {
      setControlsVisibleValue(true);
    }
  }, [errorMessage, preparing, setControlsVisibleValue]);

  const visible = phoneFullscreenCinema && controlsManuallyHidden
    ? false
    : phoneInlineMinimal
      ? controlsVisible
      : shouldOverlayBeVisible({
      isPlaying,
      preparing,
      hasError: Boolean(errorMessage),
      isDraggingTimeline,
      anyMenuOpen: isAnyMenuOpen,
      controlsFocused,
      lastInteractionAtMs: controlsVisible ? 0 : -(idleHideDelayMs + 1),
      nowMs: 0,
      idleHideDelayMs,
    });

  const visibilityClass = visible ? " elvern-overlay--visible" : " elvern-overlay--idle";
  const variantClass = ` elvern-overlay--variant-${layoutCapabilities.variant} elvern-overlay--${layoutCapabilities.variant}${phoneInlineMinimal ? " elvern-overlay--phone-inline-minimal" : ""}`;

  const captionActiveCount = textTracks.filter((track) => track.selected || track.mode === "showing").length;
  const audioMenuAvailable = audioTracks.length > 1;
  const captionsMenuAvailable = textTracks.length > 0;
  const phoneTrackShortcutButtons = phoneFullscreenCinema;
  const previousAudioTrackIdDuringClientAttach = audioSwitchVisual?.phase === "attaching"
    ? audioSwitchVisual.previousTrackId || ""
    : "";
  const visualPendingAudioTrackLabel = (
    audioTracks.find((track) => track.id === visualPendingAudioTrackId || track.pending)?.label
    || "audio"
  );
  const audioButtonErrorActive = Boolean(audioTrackError);
  const isAudioTrackChecked = useCallback((track) => {
    if (
      previousAudioTrackIdDuringClientAttach
      && visualPendingAudioTrackId
      && track.id === visualPendingAudioTrackId
    ) {
      return false;
    }
    if (previousAudioTrackIdDuringClientAttach && track.id === previousAudioTrackIdDuringClientAttach) {
      return true;
    }
    return Boolean(track.selected || track.enabled);
  }, [previousAudioTrackIdDuringClientAttach, visualPendingAudioTrackId]);

  const pipAvailable = SUPPORTS_DOCUMENT
    && Boolean(document.pictureInPictureEnabled)
    && Boolean(videoRef?.current?.requestPictureInPicture)
    && !videoRef?.current?.disablePictureInPicture;

  const fullscreenButtonLabel = fullscreenLikeActive
    ? "Exit fullscreen"
    : (fallbackFullscreenButtonLabel || "Fullscreen");
  const fullscreenButtonRendered = fullscreenApiAvailable || typeof onToggleFullscreen === "function";

  const moreMenuItems = useMemo(() => {
    if (!layoutCapabilities.useMoreMenu) {
      return [];
    }
    const items = [];
    if (captionsMenuAvailable && !layoutCapabilities.showInlineCaptions && !phoneTrackShortcutButtons) {
      items.push("captions");
    }
    if (audioMenuAvailable && !layoutCapabilities.showInlineAudio && !phoneTrackShortcutButtons) {
      items.push("audio");
    }
    if (!layoutCapabilities.showInlineSpeed) {
      items.push("speed");
    }
    if (pipAvailable && !layoutCapabilities.showInlinePip) {
      items.push("pip");
    }
    if (!layoutCapabilities.showInlineMuteToggle) {
      items.push("mute");
    }
    if (videoFitToggleAvailable) {
      items.push("video-fit");
    }
    return items;
  }, [
    audioMenuAvailable,
    captionsMenuAvailable,
    layoutCapabilities.showInlineAudio,
    layoutCapabilities.showInlineCaptions,
    layoutCapabilities.showInlineMuteToggle,
    layoutCapabilities.showInlinePip,
    layoutCapabilities.showInlineSpeed,
    layoutCapabilities.useMoreMenu,
    pipAvailable,
    phoneTrackShortcutButtons,
    videoFitToggleAvailable,
  ]);

  const moreButtonAvailable = !phoneInlineMinimal && moreMenuItems.length > 0;
  const trackMenuOpen = showCaptionsMenu || showAudioMenu;
  const trackMenuClass = trackMenuOpen ? " elvern-overlay--track-menu-open" : "";

  useEffect(() => {
    const shell = shellRef?.current || null;
    if (!shell?.classList) {
      return undefined;
    }
    const shouldRelaxTrackMenuTouch = phoneFullscreenCinema && trackMenuOpen;
    if (shouldRelaxTrackMenuTouch) {
      shell.classList.add("player-shell--track-menu-open");
    } else {
      shell.classList.remove("player-shell--track-menu-open");
    }
    return () => {
      shell.classList.remove("player-shell--track-menu-open");
    };
  }, [phoneFullscreenCinema, shellRef, trackMenuOpen]);

  const trackMenuSurfaceHandlers = {
    onClick: stopTrackMenuSurfaceEvent,
    onPointerDown: stopTrackMenuSurfaceEvent,
    onPointerMove: stopTrackMenuSurfaceEvent,
    onTouchMove: stopTrackMenuSurfaceEvent,
    onTouchStart: stopTrackMenuSurfaceEvent,
  };

  const subtitleTrackMenuContent = (
    <>
      <button
        aria-checked={captionActiveCount === 0}
        aria-disabled={switchInteractionLocked ? "true" : undefined}
        className={`elvern-overlay__menu-item elvern-overlay__track-menu-item${switchInteractionLocked ? " elvern-overlay__track-menu-item--locked" : ""}`}
        onClick={handleTextTrackOff}
        role="menuitemradio"
        type="button"
      >
        Off
      </button>
      {textTracks.length > 0 ? (
        textTracks.map((track) => (
          <TrackMenuItem
            checked={track.selected || track.mode === "showing"}
            disabled={switchInteractionLocked && pendingSubtitleTrackId !== track.id}
            key={track.id}
            kind="subtitle"
            onSelect={handleTextTrackSelect}
            pending={pendingSubtitleTrackId === track.id || track.pending}
            track={track}
          />
        ))
      ) : (
        <div className="elvern-overlay__menu-item elvern-overlay__menu-item--disabled elvern-overlay__track-menu-item" role="menuitem">
          No subtitle tracks found
        </div>
      )}
      {subtitleTrackError ? (
        <div className="elvern-overlay__track-menu-feedback" role="alert">{subtitleTrackError}</div>
      ) : null}
    </>
  );

  const audioTrackMenuContent = (
    <>
      {audioTracks.length > 0 ? (
        audioTracks.map((track) => (
          <TrackMenuItem
            checked={isAudioTrackChecked(track)}
            disabled={switchInteractionLocked && visualPendingAudioTrackId !== track.id && !track.pending}
            error={audioTrackErrorTrackId === track.id}
            key={track.id}
            kind="audio"
            onSelect={handleAudioTrackSelect}
            pending={visualPendingAudioTrackId === track.id || track.pending}
            track={track}
          />
        ))
      ) : (
        <div className="elvern-overlay__menu-item elvern-overlay__menu-item--disabled elvern-overlay__track-menu-item" role="menuitem">
          {resolveAudioTrackUnavailableMessage(audioTrackScanStatus, audioTrackScanError)}
        </div>
      )}
      {visualPendingAudioTrackId ? (
        <div className="elvern-overlay__track-menu-feedback" role="status">
          Preparing {visualPendingAudioTrackLabel}...
        </div>
      ) : null}
      {audioTrackError ? (
        <div className="elvern-overlay__track-menu-feedback" role="alert">{audioTrackError}</div>
      ) : null}
    </>
  );

  return (
    <div
      ref={overlayRootRef}
      className={`elvern-overlay${variantClass}${visibilityClass}${trackMenuClass}`}
      data-preparing={preparing ? "true" : "false"}
      onPointerEnter={handlePointerEnter}
      onPointerDownCapture={handlePointerDownCapture}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      onKeyDownCapture={handleKeyDownCapture}
      onKeyUpCapture={handleKeyUpCapture}
      onFocusCapture={handleFocusCapture}
      onBlurCapture={handleBlurCapture}
    >
      <button
        aria-label={visible ? "Hide playback controls" : "Show playback controls"}
        className="elvern-overlay__tap-surface"
        onClick={handleTapSurfaceClick}
        onPointerUp={handleTapSurfacePointerUp}
        tabIndex={-1}
        type="button"
      />

      <button
        aria-label={isPlaying ? "Pause" : "Play"}
        className="elvern-overlay__center-transport"
        onClick={handleCenterTransportClick}
        onPointerUp={handleCenterTransportPointerUp}
        type="button"
      >
        {isPlaying ? <PauseIcon className="elvern-overlay__center-transport-icon" /> : <PlayIcon className="elvern-overlay__center-transport-icon" />}
      </button>

      {phoneInlineMinimal && fullscreenButtonRendered ? (
        <button
          aria-label={fullscreenButtonLabel}
          aria-pressed={fullscreenLikeActive}
          className="elvern-overlay__inline-maximize"
          onClick={handleInlineMaximizeClick}
          onPointerUp={handleInlineMaximizePointerUp}
          type="button"
        >
          {fullscreenLikeActive
            ? <FullscreenExitIcon className="elvern-overlay__inline-maximize-icon" />
            : <InlineExpandIcon className="elvern-overlay__inline-maximize-icon" />}
        </button>
      ) : null}

      {activeSubtitleCueTexts.length > 0 ? (
        <div className="elvern-overlay__subtitle-layer" aria-live="polite">
          {activeSubtitleCueTexts.map((cueText, index) => (
            <span className="elvern-overlay__subtitle-cue" key={`${cueText}-${index}`}>
              {cueText}
            </span>
          ))}
        </div>
      ) : null}

      {!phoneInlineMinimal && (title || errorMessage) ? (
        <div className="elvern-overlay__top-bar" aria-hidden={visible ? undefined : true}>
          {title ? <span className="elvern-overlay__title">{title}</span> : null}
          {errorMessage ? <span className="elvern-overlay__error">{errorMessage}</span> : null}
        </div>
      ) : null}

      {!phoneInlineMinimal ? (
        <div className="elvern-overlay__bottom-bar">
          <div className="elvern-overlay__time-row">
            <span className="elvern-overlay__time-current" aria-label="Current time">
              {formatPlaybackTime(currentTimeSeconds)}
            </span>
            <span className="elvern-overlay__time-separator" aria-hidden="true">/</span>
            <span className="elvern-overlay__time-duration" aria-label="Movie duration">
              {formatPlaybackDuration(safeDuration)}
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
            preparingTargetSeconds={preparingTargetActive ? preparingTargetSeconds : null}
          />

          <div className="elvern-overlay__controls-row">
            {layoutCapabilities.showInlineMuteToggle ? (
              <div className="elvern-overlay__volume-group">
                <button
                  aria-label={isMuted || volume === 0 ? "Unmute" : "Mute"}
                  className="elvern-overlay__icon-button"
                  onClick={toggleMute}
                  type="button"
                >
                  {isMuted || volume === 0 ? <VolumeMuteIcon className="elvern-overlay__icon" /> : <VolumeOnIcon className="elvern-overlay__icon" />}
                </button>
                {layoutCapabilities.showInlineVolumeSlider ? (
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
                ) : null}
              </div>
            ) : null}

            <div className="elvern-overlay__spacer" aria-hidden="true" />

            {captionsMenuAvailable && layoutCapabilities.showInlineCaptions ? (
              <div className="elvern-overlay__menu-host">
                <button
                  aria-expanded={showCaptionsMenu}
                  aria-label="Subtitles"
                  className={`elvern-overlay__icon-button${captionActiveCount > 0 ? " elvern-overlay__icon-button--active" : ""}`}
                  onClick={() => {
                    setShowCaptionsMenu((value) => !value);
                    setShowSpeedMenu(false);
                    setShowAudioMenu(false);
                    setShowMoreMenu(false);
                    refreshControlsTimer();
                  }}
                  type="button"
                >
                  <CaptionsIcon className="elvern-overlay__icon" />
                </button>
                {showCaptionsMenu ? (
                  <div
                    className="elvern-overlay__menu elvern-overlay__track-menu"
                    {...trackMenuSurfaceHandlers}
                    role="menu"
                  >
                    {subtitleTrackMenuContent}
                  </div>
                ) : null}
              </div>
            ) : null}

            {audioMenuAvailable && layoutCapabilities.showInlineAudio ? (
              <div className="elvern-overlay__menu-host">
                <button
                  aria-expanded={showAudioMenu}
                  aria-label="Audio track"
                  className={`elvern-overlay__icon-button${audioButtonErrorActive ? " elvern-overlay__icon-button--error" : ""}`}
                  onClick={() => {
                    setShowAudioMenu((value) => !value);
                    setShowSpeedMenu(false);
                    setShowCaptionsMenu(false);
                    setShowMoreMenu(false);
                    refreshControlsTimer();
                  }}
                  type="button"
                >
                  <AudioTrackIcon className="elvern-overlay__icon" />
                </button>
                {showAudioMenu ? (
                  <div
                    className="elvern-overlay__menu elvern-overlay__track-menu"
                    {...trackMenuSurfaceHandlers}
                    role="menu"
                  >
                    {audioTrackMenuContent}
                  </div>
                ) : null}
              </div>
            ) : null}

            {layoutCapabilities.showInlineSpeed ? (
              <div className="elvern-overlay__menu-host">
                <button
                  aria-expanded={showSpeedMenu}
                  aria-label={`Playback speed ${playbackRate.toFixed(2)}x`}
                  className={`elvern-overlay__icon-button${playbackRate !== 1 ? " elvern-overlay__icon-button--active" : ""}`}
                  onClick={() => {
                    setShowSpeedMenu((value) => !value);
                    setShowCaptionsMenu(false);
                    setShowAudioMenu(false);
                    setShowMoreMenu(false);
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
            ) : null}

            {pipAvailable && layoutCapabilities.showInlinePip ? (
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

            {phoneTrackShortcutButtons ? (
              <div className="elvern-overlay__menu-host elvern-overlay__track-shortcut">
                <button
                  aria-expanded={showCaptionsMenu}
                  aria-label="Subtitles"
                  className={`elvern-overlay__icon-button${captionActiveCount > 0 ? " elvern-overlay__icon-button--active" : ""}`}
                  onClick={() => {
                    setShowCaptionsMenu((value) => !value);
                    setShowSpeedMenu(false);
                    setShowAudioMenu(false);
                    setShowMoreMenu(false);
                    refreshControlsTimer();
                  }}
                  type="button"
                >
                  <CaptionsIcon className="elvern-overlay__icon" />
                </button>
                {showCaptionsMenu ? (
                  <div
                    className="elvern-overlay__menu elvern-overlay__track-menu"
                    {...trackMenuSurfaceHandlers}
                    role="menu"
                  >
                    {subtitleTrackMenuContent}
                  </div>
                ) : null}
              </div>
            ) : null}

            {phoneTrackShortcutButtons ? (
              <div className="elvern-overlay__menu-host elvern-overlay__track-shortcut">
                <button
                  aria-expanded={showAudioMenu}
                  aria-label="Audio track"
                  className={`elvern-overlay__icon-button${audioButtonErrorActive ? " elvern-overlay__icon-button--error" : ""}`}
                  onClick={() => {
                    setShowAudioMenu((value) => !value);
                    setShowSpeedMenu(false);
                    setShowCaptionsMenu(false);
                    setShowMoreMenu(false);
                    refreshControlsTimer();
                  }}
                  type="button"
                >
                  <AudioTrackIcon className="elvern-overlay__icon" />
                </button>
                {showAudioMenu ? (
                  <div
                    className="elvern-overlay__menu elvern-overlay__track-menu"
                    {...trackMenuSurfaceHandlers}
                    role="menu"
                  >
                    {audioTrackMenuContent}
                  </div>
                ) : null}
              </div>
            ) : null}

            {moreButtonAvailable ? (
              <div className="elvern-overlay__menu-host">
                <button
                  aria-expanded={showMoreMenu}
                  aria-label="More options"
                  className="elvern-overlay__icon-button"
                  onClick={() => {
                    setShowMoreMenu((value) => !value);
                    setShowSpeedMenu(false);
                    setShowCaptionsMenu(false);
                    setShowAudioMenu(false);
                    refreshControlsTimer();
                  }}
                  type="button"
                >
                  <MoreIcon className="elvern-overlay__icon" />
                </button>
                {showMoreMenu ? (
                  <div className="elvern-overlay__menu elvern-overlay__menu--sheet" role="menu">
                    {moreMenuItems.includes("mute") ? (
                      <button
                        className="elvern-overlay__menu-item elvern-overlay__menu-item--row"
                        onClick={() => {
                          toggleMute();
                          setShowMoreMenu(false);
                        }}
                        role="menuitem"
                        type="button"
                      >
                        <span className="elvern-overlay__menu-item-icon">
                          {isMuted ? <VolumeMuteIcon className="elvern-overlay__icon" /> : <VolumeOnIcon className="elvern-overlay__icon" />}
                        </span>
                        <span>{isMuted ? "Unmute" : "Mute"}</span>
                      </button>
                    ) : null}
                    {moreMenuItems.includes("video-fit") ? (
                      <button
                        aria-pressed={currentVideoFitMode === VIDEO_FIT_ZOOM_FILL}
                        className="elvern-overlay__menu-item elvern-overlay__menu-item--row"
                        onClick={handleVideoFitModeToggle}
                        role="menuitem"
                        type="button"
                      >
                        <span className="elvern-overlay__menu-item-icon">
                          <InlineExpandIcon className="elvern-overlay__icon" />
                        </span>
                        <span>{currentVideoFitMode === VIDEO_FIT_ZOOM_FILL ? "Fit screen" : "Fill screen"}</span>
                      </button>
                    ) : null}
                    {moreMenuItems.includes("speed") ? (
                      <div className="elvern-overlay__menu-section">
                        <span className="elvern-overlay__menu-section-label">Speed</span>
                        <div className="elvern-overlay__menu-chip-row">
                          {PLAYBACK_RATES.map((rate) => (
                            <button
                              aria-checked={Math.abs(rate - playbackRate) < 0.01}
                              className="elvern-overlay__menu-chip"
                              key={rate}
                              onClick={() => handleRateSelect(rate)}
                              role="menuitemradio"
                              type="button"
                            >
                              {`${rate}x`}
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {moreMenuItems.includes("captions") ? (
                      <div className="elvern-overlay__menu-section">
                        <span className="elvern-overlay__menu-section-label">Subtitles</span>
                        <button
                          aria-checked={captionActiveCount === 0}
                          aria-disabled={switchInteractionLocked ? "true" : undefined}
                          className={`elvern-overlay__menu-item elvern-overlay__menu-item--row elvern-overlay__track-menu-item${switchInteractionLocked ? " elvern-overlay__track-menu-item--locked" : ""}`}
                          onClick={handleTextTrackOff}
                          role="menuitemradio"
                          type="button"
                        >
                          Off
                        </button>
                        {textTracks.map((track) => (
                          <TrackMenuItem
                            checked={track.selected || track.mode === "showing"}
                            disabled={switchInteractionLocked && pendingSubtitleTrackId !== track.id}
                            key={track.id}
                            kind="subtitle"
                            onSelect={handleTextTrackSelect}
                            pending={pendingSubtitleTrackId === track.id || track.pending}
                            track={track}
                          />
                        ))}
                        {subtitleTrackError ? (
                          <div className="elvern-overlay__track-menu-feedback" role="alert">{subtitleTrackError}</div>
                        ) : null}
                      </div>
                    ) : null}
                    {moreMenuItems.includes("audio") ? (
                      <div className="elvern-overlay__menu-section">
                        <span className="elvern-overlay__menu-section-label">Audio track</span>
                        {audioTracks.map((track) => (
                          <TrackMenuItem
                            checked={isAudioTrackChecked(track)}
                            disabled={switchInteractionLocked && visualPendingAudioTrackId !== track.id && !track.pending}
                            error={audioTrackErrorTrackId === track.id}
                            key={track.id}
                            kind="audio"
                            onSelect={handleAudioTrackSelect}
                            pending={visualPendingAudioTrackId === track.id || track.pending}
                            track={track}
                          />
                        ))}
                        {visualPendingAudioTrackId ? (
                          <div className="elvern-overlay__track-menu-feedback" role="status">
                            Preparing {visualPendingAudioTrackLabel}...
                          </div>
                        ) : null}
                        {audioTrackError ? (
                          <div className="elvern-overlay__track-menu-feedback" role="alert">{audioTrackError}</div>
                        ) : null}
                      </div>
                    ) : null}
                    {moreMenuItems.includes("pip") ? (
                      <button
                        className="elvern-overlay__menu-item elvern-overlay__menu-item--row"
                        onClick={togglePip}
                        role="menuitem"
                        type="button"
                      >
                        <span className="elvern-overlay__menu-item-icon">
                          <PipIcon className="elvern-overlay__icon" />
                        </span>
                        <span>{pipActive ? "Exit picture in picture" : "Picture in picture"}</span>
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
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
      ) : null}
    </div>
  );
}
