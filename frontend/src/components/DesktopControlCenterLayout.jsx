import {
  Cloud,
  Database,
  EyeOff,
  FileClock,
  Gauge,
  Library,
  MonitorPlay,
  Palette,
  RotateCcw,
  Shield,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext.jsx";
import {
  markLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation.js";
import { useControlCenterSession } from "./ControlCenterSessionContext.jsx";
import { SystemStatusRail } from "./SystemStatusRail.jsx";
import { applyControlCenterPaint } from "../lib/controlCenterPaint.js";

const SETTINGS_NAV = [
  ["appearance", "Appearance", Palette],
  ["library", "Library", Library],
  ["cloud-sharing", "Cloud & Sharing", Cloud],
  ["hidden-titles", "Hidden titles", EyeOff],
  ["playback-apps", "Playback & Apps", MonitorPlay],
  ["server-storage", "Server & Storage", Database],
];

const ADMIN_NAV = [
  ["overview", "Overview", Gauge],
  ["users-invites", "Users & Invites", Users],
  ["security", "Security", Shield],
  ["logs", "Logs", FileClock],
  ["recovery", "Recovery", RotateCcw],
];

const TITLES = {
  appearance: ["Appearance", "Poster cards, the floating island, and your library background."],
  library: ["Library", "What shows up in your library and how duplicates are handled."],
  "cloud-sharing": ["Cloud & Sharing", "Google Drive libraries — yours and the ones shared with everyone."],
  "hidden-titles": ["Hidden titles", "Titles removed from browsing, for you or for every user."],
  "playback-apps": ["Playback & Apps", "Host playback via VLC and device diagnostics."],
  "server-storage": ["Server & Storage", "OAuth, scan folders, and the global poster directory."],
  overview: ["Overview", "Library health, security posture, and maintenance at a glance."],
  "users-invites": ["Users & Invites", "Accounts, new-user creation, invite codes, and password help."],
  security: ["Security", "URL prefix, two-factor authentication, and exposure planning."],
  logs: ["Logs", "Active sessions and the recent audit trail."],
  recovery: ["Recovery", ""],
};

function roleLabel(role) {
  return role === "admin" ? "Administrator" : "Standard user";
}

function hostnameLabel() {
  if (typeof window === "undefined") return "Elvern host";
  return window.location.hostname || "Elvern host";
}

function BackIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M14.5 5L7.5 12l7 7" /></svg>;
}

function MixedThemeIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.4" /><path d="M12 3.6a8.4 8.4 0 010 16.8z" fill="currentColor" /></svg>;
}

function LightThemeIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" /><path d="M12 3v2.4M12 18.6V21M21 12h-2.4M5.4 12H3M18.4 5.6l-1.7 1.7M7.3 16.7l-1.7 1.7M18.4 18.4l-1.7-1.7M7.3 7.3L5.6 5.6" /></svg>;
}

function DarkThemeIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M19.5 13.5A7.5 7.5 0 0110.5 4.5a7.5 7.5 0 109 9z" /></svg>;
}

function StatusPanelIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><rect height="14" rx="2" width="17" x="3.5" y="5" /><path d="M14.5 5v14" /></svg>;
}

