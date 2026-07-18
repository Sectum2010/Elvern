import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
import {
  applyInitialSpaCanonicalization,
  detectSpaBasename,
} from "./lib/canonicalSpaPath.js";
import { registerElvernServiceWorker } from "./lib/serviceWorkerRegistration.js";
import { installIOSViewportCoordinator } from "./lib/iosViewportCoordinator.js";


window.__elvernAppBootstrapStarted = true;
window.__elvernBootstrapPhase = "module_bootstrap_started";
installIOSViewportCoordinator();

const basename = detectSpaBasename();
applyInitialSpaCanonicalization(window, { basename });

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
