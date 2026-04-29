export function shouldShowDesktopBrowserSeekControl({
  desktopPlatform,
  iosMobile,
  showPlayerShell,
  hasMobileSession,
  fullDuration,
} = {}) {
  return desktopPlatform === "mac"
    && !iosMobile
    && Boolean(showPlayerShell)
    && Boolean(hasMobileSession)
    && Number(fullDuration || 0) > 0;
}

export function shouldShowMacAppFullscreenControl({
  desktopPlatform,
  iosMobile,
  showPlayerShell,
} = {}) {
  return desktopPlatform === "mac"
    && !iosMobile
    && Boolean(showPlayerShell);
}

export function resolveDetailVlcActionRoute({
  desktopPlatform,
  iosMobile,
  desktopPlayback,
} = {}) {
  if (iosMobile) {
    return {
      surface: "ios_external",
      endpoint: "ios_external_app",
    };
  }
  if (!desktopPlatform) {
    return {
      surface: "none",
      endpoint: null,
    };
  }
  if (!desktopPlayback) {
    return {
      surface: "desktop_pending",
      endpoint: null,
    };
  }
  if (
    desktopPlayback.open_supported
    || desktopPlayback.same_host_launch
    || desktopPlayback.open_method === "spawn_vlc"
  ) {
    return {
      surface: "desktop_open",
      endpoint: "desktop_open",
    };
  }
  if (desktopPlayback.handoff_supported) {
    return {
      surface: "desktop_helper",
      endpoint: "desktop_handoff",
    };
  }
  return {
    surface: "desktop_playlist",
    endpoint: "desktop_playlist",
  };
}
