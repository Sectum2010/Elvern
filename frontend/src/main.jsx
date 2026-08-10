import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
import "./controlCenter.css";
import {
  applyInitialSpaCanonicalization,
  detectSpaBasename,
} from "./lib/canonicalSpaPath.js";
import { registerElvernServiceWorker } from "./lib/serviceWorkerRegistration.js";
import { installIOSViewportCoordinator } from "./lib/iosViewportCoordinator.js";
import { installPageResumeCoordinator } from "./lib/pageResume.js";
import { applyControlCenterPaint } from "./lib/controlCenterPaint.js";
import { classifyControlCenterPath, isDesktopControlCenterDevice } from "./lib/controlCenterRoutes.js";
import { detectClientDeviceClass, detectClientPlatform } from "./lib/platformDetection.js";


window.__elvernAppBootstrapStarted = true;
window.__elvernBootstrapPhase = "module_bootstrap_started";
installIOSViewportCoordinator();
installPageResumeCoordinator();

const basename = detectSpaBasename();
applyInitialSpaCanonicalization(window, { basename });
const initialRelativePath = basename === "/"
  ? window.location.pathname
  : window.location.pathname.slice(basename.length) || "/";
applyControlCenterPaint({
  active: isDesktopControlCenterDevice(detectClientDeviceClass(), detectClientPlatform())
    && Boolean(classifyControlCenterPath(initialRelativePath).area),
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void registerElvernServiceWorker();
  }, { once: true });
}

window.__elvernBootstrapPhase = "react_started";
ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter basename={basename}>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
