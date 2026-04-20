import { useEffect, useRef, useState } from "react";
import { apiRequest } from "../../lib/api";
import {
  buildFullPlaybackReadyKey,
  buildHlsProbeSegmentUrl,
  getPlaybackMode,
  resolveBrowserPlaybackSessionRoot,
} from "../../lib/browserPlayback";

export function usePlaybackReadyNotice({ pathname, navigate }) {
  const [playbackReadyNotice, setPlaybackReadyNotice] = useState(null);
  const fullProbeInFlightRef = useRef(false);
  const announcedFullReadyKeysRef = useRef(new Set());

  useEffect(() => {
    let cancelled = false;
    let timerId = 0;
    const sessionRoot = resolveBrowserPlaybackSessionRoot();

    async function sendBackgroundFullProbe(sessionPayload) {
      if (fullProbeInFlightRef.current) {
        return;
      }
      const probeUrl = buildHlsProbeSegmentUrl(sessionPayload);
      if (!probeUrl) {
        return;
      }
      fullProbeInFlightRef.current = true;
      const startedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
      try {
        const response = await fetch(probeUrl, {
          credentials: "include",
          cache: "no-store",
        });
        if (!response.ok) {
          return;
        }
        const buffer = await response.arrayBuffer();
        if (buffer.byteLength <= 0) {
          return;
        }
        const finishedAt = typeof performance !== "undefined" ? performance.now() : Date.now();
        const durationMs = Math.max(1, Math.round(finishedAt - startedAt));
        await apiRequest(
          sessionPayload.heartbeat_url || `${sessionRoot}/sessions/${sessionPayload.session_id}/heartbeat`,
          {
            method: "POST",
            data: {
              client_probe_bytes: buffer.byteLength,
              client_probe_duration_ms: durationMs,
              lifecycle_state: "attached",
              playing: false,
            },
          },
        );
      } catch {
        // Background Full probes are best-effort only.
      } finally {
        fullProbeInFlightRef.current = false;
      }
    }

    async function pollActivePlayback() {
      let nextDelayMs = 6000;
      try {
        const payload = await apiRequest(`${sessionRoot}/active`);
        if (cancelled) {
          return;
        }
        const isFullPlayback = getPlaybackMode(payload?.playback_mode) === "full";
        const onMatchingDetailPage = Boolean(payload?.media_item_id) && pathname === `/library/${payload.media_item_id}`;

        if (!payload || !isFullPlayback) {
          setPlaybackReadyNotice(null);
          nextDelayMs = 6000;
        } else {
          const readyKey = buildFullPlaybackReadyKey(payload);
          if (!payload.mode_ready && !onMatchingDetailPage) {
            await sendBackgroundFullProbe(payload);
            nextDelayMs = 3000;
          } else {
            nextDelayMs = payload.mode_ready ? 5000 : 3000;
          }
          if (payload.mode_ready && readyKey && !announcedFullReadyKeysRef.current.has(readyKey) && !onMatchingDetailPage) {
            announcedFullReadyKeysRef.current.add(readyKey);
            setPlaybackReadyNotice({
              key: readyKey,
              itemId: payload.media_item_id,
              text: "Full Playback Ready",
            });
            if (typeof Notification !== "undefined" && Notification.permission === "granted") {
              const notification = new Notification("Full Playback Ready");
              notification.onclick = () => {
                window.focus();
                navigate(`/library/${payload.media_item_id}`);
                notification.close();
              };
            }
          }
          if (onMatchingDetailPage) {
            setPlaybackReadyNotice((current) => (
              current?.itemId === payload.media_item_id ? null : current
            ));
          }
        }
      } catch {
        if (cancelled) {
          return;
        }
        nextDelayMs = 6000;
      }
      if (!cancelled) {
        timerId = window.setTimeout(() => {
          pollActivePlayback().catch(() => {
            // The next timeout will retry.
          });
        }, nextDelayMs);
      }
    }

    pollActivePlayback().catch(() => {
      // The retry loop above handles transient watcher failures.
    });
    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, [pathname, navigate]);

  function dismissPlaybackReadyNotice() {
    setPlaybackReadyNotice(null);
  }

  function openPlaybackReadyNotice() {
    if (!playbackReadyNotice) {
      return;
    }
    setPlaybackReadyNotice(null);
    navigate(`/library/${playbackReadyNotice.itemId}`);
  }

  return {
    playbackReadyNotice,
    dismissPlaybackReadyNotice,
    openPlaybackReadyNotice,
  };
}
