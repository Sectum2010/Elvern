export function resolveSeriesRailViewportKind({
  deviceClass = "",
  isLandscape = false,
} = {}) {
  if (deviceClass === "phone") {
    return isLandscape ? "phone-landscape" : "phone-portrait";
  }
  return "desktop";
}
