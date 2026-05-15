import { useCallback, useEffect, useState } from "react";

const HLS_TRACK_EVENTS = [
  "hlsManifestParsed",
  "hlsAudioTracksUpdated",
  "hlsAudioTrackSwitched",
  "hlsSubtitleTracksUpdated",
  "hlsSubtitleTrackSwitch",
  "hlsSubtitleTrackLoaded",
];
const TEXT_SUBTITLE_CODECS = new Set(["ass", "ssa", "subrip", "srt", "text", "webvtt", "vtt", "mov_text", "tx3g"]);
const IMAGE_SUBTITLE_CODECS = new Set(["dvd_subtitle", "dvdsub", "dvb_subtitle", "hdmv_pgs_subtitle", "pgs", "xsub"]);
const COMMENTARY_PATTERN = /\b(commentary|commentaries|commentaire|commentaires|director[’']?s?\s+commentary|audio\s+commentary|commentary\s+track)\b/i;
const GENERIC_AUDIO_LABEL_PATTERN = /^(audio|default audio|main audio|track)\s*\d*$/i;
const GENERIC_SUBTITLE_LABEL_PATTERN = /^(subtitle|subtitles|caption|captions|track)\s*\d*$/i;
const LANGUAGE_LABELS = new Map([
  ["ara", "Arabic"],
  ["ar", "Arabic"],
  ["chi", "Chinese"],
  ["zho", "Chinese"],
  ["zh", "Chinese"],
  ["cmn", "Chinese"],
  ["eng", "English"],
  ["en", "English"],
  ["fre", "French"],
  ["fra", "French"],
  ["fr", "French"],
  ["ger", "German"],
  ["deu", "German"],
  ["de", "German"],
  ["ita", "Italian"],
  ["it", "Italian"],
  ["jpn", "Japanese"],
  ["ja", "Japanese"],
  ["kor", "Korean"],
  ["ko", "Korean"],
  ["por", "Portuguese"],
  ["pt", "Portuguese"],
  ["rus", "Russian"],
  ["ru", "Russian"],
  ["spa", "Spanish"],
  ["es", "Spanish"],
]);
const SUBTITLE_CODEC_LABELS = new Map([
  ["ass", "ASS"],
  ["ssa", "SSA"],
  ["subrip", "SRT"],
  ["srt", "SRT"],
  ["text", "Text"],
  ["webvtt", "VTT"],
  ["vtt", "VTT"],
  ["mov_text", "Text"],
  ["tx3g", "Text"],
  ["hdmv_pgs_subtitle", "PGS"],
  ["pgs", "PGS"],
  ["dvd_subtitle", "DVD"],
  ["dvb_subtitle", "DVB"],
  ["dvdsub", "DVD"],
  ["xsub", "XSUB"],
]);
const AUDIO_CODEC_LABELS = new Map([
  ["aac", "AAC"],
  ["ac3", "AC3"],
  ["eac3", "E-AC3"],
  ["e-ac-3", "E-AC3"],
  ["truehd", "TrueHD"],
  ["dts", "DTS"],
  ["dca", "DTS"],
  ["flac", "FLAC"],
  ["opus", "Opus"],
  ["mp3", "MP3"],
  ["mp2", "MP2"],
  ["pcm_s16le", "PCM"],
  ["pcm_s24le", "PCM"],
]);

function readHls(hlsRef) {
  return hlsRef?.current || null;
}

function buildTrackLabel(track, fallback) {
  if (track?.label) {
    return track.label;
  }
  const title = track?.label || track?.title || track?.name;
  const language = track?.lang || track?.language;
  const codec = track?.codec;
  const channels = Number.isFinite(Number(track?.channels)) && Number(track.channels) > 0
    ? `${Number(track.channels)}ch`
    : "";
  const main = title || language || fallback;
  const details = [language && title && language !== title ? language : "", codec, channels].filter(Boolean).join(" · ");
  return details ? `${main} (${details})` : main;
}

function normalizeCodec(value) {
  return String(value || "").trim().toLowerCase();
}

function compactWhitespace(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function humanizeLanguage(value) {
  const raw = compactWhitespace(value);
  if (!raw || raw.toLowerCase() === "und" || raw.toLowerCase() === "unknown") {
    return "";
  }
  const normalized = raw.toLowerCase();
  if (LANGUAGE_LABELS.has(normalized)) {
    return LANGUAGE_LABELS.get(normalized);
  }
  return raw.length <= 3 ? raw.toUpperCase() : raw.replace(/[_-]+/g, " ");
}

function normalizeTitleToken(value) {
  const token = compactWhitespace(value)
    .replace(/[_-]+/g, " ")
    .replace(/\bsubrip\b/ig, "SRT")
    .replace(/\bwebvtt\b/ig, "VTT")
    .replace(/\bmov text\b/ig, "Text")
    .replace(/\bhdmv pgs subtitle\b/ig, "PGS")
    .replace(/\bdvd subtitle\b/ig, "DVD")
    .replace(/\b(\d)\s*channels?\b/ig, "$1ch");
  if (!token) {
    return "";
  }
  const lower = token.toLowerCase();
  if (lower === "subrip") {
    return "SRT";
  }
  if (lower === "hdmv pgs subtitle") {
    return "PGS";
  }
  return token;
}

function extractMeaningfulName(value, genericPattern) {
  const raw = compactWhitespace(value);
  if (!raw) {
    return "";
  }
  const normalized = raw
    .replace(/\s*\(([^)]*)\)\s*/gu, (_match, details) => {
      const usefulDetails = String(details || "")
        .split(/[\/,;|]+/u)
        .map(normalizeTitleToken)
        .filter(Boolean)
        .filter((entry) => !SUBTITLE_CODEC_LABELS.has(normalizeCodec(entry)));
      return usefulDetails.length > 0 ? ` ${usefulDetails.join(" ")}` : " ";
    });
  const cleaned = normalizeTitleToken(normalized);
  if (!cleaned || genericPattern.test(cleaned)) {
    return "";
  }
  return cleaned;
}

function codecLabel(value, kind) {
  const normalized = normalizeCodec(value);
  if (!normalized) {
    return "";
  }
  const labels = kind === "audio" ? AUDIO_CODEC_LABELS : SUBTITLE_CODEC_LABELS;
  return labels.get(normalized) || normalized.toUpperCase();
}

function formatChannels(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return "";
  }
  if (numeric === 1) {
    return "Mono";
  }
  if (numeric === 2) {
    return "2ch";
  }
  if (numeric === 6) {
    return "5.1";
  }
  if (numeric === 8) {
    return "7.1";
  }
  return `${numeric}ch`;
}

function uniqueParts(parts) {
  const seen = new Set();
  const result = [];
  for (const part of parts) {
    const cleaned = compactWhitespace(part);
    if (!cleaned) {
      continue;
    }
    const key = cleaned.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(cleaned);
  }
  return result;
}

function appendTrackDetails(baseLabel, details) {
  const base = compactWhitespace(baseLabel);
  const usefulDetails = uniqueParts(details).filter((detail) => !base.toLowerCase().includes(detail.toLowerCase()));
  if (usefulDetails.length === 0) {
    return base;
  }
  return `${base} · ${usefulDetails.join(" · ")}`;
}

function disambiguateLabels(tracks, kind) {
  const groups = new Map();
  for (const track of tracks) {
    const key = compactWhitespace(track.label).toLowerCase();
    groups.set(key, [...(groups.get(key) || []), track]);
  }
  const pickUniqueDetail = (track, group) => {
    const candidates = track._disambiguators || [];
    for (let index = 0; index < candidates.length; index += 1) {
      const candidate = candidates[index];
      if (!candidate) {
        continue;
      }
      const collision = group.some((other) => (
        other !== track
        && compactWhitespace((other._disambiguators || [])[index]).toLowerCase() === compactWhitespace(candidate).toLowerCase()
      ));
      if (!collision) {
        return [candidate];
      }
    }
    return candidates.slice(0, 1);
  };
  return tracks.map((track) => {
    const group = groups.get(compactWhitespace(track.label).toLowerCase()) || [];
    const forceDetails =
      kind === "subtitle"
        ? Boolean(track.imageBased)
        : Boolean(track._labelNeedsAudioDetails);
    if (group.length <= 1 && !forceDetails) {
      return track;
    }
    const details = kind === "audio" && track._labelNeedsAudioDetails
      ? (track._disambiguators || []).filter((detail) => !["Default"].includes(detail)).slice(0, 2)
      : pickUniqueDetail(track, group);
    return {
      ...track,
      label: appendTrackDetails(track.label, details),
    };
  }).map(({ _disambiguators, _labelNeedsAudioDetails, ...track }) => track);
}

function isCommentaryTrack(track) {
  if (!track) {
    return false;
  }
  if (track.disposition_commentary || track.commentary) {
    return true;
  }
  const haystack = [
    track.title,
    track.label,
    track.name,
    track.language,
    track.lang,
  ].filter(Boolean).join(" ");
  return COMMENTARY_PATTERN.test(haystack);
}

function buildBackendSubtitleLabel(track, fallback) {
  const title = extractMeaningfulName(track?.title || "", GENERIC_SUBTITLE_LABEL_PATTERN);
  if (title) {
    return title;
  }
  const label = extractMeaningfulName(track?.label || "", GENERIC_SUBTITLE_LABEL_PATTERN);
  if (label) {
    return label;
  }
  return humanizeLanguage(track?.language) || fallback;
}

function buildBackendAudioLabel(track, fallback) {
  const title = extractMeaningfulName(track?.title || "", GENERIC_AUDIO_LABEL_PATTERN);
  if (title) {
    return title;
  }
  const label = extractMeaningfulName(track?.label || "", GENERIC_AUDIO_LABEL_PATTERN);
  if (label) {
    return label;
  }
  const language = humanizeLanguage(track?.language);
  if (track?.disposition_default && !language) {
    return "Default";
  }
  return language || fallback;
}

function buildSubtitleDisambiguators(track) {
  return uniqueParts([
    track?.disposition_forced || track?.forced ? "Forced" : "",
    track?.disposition_default ? "Default" : "",
    codecLabel(track?.codec, "subtitle"),
    Number.isInteger(track?.index) ? `Stream ${track.index}` : "",
  ]);
}

function buildAudioDisambiguators(track) {
  return uniqueParts([
    codecLabel(track?.codec, "audio"),
    formatChannels(track?.channels),
    track?.disposition_default ? "Default" : "",
    humanizeLanguage(track?.language),
    Number.isInteger(track?.index) ? `Stream ${track.index}` : "",
  ]);
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
  return collected.filter((track) => !isCommentaryTrack(track));
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
  return collected.filter((track) => !isCommentaryTrack(track));
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
  })).filter((track) => !isCommentaryTrack(track));
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
  })).filter((track) => !isCommentaryTrack(track));
}

