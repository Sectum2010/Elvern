import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";


const LEGACY_SW_RESET_KEY = "elvern-sw-reset";
const VIEWPORT_SYNC_SENTINEL = "__elvernViewportSyncInstalled";
const VIEWPORT_SYNC_API_KEY = "__elvernRequestViewportNormalization";
const BASE_VIEWPORT_CONTENT = "width=device-width, initial-scale=1.0, viewport-fit=cover, shrink-to-fit=no";
const RESET_VIEWPORT_CONTENT = `${BASE_VIEWPORT_CONTENT}, maximum-scale=1.0`;

function isIPhoneShellDevice() {
  if (typeof navigator === "undefined") {
    return false;
  }
  const userAgent = navigator.userAgent || "";
  return /iphone|ipod/i.test(userAgent);
}

function installViewportSync() {
  if (typeof window === "undefined") {
    return;
  }
  if (window[VIEWPORT_SYNC_SENTINEL]) {
    return;
  }
  window[VIEWPORT_SYNC_SENTINEL] = true;

  let pendingFrame = 0;
  let pendingViewportRestore = 0;
  const isIPhoneShell = isIPhoneShellDevice();
  const viewportMeta = document.querySelector('meta[name="viewport"]');

  function syncViewportMetrics() {
    pendingFrame = 0;
    const viewport = window.visualViewport;
    const width = Math.round(viewport?.width || window.innerWidth || 0);
    const height = Math.round(viewport?.height || window.innerHeight || 0);
    const offsetLeft = Math.round(viewport?.offsetLeft || 0);
    const layoutWidth = Math.round(window.innerWidth || width || 0);
    const offsetRight = Math.max(0, layoutWidth - width - offsetLeft);
    if (!width || !height) {
      return;
    }
    const root = document.documentElement;
    root.style.setProperty("--app-viewport-height", `${height}px`);
    root.style.setProperty("--app-viewport-bleed", `${Math.max(240, Math.round(height * 0.38))}px`);
    root.style.setProperty("--app-viewport-offset-left", `${offsetLeft}px`);
    root.style.setProperty("--app-viewport-offset-right", `${offsetRight}px`);
    root.dataset.viewportOrientation = width > height ? "landscape" : "portrait";
    if (isIPhoneShell) {
      root.dataset.deviceShell = "iphone";
    } else {
      delete root.dataset.deviceShell;
    }
  }

  function requestViewportSync() {
    if (pendingFrame) {
      return;
    }
    pendingFrame = window.requestAnimationFrame(syncViewportMetrics);
  }

  function requestIPhoneViewportReset() {
    if (!isIPhoneShell || !viewportMeta) {
      return;
    }
    window.clearTimeout(pendingViewportRestore);
    viewportMeta.setAttribute("content", RESET_VIEWPORT_CONTENT);
    pendingViewportRestore = window.setTimeout(() => {
      viewportMeta.setAttribute("content", BASE_VIEWPORT_CONTENT);
      requestViewportSync();
      window.setTimeout(requestViewportSync, 120);
    }, 180);
  }

  function requestSettledViewportSync({ resetViewport = false } = {}) {
    requestViewportSync();
    window.setTimeout(requestViewportSync, 60);
    window.setTimeout(requestViewportSync, 240);
    window.setTimeout(requestViewportSync, 600);
    window.setTimeout(requestViewportSync, 1000);
    if (resetViewport) {
      requestIPhoneViewportReset();
    }
  }

  window[VIEWPORT_SYNC_API_KEY] = requestSettledViewportSync;

  syncViewportMetrics();
  window.addEventListener("resize", requestViewportSync, { passive: true });
  window.addEventListener("orientationchange", () => requestSettledViewportSync({ resetViewport: true }), {
    passive: true,
  });
  window.addEventListener("pageshow", () => requestSettledViewportSync({ resetViewport: true }), {
    passive: true,
  });
  window.addEventListener("focus", requestViewportSync, { passive: true });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      requestSettledViewportSync({ resetViewport: true });
    }
  });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", requestViewportSync, { passive: true });
    window.visualViewport.addEventListener("scroll", requestViewportSync, { passive: true });
  }
}

installViewportSync();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", async () => {
    try {
      const registrations = await navigator.serviceWorker.getRegistrations();
      if (!registrations.length) {
        window.sessionStorage.removeItem(LEGACY_SW_RESET_KEY);
        return;
      }

      await Promise.all(registrations.map((registration) => registration.unregister()));

      if ("caches" in window) {
        const cacheKeys = await window.caches.keys();
        await Promise.all(
          cacheKeys
            .filter((key) => key.startsWith("elvern-shell"))
            .map((key) => window.caches.delete(key)),
        );
      }

      if (!window.sessionStorage.getItem(LEGACY_SW_RESET_KEY)) {
        window.sessionStorage.setItem(LEGACY_SW_RESET_KEY, "1");
        window.location.reload();
        return;
      }

      window.sessionStorage.removeItem(LEGACY_SW_RESET_KEY);
    } catch (error) {
      console.error("Failed to disable legacy service worker caching", error);
    }
  });
}


ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
