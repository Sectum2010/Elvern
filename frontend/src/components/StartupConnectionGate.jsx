import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import {
  CONNECTION_FAMILIARS,
  CONNECTION_FAMILIAR_ROTATION_MS,
  CONNECTION_SERVER_OOPS_COPY,
  CONNECTION_STATUS_WORDS,
  CONNECTION_VPN_OOPS_COPY,
  CONNECTIVITY_BACKEND_UNREACHABLE,
  CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE,
  CONNECTIVITY_INTERNET_OFFLINE,
  createStartupConnectionController,
  NO_INTERNET_REAPPEAR_MS,
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


function updateStaticConnectionShell({ status, classification, familiarIndex, wordIndex, visible }) {
  const shell = document.getElementById("elvern-connection-shell");
  if (!shell) {
    return;
  }
  const waitingWord = shell.querySelector("[data-connection-waiting-word]");
  const oopsCopy = shell.querySelector("[data-connection-oops-copy]");
  renderWaitingWord(waitingWord, CONNECTION_STATUS_WORDS[wordIndex] || CONNECTION_STATUS_WORDS[0]);
  if (oopsCopy) {
    oopsCopy.textContent = classification === CONNECTIVITY_BACKEND_UNREACHABLE
      ? CONNECTION_SERVER_OOPS_COPY
      : CONNECTION_VPN_OOPS_COPY;
  }
  shell.dataset.familiar = CONNECTION_FAMILIARS[familiarIndex] || CONNECTION_FAMILIARS[0];
  shell.dataset.classification = classification || "";
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


export function RuntimeConnectivityLayer({ controller, snapshot }) {
  const [internetNoticeDismissed, setInternetNoticeDismissed] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const pointerStartYRef = useRef(null);
  const reappearTimerRef = useRef(0);
  const internetOffline = snapshot.runtimeReady
    && snapshot.classification === CONNECTIVITY_INTERNET_OFFLINE;
  const showRuntimeOops = snapshot.runtimeReady
    && snapshot.status === "unreachable"
    && [CONNECTIVITY_BACKEND_UNREACHABLE, CONNECTIVITY_FRONTEND_OR_VPN_UNREACHABLE]
      .includes(snapshot.classification);

  useEffect(() => {
    if (!internetOffline) {
      window.clearTimeout(reappearTimerRef.current);
      reappearTimerRef.current = 0;
      setInternetNoticeDismissed(false);
      setDragOffset(0);
    }
  }, [internetOffline]);

  useEffect(() => () => {
    window.clearTimeout(reappearTimerRef.current);
  }, []);

  function dismissInternetNotice() {
    if (!internetOffline || internetNoticeDismissed) {
      return;
    }
    setInternetNoticeDismissed(true);
    setDragOffset(0);
    window.clearTimeout(reappearTimerRef.current);
    reappearTimerRef.current = window.setTimeout(() => {
      reappearTimerRef.current = 0;
      setInternetNoticeDismissed(false);
    }, NO_INTERNET_REAPPEAR_MS);
  }

  function handlePointerDown(event) {
    pointerStartYRef.current = event.clientY;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handlePointerMove(event) {
    if (pointerStartYRef.current == null) {
      return;
    }
    setDragOffset(Math.min(0, event.clientY - pointerStartYRef.current));
  }

  function handlePointerEnd(event) {
    if (pointerStartYRef.current == null) {
      return;
    }
    const movement = event.clientY - pointerStartYRef.current;
    pointerStartYRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (movement <= -24) {
      dismissInternetNotice();
      return;
    }
    setDragOffset(0);
  }

  const oopsCopy = snapshot.classification === CONNECTIVITY_BACKEND_UNREACHABLE
    ? CONNECTION_SERVER_OOPS_COPY
    : CONNECTION_VPN_OOPS_COPY;

  return (
    <>
      {internetOffline && !internetNoticeDismissed ? (
        <div
          className="runtime-connectivity-notice"
          onPointerCancel={handlePointerEnd}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerEnd}
          role="status"
          style={{ transform: `translate(-50%, ${dragOffset}px)` }}
        >
          No Internet
        </div>
      ) : null}
      {showRuntimeOops ? (
        <div className="runtime-connectivity-oops" role="alert">
          <div className="runtime-connectivity-oops__content">
            <h1>Oops!</h1>
            <p>{oopsCopy}</p>
            <button onClick={() => void controller.retry()} type="button">Retry</button>
          </div>
        </div>
      ) : null}
    </>
  );
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
      status: snapshot.runtimeReady ? "connected" : snapshot.status,
      classification: snapshot.classification,
      familiarIndex: rotationIndex % CONNECTION_FAMILIARS.length,
      wordIndex: rotationIndex % CONNECTION_STATUS_WORDS.length,
      visible: shellVisible,
    });
  }, [rotationIndex, shellVisible, snapshot.classification, snapshot.runtimeReady, snapshot.status]);

  const canRenderApplication = snapshot.runtimeReady || snapshot.serviceReachable;
  return (
    <>
      {canRenderApplication ? children : null}
      <RuntimeConnectivityLayer controller={controller} snapshot={snapshot} />
    </>
  );
}