function normalizeBackendSubtitleTracks(tracks) {
  if (!Array.isArray(tracks)) {
    return [];
  }
  const normalized = tracks
    .filter((track) => !isCommentaryTrack(track))
    .map((track, index) => {
      const trackSource = track.track_source || track.source || "";
      const textBased = Boolean(track.text_based || TEXT_SUBTITLE_CODECS.has(normalizeCodec(track.codec)));
      const imageBased = Boolean(track.image_based || IMAGE_SUBTITLE_CODECS.has(normalizeCodec(track.codec)));
      const fallbackOnly = trackSource === "subtitle_table_fallback";
      return {
        id: `backend-subtitle-${track.index ?? track.id ?? index}`,
        index: Number.isInteger(track.index) ? track.index : index,
        label: buildBackendSubtitleLabel(track, `Subtitle ${index + 1}`),
        language: track.language || "",
        mode: "disabled",
        selected: false,
        source: "backend",
        codec: track.codec || "",
        codecLongName: track.codec_long_name || "",
        browserSupported: Boolean(
          !fallbackOnly && (
            track.browser_supported
            || textBased
            || TEXT_SUBTITLE_CODECS.has(normalizeCodec(track.codec))
          ),
        ),
        unsupportedReason: "",
        textBased,
        imageBased,
        trackSource,
        _disambiguators: buildSubtitleDisambiguators(track),
      };
    })
    .sort((left, right) => {
      const leftRank = left.browserSupported ? 0 : left.imageBased ? 2 : 1;
      const rightRank = right.browserSupported ? 0 : right.imageBased ? 2 : 1;
      if (leftRank !== rightRank) {
        return leftRank - rightRank;
      }
      if (left.browserSupported !== right.browserSupported) {
        return left.browserSupported ? -1 : 1;
      }
      if (left.imageBased !== right.imageBased) {
        return left.imageBased ? 1 : -1;
      }
      return left.index - right.index;
    });
  return disambiguateLabels(normalized, "subtitle");
}

