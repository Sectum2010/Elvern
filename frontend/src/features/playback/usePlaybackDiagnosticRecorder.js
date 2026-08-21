import { useEffect, useRef } from "react";

import { diagnosticUrlIdentity } from "../../lib/playbackDiagnostics/privacy";
import { PlaybackDiagnosticRecorder } from "../../lib/playbackDiagnostics/recorder";

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
  const sessionId = mobileSession?.session_id || "";

  useEffect(() => {
    const video = videoRef.current;
    if (!sessionId || !video) return undefined;
    let recorder;
    try {
      recorder = new PlaybackDiagnosticRecorder({
        playbackSessionId: sessionId,
        video,
        hlsEvents,
        context: {
          device_class: deviceClass || "unknown",
          hls_engine: hlsEngineDiagnostics?.selectedEngine || mobileSession?.selected_hls_engine || "unknown",
          playback_mode: mobileSession?.playback_mode || "unknown",
          stream_mode: mobileSession?.engine_mode || "unknown",
          source_kind: mobileSession?.source_kind || "unknown",
          epoch_id: mobileSession?.active_epoch_id || null,
          attachment_revision: mobileSession?.attach_revision ?? null,
          stream_identity: streamSource?.url
            ? diagnosticUrlIdentity(streamSource.url).url_hash
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
  }, [sessionId, videoElementKey]);

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
          ? diagnosticUrlIdentity(streamSource.url).url_hash
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
        recorderRef.current?.recordAction(eventName, origin, payload);
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
