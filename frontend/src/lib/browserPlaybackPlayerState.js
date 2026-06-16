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
  const route2PreparingWithoutSource = isRoute2SessionPayload(mobileSession)
    && hasMobileSession
    && !hasStreamSource
    && !mobilePlayerCanPlay;
  const browserPreparingBeforeSession = Boolean(optimizedPlaybackPending && !hasMobileSession);

  const showInlinePlayer = !hasMobileSession || (hasStreamSource && (!requiresIosWarmupGate || mobilePlayerCanPlay));
  const showMobileWarmupShell =
    (
      requiresIosWarmupGate
      && (hasStreamSource || Boolean(mobileFrozenFrameUrl))
      && !mobilePlayerCanPlay
    )
    || route2PreparingWithoutSource
    || browserPreparingBeforeSession;
  const showMobilePrewarmCard = showMobileWarmupShell && !mobileFrozenFrameUrl;
  const showPlayerShell = showInlinePlayer || showMobileWarmupShell;

  const browserPlaybackPreparing = hasMobileSession
    ? (requiresIosWarmupGate ? !mobilePlayerCanPlay : !hasStreamSource)
    : optimizedPlaybackPending;

  const showMobilePreparingPlaceholder = isRoute2SessionPayload(mobileSession)
    ? false
    : !showPlayerShell
      && (
        optimizedPlaybackPending
        || (requiresIosWarmupGate && hasMobileSession && !mobilePlayerCanPlay)
      );

  return {
    browserPlaybackPreparing,
    playerClassName:
      requiresIosWarmupGate && !mobilePlayerCanPlay
        ? "player player--warmup"
        : "player",
    showInlinePlayer,
    showMobilePreparingPlaceholder,
    showMobilePrewarmCard,
    showMobileWarmupShell,
    showPlayerShell,
    videoControlsEnabled:
      !hasMobileSession
      || !requiresIosWarmupGate
      || mobilePlayerCanPlay,
  };
}
