import {
  ArrowLeft,
  Cloud,
  Database,
  EyeOff,
  FileClock,
  Gauge,
  Library,
  MonitorPlay,
  Moon,
  PanelRightOpen,
  Palette,
  RotateCcw,
  Settings,
  Shield,
  Sun,
  Users,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext.jsx";
import {
  markLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation.js";
import { useControlCenterSession } from "./ControlCenterSessionContext.jsx";
import { SystemStatusRail } from "./SystemStatusRail.jsx";

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
  appearance: ["Appearance", "Shape the way Elvern feels on this device."],
  library: ["Library", "Manage how your library is organized and protected."],
  "cloud-sharing": ["Cloud & Sharing", "Connect and manage the libraries available to you."],
  "hidden-titles": ["Hidden titles", "Review personal and shared hidden-title scopes."],
  "playback-apps": ["Playback & Apps", "Set up the players and handoff tools used by this device."],
  "server-storage": ["Server & Storage", "Configure server-owned integrations and storage references."],
  overview: ["Overview", "Security posture and operational controls."],
  "users-invites": ["Users & Invites", "Manage people, access, invitations, and active playback work."],
  security: ["Security", "Review sessions, identity controls, and private access."],
  logs: ["Logs", "Inspect active sessions and recent administrative activity."],
  recovery: ["Recovery", "Create and inspect encrypted recovery checkpoints."],
};

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
  const switchTimerRef = useRef(0);
  const navItems = (area === "admin" ? ADMIN_NAV : SETTINGS_NAV)
    .filter(([key]) => key !== "server-storage" || user?.role === "admin");
  const [title, subtitle] = TITLES[tab] || TITLES[area === "admin" ? "overview" : "appearance"];

  useEffect(() => () => window.clearTimeout(switchTimerRef.current), []);

  function returnToLibrary() {
    const identity = { userId: user?.id, role: user?.role };
    const target = readLibraryReturnTarget(identity);
    if (target) {
      markLibraryReturnPending(identity);
    }
    navigate(target?.listPath || "/library?category=movies", {
      state: target ? { restoreLibraryReturn: true } : undefined,
    });
  }

  function switchArea() {
    if (switching) {
      return;
    }
    const destination = area === "admin"
      ? `/settings/${settingsTab}`
      : `/admin/${adminTab}`;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    const midpointDelay = reducedMotion ? 40 : 275;
    const completionDelay = reducedMotion ? 80 : 275;
    setSwitching(true);
    switchTimerRef.current = window.setTimeout(() => {
      navigate(destination);
      switchTimerRef.current = window.setTimeout(() => setSwitching(false), completionDelay);
    }, midpointDelay);
  }

  function cycleTheme() {
    setTheme(theme === "light" ? "mixed" : theme === "mixed" ? "dark" : "light");
  }

  const ThemeIcon = theme === "light" ? Sun : theme === "mixed" ? Settings : Moon;

  return (
    <section
      className={`control-center-desktop control-center-desktop--${theme}${switching ? " control-center-desktop--switching" : ""}${statusRailOpen && user?.role === "admin" ? " control-center-desktop--rail-open" : ""}`}
      data-control-center-area={area}
      data-control-center-tab={tab}
      data-control-center-theme={theme}
    >
      <aside className="control-center-desktop__sidebar">
        <button className="control-center-desktop__brand" onClick={returnToLibrary} type="button">
          <span className="control-center-desktop__brand-mark">E</span>
          <span>Elvern</span>
        </button>
        <div className="control-center-desktop__sidebar-heading">
          {area === "admin" ? "Admin Panel" : "Settings"}
        </div>
        <nav aria-label={`${area === "admin" ? "Admin" : "Settings"} sections`} className="control-center-desktop__nav">
          {navItems.map(([key, label, Icon]) => (
            <NavLink
              className={({ isActive }) => `control-center-desktop__nav-link${isActive ? " control-center-desktop__nav-link--active" : ""}`}
              key={key}
              to={`/${area}/${key}`}
            >
              <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="control-center-desktop__sidebar-footer">
          <button className="control-center-desktop__library-return" onClick={returnToLibrary} type="button">
            <ArrowLeft aria-hidden="true" size={16} />
            Library
          </button>
          {user?.role === "admin" ? (
            <button className="control-center-desktop__area-switch" onClick={switchArea} type="button">
              {area === "admin" ? <Settings aria-hidden="true" size={18} /> : <Shield aria-hidden="true" size={18} />}
              <span>
                <small>Switch to</small>
                {area === "admin" ? "Settings" : "Admin Panel"}
              </span>
            </button>
          ) : null}
        </div>
      </aside>

      <div className="control-center-desktop__workspace">
        <header className="control-center-desktop__header">
          <div>
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </div>
          {user?.role === "admin" ? (
            <button
              aria-expanded={statusRailOpen}
              aria-label="System status"
              className="control-center-desktop__status-button"
              onClick={() => setStatusRailOpen(!statusRailOpen)}
              type="button"
            >
              <PanelRightOpen aria-hidden="true" size={17} />
              Status
            </button>
          ) : null}
        </header>
        <div className="control-center-desktop__content" key={`${area}:${tab}`}>
          <Outlet />
        </div>
      </div>

      {user?.role === "admin" ? <SystemStatusRail /> : null}

      <button
        aria-label={`Theme: ${theme}. Change Control Center theme`}
        className="control-center-desktop__theme-button"
        onClick={cycleTheme}
        title={`Control Center theme: ${theme}`}
        type="button"
      >
        <ThemeIcon aria-hidden="true" size={19} />
      </button>
    </section>
  );
}
