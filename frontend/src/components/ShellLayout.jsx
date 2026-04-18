import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";
import {
  buildFullPlaybackReadyKey,
  buildRoute2ProbeSegmentUrl,
  getPlaybackMode,
  resolveBrowserPlaybackSessionRoot,
} from "../lib/browserPlayback";
import {
  markLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation";

const USER_SETTINGS_CHANGED_EVENT = "elvern:user-settings-changed";

export function ShellLayout({ children }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [floatingControlsPosition, setFloatingControlsPosition] = useState("bottom");
  const [accountExpanded, setAccountExpanded] = useState(false);
  const [playbackReadyNotice, setPlaybackReadyNotice] = useState(null);
  const collapseTimerRef = useRef(0);
  const fullProbeInFlightRef = useRef(false);
  const announcedFullReadyKeysRef = useRef(new Set());
  const navigation = [
    { to: "/library", label: "Library" },
    { to: "/install", label: "Install" },
    { to: "/settings", label: "Settings" },
    ...(user?.assistant_beta_enabled ? [{ to: "/assistant", label: "Assistant", state: { fromPath: location.pathname } }] : []),
    ...(user?.role === "admin" ? [{ to: "/admin", label: "Admin" }] : []),
  ];
  const isLibraryRootPage = location.pathname === "/library";
  const isLibrarySourcePage = location.pathname === "/library/local" || location.pathname === "/library/cloud";

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
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
        }
      } catch {
        if (active) {
          setFloatingControlsPosition("bottom");
        }
      }
    }

    function handleSettingsChanged(event) {
      const nextValue = event?.detail?.floating_controls_position;
      setFloatingControlsPosition(nextValue === "top" ? "top" : "bottom");
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
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timerId = 0;
    const sessionRoot = resolveBrowserPlaybackSessionRoot();

    async function sendBackgroundFullProbe(sessionPayload) {
      if (fullProbeInFlightRef.current) {
        return;
      }
      const probeUrl = buildRoute2ProbeSegmentUrl(sessionPayload);
      if (!probeUrl) {
        return;
      }
      fullProbeInFlightRef.current = true;
      const startedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
      try {
        const response = await fetch(probeUrl, {
          credentials: "include",
          cache: "no-store",
        });
        if (!response.ok) {
          return;
        }
        const buffer = await response.arrayBuffer();
        if (buffer.byteLength <= 0) {
          return;
        }
        const finishedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
        const durationMs = Math.max(1, Math.round(finishedAt - startedAt));
        await apiRequest(
          sessionPayload.heartbeat_url || `${sessionRoot}/sessions/${sessionPayload.session_id}/heartbeat`,
          {
            method: "POST",
            data: {
              client_probe_bytes: buffer.byteLength,
              client_probe_duration_ms: durationMs,
              lifecycle_state: "attached",
              playing: false,
            },
          },
        );
      } catch {
        // Background Full probes are best-effort only.
      } finally {
        fullProbeInFlightRef.current = false;
      }
    }

    async function pollActivePlayback() {
      let nextDelayMs = 6000;
      try {
        const payload = await apiRequest(`${sessionRoot}/active`);
        if (cancelled) {
          return;
        }
        const isFullPlayback = getPlaybackMode(payload?.playback_mode) === "full";
        const onMatchingDetailPage = Boolean(payload?.media_item_id) && location.pathname === `/library/${payload.media_item_id}`;

        if (!payload || !isFullPlayback) {
          setPlaybackReadyNotice(null);
          nextDelayMs = 6000;
        } else {
          const readyKey = buildFullPlaybackReadyKey(payload);
          if (!payload.mode_ready && !onMatchingDetailPage) {
            await sendBackgroundFullProbe(payload);
            nextDelayMs = 3000;
          } else {
            nextDelayMs = payload.mode_ready ? 5000 : 3000;
          }
          if (payload.mode_ready && readyKey && !announcedFullReadyKeysRef.current.has(readyKey) && !onMatchingDetailPage) {
            announcedFullReadyKeysRef.current.add(readyKey);
            setPlaybackReadyNotice({
              key: readyKey,
              itemId: payload.media_item_id,
              text: "Full Playback Ready",
            });
            if (typeof Notification !== "undefined" && Notification.permission === "granted") {
              const notification = new Notification("Full Playback Ready");
              notification.onclick = () => {
                window.focus();
                navigate(`/library/${payload.media_item_id}`);
                notification.close();
              };
            }
          }
          if (onMatchingDetailPage) {
            setPlaybackReadyNotice((current) => (
              current?.itemId === payload.media_item_id ? null : current
            ));
          }
        }
      } catch {
        if (cancelled) {
          return;
        }
        nextDelayMs = 6000;
      }
      if (!cancelled) {
        timerId = window.setTimeout(() => {
          pollActivePlayback().catch(() => {
            // The next timeout will retry.
          });
        }, nextDelayMs);
      }
    }

    pollActivePlayback().catch(() => {
      // The retry loop above handles transient watcher failures.
    });
    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, [location.pathname, navigate]);

  return (
    <div
      className={[
        "app-shell",
        `app-shell--floating-island-${floatingControlsPosition}`,
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
            onClick={() => {
              setPlaybackReadyNotice(null);
              navigate(`/library/${playbackReadyNotice.itemId}`);
            }}
            type="button"
          >
            {playbackReadyNotice.text}
          </button>
          <button
            aria-label="Dismiss playback ready notification"
            className="playback-ready-bubble__dismiss"
            onClick={() => setPlaybackReadyNotice(null)}
            type="button"
          >
            Dismiss
          </button>
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
