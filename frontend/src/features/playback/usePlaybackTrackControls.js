import { useCallback, useEffect, useState } from "react";

const HLS_TRACK_EVENTS = [
  "hlsManifestParsed",
  "hlsAudioTracksUpdated",
  "hlsAudioTrackSwitched",
  "hlsSubtitleTracksUpdated",
  "hlsSubtitleTrackSwitch",
  "hlsSubtitleTrackLoaded",
];

function readHls(hlsRef) {
  return hlsRef?.current || null;
}

function buildTrackLabel(track, fallback) {
  return track?.label || track?.name || track?.lang || track?.language || fallback;
}

function collectNativeSubtitleTracks(video) {
  if (!video?.textTracks || typeof video.textTracks.length !== "number") {
    return [];
  }
  const collected = [];
  for (let index = 0; index < video.textTracks.length; index += 1) {
    const track = video.textTracks[index];
    if (track.kind === "subtitles" || track.kind === "captions") {
      collected.push({
        id: `native-subtitle-${index}`,
        index,
        label: buildTrackLabel(track, `Subtitle ${index + 1}`),
        language: track.language || "",
        mode: track.mode,
        selected: track.mode === "showing",
        source: "native",
      });
    }
  }
  return collected;
}

function collectNativeAudioTracks(video) {
  const native = video?.audioTracks;
  if (!native || typeof native.length !== "number") {
    return [];
  }
  const collected = [];
  for (let index = 0; index < native.length; index += 1) {
    const track = native[index];
    collected.push({
      id: `native-audio-${index}`,
      index,
      label: buildTrackLabel(track, `Audio ${index + 1}`),
      language: track.language || "",
      enabled: Boolean(track.enabled),
      selected: Boolean(track.enabled),
      source: "native",
    });
  }
  return collected;
}

function collectHlsSubtitleTracks(hls) {
  if (!Array.isArray(hls?.subtitleTracks) || hls.subtitleTracks.length === 0) {
    return [];
  }
  const selectedIndex = Number.isInteger(hls.subtitleTrack) ? hls.subtitleTrack : -1;
  const displayEnabled = hls.subtitleDisplay !== false;
  return hls.subtitleTracks.map((track, index) => ({
    id: `hls-subtitle-${index}`,
    index,
    label: buildTrackLabel(track, `Subtitle ${index + 1}`),
    language: track.lang || track.language || "",
    mode: displayEnabled && selectedIndex === index ? "showing" : "disabled",
    selected: displayEnabled && selectedIndex === index,
    source: "hls_js",
  }));
}

function collectHlsAudioTracks(hls) {
  if (!Array.isArray(hls?.audioTracks) || hls.audioTracks.length === 0) {
    return [];
  }
  const selectedIndex = Number.isInteger(hls.audioTrack) ? hls.audioTrack : 0;
  return hls.audioTracks.map((track, index) => ({
    id: `hls-audio-${index}`,
    index,
    label: buildTrackLabel(track, `Audio ${index + 1}`),
    language: track.lang || track.language || "",
    enabled: selectedIndex === index,
    selected: selectedIndex === index,
    source: "hls_js",
  }));
}

function resolveTrackState({ video, hls }) {
  const hlsSubtitleTracks = collectHlsSubtitleTracks(hls);
  const hlsAudioTracks = collectHlsAudioTracks(hls);
  const subtitleTracks = hlsSubtitleTracks.length > 0
    ? hlsSubtitleTracks
    : collectNativeSubtitleTracks(video);
  const audioTracks = hlsAudioTracks.length > 0
    ? hlsAudioTracks
    : collectNativeAudioTracks(video);
  const source = hlsSubtitleTracks.length > 0 || hlsAudioTracks.length > 0
    ? "hls_js"
    : subtitleTracks.length > 0 || audioTracks.length > 0
      ? "native"
      : "none";

  return {
    audioTracks,
    selectedAudioTrackId: audioTracks.find((track) => track.selected)?.id || null,
    selectedSubtitleTrackId: subtitleTracks.find((track) => track.selected)?.id || null,
    source,
    subtitleTracks,
  };
}

