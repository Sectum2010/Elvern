export const DEFAULT_POSTER_DISPLAY_WIDTH = "1400";
const POSTER_DISPLAY_WIDTH_IDENTITIES = new Set([
  "800",
  "1000",
  "1200",
  "1400",
  "1600",
  "1800",
  "2000",
  "2200",
  "original",
]);


export function normalizePosterDisplayWidth(value) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return POSTER_DISPLAY_WIDTH_IDENTITIES.has(normalized)
    ? normalized
    : DEFAULT_POSTER_DISPLAY_WIDTH;
}


export function getCardPosterUrl(posterUrl, displayWidth = DEFAULT_POSTER_DISPLAY_WIDTH) {
  if (!posterUrl) {
    return posterUrl;
  }
  try {
    const resolvedUrl = new URL(posterUrl, "http://elvern.local");
    resolvedUrl.searchParams.set("variant", "card");
    resolvedUrl.searchParams.set("display_width", normalizePosterDisplayWidth(displayWidth));
    return `${resolvedUrl.pathname}${resolvedUrl.search}${resolvedUrl.hash}`;
  } catch {
    return posterUrl;
  }
}
