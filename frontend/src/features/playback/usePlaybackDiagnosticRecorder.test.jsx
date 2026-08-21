import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const { recorder, RecorderMock } = vi.hoisted(() => ({
  recorder: {
    start: vi.fn(async () => {}),
    stop: vi.fn(),
    updateContext: vi.fn(),
    attachHls: vi.fn(),
    detachHls: vi.fn(),
    recordAction: vi.fn(),
    record: vi.fn(),
  },
  RecorderMock: vi.fn(),
}));

vi.mock("../../lib/playbackDiagnostics/recorder", () => ({
  PlaybackDiagnosticRecorder: RecorderMock,
}));

import { usePlaybackDiagnosticRecorder } from "./usePlaybackDiagnosticRecorder";

beforeEach(() => {
  vi.clearAllMocks();
  RecorderMock.mockImplementation(function RecorderConstructor() {
    return recorder;
  });
});

function options() {
  return {
    hlsEvents: {},
    hlsRef: { current: {} },
    videoRef: { current: {} },
    mobileSession: {
      session_id: "session-synthetic-0001",
      playback_diagnostics_enabled: true,
    },
    streamSource: null,
    hlsEngineDiagnostics: null,
    deviceClass: "desktop",
    videoElementKey: 1,
    itemId: 42,
    initialDiagnosticsEnabled: true,
    ownerUserId: 7,
  };
}

test("diagnostic facade failures never escape into playback callers or cleanup", () => {
  recorder.updateContext.mockImplementation(() => { throw new Error("context"); });
  recorder.attachHls.mockImplementation(() => { throw new Error("attach"); });
  recorder.detachHls.mockImplementation(() => { throw new Error("detach"); });
  recorder.recordAction.mockImplementation(() => { throw new Error("action"); });
  recorder.record.mockImplementation(() => { throw new Error("event"); });
  recorder.stop.mockImplementation(() => { throw new Error("stop"); });

  const { result, rerender, unmount } = renderHook(
    (props) => usePlaybackDiagnosticRecorder(props),
    { initialProps: options() },
  );

  expect(() => act(() => result.current.attachDiagnosticHls({}))).not.toThrow();
  expect(() => act(() => result.current.detachDiagnosticHls())).not.toThrow();
  expect(() => act(() => result.current.recordDiagnosticAction("play_intent", "user"))).not.toThrow();
  expect(() => act(() => result.current.recordDiagnosticEvent("synthetic"))).not.toThrow();
  expect(() => rerender({ ...options(), deviceClass: "tablet" })).not.toThrow();
  expect(() => unmount()).not.toThrow();
});

test("recorder construction failure leaves playback hook usable", () => {
  RecorderMock.mockImplementationOnce(function FailingRecorderConstructor() {
    throw new Error("constructor");
  });
  const { result } = renderHook(() => usePlaybackDiagnosticRecorder(options()));

  expect(() => result.current.recordDiagnosticAction("play_intent", "user")).not.toThrow();
});

test("disabled diagnostics never construct a recorder or attach to playback", () => {
  const disabled = options();
  disabled.mobileSession = {
    ...disabled.mobileSession,
    playback_diagnostics_enabled: false,
  };

  const { result, unmount } = renderHook(() => usePlaybackDiagnosticRecorder(disabled));

  act(() => result.current.recordDiagnosticAction("play_intent", "user"));
  unmount();
  expect(RecorderMock).not.toHaveBeenCalled();
  expect(recorder.start).not.toHaveBeenCalled();
  expect(recorder.attachHls).not.toHaveBeenCalled();
  expect(recorder.recordAction).not.toHaveBeenCalled();
});

test("pre-session play intent is handed to the recorder when diagnostics become enabled", () => {
  const pending = options();
  pending.mobileSession = null;
  const { result, rerender } = renderHook(
    (props) => usePlaybackDiagnosticRecorder(props),
    { initialProps: pending },
  );

  act(() => result.current.recordDiagnosticAction("play_intent", "user", { mode: "lite" }));
  rerender(options());

  expect(RecorderMock).toHaveBeenCalledTimes(1);
  const constructorOptions = RecorderMock.mock.calls[0][0];
  expect(constructorOptions.provisionalEvents).toHaveLength(1);
  expect(constructorOptions.provisionalEvents[0]).toMatchObject({
    eventName: "play_intent",
    options: {
      priority: "high",
      payload: { mode: "lite", action_origin: "user" },
    },
  });
});

test("enabled diagnostics attach an already available HLS engine", () => {
  const configured = options();
  const hls = configured.hlsRef.current;

  renderHook(() => usePlaybackDiagnosticRecorder(configured));

  expect(RecorderMock).toHaveBeenCalledTimes(1);
  expect(recorder.start).toHaveBeenCalledTimes(1);
  expect(recorder.attachHls).toHaveBeenCalledWith(hls);
});
