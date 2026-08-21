import { PlaybackDiagnosticsDataPlane } from "./dataPlane";
import { PlaybackDiagnosticsRecoveryCoordinator } from "./recoveryCoordinator";

const planes = new Map();

function post(type, payload = {}) {
  globalThis.postMessage({ type, ...payload });
}

const recoveryCoordinator = new PlaybackDiagnosticsRecoveryCoordinator({
  runtimeRef: globalThis,
  onHealth: (health) => post("health", { clientId: "recovery", health }),
  onComplete: ({ recovered, pending }) => post("recovery_complete", { recovered, pending }),
  onIdle: () => post("recovery_idle"),
});

async function startPlane(message) {
  let plane;
  const notifySealed = () => {
    if (planes.get(message.clientId) !== plane) return;
    post("close_result", { clientId: message.clientId, sealed: true });
    plane.stop();
    planes.delete(message.clientId);
  };
  plane = new PlaybackDiagnosticsDataPlane({
    ...message.options,
    runtimeRef: globalThis,
    onHealth: (health) => post("health", { clientId: message.clientId, health }),
    onSealed: notifySealed,
  });
  planes.set(message.clientId, plane);
  await plane.start();
  post("ready", {
    clientId: message.clientId,
    persistent: plane.persistent,
    playbackAttemptId: plane.playbackAttemptId,
  });
}

globalThis.addEventListener("message", (event) => {
  const message = event.data || {};
  const operation = async () => {
    if (message.type === "start") return startPlane(message);
    if (message.type === "recover_all") return recoveryCoordinator.wake(message);
    const plane = planes.get(message.clientId);
    if (!plane) return undefined;
    if (message.type === "capture") {
      return plane.capture(message.eventName, message.options, { queuedAtMs: message.queuedAtMs });
    }
    if (message.type === "update_context") return plane.updateContext(message.context);
    if (message.type === "set_attempt") return plane.setPlaybackAttempt(message.playbackAttemptId);
    if (message.type === "set_overhead_mode") {
      return plane.setOverheadMode(message.mode, message.reason);
    }
    if (message.type === "declare_drop") return plane.declareDropped(message.count, message.reasonCode);
    if (message.type === "wake") return plane.wake(message.options);
    if (message.type === "close") {
      const sealed = await plane.close(message.reason);
      post("close_result", { clientId: message.clientId, sealed });
      if (sealed) {
        plane.stop();
        planes.delete(message.clientId);
      }
      return sealed;
    }
    if (message.type === "stop") {
      plane.stop();
      planes.delete(message.clientId);
    }
    return undefined;
  };
  Promise.resolve(operation()).catch((error) => {
    post("failure", {
      clientId: message.clientId,
      messageId: message.messageId,
      errorClass: error?.name || "Error",
    });
  }).finally(() => {
    if (message.messageId != null) {
      post("ack", { clientId: message.clientId, messageId: message.messageId });
    }
  });
});
