const TITLE_NOISE_TOKENS = new Set([
  "4k",
  "2160p",
  "1080p",
  "720p",
  "540p",
  "480p",
  "320p",
  "uhd",
  "hdr",
  "hdr10",
  "hdr10plus",
  "hdr10+",
  "dv",
  "dovi",
  "sdr",
  "bluray",
  "blu-ray",
  "bdrip",
  "brrip",
  "webrip",
  "webdl",
  "web-dl",
  "web",
  "remux",
  "x264",
  "x265",
  "h264",
  "h265",
  "hevc",
  "avc",
  "truehd",
  "atmos",
  "dts",
  "dts-hd",
  "dtshd",
  "aac",
  "ac3",
  "eac3",
  "ddp",
  "ma",
  "proper",
  "repack",
  "imax",
  "criterion",
  "hybrid",
  "multi",
  "dual",
  "subbed",
  "dubbed",
  "remastered",
  "extended",
  "unrated",
  "10bit",
  "8bit",
  "yify",
  "rarbg",
  "framestor",
  "brremux",
  "ptbr",
]);

const SMALL_WORDS = new Set(["a", "an", "and", "as", "at", "for", "in", "of", "on", "the", "to"]);
const YEAR_RE = /^(19|20)\d{2}$/;
const RESOLUTION_RE = /^\d{3,4}p$/i;
const CODEC_RE = /^(?:x|h)\.?26[45]$/i;
const AUDIO_CHANNEL_RE = /^\d(?:\.\d)?$/;
const BIT_DEPTH_RE = /^\d+bit$/i;
const ROMAN_NUMERAL_RE = /^(?=[ivxlcdm]+$)[ivxlcdm]+$/i;

function stripPathAndExtension(value) {
  const basename = String(value || "").split(/[\\/]/).pop() || "";
  return basename.replace(/\.[a-z0-9]{2,5}$/i, "");
}

function canonicalToken(token) {
  return String(token || "")
    .toLowerCase()
    .replace(/^[()[\]{}"'`]+|[()[\]{}"'`,:;!?.]+$/g, "")
    .replace(/[–—]/g, "-");
}

function isNoiseToken(token) {
  const canonical = canonicalToken(token);
  if (!canonical) {
    return false;
  }
  return (
    TITLE_NOISE_TOKENS.has(canonical)
    || RESOLUTION_RE.test(canonical)
    || CODEC_RE.test(canonical)
    || AUDIO_CHANNEL_RE.test(canonical)
    || BIT_DEPTH_RE.test(canonical)
  );
}

function toDisplayTitleCase(value) {
  const words = String(value || "").split(/\s+/).filter(Boolean);
  return words
    .map((word, wordIndex) => word
      .split("-")
      .map((part, partIndex, parts) => {
        const lower = part.toLowerCase();
        if (!lower) {
          return part;
        }
        if (ROMAN_NUMERAL_RE.test(lower)) {
          return lower.toUpperCase();
        }
        if (
          wordIndex > 0
          && wordIndex < words.length - 1
          && partIndex === 0
          && parts.length === 1
          && SMALL_WORDS.has(lower)
        ) {
          return lower;
        }
        return lower.charAt(0).toUpperCase() + lower.slice(1);
      })
      .join("-"))
    .join(" ");
}

function cleanMovieTitleCandidate(value, { year = null, filenameLike = false } = {}) {
  let working = stripPathAndExtension(value);
  if (!working) {
    return "";
  }
  if (filenameLike) {
    working = working.replace(/[._]+/g, " ");
  }
  working = working.replace(/\s+/g, " ").trim();
  if (!working) {
    return "";
  }

  const tokens = working.split(" ");
  const yearToken = year ? String(year) : "";
  let stopIndex = tokens.length;

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    const canonical = canonicalToken(token);
    if (!canonical) {
      continue;
    }
    if (yearToken && canonical === yearToken && index > 0) {
      stopIndex = index;
      break;
    }
    if (isNoiseToken(canonical)) {
      stopIndex = index;
      break;
    }
  }

  let candidate = tokens.slice(0, stopIndex).join(" ").trim();
  if (!candidate) {
    candidate = working;
  }

  candidate = candidate
    .replace(/\s+/g, " ")
    .replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9]+$/g, "")
    .trim();

  if (!candidate) {
    return "";
  }

  return filenameLike ? toDisplayTitleCase(candidate) : candidate;
}

export function getMovieCardTitle(item) {
  const cleanedTitle = cleanMovieTitleCandidate(item?.title, { year: item?.year, filenameLike: false });
  if (cleanedTitle) {
    return cleanedTitle;
  }

  const cleanedFilename = cleanMovieTitleCandidate(item?.original_filename, {
    year: item?.year,
    filenameLike: true,
  });
  if (cleanedFilename) {
    return cleanedFilename;
  }

  return String(item?.title || item?.original_filename || "Untitled").trim() || "Untitled";
}
