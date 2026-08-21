import { PLAYBACK_DIAGNOSTICS_CLIENT_STATES } from "./constants";

const TERMINAL_STATES = new Set(["sealed", "orphaned_local", "terminal_rejected"]);
const TRANSITIONS = Object.freeze({
  open: new Set([
    "closing",
    "paused_authentication",
    "paused_capacity",
    "interrupted_recoverable",
    "orphaned_local",
    "terminal_rejected",
  ]),
  closing: new Set([
    "sealed",
    "paused_authentication",
    "paused_capacity",
    "interrupted_recoverable",
    "orphaned_local",
    "terminal_rejected",
  ]),
  paused_authentication: new Set(["open", "closing", "sealed", "orphaned_local"]),
  paused_capacity: new Set(["open", "closing", "sealed", "orphaned_local"]),
  interrupted_recoverable: new Set([
    "open",
    "closing",
    "sealed",
    "paused_authentication",
    "paused_capacity",
    "orphaned_local",
  ]),
  orphaned_local: new Set(),
  terminal_rejected: new Set(),
  sealed: new Set(),
});

export function isPlaybackDiagnosticClientState(value) {
  return PLAYBACK_DIAGNOSTICS_CLIENT_STATES.includes(String(value || ""));
}

export class PlaybackDiagnosticClientStateMachine {
  constructor(initialState = "open") {
    if (!isPlaybackDiagnosticClientState(initialState)) {
      throw new TypeError("Invalid playback diagnostics client state");
    }
    this.state = initialState;
    this.generation = 0;
  }

  transition(nextState) {
    const normalized = String(nextState || "");
    if (normalized === this.state) return this.state;
    if (!isPlaybackDiagnosticClientState(normalized)) {
      throw new TypeError("Invalid playback diagnostics client state");
    }
    if (!TRANSITIONS[this.state]?.has(normalized)) {
      throw new Error(`Invalid playback diagnostics transition: ${this.state} -> ${normalized}`);
    }
    this.state = normalized;
    this.generation += 1;
    return this.state;
  }

  canCapture({ critical = false } = {}) {
    if (this.state === "open") return true;
    return critical && ["paused_authentication", "paused_capacity"].includes(this.state);
  }

  get closing() {
    return this.state === "closing";
  }

  get terminal() {
    return TERMINAL_STATES.has(this.state);
  }
}
