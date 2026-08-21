export class PlaybackLifecycleDiagnosticObserver {
  constructor({
    record,
    recalibrateClock = () => {},
    windowRef = globalThis.window,
    documentRef = globalThis.document,
    navigatorRef = globalThis.navigator,
  }) {
    this.record = record;
    this.recalibrateClock = recalibrateClock;
    this.windowRef = windowRef;
    this.documentRef = documentRef;
    this.navigatorRef = navigatorRef;
    this.listeners = [];
    this.lastClientObservedAt = Date.now();
    this.hiddenAt = null;
    this.hiddenMonotonicAt = null;
    this.frozen = false;
  }

  start() {
    this.listen(this.documentRef, "visibilitychange", () => {
      const hidden = this.documentRef.visibilityState === "hidden";
      const now = Date.now();
      const monotonicNow = this.windowRef?.performance?.now?.() ?? now;
      if (hidden) {
        this.hiddenAt = now;
        this.hiddenMonotonicAt = monotonicNow;
        this.record("page_hidden_started", {
          payload: { page_state: "hidden", visible: false },
        });
      } else {
        const hiddenDurationMs = this.hiddenMonotonicAt != null
          ? Math.max(0, monotonicNow - this.hiddenMonotonicAt)
          : null;
        this.record("page_hidden_ended", {
          payload: {
            last_client_observed_time: this.lastClientObservedAt,
            hidden_duration_ms: hiddenDurationMs,
            page_state: "visible",
            visible: true,
          },
        });
        this.hiddenAt = null;
        this.hiddenMonotonicAt = null;
        this.recalibrateClock();
      }
      this.lastClientObservedAt = now;
      this.record("page_visibility_changed", {
        payload: {
          visible: !hidden,
          page_state: hidden
            ? "hidden"
            : (this.documentRef.hasFocus?.() ? "visible_focused" : "visible_unfocused"),
        },
      });
    });
    this.listen(this.windowRef, "focus", () => this.recordPageState("visible_focused"));
    this.listen(this.windowRef, "blur", () => this.recordPageState(
      this.documentRef.visibilityState === "hidden" ? "hidden" : "visible_unfocused",
    ));
    this.listen(this.windowRef, "pagehide", () => this.record("pagehide", {
      priority: "critical",
      payload: { page_state: "pagehide" },
    }));
    this.listen(this.windowRef, "pageshow", () => {
      this.record("pageshow", { payload: { page_state: "pageshow" } });
      this.recalibrateClock();
    });
    this.listen(this.windowRef, "online", () => {
      this.record("network_online", { payload: { online: true, network_state: "online" } });
      this.recalibrateClock();
    });
    this.listen(this.windowRef, "offline", () => this.record("network_offline", {
      priority: "high",
      payload: { online: false, network_state: "offline" },
    }));
    this.listen(this.documentRef, "fullscreenchange", () => this.record(
      this.documentRef.fullscreenElement ? "fullscreen_entered" : "fullscreen_exited",
      { payload: { active: Boolean(this.documentRef.fullscreenElement), action_origin: "browser" } },
    ));
    const freezeResumeSupported = Boolean(
      this.documentRef && ("onfreeze" in this.documentRef || "wasDiscarded" in this.documentRef)
    );
    if (freezeResumeSupported) {
      this.listen(this.documentRef, "freeze", () => {
        this.frozen = true;
        this.record("page_freeze", {
          observationKind: "measured_client",
          payload: { page_state: "freeze" },
        });
      });
      this.listen(this.documentRef, "resume", () => {
        this.frozen = false;
        this.record("page_resume", {
          observationKind: "measured_client",
          payload: { page_state: "resume" },
        });
        this.recalibrateClock();
      });
    } else {
      this.record("client_capability_unavailable", {
        observationKind: "unsupported",
        capabilityAvailable: false,
        unavailableReason: "freeze_resume_not_detected",
        payload: {
          type: "freeze_resume_events",
          available: false,
          unavailable_reason: "freeze_resume_not_detected",
        },
      });
    }
    this.listen(this.windowRef?.screen?.orientation, "change", () => this.recordOrientation());
    this.record("page_lifecycle_started", {
      payload: {
        page_state: this.documentRef?.visibilityState || "unknown",
        online: this.navigatorRef?.onLine !== false,
        orientation: this.readOrientation(),
        standalone: Boolean(
          this.navigatorRef?.standalone
          || this.windowRef?.matchMedia?.("(display-mode: standalone)")?.matches
        ),
        document_was_discarded: this.documentRef?.wasDiscarded ?? null,
      },
    });
  }

  stop() {
    this.listeners.forEach(({ target, name, handler }) => target.removeEventListener(name, handler));
    this.listeners = [];
  }

  listen(target, name, handler) {
    if (!target?.addEventListener) return;
    target.addEventListener(name, handler);
    this.listeners.push({ target, name, handler });
  }

  recordPageState(pageState) {
    this.lastClientObservedAt = Date.now();
    this.record("page_focus_changed", { payload: { page_state: pageState } });
  }

  readOrientation() {
    return this.windowRef?.screen?.orientation?.type
      || (this.windowRef?.innerWidth > this.windowRef?.innerHeight ? "landscape" : "portrait");
  }

  recordOrientation() {
    this.record("orientation_changed", { payload: { orientation: this.readOrientation() } });
  }
}
