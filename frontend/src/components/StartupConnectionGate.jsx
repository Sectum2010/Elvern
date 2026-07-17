import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import {
  CONNECTION_FAMILIARS,
  CONNECTION_FAMILIAR_ROTATION_MS,
  CONNECTION_STATUS_WORDS,
  createStartupConnectionController,
  STARTUP_APPLICATION_READY_EVENT,
  STARTUP_CONNECTIVITY_FAILURE_EVENT,
  STARTUP_SHELL_REVEAL_DELAY_MS,
} from "../lib/startupConnection.js";


const READY_TRANSITION_MS = 180;


function renderWaitingWord(element, text) {
  if (!element || (element.getAttribute("aria-label") === text && element.children.length === text.length)) {
    return;
  }
  element.setAttribute("aria-label", text);
  const letters = Array.from(text, (letter, index) => {
    const span = document.createElement("span");
    span.className = "elvern-connection-shell__letter";
    span.setAttribute("aria-hidden", "true");
    span.style.setProperty("--letter-index", String(index));
    span.textContent = letter === " " ? "\u00a0" : letter;
    return span;
  });
  element.replaceChildren(...letters);
}


function updateStaticConnectionShell({ status, familiarIndex, wordIndex, visible }) {
  const shell = document.getElementById("elvern-connection-shell");
  if (!shell) {
    return;
  }
  const waitingWord = shell.querySelector("[data-connection-waiting-word]");
  renderWaitingWord(waitingWord, CONNECTION_STATUS_WORDS[wordIndex] || CONNECTION_STATUS_WORDS[0]);
  shell.dataset.familiar = CONNECTION_FAMILIARS[familiarIndex] || CONNECTION_FAMILIARS[0];
  shell.dataset.state = status;
  if (status === "connected") {
    shell.classList.remove("elvern-connection-shell--visible");
    shell.classList.add("elvern-connection-shell--ready");
    shell.setAttribute("aria-hidden", "true");
    window.setTimeout(() => {
      if (shell.dataset.state === "connected") {
        shell.hidden = true;
      }
    }, READY_TRANSITION_MS);
  } else {
    shell.hidden = false;
    shell.classList.remove("elvern-connection-shell--ready");
    shell.removeAttribute("aria-hidden");
    shell.classList.toggle("elvern-connection-shell--visible", visible || status === "unreachable");
  }
}


export function StartupConnectionGate({ children, controller: providedController = null }) {
  const controllerRef = useRef(providedController || createStartupConnectionController({ requireApplicationReady: true }));
  const controller = controllerRef.current;
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  const [rotationIndex, setRotationIndex] = useState(0);
  const [shellVisible, setShellVisible] = useState(false);

  useEffect(() => {
    window.__elvernStaticConnectionShellCleanup?.();
    controller.start();
    return () => controller.stop();
  }, [controller]);

  useEffect(() => {
    const startedAt = Number(window.__elvernConnectionStartedAt) || Date.now();
    const remaining = Math.max(0, STARTUP_SHELL_REVEAL_DELAY_MS - (Date.now() - startedAt));
    if (remaining === 0) {
      setShellVisible(true);
      return undefined;
    }
    const timeoutId = window.setTimeout(() => setShellVisible(true), remaining);
    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    function handleApplicationReady() {
      controller.reportApplicationReady();
    }
    window.addEventListener(STARTUP_APPLICATION_READY_EVENT, handleApplicationReady);
    return () => window.removeEventListener(STARTUP_APPLICATION_READY_EVENT, handleApplicationReady);
  }, [controller]);

  useEffect(() => {
    function handleConnectivityFailure() {
      controller.reportFailure();
    }
    window.addEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleConnectivityFailure);
    return () => window.removeEventListener(STARTUP_CONNECTIVITY_FAILURE_EVENT, handleConnectivityFailure);
  }, [controller]);

  useEffect(() => {
    const retryButton = document.querySelector("#elvern-connection-shell [data-connection-retry]");
    if (!retryButton) {
      return undefined;
    }
    const handleRetry = () => {
      void controller.retry();
    };
    retryButton.addEventListener("click", handleRetry);
    return () => retryButton.removeEventListener("click", handleRetry);
  }, [controller]);

  useEffect(() => {
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    if (reducedMotion || snapshot.status !== "connecting") {
      setRotationIndex(0);
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      setRotationIndex((current) => current + 1);
    }, CONNECTION_FAMILIAR_ROTATION_MS);
    return () => window.clearInterval(intervalId);
  }, [snapshot.status]);

  useEffect(() => {
    updateStaticConnectionShell({
      status: snapshot.status,
      familiarIndex: rotationIndex % CONNECTION_FAMILIARS.length,
      wordIndex: rotationIndex % CONNECTION_STATUS_WORDS.length,
      visible: shellVisible,
    });
  }, [rotationIndex, shellVisible, snapshot.status]);

  return snapshot.serviceReachable ? children : null;
}