export function DesktopControlCenterLayout() {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const {
    adminTab,
    settingsTab,
    statusRailOpen,
    setStatusRailOpen,
    theme,
    setTheme,
  } = useControlCenterSession();
  const area = location.pathname.startsWith("/admin/") ? "admin" : "settings";
  const tab = location.pathname.split("/")[2] || (area === "admin" ? "overview" : "appearance");
  const [switching, setSwitching] = useState(false);
  const [switchRotation, setSwitchRotation] = useState(area === "admin" ? 180 : 0);
  const [tilt, setTilt] = useState({ x: 0, y: 0, active: false });
  const switchTimerRef = useRef(0);
  const navItems = (area === "admin" ? ADMIN_NAV : SETTINGS_NAV)
    .filter(([key]) => key !== "server-storage" || user?.role === "admin");
  const activeNavIndex = Math.max(0, navItems.findIndex(([key]) => key === tab));
  const [title, subtitle] = TITLES[tab] || TITLES[area === "admin" ? "overview" : "appearance"];
  const host = useMemo(hostnameLabel, []);

  useEffect(() => () => window.clearTimeout(switchTimerRef.current), []);

  useEffect(() => {
    if (!switching) setSwitchRotation(area === "admin" ? 180 : 0);
  }, [area, switching]);

  function returnToLibrary() {
    const identity = { userId: user?.id, role: user?.role };
    const target = readLibraryReturnTarget(identity);
    if (target) markLibraryReturnPending(identity);
    navigate(target?.listPath || "/library?category=movies", {
      state: target ? { restoreLibraryReturn: true } : undefined,
    });
  }

  function switchArea() {
    if (switching || user?.role !== "admin") return;
    const destination = area === "admin" ? `/settings/${settingsTab}` : `/admin/${adminTab}`;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    const midpointDelay = reducedMotion ? 40 : 275;
    const completionDelay = reducedMotion ? 80 : 275;
    setTilt({ x: 0, y: 0, active: false });
    setSwitchRotation(area === "admin" ? 0 : 180);
    setSwitching(true);
    switchTimerRef.current = window.setTimeout(() => {
      navigate(destination);
      switchTimerRef.current = window.setTimeout(() => setSwitching(false), completionDelay);
    }, midpointDelay);
  }

  function handleSwitchMove(event) {
    if (switching) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const horizontal = (event.clientX - bounds.left) / bounds.width - 0.5;
    const vertical = (event.clientY - bounds.top) / bounds.height - 0.5;
    setTilt({ x: vertical * -44, y: horizontal * 38, active: true });
  }

  function cycleTheme() {
    const nextTheme = theme === "light" ? "mixed" : theme === "mixed" ? "dark" : "light";
    applyControlCenterPaint({ active: true, theme: nextTheme });
    setTheme(nextTheme);
  }

  const ThemeIcon = theme === "light" ? LightThemeIcon : theme === "dark" ? DarkThemeIcon : MixedThemeIcon;
  const avatar = String(user?.username || "E").trim().charAt(0).toUpperCase() || "E";
  const switchTransform = `rotateX(${tilt.x.toFixed(1)}deg) rotateY(${(switchRotation + tilt.y).toFixed(1)}deg)`;

  return (
    <section
      className={`control-center-desktop meridian-control-center meridian-control-center--${theme}${statusRailOpen && user?.role === "admin" ? " meridian-control-center--rail-open" : ""}`}
      data-control-center-area={area}
      data-control-center-tab={tab}
      data-control-center-theme={theme}
      data-meridian-theme={theme}
      data-visual-landmark="control-center-root"
    >
      <aside className="meridian-sidebar" data-visual-landmark="sidebar">
        <button className="meridian-sidebar__back" onClick={returnToLibrary} type="button">
          <BackIcon />
          <span>Library</span>
        </button>

        <div className="meridian-sidebar__heading">
          <strong>{area === "admin" ? "Admin Panel" : "Settings"}</strong>
          <span>Elvern · {host}</span>
        </div>

        {user?.role === "admin" ? (
          <div className="meridian-area-switch-wrap">
            <button
              aria-label={`Switch to ${area === "admin" ? "Settings" : "Admin Panel"}`}
              className={`meridian-area-switch${tilt.active ? " meridian-area-switch--tilting" : ""}`}
              disabled={switching}
              onClick={switchArea}
              onMouseLeave={() => setTilt({ x: 0, y: 0, active: false })}
              onMouseMove={handleSwitchMove}
              style={{ transform: switchTransform }}
              type="button"
            >
              <span className="meridian-area-switch__face meridian-area-switch__face--front">Admin Panel</span>
              <span className="meridian-area-switch__face meridian-area-switch__face--back">Settings</span>
            </button>
          </div>
        ) : null}

        <nav aria-label={`${area === "admin" ? "Admin" : "Settings"} sections`} className="meridian-nav">
          <span
            aria-hidden="true"
            className="meridian-nav__active"
            style={{ transform: `translateY(${activeNavIndex * 44}px)` }}
          />
          {navItems.map(([key, label, Icon]) => (
            <NavLink
              className={({ isActive }) => `meridian-nav__link${isActive ? " meridian-nav__link--active" : ""}`}
              key={key}
              to={`/${area}/${key}`}
            >
              <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="meridian-sidebar__spacer" />
        <div className="meridian-user-card">
          <span aria-hidden="true" className="meridian-user-card__avatar">{avatar}</span>
          <span className="meridian-user-card__copy">
            <strong>{user?.username || "User"}</strong>
            <small>{roleLabel(user?.role)}</small>
          </span>
        </div>
      </aside>

      <main className="meridian-workspace" data-visual-landmark="workspace">
        <div className="meridian-workspace__inner">
          <header className="meridian-page-header" data-visual-landmark="page-header">
            <h1>{title}</h1>
            {subtitle ? <p>{subtitle}</p> : null}
          </header>
          <div className="meridian-content" key={`${area}:${tab}`}>
            <Outlet />
          </div>
        </div>
      </main>

      {user?.role === "admin" ? <SystemStatusRail /> : null}

      {user?.role === "admin" ? (
        <button
          aria-expanded={statusRailOpen}
          aria-label="System status"
          className="meridian-status-button"
          onClick={() => setStatusRailOpen(!statusRailOpen)}
          title="System status"
          type="button"
        >
          <StatusPanelIcon />
        </button>
      ) : null}

      <button
        aria-label={`Theme: ${theme}. Change Control Center theme`}
        className="meridian-theme-button"
        onClick={cycleTheme}
        title={`Control Center theme: ${theme}`}
        type="button"
      >
        <ThemeIcon aria-hidden="true" size={19} />
      </button>
    </section>
  );
}
