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
  RecorderMock.mockImplementation(() => recorder);
});

function options() {
  return {
    hlsEvents: {},
    hlsRef: { current: {} },
    videoRef: { current: {} },
    mobileSession: { session_id: "session-synthetic-0001" },
    streamSource: null,
    hlsEngineDiagnostics: null,
    deviceClass: "desktop",
    videoElementKey: 1,
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
  RecorderMock.mockImplementationOnce(() => { throw new Error("constructor"); });
  const { result } = renderHook(() => usePlaybackDiagnosticRecorder(options()));

  expect(() => result.current.recordDiagnosticAction("play_intent", "user")).not.toThrow();
});