function normalizeBackendAudioTracks(tracks, sessionPayload = null) {
  if (!Array.isArray(tracks)) {
    return [];
  }
  const activeIndex = Number.isInteger(sessionPayload?.active_audio_stream_index)
    ? sessionPayload.active_audio_stream_index
    : Number.isInteger(sessionPayload?.selected_audio_stream_index)
      ? sessionPayload.selected_audio_stream_index
      : null;
  const pendingIndex = Number.isInteger(sessionPayload?.pending_audio_stream_index)
    ? sessionPayload.pending_audio_stream_index
    : null;
  const filteredTracks = tracks.filter((track) => !isCommentaryTrack(track));
  let fallbackSelectedStreamIndex = null;
  if (activeIndex == null && filteredTracks.length > 0) {
    const defaultTrack = filteredTracks.find((track) => Boolean(track?.disposition_default));
    const fallbackTrack = defaultTrack || filteredTracks[0];
    const fallbackOrdinal = filteredTracks.indexOf(fallbackTrack);
    fallbackSelectedStreamIndex = Number.isInteger(fallbackTrack?.index)
      ? fallbackTrack.index
      : fallbackOrdinal;
  }
  const switchState = String(sessionPayload?.audio_switch_state || "").trim().toLowerCase();
  const normalized = filteredTracks.map((track, index) => {
    const streamIndex = Number.isInteger(track.index) ? track.index : index;
    const label = buildBackendAudioLabel(track, `Audio ${index + 1}`);
    const hasExplicitName = Boolean(
      extractMeaningfulName(track?.title || "", GENERIC_AUDIO_LABEL_PATTERN)
      || extractMeaningfulName(track?.label || "", GENERIC_AUDIO_LABEL_PATTERN),
    );
    const selected = activeIndex != null
      ? streamIndex === activeIndex
      : streamIndex === fallbackSelectedStreamIndex;
    return {
      id: `backend-audio-${streamIndex}`,
      index: streamIndex,
      label,
      language: track.language || "",
      enabled: selected,
      selected,
      pending: pendingIndex === streamIndex && switchState !== "active",
      source: "backend",
      codec: track.codec || "",
      codecLongName: track.codec_long_name || "",
      browserSupported: true,
      switchRequiresPreparation: true,
      switchState: sessionPayload?.audio_switch_state || "",
      trackSource: track.track_source || track.source || "",
      _disambiguators: buildAudioDisambiguators({ ...track, index: streamIndex }),
      _labelNeedsAudioDetails: !hasExplicitName,
    };
  });
  return disambiguateLabels(normalized, "audio");
}