export function usePlaybackTrackControls({
  hlsRef = null,
  trackRefreshKey = "",
  videoElementKey = 0,
  videoRef = null,
} = {}) {
  const [trackState, setTrackState] = useState(() => resolveTrackState({
    hls: readHls(hlsRef),
    video: videoRef?.current || null,
  }));

  const refreshTrackState = useCallback(() => {
    setTrackState(resolveTrackState({
      hls: readHls(hlsRef),
      video: videoRef?.current || null,
    }));
  }, [hlsRef, videoRef]);

  useEffect(() => {
    const video = videoRef?.current || null;
    const hls = readHls(hlsRef);
    refreshTrackState();

    const nativeLists = [
      video?.textTracks,
      video?.audioTracks,
    ].filter((list) => list?.addEventListener);
    nativeLists.forEach((list) => {
      list.addEventListener("change", refreshTrackState);
      list.addEventListener("addtrack", refreshTrackState);
      list.addEventListener("removetrack", refreshTrackState);
    });
    video?.addEventListener?.("loadedmetadata", refreshTrackState);

    if (hls?.on) {
      HLS_TRACK_EVENTS.forEach((eventName) => {
        hls.on(eventName, refreshTrackState);
      });
    }

    return () => {
      nativeLists.forEach((list) => {
        list.removeEventListener("change", refreshTrackState);
        list.removeEventListener("addtrack", refreshTrackState);
        list.removeEventListener("removetrack", refreshTrackState);
      });
      video?.removeEventListener?.("loadedmetadata", refreshTrackState);
      if (hls?.off) {
        HLS_TRACK_EVENTS.forEach((eventName) => {
          hls.off(eventName, refreshTrackState);
        });
      }
    };
  }, [hlsRef, refreshTrackState, trackRefreshKey, videoElementKey, videoRef]);

  const subtitlesOff = useCallback(() => {
    const hls = readHls(hlsRef);
    if (hls && Array.isArray(hls.subtitleTracks) && hls.subtitleTracks.length > 0) {
      hls.subtitleTrack = -1;
      hls.subtitleDisplay = false;
    }
    const video = videoRef?.current || null;
    if (video?.textTracks) {
      for (let index = 0; index < video.textTracks.length; index += 1) {
        video.textTracks[index].mode = "disabled";
      }
    }
    refreshTrackState();
  }, [hlsRef, refreshTrackState, videoRef]);

  const selectSubtitleTrack = useCallback((trackId) => {
    const selected = trackState.subtitleTracks.find((track) => track.id === trackId);
    if (!selected) {
      return;
    }
    if (selected.source === "hls_js") {
      const hls = readHls(hlsRef);
      if (hls) {
        hls.subtitleTrack = selected.index;
        hls.subtitleDisplay = true;
      }
    } else {
      const video = videoRef?.current || null;
      if (video?.textTracks) {
        for (let index = 0; index < video.textTracks.length; index += 1) {
          video.textTracks[index].mode = index === selected.index ? "showing" : "disabled";
        }
      }
    }
    refreshTrackState();
  }, [hlsRef, refreshTrackState, trackState.subtitleTracks, videoRef]);

  const selectAudioTrack = useCallback((trackId) => {
    const selected = trackState.audioTracks.find((track) => track.id === trackId);
    if (!selected) {
      return;
    }
    if (selected.source === "hls_js") {
      const hls = readHls(hlsRef);
      if (hls) {
        hls.audioTrack = selected.index;
      }
    } else {
      const native = videoRef?.current?.audioTracks;
      if (native) {
        for (let index = 0; index < native.length; index += 1) {
          native[index].enabled = index === selected.index;
        }
      }
    }
    refreshTrackState();
  }, [hlsRef, refreshTrackState, trackState.audioTracks, videoRef]);

  return {
    ...trackState,
    refreshTrackState,
    selectAudioTrack,
    selectSubtitleTrack,
    subtitlesOff,
  };
}

export {
  collectHlsAudioTracks,
  collectHlsSubtitleTracks,
  collectNativeAudioTracks,
  collectNativeSubtitleTracks,
  resolveTrackState,
};
