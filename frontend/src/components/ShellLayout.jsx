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
import { usePlaybackReadyNotice } from "../features/playback/usePlaybackReadyNotice";

const USER_SETTINGS_CHANGED_EVENT = "elvern:user-settings-changed";

function normalizePosterCardAppearance(value) {
  return value === "modern" ? "modern" : "classic";
}

export function ShellLayout({ children }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [floatingControlsPosition, setFloatingControlsPosition] = useState("bottom");
  const [posterCardAppearance, setPosterCardAppearance] = useState("classic");
  const [accountExpanded, setAccountExpanded] = useState(false);
  const [logoutWorkerModal, setLogoutWorkerModal] = useState(null);
  const [logoutWorkerPending, setLogoutWorkerPending] = useState("");
  const [logoutWorkerError, setLogoutWorkerError] = useState("");
  const collapseTimerRef = useRef(0);
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
        }
      } catch {
        if (active) {
          setFloatingControlsPosition("bottom");
          setPosterCardAppearance("classic");
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
    }

    loadUserSettings();
    window.addEventListener(USER_SETTINGS_CHANGED_EVENT, handleSettingsChanged);
    return () => {
      active = false;
      window.removeEventListener(USER_SETTINGS_CHANGED_EVENT, handleSettingsChanged);
    };
  }, []);

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

      <div
        className={`floating-island floating-island--${floatingControlsPosition}`}
        aria-label="Primary navigation and account controls"
      >
        <nav className="floating-island__nav" aria-label="Primary">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              className={({ isActive }) =>
                isActive ? "floating-island__link floating-island__link--active" : "floating-island__link"
              }
              onClick={(event) => {
                handleNavigationClick(event, item).catch(() => {
                  // Fall back to the default route if validation fails unexpectedly.
                });
              }}
              to={item.to}
              state={item.state}
            >
              {item.label}
            </NavLink>
          ))}
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

      <main className="page-shell">{children}</main>
    </div>
  );
}
