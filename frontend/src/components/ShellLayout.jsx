import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { PosterContextMenuProvider } from "./PosterContextMenu";
import {
  markLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation";
import {
  applyUserBackgroundTheme,
  normalizeUserBackgroundSettings,
  resetUserBackgroundTheme,
} from "../lib/userBackground";
import { detectClientDeviceClass } from "../lib/platformDetection";
import { detectClientPlatform, isDesktopClientPlatform } from "../lib/platformDetection";
import { usePlaybackReadyNotice } from "../features/playback/usePlaybackReadyNotice";
import { resolveUserSettings, useUserSettingsQuery } from "../lib/userSettingsQueries";
import { classifyLibrarySpaPath } from "../lib/canonicalSpaPath.js";
import { DesktopLibraryIsland } from "./DesktopLibraryIsland.jsx";
import {
  DesktopLibraryIslandProvider,
  useDesktopLibraryIslandContext,
} from "./DesktopLibraryIslandContext.jsx";
import {
  canAccessAssistant,
  classifyPrimaryNavigationRoute,
  resolveAssistantNavigationTarget,
} from "../lib/assistantAccess.js";
import { classifyControlCenterPath, isDesktopControlCenterDevice } from "../lib/controlCenterRoutes.js";
import { applyControlCenterPaint } from "../lib/controlCenterPaint.js";
import { readControlCenterTheme } from "../lib/controlCenterSession.js";
import { usePlaybackAwareLogout } from "../features/auth/usePlaybackAwareLogout.js";
import { PlaybackWorkerLogoutDialog } from "../features/auth/PlaybackWorkerLogoutDialog.jsx";

function normalizePosterCardAppearance(value) {
  if (value === "modern" || value === "clean") {
    return value;
  }
  return "classic";
}

function ShellLayoutContent({ children }) {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const userSettingsQuery = useUserSettingsQuery(user);
  const userSettings = resolveUserSettings(userSettingsQuery.data);
  const desktopFloatingIslandPosition = (
    userSettings.desktop_floating_island_position === "bottom" ? "bottom" : "top"
  );
  const { libraryState } = useDesktopLibraryIslandContext();
  const posterCardAppearance = normalizePosterCardAppearance(userSettings.poster_card_appearance);
  const backgroundSettings = useMemo(
    () => normalizeUserBackgroundSettings(userSettings),
    [userSettingsQuery.data],
  );
  const [accountExpanded, setAccountExpanded] = useState(false);
  const collapseTimerRef = useRef(0);
  const floatingNavRef = useRef(null);
  const floatingLinkRefs = useRef([]);
  const floatingDragRef = useRef(false);
  const floatingIgnoreNextClickRef = useRef(false);
  const floatingDragBoundsRef = useRef({ clientX: 0, min: 0, max: 0 });
  const [floatingNavDragging, setFloatingNavDragging] = useState(false);
  const [floatingNavDragOffset, setFloatingNavDragOffset] = useState(0);
  const [floatingNavPreviewIndex, setFloatingNavPreviewIndex] = useState(null);
  const [floatingNavIndicatorFrame, setFloatingNavIndicatorFrame] = useState({ left: 0, width: 0 });
  const assistantNavigationTarget = resolveAssistantNavigationTarget(user);
  const activeNavigationKey = classifyPrimaryNavigationRoute(location.pathname, user);
  const navigation = [
    {
      to: "/library",
      label: "Library",
      key: "library",
    },
    {
      to: "/settings",
      label: "Settings",
      key: "settings",
    },
    ...(canAccessAssistant(user) ? [{
      to: assistantNavigationTarget,
      label: "Assistant",
      key: "assistant",
      state: user?.role === "admin"
        ? undefined
        : { fromPath: location.pathname },
    }] : []),
    ...(user?.role === "admin" ? [{
      to: "/admin",
      label: "Admin",
      key: "admin",
    }] : []),
  ];
  const {
    playbackReadyNotice,
    dismissPlaybackReadyNotice,
    openPlaybackReadyNotice,
  } = usePlaybackReadyNotice({
    pathname: location.pathname,
    navigate,
  });
  const libraryPath = classifyLibrarySpaPath(location.pathname);
  const isLibraryRootPage = libraryPath.kind === "root";
  const isLibrarySourcePage = libraryPath.kind === "source";
  const hideFloatingIsland = location.pathname === "/setup/totp";
  const clientDeviceClass = detectClientDeviceClass();
  const clientPlatform = detectClientPlatform();
  const desktopPosterContextMenuEnabled = isDesktopClientPlatform(clientPlatform);
  const desktopLibraryClient = clientDeviceClass === "desktop"
    && isDesktopClientPlatform(clientPlatform);
  const desktopControlCenter = isDesktopControlCenterDevice(clientDeviceClass, clientPlatform)
    && Boolean(classifyControlCenterPath(location.pathname).area);

  useLayoutEffect(() => {
    applyControlCenterPaint({
      active: desktopControlCenter,
      theme: readControlCenterTheme(),
    });
    return () => applyControlCenterPaint({ active: false });
  }, [desktopControlCenter, location.pathname]);
  const showDesktopLibraryIsland = desktopLibraryClient
    && libraryPath.kind === "root";
  const protectedDesktopLibraryState = (
    libraryState?.userId === String(user?.id ?? "")
    && libraryState?.role === String(user?.role ?? "").trim().toLowerCase()
  ) ? libraryState : null;
  const mobileSelectionGuardEnabled = clientDeviceClass === "phone" || clientDeviceClass === "tablet";
  const floatingNavDragEnabled = clientDeviceClass !== "phone" && clientDeviceClass !== "tablet";
  const floatingActiveIndex = navigation.findIndex(
    (item) => item.key === activeNavigationKey,
  );
  const floatingVisualIndex =
    floatingNavDragging && floatingNavPreviewIndex !== null
      ? floatingNavPreviewIndex
      : floatingActiveIndex;
  const floatingIndicatorStyle = {
    left: `${floatingNavIndicatorFrame.left}px`,
    width: `${floatingNavIndicatorFrame.width}px`,
    transform: floatingNavDragging ? `translateX(${floatingNavDragOffset}px)` : "translateX(0)",
  };
  const logoutCoordinator = usePlaybackAwareLogout({ onBeforeLogout: clearLogoutInteractionState });

  function clearLogoutInteractionState() {
    if (typeof window !== "undefined" && collapseTimerRef.current) {
      window.clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = 0;
    }
    if (typeof document !== "undefined") {
      const activeElement = document.activeElement;
      if (activeElement && typeof activeElement.blur === "function") {
        activeElement.blur();
      }
      document.body?.style.removeProperty("overflow");
      document.body?.style.removeProperty("pointer-events");
      document.body?.removeAttribute("inert");
      document.documentElement?.removeAttribute("inert");
    }
    setAccountExpanded(false);
  }

  function navigateToFloatingItem(item) {
    if (item.to !== "/library" || libraryPath.kind !== "detail") {
      navigate(item.to, { state: item.state });
      return;
    }
    const protectedIdentity = {
      userId: user?.id,
      role: user?.role,
    };
    const rememberedTarget = readLibraryReturnTarget(protectedIdentity);
    if (rememberedTarget) {
      markLibraryReturnPending(protectedIdentity);
    }
    navigate(rememberedTarget?.listPath || "/library", {
      state: { restoreLibraryReturn: true },
    });
  }

  function handleNavigationClick(event, item) {
    if (floatingIgnoreNextClickRef.current) {
      event.preventDefault();
      floatingIgnoreNextClickRef.current = false;
      return;
    }
    if (item.to !== "/library" || libraryPath.kind !== "detail") {
      return;
    }
    event.preventDefault();
    navigateToFloatingItem(item);
  }

  function getFloatingNavigationIndexFromPoint(clientX) {
    const links = floatingLinkRefs.current;
    if (!links.length) {
      return floatingActiveIndex;
    }
    let closestIndex = floatingActiveIndex;
    let closestDistance = Number.POSITIVE_INFINITY;
    links.forEach((linkNode, index) => {
      const rect = linkNode?.getBoundingClientRect?.();
      if (!rect) {
        return;
      }
      if (clientX >= rect.left && clientX <= rect.right) {
        closestIndex = index;
        closestDistance = -1;
        return;
      }
      const distance = Math.min(Math.abs(clientX - rect.left), Math.abs(clientX - rect.right));
      if (distance < closestDistance) {
        closestIndex = index;
        closestDistance = distance;
      }
    });
    return Math.max(0, Math.min(navigation.length - 1, closestIndex));
  }

  function handleFloatingActivePointerDown(event) {
    if (!floatingNavDragEnabled) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const navRect = floatingNavRef.current?.getBoundingClientRect?.();
    const linkRect = event.currentTarget.getBoundingClientRect();
    floatingDragBoundsRef.current = {
      clientX: event.clientX,
      min: navRect ? navRect.left - linkRect.left : 0,
      max: navRect ? navRect.right - linkRect.right : 0,
    };
    floatingDragRef.current = true;
    floatingIgnoreNextClickRef.current = true;
    setFloatingNavDragOffset(0);
    setFloatingNavPreviewIndex(floatingActiveIndex);
    setFloatingNavDragging(true);
  }

  function handleFloatingActivePointerMove(event) {
    if (!floatingDragRef.current) {
      return;
    }
    const bounds = floatingDragBoundsRef.current;
    const nextOffset = Math.max(bounds.min, Math.min(bounds.max, event.clientX - bounds.clientX));
    setFloatingNavDragOffset(nextOffset);
    setFloatingNavPreviewIndex(getFloatingNavigationIndexFromPoint(event.clientX));
  }

  function handleFloatingActivePointerEnd(event) {
    if (!floatingDragRef.current) {
      return;
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    const nextIndex = getFloatingNavigationIndexFromPoint(event.clientX);
    const nextItem = navigation[nextIndex];
    floatingDragRef.current = false;
    setFloatingNavDragging(false);
    setFloatingNavDragOffset(0);
    setFloatingNavPreviewIndex(null);
    const activatesCurrentLibraryDetail = Boolean(
      nextItem?.to === "/library"
      && libraryPath.kind === "detail"
      && nextIndex === floatingActiveIndex
    );
    if (nextItem && (nextIndex !== floatingActiveIndex || activatesCurrentLibraryDetail)) {
      navigateToFloatingItem(nextItem);
    }
    if (typeof window !== "undefined") {
      window.setTimeout(() => {
        floatingIgnoreNextClickRef.current = false;
      }, 120);
    } else {
      floatingIgnoreNextClickRef.current = false;
    }
  }

  function handleFloatingActivePointerCancel(event) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    floatingDragRef.current = false;
    floatingIgnoreNextClickRef.current = false;
    setFloatingNavDragging(false);
    setFloatingNavDragOffset(0);
    setFloatingNavPreviewIndex(null);
  }

  function scheduleAccountCollapse() {
    if (typeof window === "undefined") {
      return;
    }
    window.clearTimeout(collapseTimerRef.current);
    collapseTimerRef.current = window.setTimeout(() => {
      setAccountExpanded(false);
      collapseTimerRef.current = 0;
    }, 10_000);
  }

  function handleAccountToggle() {
    if (typeof window !== "undefined" && collapseTimerRef.current) {
      window.clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = 0;
    }
    setAccountExpanded((current) => {
      if (current) {
        return false;
      }
      scheduleAccountCollapse();
      return true;
    });
  }

  useEffect(() => {
    if (desktopControlCenter) {
      resetUserBackgroundTheme();
      return undefined;
    }
    applyUserBackgroundTheme(backgroundSettings);
    return () => {
      resetUserBackgroundTheme();
    };
  }, [backgroundSettings, desktopControlCenter]);

  useEffect(() => {
    floatingLinkRefs.current.length = navigation.length;
    function updateFloatingNavIndicator() {
      const navNode = floatingNavRef.current;
      const navRect = navNode?.getBoundingClientRect?.();
      const activeLinkRect = floatingLinkRefs.current[floatingActiveIndex]?.getBoundingClientRect?.();
      if (!navRect || !activeLinkRect) {
        setFloatingNavIndicatorFrame({ left: 0, width: 0 });
        return;
      }
      setFloatingNavIndicatorFrame({
        left: activeLinkRect.left - navRect.left + (navNode?.scrollLeft || 0),
        width: activeLinkRect.width,
      });
    }

    updateFloatingNavIndicator();
    if (typeof window === "undefined") {
      return undefined;
    }
    const navNode = floatingNavRef.current;
    const frameId = window.requestAnimationFrame(updateFloatingNavIndicator);
    window.addEventListener("resize", updateFloatingNavIndicator);
    navNode?.addEventListener("scroll", updateFloatingNavIndicator, { passive: true });
    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", updateFloatingNavIndicator);
      navNode?.removeEventListener("scroll", updateFloatingNavIndicator);
    };
  }, [floatingActiveIndex, navigation.length]);

  useEffect(() => () => {
    if (typeof window !== "undefined" && collapseTimerRef.current) {
      window.clearTimeout(collapseTimerRef.current);
    }
    if (typeof document !== "undefined") {
      document.body?.style.removeProperty("overflow");
      document.body?.style.removeProperty("pointer-events");
      document.body?.removeAttribute("inert");
      document.documentElement?.removeAttribute("inert");
    }
  }, []);

  return (
    <PosterContextMenuProvider enabled={desktopPosterContextMenuEnabled}>
      <div
        className={[
          "app-shell",
          "app-shell--floating-island-bottom",
          `app-shell--poster-card-${posterCardAppearance}`,
          `app-shell--background-${backgroundSettings.background_mode}`,
          backgroundSettings.background_mode === "preset"
            ? `app-shell--background-preset-${backgroundSettings.background_preset}`
            : "",
          isLibraryRootPage ? "app-shell--library-root" : "",
          isLibrarySourcePage ? "app-shell--library-source" : "",
          desktopLibraryClient ? "app-shell--desktop-client" : "",
          desktopControlCenter ? "app-shell--desktop-control-center" : "",
          showDesktopLibraryIsland
            ? `app-shell--desktop-library-island-${desktopFloatingIslandPosition}`
            : "",
          mobileSelectionGuardEnabled ? "app-shell--selection-guard" : "",
        ].filter(Boolean).join(" ")}
        style={desktopControlCenter ? {
          inlineSize: "100%",
          maxInlineSize: "none",
          margin: 0,
          padding: 0,
        } : undefined}
      >
      {playbackReadyNotice ? (
        <div className="playback-ready-bubble" role="status">
          <button
            className="playback-ready-bubble__action"
            onClick={openPlaybackReadyNotice}
            type="button"
          >
            {playbackReadyNotice.text}
          </button>
          <button
            aria-label="Dismiss playback ready notification"
            className="playback-ready-bubble__dismiss"
            onClick={dismissPlaybackReadyNotice}
            type="button"
          >
            Dismiss
          </button>
        </div>
      ) : null}

      <PlaybackWorkerLogoutDialog coordinator={logoutCoordinator} />

      {showDesktopLibraryIsland ? (
        <DesktopLibraryIsland
          libraryState={protectedDesktopLibraryState}
          onLogout={logoutCoordinator.requestLogout}
          position={desktopFloatingIslandPosition}
          user={user}
        />
      ) : null}

      {!desktopLibraryClient && !hideFloatingIsland ? (
        <div
          className="floating-island floating-island--bottom"
          aria-label="Primary navigation and account controls"
        >
          <nav className="floating-island__nav" aria-label="Primary" ref={floatingNavRef}>
            {floatingActiveIndex >= 0 ? (
              <span
                aria-hidden="true"
                className={[
                  "floating-island__nav-indicator",
                  floatingNavDragging ? "floating-island__nav-indicator--dragging" : "",
                ].filter(Boolean).join(" ")}
                style={floatingIndicatorStyle}
              />
            ) : null}
            {navigation.map((item, index) => {
              const isCurrent = index === floatingActiveIndex;
              const isVisuallyActive = index === floatingVisualIndex;
              const canDragCurrentItem = isCurrent && floatingNavDragEnabled;
              return (
              <NavLink
                key={item.to}
                className={[
                  "floating-island__link",
                  isVisuallyActive ? "floating-island__link--active" : "",
                  canDragCurrentItem ? "floating-island__link--current" : "",
                  canDragCurrentItem && floatingNavDragging ? "floating-island__link--dragging" : "",
                ].filter(Boolean).join(" ")}
                onClick={(event) => handleNavigationClick(event, item)}
                onPointerCancel={canDragCurrentItem ? handleFloatingActivePointerCancel : undefined}
                onPointerDown={canDragCurrentItem ? handleFloatingActivePointerDown : undefined}
                onPointerMove={canDragCurrentItem ? handleFloatingActivePointerMove : undefined}
                onPointerUp={canDragCurrentItem ? handleFloatingActivePointerEnd : undefined}
                ref={(node) => {
                  floatingLinkRefs.current[index] = node;
                }}
                to={item.to}
                state={item.state}
              >
                {item.label}
              </NavLink>
              );
            })}
          </nav>
          <div className="floating-island__account">
            <button
              aria-expanded={accountExpanded}
              aria-label={accountExpanded ? `Account: ${user?.username}` : "Show account name"}
              className={accountExpanded ? "account-badge account-badge--expanded" : "account-badge"}
              onClick={handleAccountToggle}
              type="button"
            >
              <span aria-hidden="true" className="account-badge__icon" />
              {accountExpanded ? <span className="account-badge__label">{user?.username}</span> : null}
            </button>
            <button className="ghost-button ghost-button--inline ghost-button--floating" type="button" onClick={logoutCoordinator.requestLogout}>
              Logout
            </button>
          </div>
        </div>
      ) : null}

        <main
          className={`page-shell${desktopControlCenter ? " page-shell--desktop-control-center" : ""}`}
          style={desktopControlCenter ? { margin: 0 } : undefined}
        >
          {children}
        </main>
      </div>
    </PosterContextMenuProvider>
  );
}


export function ShellLayout({ children }) {
  return (
    <DesktopLibraryIslandProvider>
      <ShellLayoutContent>{children}</ShellLayoutContent>
    </DesktopLibraryIslandProvider>
  );
}
