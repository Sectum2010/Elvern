export function getCardPosterUrl(posterUrl) {
  if (!posterUrl) {
    return posterUrl;
  }
  try {
    const resolvedUrl = new URL(posterUrl, "http://elvern.local");
    resolvedUrl.searchParams.set("variant", "card");
    return `${resolvedUrl.pathname}${resolvedUrl.search}${resolvedUrl.hash}`;
  } catch {
    return posterUrl;
  }
}
