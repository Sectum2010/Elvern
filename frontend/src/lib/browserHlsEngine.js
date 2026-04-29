export function resolveBrowserHlsEngine({
  deviceClass,
  hlsJsSupported,
  iosMobile,
  nativeHlsSupport,
} = {}) {
  const hasNativeHls = Boolean(nativeHlsSupport);
  const hasHlsJs = Boolean(hlsJsSupported);
  const isDesktop = deviceClass === "desktop";

  if (iosMobile && hasNativeHls) {
    return "native_hls";
  }
  if (isDesktop && hasHlsJs) {
    return "hls.js";
  }
  if (hasNativeHls) {
    return "native_hls";
  }
  if (hasHlsJs) {
    return "hls.js";
  }
  return "unsupported_hls";
}
