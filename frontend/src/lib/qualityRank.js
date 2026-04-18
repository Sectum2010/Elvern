const RANK_DEFINITIONS = [
  {
    key: "diamond",
    label: "Diamond",
    minScore: 15,
    description: "Reference-grade library copy with minimal compromise.",
  },
  {
    key: "gold",
    label: "Gold",
    minScore: 11,
    description: "Excellent quality, just below reference tier.",
  },
  {
    key: "silver",
    label: "Silver",
    minScore: 7,
    description: "Good quality, highly watchable.",
  },
  {
    key: "iron",
    label: "Iron",
    minScore: 5,
    description: "Decent but clearly compromised.",
  },
  {
    key: "bronze",
    label: "Bronze",
    minScore: 3,
    description: "Lower-quality convenience copy.",
  },
  {
    key: "wood",
    label: "Wood",
    minScore: Number.NEGATIVE_INFINITY,
    description: "Basic fallback copy.",
  },
];

function hasToken(haystack, ...tokens) {
  return tokens.some((token) => haystack.includes(token));
}

function detectSource(haystack) {
  if (hasToken(haystack, "remux")) {
    return { score: 6, label: "REMUX" };
  }
  if (hasToken(haystack, "bluray", "blu-ray", "bdrip", "bdrip")) {
    return { score: 5, label: "BluRay" };
  }
  if (hasToken(haystack, "web-dl", "webdl")) {
    return { score: 4, label: "WEB-DL" };
  }
  if (hasToken(haystack, "webrip", "web-rip")) {
    return { score: 3, label: "WEBRip" };
  }
  if (hasToken(haystack, "hdtv", "hdrip", "dvdrip")) {
    return { score: 2, label: "Legacy source" };
  }
  return { score: 0, label: null };
}

function detectResolution(item, haystack) {
  const width = Number(item.width || 0);
  const height = Number(item.height || 0);
  if (width >= 3800 || height >= 2100 || hasToken(haystack, "2160p", "4k", "uhd")) {
    return { score: 4, label: "2160p" };
  }
  if (width >= 1900 || height >= 1000 || hasToken(haystack, "1080p")) {
    return { score: 3, label: "1080p" };
  }
  if (width >= 1200 || height >= 700 || hasToken(haystack, "720p")) {
    return { score: 2, label: "720p" };
  }
  if (hasToken(haystack, "480p", "576p")) {
    return { score: 1, label: "SD" };
  }
  return { score: 0, label: null };
}

function detectAudio(haystack) {
  if (hasToken(haystack, "atmos")) {
    return { score: 3, label: "Atmos" };
  }
  if (hasToken(haystack, "truehd", "dts-hd", "dtshd", "master audio", "ma ")) {
    return { score: 3, label: "TrueHD / DTS-HD" };
  }
  if (hasToken(haystack, "dts")) {
    return { score: 2, label: "DTS" };
  }
  if (hasToken(haystack, "ddp", "eac3", "ac3", "dolby digital")) {
    return { score: 1, label: "Dolby Digital" };
  }
  if (hasToken(haystack, "aac")) {
    return { score: 0, label: "AAC" };
  }
  return { score: 0, label: null };
}

function detectCodec(haystack) {
  if (hasToken(haystack, "hevc", "x265", "h265")) {
    return { score: 1, label: "HEVC" };
  }
  if (hasToken(haystack, "av1")) {
    return { score: 1, label: "AV1" };
  }
  if (hasToken(haystack, "x264", "h264", "avc")) {
    return { score: 0, label: "AVC" };
  }
  return { score: 0, label: null };
}

function detectSize(fileSize) {
  const gib = Number(fileSize || 0) / (1024 ** 3);
  if (gib >= 80) {
    return { score: 3, label: `${Math.round(gib)} GB` };
  }
  if (gib >= 50) {
    return { score: 2, label: `${Math.round(gib)} GB` };
  }
  if (gib >= 20) {
    return { score: 1, label: `${Math.round(gib)} GB` };
  }
  if (gib >= 8) {
    return { score: 0.5, label: `${Math.round(gib)} GB` };
  }
  if (gib > 0 && gib < 2) {
    return { score: -1, label: `${gib.toFixed(1)} GB` };
  }
  return { score: 0, label: gib > 0 ? `${gib.toFixed(1)} GB` : null };
}

export function getQualityRank(item) {
  const haystack = [
    item.title,
    item.original_filename,
    item.video_codec,
    item.audio_codec,
    item.container,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  const source = detectSource(haystack);
  const resolution = detectResolution(item, haystack);
  const audio = detectAudio(haystack);
  const codec = detectCodec(haystack);
  const size = detectSize(item.file_size);

  const score = source.score + resolution.score + audio.score + codec.score + size.score;
  const rank = RANK_DEFINITIONS.find((entry) => score >= entry.minScore) || RANK_DEFINITIONS.at(-1);
  const detected = [source.label, resolution.label, audio.label, codec.label, size.label].filter(Boolean);

  return {
    key: rank.key,
    label: rank.label,
    score,
    description: rank.description,
    detected,
    tooltip: detected.length > 0
      ? `${rank.description} Detected: ${detected.join(" · ")}.`
      : rank.description,
  };
}
