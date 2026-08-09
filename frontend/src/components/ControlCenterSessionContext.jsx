import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

import {
  CONTROL_CENTER_SESSION_RESET_EVENT,
  CONTROL_CENTER_THEMES,
  readControlCenterTab,
  readControlCenterTheme,
  writeControlCenterTab,
  writeControlCenterTheme,
} from "../lib/controlCenterSession.js";
import { classifyControlCenterPath } from "../lib/controlCenterRoutes.js";

const STATUS_RAIL_STORAGE_KEY = "elvern:control-center:status-rail-open";
const ControlCenterSessionContext = createContext(null);

function readRailOpen() {
  try {
    return window.localStorage.getItem(STATUS_RAIL_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function ControlCenterSessionProvider({ children }) {
  const location = useLocation();
  const [theme, setThemeState] = useState(readControlCenterTheme);
  const [settingsTab, setSettingsTab] = useState(() => readControlCenterTab("settings"));
  const [adminTab, setAdminTab] = useState(() => readControlCenterTab("admin"));
  const [statusRailOpen, setStatusRailOpenState] = useState(readRailOpen);
  const classification = classifyControlCenterPath(location.pathname);

  useEffect(() => {
    if (!classification.tab) {
      return;
    }
    writeControlCenterTab(classification.area, classification.tab);
    if (classification.area === "admin") {
      setAdminTab(classification.tab);
    } else {
      setSettingsTab(classification.tab);
    }
  }, [classification.area, classification.tab]);

  useEffect(() => {
    function handleSessionReset() {
      setThemeState("light");
      setSettingsTab("appearance");
      setAdminTab("overview");
    }
    window.addEventListener(CONTROL_CENTER_SESSION_RESET_EVENT, handleSessionReset);
    return () => window.removeEventListener(CONTROL_CENTER_SESSION_RESET_EVENT, handleSessionReset);
  }, []);

  const value = useMemo(() => ({
    theme,
    settingsTab,
    adminTab,
    statusRailOpen,
    setTheme(nextTheme) {
      const normalized = CONTROL_CENTER_THEMES.includes(nextTheme) ? nextTheme : "light";
      writeControlCenterTheme(normalized);
      setThemeState(normalized);
    },
    setStatusRailOpen(nextOpen) {
      const open = Boolean(nextOpen);
      try {
        window.localStorage.setItem(STATUS_RAIL_STORAGE_KEY, open ? "1" : "0");
      } catch {
        // Device-local visual preference is best-effort.
      }
      setStatusRailOpenState(open);
    },
  }), [adminTab, settingsTab, statusRailOpen, theme]);

  return (
    <ControlCenterSessionContext.Provider value={value}>
      {children}
    </ControlCenterSessionContext.Provider>
  );
}

export function useControlCenterSession() {
  const context = useContext(ControlCenterSessionContext);
  if (!context) {
    throw new Error("useControlCenterSession must be used inside ControlCenterSessionProvider");
  }
  return context;
}
