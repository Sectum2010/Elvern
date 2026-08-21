import { useEffect, useRef } from "react";

import { diagnosticUrlIdentity } from "../../lib/playbackDiagnostics/privacy";
import { PlaybackDiagnosticRecorder } from "../../lib/playbackDiagnostics/recorder";
import { captureClientClock, createDiagnosticId } from "../../lib/playbackDiagnostics/schema";

export function usePlaybackDiagnosticRecorder({
  hlsEvents,
  hlsRef,
  videoRef,
  mobileSession,
  streamSource,
  hlsEngineDiagnostics,
  deviceClass,
  videoElementKey,
}) {
  const recorderRef = useRef(null);
  const pendingActionsRef = useRef([]);
  const playbackAttemptIdRef = useRef(createDiagnosticId("attempt"));
  const sessionId = mobileSession?.session_id || "";
  const diagnosticsEnabled = mobileSession?.playback_diagnostics_enabled === true;

  useEffect(() => {
    const video = videoRef.current;
    if (!diagnosticsEnabled) {
      if (mobileSession?.playback_diagnostics_enabled === false) pendingActionsRef.current = [];
      return undefined;
    }
    if (!sessionId || !video) return undefined;
    let recorder;
    const provisionalEvents = pendingActionsRef.current.splice(0);
    try {
      recorder = new PlaybackDiagnosticRecorder({
        playbackSessionId: sessionId,
        video,
        hlsEvents,
        provisionalEvents,
        playbackAttemptId: playbackAttemptIdRef.current,
        context: {
          device_class: deviceClass || "unknown",
          hls_engine: hlsEngineDiagnostics?.selectedEngine || mobileSession?.selected_hls_engine || "unknown",
          playback_mode: mobileSession?.playback_mode || "unknown",
          stream_mode: mobileSession?.engine_mode || "unknown",
          source_kind: mobileSession?.source_kind || "unknown",
          epoch_id: mobileSession?.active_epoch_id || null,
          attachment_revision: mobileSession?.attach_revision ?? null,
          stream_identity: streamSource?.url
            ? diagnosticUrlIdentity(streamSource.url).normalized_route
            : null,
        },
      });
    } catch {
      return undefined;
    }
    recorderRef.current = recorder;
    recorder.start().catch(() => {});
    return () => {
      try {
        recorder.stop();
      } catch {
        // Diagnostics teardown cannot affect playback component cleanup.
      }
      if (recorderRef.current === recorder) recorderRef.current = null;
    };
  }, [diagnosticsEnabled, sessionId, videoElementKey]);

  useEffect(() => {
    try {
      recorderRef.current?.updateContext({
        device_class: deviceClass || "unknown",
        hls_engine: hlsEngineDiagnostics?.selectedEngine || mobileSession?.selected_hls_engine || "unknown",
        playback_mode: mobileSession?.playback_mode || "unknown",
        stream_mode: mobileSession?.engine_mode || "unknown",
        source_kind: mobileSession?.source_kind || "unknown",
        epoch_id: mobileSession?.active_epoch_id || null,
        attachment_revision: mobileSession?.attach_revision ?? null,
        stream_identity: streamSource?.url
          ? diagnosticUrlIdentity(streamSource.url).normalized_route
          : null,
      });
    } catch {
      // Diagnostics context updates cannot affect playback state.
    }
  }, [
    deviceClass,
    hlsEngineDiagnostics?.selectedEngine,
    mobileSession?.active_epoch_id,
    mobileSession?.attach_revision,
    mobileSession?.engine_mode,
    mobileSession?.playback_mode,
    mobileSession?.selected_hls_engine,
    mobileSession?.source_kind,
    streamSource?.url,
  ]);

  useEffect(() => {
    try {
      recorderRef.current?.attachHls(hlsRef.current);
    } catch {
      // Diagnostics attachment cannot affect the HLS engine.
    }
    return () => {
      try {
        recorderRef.current?.detachHls();
      } catch {
        // Diagnostics teardown cannot affect HLS teardown.
      }
    };
  }, [hlsEngineDiagnostics?.selectedEngine, streamSource?.url]);

  return {
    attachDiagnosticHls(hls) {
      try {
        recorderRef.current?.attachHls(hls);
      } catch {
        // Diagnostics are never a playback control input.
      }
    },
    detachDiagnosticHls() {
      try {
        recorderRef.current?.detachHls();
      } catch {
        // Diagnostics are never a playback control input.
      }
    },
    recordDiagnosticAction(eventName, origin, payload) {
      try {
        if (recorderRef.current) {
          recorderRef.current.recordAction(eventName, origin, payload);
        } else if (mobileSession?.playback_diagnostics_enabled !== false) {
          pendingActionsRef.current.push({
            eventName,
            options: {
              priority: "high",
              payload: { ...payload, action_origin: origin },
              capturedClock: captureClientClock(),
              playbackAttemptId: playbackAttemptIdRef.current,
            },
          });
          if (pendingActionsRef.current.length > 16) pendingActionsRef.current.shift();
        }
      } catch {
        // Diagnostics are never a playback control input.
      }
    },
    recordDiagnosticEvent(eventName, options) {
      try {
        recorderRef.current?.record(eventName, options);
      } catch {
        // Diagnostics are never a playback control input.
      }
    },
  };
}
