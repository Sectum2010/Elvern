import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";
import { resolveBrowserPlaybackSessionRoot } from "../lib/browserPlayback";
import {
  markLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation";
import { buildLogoutPlaybackWorkerPrompt } from "../lib/playbackWorkerOwnership";
import {
  DEFAULT_BACKGROUND_SETTINGS,
  applyUserBackgroundTheme,
  normalizeUserBackgroundSettings,
  resetUserBackgroundTheme,
} from "../lib/userBackground";
import { detectClientDeviceClass } from "../lib/platformDetection";
import { usePlaybackReadyNotice } from "../features/playback/usePlaybackReadyNotice";

const USER_SETTINGS_CHANGED_EVENT = "elvern:user-settings-changed";

function normalizePosterCardAppearance(value) {
  if (value === "modern" || value === "clean") {
    return value;
  }
  return "classic";
}

export function ShellLayout({ children }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [floatingControlsPosition, setFloatingControlsPosition] = useState("bottom");
  const [posterCardAppearance, setPosterCardAppearance] = useState("classic");
  const [backgroundSettings, setBackgroundSettings] = useState(DEFAULT_BACKGROUND_SETTINGS);
  const [accountExpanded, setAccountExpanded] = useState(false);
  const [logoutWorkerModal, setLogoutWorkerModal] = useState(null);
  const [logoutWorkerPending, setLogoutWorkerPending] = useState("");
  const [logoutWorkerError, setLogoutWorkerError] = useState("");
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
  const navigation = [
    { to: "/library", label: "Library" },
    { to: "/install", label: "Install" },
    { to: "/settings", label: "Settings" },
    ...(user?.assistant_beta_enabled ? [{ to: "/assistant", label: "Assistant", state: { fromPath: location.pathname } }] : []),
    ...(user?.role === "admin" ? [{ to: "/admin", label: "Admin" }] : []),
  ];
  const {
    playbackReadyNotice,
    dismissPlaybackReadyNotice,
    openPlaybackReadyNotice,
  } = usePlaybackReadyNotice({
    pathname: location.pathname,
    navigate,
  });
  const isLibraryRootPage = location.pathname === "/library";
  const isLibrarySourcePage = location.pathname === "/library/local" || location.pathname === "/library/cloud";
  const hideFloatingIsland = location.pathname === "/setup/totp";
  const clientDeviceClass = detectClientDeviceClass();
  const floatingNavDragEnabled = clientDeviceClass !== "phone" && clientDeviceClass !== "tablet";
  const floatingActiveIndex = Math.max(0, navigation.findIndex((item) => (
    item.to === "/library"
      ? location.pathname.startsWith("/library")
      : location.pathname === item.to || location.pathname.startsWith(`${item.to}/`)
  )));
  const floatingVisualIndex =
    floatingNavDragging && floatingNavPreviewIndex !== null
      ? floatingNavPreviewIndex
      : floatingActiveIndex;
  const floatingIndicatorStyle = {
    left: `${floatingNavIndicatorFrame.left}px`,
    width: `${floatingNavIndicatorFrame.width}px`,
    transform: floatingNavDragging ? `translateX(${floatingNavDragOffset}px)` : "translateX(0)",
  };

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
    setLogoutWorkerModal(null);
    setLogoutWorkerError("");
  }

  async function completeLogout() {
    clearLogoutInteractionState();
    await logout();
    navigate("/login", { replace: true });
  }

  async function handleLogout() {
    setLogoutWorkerError("");
    try {
      const sessionRoot = resolveBrowserPlaybackSessionRoot();
      const activeSession = await apiRequest(`${sessionRoot}/active`);
      if (!activeSession?.session_id) {
        await completeLogout();
        return;
      }
      let movieTitle = "This movie";
      try {
        const itemPayload = await apiRequest(`/api/library/item/${encodeURIComponent(activeSession.media_item_id)}`);
        if (typeof itemPayload?.title === "string" && itemPayload.title.trim()) {
          movieTitle = itemPayload.title.trim();
        }
      } catch {
        // Fall back to the generic title if the item detail lookup fails.
      }
      setLogoutWorkerModal({
        movieTitle,
        sessionId: String(activeSession.session_id),
        stopUrl: typeof activeSession.stop_url === "string" ? activeSession.stop_url : "",
        sessionRoot,
      });
    } catch (requestError) {
      if (requestError?.status === 401 || requestError?.status === 403) {
        await completeLogout();
        return;
      }
      await completeLogout();
    }
  }

  function closeLogoutWorkerModal() {
    if (logoutWorkerPending) {
      return;
    }
    setLogoutWorkerModal(null);
    setLogoutWorkerError("");
  }

  async function handleLogoutKeepPreparing() {
    if (!logoutWorkerModal || logoutWorkerPending) {
      return;
    }
    setLogoutWorkerPending("keep");
    setLogoutWorkerError("");
    try {
      await completeLogout();
    } catch (requestError) {
      setLogoutWorkerModal((current) => current || logoutWorkerModal);
      setLogoutWorkerError(requestError.message || "Failed to log out");
    } finally {
      setLogoutWorkerPending("");
    }
  }

  async function handleLogoutTerminateProcess() {
    if (!logoutWorkerModal?.sessionId || logoutWorkerPending) {
      return;
    }
    setLogoutWorkerPending("terminate");
    setLogoutWorkerError("");
    const stopUrl =
      logoutWorkerModal.stopUrl
      || `${logoutWorkerModal.sessionRoot}/sessions/${encodeURIComponent(logoutWorkerModal.sessionId)}/stop`;
    try {
      await apiRequest(stopUrl, { method: "POST" });
    } catch {
      // Logout is explicit user intent here; a failed worker stop must not trap the session.
    }
    try {
      await completeLogout();
    } catch (requestError) {
      setLogoutWorkerModal((current) => current || logoutWorkerModal);
      setLogoutWorkerError(requestError.message || "Failed to log out");
    } finally {
      setLogoutWorkerPending("");
    }
  }

  function isLibraryDetailPath(pathname) {
    return /^\/library\/\d+$/.test(pathname || "");
  }

  const showLibraryHeader =
    location.pathname.startsWith("/library")
    && !isLibrarySourcePage
    && !isLibraryDetailPath(location.pathname);

  async function handleNavigationClick(event, item) {
    if (floatingIgnoreNextClickRef.current) {
      event.preventDefault();
      floatingIgnoreNextClickRef.current = false;
      return;
    }
    if (item.to !== "/library" || location.pathname.startsWith("/library") || !isLibraryDetailPath(location.pathname)) {
      return;
    }
    event.preventDefault();
    const rememberedTarget = readLibraryReturnTarget();
    if (rememberedTarget) {
      markLibraryReturnPending();
    }
    navigate(rememberedTarget?.listPath || "/library", {
      state: { restoreLibraryReturn: true },
    });
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
    if (nextItem && nextIndex !== floatingActiveIndex) {
      navigate(nextItem.to, { state: nextItem.state });
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
    let active = true;

    async function loadUserSettings() {
      try {
        const payload = await apiRequest("/api/user-settings");
        if (active) {
          setFloatingControlsPosition(payload.floating_controls_position === "top" ? "top" : "bottom");
          setPosterCardAppearance(normalizePosterCardAppearance(payload.poster_card_appearance));
          setBackgroundSettings(normalizeUserBackgroundSettings(payload));
        }
      } catch {
        if (active) {
          setFloatingControlsPosition("bottom");
          setPosterCardAppearance("classic");
          setBackgroundSettings(DEFAULT_BACKGROUND_SETTINGS);
        }
      }
    }

    function handleSettingsChanged(event) {
      const nextFloatingControlsPosition = event?.detail?.floating_controls_position;
      const nextPosterCardAppearance = event?.detail?.poster_card_appearance;
      if (nextFloatingControlsPosition !== undefined) {
        setFloatingControlsPosition(nextFloatingControlsPosition === "top" ? "top" : "bottom");
      }
      if (nextPosterCardAppearance !== undefined) {
        setPosterCardAppearance(normalizePosterCardAppearance(nextPosterCardAppearance));
      }
      if (
        event?.detail?.background_mode !== undefined
        || event?.detail?.background_preset !== undefined
        || event?.detail?.background_gradient_start !== undefined
        || event?.detail?.background_gradient_end !== undefined
        || event?.detail?.background_gradient_accent !== undefined
        || event?.detail?.background_solid_color !== undefined
        || event?.detail?.background_photo_url !== undefined
      ) {
        setBackgroundSettings(normalizeUserBackgroundSettings(event.detail));
      }
    }

    loadUserSettings();
    window.addEventListener(USER_SETTINGS_CHANGED_EVENT, handleSettingsChanged);
    return () => {
      active = false;
      window.removeEventListener(USER_SETTINGS_CHANGED_EVENT, handleSettingsChanged);
    };
  }, []);

  useEffect(() => {
    applyUserBackgroundTheme(backgroundSettings);
    return () => {
      resetUserBackgroundTheme();
    };
  }, [backgroundSettings]);

  useEffect(() => {
    floatingLinkRefs.current.length = navigation.length;
    function updateFloatingNavIndicator() {
      const navRect = floatingNavRef.current?.getBoundingClientRect?.();
      const activeLinkRect = floatingLinkRefs.current[floatingActiveIndex]?.getBoundingClientRect?.();
      if (!navRect || !activeLinkRect) {
        setFloatingNavIndicatorFrame({ left: 0, width: 0 });
        return;
      }
      setFloatingNavIndicatorFrame({
        left: activeLinkRect.left - navRect.left,
        width: activeLinkRect.width,
      });
    }

    updateFloatingNavIndicator();
    if (typeof window === "undefined") {
      return undefined;
    }
    window.addEventListener("resize", updateFloatingNavIndicator);
    return () => {
      window.removeEventListener("resize", updateFloatingNavIndicator);
    };
  }, [floatingActiveIndex, floatingControlsPosition, navigation.length]);

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

  useEffect(() => {
    if (!logoutWorkerModal || typeof window === "undefined") {
      return undefined;
    }

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        closeLogoutWorkerModal();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [logoutWorkerModal, logoutWorkerPending]);

  return (
    <div
      className={[
        "app-shell",
        `app-shell--floating-island-${floatingControlsPosition}`,
        `app-shell--poster-card-${posterCardAppearance}`,
        `app-shell--background-${backgroundSettings.background_mode}`,
        backgroundSettings.background_mode === "preset"
          ? `app-shell--background-preset-${backgroundSettings.background_preset}`
          : "",
        isLibraryRootPage ? "app-shell--library-root" : "",
        isLibrarySourcePage ? "app-shell--library-source" : "",
      ].filter(Boolean).join(" ")}
    >
      {showLibraryHeader ? (
        <header className="topbar">
          <div>
            <p className="eyebrow">Private Media Library</p>
            <NavLink className="brand" to="/library">
              Elvern
            </NavLink>
          </div>
        </header>
      ) : null}

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

      {logoutWorkerModal ? (
        <div
          aria-labelledby="logout-playback-worker-modal-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <div
            aria-hidden="true"
            className="browser-resume-modal__backdrop"
            onClick={closeLogoutWorkerModal}
          />
          <div className="browser-resume-modal__card detail-info-modal__card playback-worker-choice-modal">
            <div className="detail-info-modal__copy">
              <p className="eyebrow detail-info-modal__eyebrow">PLAYBACK WORKER</p>
              <p className="detail-info-modal__title" id="logout-playback-worker-modal-title">
                {buildLogoutPlaybackWorkerPrompt(logoutWorkerModal.movieTitle)}
              </p>
              {logoutWorkerError ? (
                <p className="page-subnote playback-worker-choice-modal__error" role="alert">
                  {logoutWorkerError}
                </p>
              ) : null}
            </div>
            <div className="browser-resume-modal__actions playback-worker-choice-modal__actions">
              <button
                className="primary-button"
                disabled={Boolean(logoutWorkerPending)}
                onClick={handleLogoutKeepPreparing}
                type="button"
              >
                Keep Preparing
              </button>
              <button
                className="ghost-button ghost-button--danger"
                disabled={Boolean(logoutWorkerPending)}
                onClick={handleLogoutTerminateProcess}
                type="button"
              >
                Terminate Process
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {!hideFloatingIsland ? (
        <div
          className={`floating-island floating-island--${floatingControlsPosition}`}
          aria-label="Primary navigation and account controls"
        >
          <nav className="floating-island__nav" aria-label="Primary" ref={floatingNavRef}>
            <span
              aria-hidden="true"
              className={[
                "floating-island__nav-indicator",
                floatingNavDragging ? "floating-island__nav-indicator--dragging" : "",
              ].filter(Boolean).join(" ")}
              style={floatingIndicatorStyle}
            />
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
                onClick={(event) => {
                  handleNavigationClick(event, item).catch(() => {
                    // Fall back to the default route if validation fails unexpectedly.
                  });
                }}
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
            <button className="ghost-button ghost-button--inline ghost-button--floating" type="button" onClick={handleLogout}>
              Logout
            </button>
          </div>
        </div>
      ) : null}

      <main className="page-shell">{children}</main>
    </div>
  );
}