function resolveTrackState({
  backendAudioTracks = [],
  backendSubtitleTracks = [],
  sessionPayload = null,
  video,
  hls,
}) {
  const hlsSubtitleTracks = collectHlsSubtitleTracks(hls);
  const hlsAudioTracks = collectHlsAudioTracks(hls);
  const nativeSubtitleTracks = collectNativeSubtitleTracks(video);
  const nativeAudioTracks = collectNativeAudioTracks(video);
  const backendSubtitleList = normalizeBackendSubtitleTracks(backendSubtitleTracks);
  const backendAudioList = normalizeBackendAudioTracks(backendAudioTracks, sessionPayload);
  const backendSubtitlesAuthoritative = Array.isArray(backendSubtitleTracks) && backendSubtitleTracks.length > 0;
  const backendAudioAuthoritative = Array.isArray(backendAudioTracks) && backendAudioTracks.length > 0;
  const subtitleTracks = backendSubtitlesAuthoritative
    ? backendSubtitleList
    : hlsSubtitleTracks.length > 0
      ? hlsSubtitleTracks
      : nativeSubtitleTracks;
  const audioTracks = backendAudioAuthoritative
    ? backendAudioList
    : hlsAudioTracks.length > 0
    ? hlsAudioTracks
    : nativeAudioTracks.length > 0
      ? nativeAudioTracks
      : [];
  const source = backendSubtitlesAuthoritative || backendAudioAuthoritative
    ? "backend"
    : hlsSubtitleTracks.length > 0 || hlsAudioTracks.length > 0
    ? "hls_js"
    : nativeSubtitleTracks.length > 0 || nativeAudioTracks.length > 0
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
  backendAudioTracks = [],
  backendSubtitleTracks = [],
  hlsRef = null,
  onBackendAudioTrackSelect = null,
  onBackendSubtitleTrackSelect = null,
  sessionPayload = null,
  trackRefreshKey = "",
  videoElementKey = 0,
  videoRef = null,
} = {}) {
  const [selectedBackendSubtitleTrackId, setSelectedBackendSubtitleTrackId] = useState(null);
  const [trackState, setTrackState] = useState(() => resolveTrackState({
    backendAudioTracks,
    backendSubtitleTracks,
    hls: readHls(hlsRef),
    sessionPayload,
    video: videoRef?.current || null,
  }));

  const refreshTrackState = useCallback(() => {
    setTrackState(resolveTrackState({
      backendAudioTracks,
      backendSubtitleTracks,
      hls: readHls(hlsRef),
      sessionPayload,
      video: videoRef?.current || null,
    }));
  }, [backendAudioTracks, backendSubtitleTracks, hlsRef, sessionPayload, videoRef]);

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
    setSelectedBackendSubtitleTrackId(null);
    refreshTrackState();
  }, [hlsRef, refreshTrackState, videoRef]);

  const selectSubtitleTrack = useCallback(async (trackId) => {
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
    } else if (selected.source === "native") {
      const video = videoRef?.current || null;
      if (video?.textTracks) {
        for (let index = 0; index < video.textTracks.length; index += 1) {
          video.textTracks[index].mode = index === selected.index ? "showing" : "disabled";
        }
      }
    } else if (selected.source === "backend" && selected.browserSupported) {
      setSelectedBackendSubtitleTrackId(selected.id);
      const prepared = await onBackendSubtitleTrackSelect?.(selected);
      refreshTrackState();
      return { ...selected, preparedSubtitle: prepared || null };
    }
    refreshTrackState();
    return selected;
  }, [hlsRef, onBackendSubtitleTrackSelect, refreshTrackState, trackState.subtitleTracks, videoRef]);

  const selectAudioTrack = useCallback(async (trackId) => {
    const selected = trackState.audioTracks.find((track) => track.id === trackId);
    if (!selected) {
      return null;
    }
    if (selected.source === "hls_js") {
      const hls = readHls(hlsRef);
      if (hls) {
        hls.audioTrack = selected.index;
      }
    } else if (selected.source === "native") {
      const native = videoRef?.current?.audioTracks;
      if (native) {
        for (let index = 0; index < native.length; index += 1) {
          native[index].enabled = index === selected.index;
        }
      }
    } else if (selected.source === "backend" && selected.browserSupported) {
      const response = await onBackendAudioTrackSelect?.(selected);
      refreshTrackState();
      return { ...selected, sessionPayload: response || null };
    }
    refreshTrackState();
    return selected;
  }, [hlsRef, onBackendAudioTrackSelect, refreshTrackState, trackState.audioTracks, videoRef]);

  const resolvedAudioTracks = trackState.audioTracks;
  const resolvedSubtitleTracks = trackState.subtitleTracks.map((track) => (
    track.source === "backend"
      ? { ...track, selected: track.id === selectedBackendSubtitleTrackId }
      : track
  ));

  return {
    ...trackState,
    audioTracks: resolvedAudioTracks,
    refreshTrackState,
    selectAudioTrack,
    selectSubtitleTrack,
    selectedAudioTrackId: resolvedAudioTracks.find((track) => track.selected)?.id || null,
    selectedSubtitleTrackId: resolvedSubtitleTracks.find((track) => track.selected)?.id || null,
    subtitleTracks: resolvedSubtitleTracks,
    subtitlesOff,
  };
}

export {
  collectHlsAudioTracks,
  collectHlsSubtitleTracks,
  collectNativeAudioTracks,
  collectNativeSubtitleTracks,
  isCommentaryTrack,
  normalizeBackendAudioTracks,
  normalizeBackendSubtitleTracks,
  resolveTrackState,
};
