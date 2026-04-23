const SMALL_WORDS = new Set(["a", "an", "and", "as", "at", "for", "in", "of", "on", "the", "to"]);
const ROMAN_NUMERAL_RE = /^(?=[ivxlcdm]+$)[ivxlcdm]+$/i;

function stripPathAndExtension(value) {
  const basename = String(value || "").split(/[\\/]/).pop() || "";
  return basename.replace(/\.[a-z0-9]{2,5}$/i, "");
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

export function getMovieCardTitle(item) {
  const parsedDisplayTitle = String(item?.parsed_title?.display_title || "").trim();
  if (parsedDisplayTitle) {
    return parsedDisplayTitle;
  }

  const providedTitle = String(item?.title || "").trim();
  if (providedTitle) {
    return providedTitle;
  }

  const fallbackFilename = stripPathAndExtension(item?.original_filename);
  if (fallbackFilename) {
    return toDisplayTitleCase(fallbackFilename.replace(/[._]+/g, " "));
  }

  return "Untitled";
}
