function isRoute2SessionPayload(payload) {
  return payload?.engine_mode === "route2";
}

export function resolveBrowserPlaybackPlayerViewState({
  activePlaybackMode,
  iosMobile,
  mobileFrozenFrameUrl,
  mobilePlayerCanPlay,
  mobileSession,
  optimizedPlaybackPending,
  streamSource,
}) {
  const hasMobileSession = Boolean(mobileSession);
  const hasStreamSource = Boolean(streamSource);
  const requiresIosWarmupGate = iosMobile && hasMobileSession;

  const showInlinePlayer = !hasMobileSession || (hasStreamSource && (!requiresIosWarmupGate || mobilePlayerCanPlay));
  const showMobileWarmupShell =
    requiresIosWarmupGate
    && (hasStreamSource || Boolean(mobileFrozenFrameUrl))
    && !mobilePlayerCanPlay;
  const showPlayerShell = showInlinePlayer || showMobileWarmupShell;

  const browserPlaybackPreparing = hasMobileSession
    ? (requiresIosWarmupGate ? !mobilePlayerCanPlay : !hasStreamSource)
    : optimizedPlaybackPending;

  const showMobilePreparingPlaceholder = isRoute2SessionPayload(mobileSession)
    ? !showPlayerShell && (!mobileSession?.attach_ready || !hasStreamSource)
    : optimizedPlaybackPending || (requiresIosWarmupGate && hasMobileSession && !mobilePlayerCanPlay);

  return {
    browserPlaybackPreparing,
    playerClassName:
      requiresIosWarmupGate && !mobilePlayerCanPlay
        ? "player player--warmup"
        : "player",
    showInlinePlayer,
    showMobilePreparingPlaceholder,
    showMobileWarmupShell,
    showPlayerShell,
    videoControlsEnabled:
      !hasMobileSession
      || !requiresIosWarmupGate
      || mobilePlayerCanPlay
      || (activePlaybackMode === "lite" && hasStreamSource),
  };
}
