import assert from "node:assert/strict";
import { test } from "vitest";

import {
  classifyBrowserPlatform,
  diagnosticUrlIdentity,
  sanitizeClientDiagnosticPayload,
  sha256DiagnosticPath,
} from "./privacy.js";
import {
  createPlaybackDiagnosticEvent,
  decimalNanoseconds,
} from "./schema.js";

test("client diagnostics payload is allowlisted before local persistence", () => {
  const sanitized = sanitizeClientDiagnosticPayload({
    buffered_ahead_ms: 1_250,
    unknown_private_state: "must not persist",
    memory: {
      used_js_heap_bytes: 42,
      username: "must not persist",
    },
  });

  assert.deepEqual(sanitized, {
    buffered_ahead_ms: 1_250,
    memory: { used_js_heap_bytes: 42 },
  });
});

test("all required emitted field families survive the client payload contract", () => {
  const intended = {
    requested: "lite",
    minimum_buffer_ms: 750,
    type: "request_video_frame_callback",
    media_element_time_ms: 1_250,
    duplicate_count: 2,
    out_of_order_count: 1,
    buffered_range_count: 3,
    seekable_range_count: 2,
    played_range_count: 1,
    chunk_sequence: 4,
    final: true,
    serialized_bytes: 512,
    samples: [{ media_element_time_ms: 1_200, buffered_range_count: 3 }],
  };

  assert.deepEqual(sanitizeClientDiagnosticPayload(intended), intended);
});

test("client diagnostics rejects secret-like strings, full URLs, and absolute paths", () => {
  for (const reason of [
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
    "https://provider.invalid/file?token=secret",
    "/private/media/title.mkv",
  ]) {
    assert.throws(
      () => sanitizeClientDiagnosticPayload({ reason }),
      /Diagnostic value/,
    );
  }
});

test("URL identity keeps only a normalized route and hashes that query-free route", async () => {
  const raw = "https://elvern.invalid/api/browser-playback/epochs/abcdef0123456789abcdef01/segments/42.m4s?token=secret#x";
  const identity = diagnosticUrlIdentity(raw);

  assert.equal(
    identity.normalized_route,
    "/api/browser-playback/epochs/:id/segments/:segment",
  );
  assert.equal(JSON.stringify(identity).includes("secret"), false);
  assert.equal(JSON.stringify(identity).includes("elvern.invalid"), false);
  assert.deepEqual(sanitizeClientDiagnosticPayload(identity), identity);
  const digest = await sha256DiagnosticPath(raw, {
    subtle: {
      digest: async (_algorithm, bytes) => {
        assert.equal(new TextDecoder().decode(bytes), identity.normalized_route);
        return new Uint8Array(32).fill(0xab).buffer;
      },
    },
  });
  assert.equal(digest, "ab".repeat(32));
});

test("client diagnostics accepts only normalized Browser Playback routes", () => {
  assert.deepEqual(
    sanitizeClientDiagnosticPayload({
      normalized_route: "/api/browser-playback/epochs/:id/segments/:segment",
    }),
    { normalized_route: "/api/browser-playback/epochs/:id/segments/:segment" },
  );
  for (const route of [
    "/srv/media/private.mkv",
    "/api/browser-playback/../../private",
    "/api/browser-playback/session?token=secret",
    "https://example.test/api/browser-playback/session",
  ]) {
    assert.throws(
      () => sanitizeClientDiagnosticPayload({ normalized_route: route }),
      /Diagnostic value/,
    );
  }
});

test("nanosecond fields use decimal strings and reject unsafe Numbers", () => {
  assert.equal(decimalNanoseconds(123n), "123");
  assert.equal(decimalNanoseconds("9007199254740993123"), "9007199254740993123");
  assert.throws(() => decimalNanoseconds(Number.MAX_SAFE_INTEGER + 1), TypeError);
});

test("event schema drops arbitrary payload fields and never stores raw user agent", () => {
  const event = createPlaybackDiagnosticEvent({
    eventName: "media_playing",
    playbackSessionId: "session-00000001",
    eventSequence: 7,
    clock: { aligned_wall_time_ns: "9007199254740993123" },
    context: {
      platform: "ios",
      browser_family: "safari",
      browser_version: "18.0",
      os_family: "ios",
      os_version: "18.0",
    },
    payload: {
      buffered_ahead_ms: 5_000,
      user_agent: "raw-user-agent",
      response_headers: { authorization: "secret" },
    },
  });

  assert.equal(event.aligned_wall_time_ns, "9007199254740993123");
  assert.equal(event.source_sequence, 7);
  assert.deepEqual(event.payload, { buffered_ahead_ms: 5_000 });
  assert.equal(JSON.stringify(event).includes("raw-user-agent"), false);
});

test("platform classification uses the supplied navigator and returns parsed fields only", () => {
  const classified = classifyBrowserPlatform({
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) Version/18.1 Mobile Safari/604.1",
  });

  assert.equal(classified.platform, "ios");
  assert.equal(classified.browser_family, "safari");
  assert.equal(classified.browser_version, "18.1");
  assert.equal(classified.os_family, "ios");
  assert.equal(classified.os_version, "18.1");
  assert.equal("user_agent" in classified, false);
});
