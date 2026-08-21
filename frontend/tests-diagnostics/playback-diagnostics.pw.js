import { expect, test } from "@playwright/test";


async function openFixture(page) {
  await page.goto("/tests-diagnostics/fixture.html");
  await expect(page.locator("#diagnostic-video")).toHaveCount(1);
}


test("IndexedDB allocates sequence and event atomically, survives reload, and deletes only on ACK", async ({ page }) => {
  await openFixture(page);

  const beforeReload = await page.evaluate(async () => {
    const [{ IndexedDbDiagnosticSpool }, constants] = await Promise.all([
      import("/src/lib/playbackDiagnostics/indexedDbSpool.js"),
      import("/src/lib/playbackDiagnostics/constants.js"),
    ]);
    const deleteDatabase = () => new Promise((resolve, reject) => {
      const request = indexedDB.deleteDatabase(constants.PLAYBACK_DIAGNOSTICS_DB_NAME);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("Diagnostic database deletion was blocked"));
    });
    await deleteDatabase();

    const sessionId = "browser-contract-session";
    const spool = new IndexedDbDiagnosticSpool({ maxBytes: 1_000_000 });
    const makeEvent = (sequence, blob, priority = "normal") => ({
      session_id: sessionId,
      source_sequence: sequence,
      event_type: "browser_contract",
      priority,
      blob,
    });
    const first = await spool.createAndEnqueue(
      sessionId,
      (sequence) => makeEvent(sequence, "a".repeat(820_000)),
    );
    const rejected = await spool.createAndEnqueue(
      sessionId,
      (sequence) => makeEvent(sequence, "b".repeat(90_000)),
    );
    const second = await spool.createAndEnqueue(
      sessionId,
      (sequence) => makeEvent(sequence, "small"),
    );
    const clientId = await spool.getOrCreateClientInstanceId(sessionId);
    await spool.updateRecoveryState(sessionId, {
      source_id: "source-browser-contract",
      last_durable_ack: 0,
      close_state: "open",
    });
    const stats = await spool.stats(sessionId);
    spool.close();
    return {
      firstStored: first.stored,
      firstSequence: first.sequence,
      rejectedStored: rejected.stored,
      rejectedReason: rejected.reason,
      secondStored: second.stored,
      secondSequence: second.sequence,
      clientId,
      stats,
    };
  });

  expect(beforeReload.firstStored).toBe(true);
  expect(beforeReload.firstSequence).toBe(1);
  expect(beforeReload.rejectedStored).toBe(false);
  expect(beforeReload.rejectedReason).toBe("client_spool_normal_capacity_reached");
  expect(beforeReload.secondStored).toBe(true);
  expect(beforeReload.secondSequence).toBe(2);
  expect(beforeReload.stats.queueDepth).toBe(2);

  await page.reload();

  const afterReload = await page.evaluate(async () => {
    const [{ IndexedDbDiagnosticSpool }, constants] = await Promise.all([
      import("/src/lib/playbackDiagnostics/indexedDbSpool.js"),
      import("/src/lib/playbackDiagnostics/constants.js"),
    ]);
    const sessionId = "browser-contract-session";
    const spool = new IndexedDbDiagnosticSpool({ maxBytes: 1_000_000 });
    const recovery = await spool.getRecoveryState(sessionId);
    const clientId = await spool.getOrCreateClientInstanceId(sessionId);
    const batch = await spool.readBatch(sessionId, { maxEvents: 10, maxBytes: 2_000_000 });
    const beforeAck = await spool.stats(sessionId);
    const ack = await spool.acknowledge(sessionId, 1);
    const afterPartialAck = await spool.stats(sessionId);
    const remaining = await spool.readBatch(sessionId, { maxEvents: 10, maxBytes: 2_000_000 });
    const finalAck = await spool.acknowledge(sessionId, 2);
    await spool.markCloseState(sessionId, "sealed");
    const cleaned = await spool.cleanupSealedSession(sessionId);
    spool.close();
    await new Promise((resolve, reject) => {
      const request = indexedDB.deleteDatabase(constants.PLAYBACK_DIAGNOSTICS_DB_NAME);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("Diagnostic database deletion was blocked"));
    });
    return {
      recovery,
      clientId,
      sequences: batch.entries.map((entry) => entry.source_sequence),
      beforeAck,
      ack,
      afterPartialAck,
      remainingSequences: remaining.entries.map((entry) => entry.source_sequence),
      finalAck,
      cleaned,
    };
  });

  expect(afterReload.recovery).toMatchObject({
    source_id: "source-browser-contract",
    last_durable_ack: 0,
    close_state: "open",
  });
  expect(afterReload.clientId).toBe(beforeReload.clientId);
  expect(afterReload.sequences).toEqual([1, 2]);
  expect(afterReload.beforeAck.queueDepth).toBe(2);
  expect(afterReload.ack.deletedEvents).toBe(1);
  expect(afterReload.afterPartialAck.queueDepth).toBe(1);
  expect(afterReload.remainingSequences).toEqual([2]);
  expect(afterReload.finalAck.deletedEvents).toBe(1);
  expect(afterReload.cleaned).toBe(true);
});


test("real module Worker persists, uploads, closes, and removes sealed recovery state", async ({ page }) => {
  const requests = [];
  await page.route("**/api/playback-diagnostics/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const payload = request.postDataJSON();
    requests.push({ path, payload });
    if (path.endsWith("/bootstrap")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          diagnostics_session_id: payload.playback_session_id,
          source_id: "source-worker-contract",
          state: "active",
          ack_watermark: 0,
          batch_max_events: 256,
          batch_max_bytes: 524288,
          client_spool_max_bytes: 64000000,
        }),
      });
      return;
    }
    if (path.endsWith("/clock")) {
      const wall = String(BigInt(Date.now()) * 1_000_000n);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          sample_id: payload.sample_id,
          client_send_wall_time_ms: payload.client_send_wall_time_ms,
          client_send_monotonic_time_us: payload.client_send_monotonic_time_us,
          server_receive_wall_time_ns: wall,
          server_receive_monotonic_time_ns: "1000000",
          server_send_wall_time_ns: wall,
          server_send_monotonic_time_ns: "1001000",
          monotonic_raw_time_ns: null,
        }),
      });
      return;
    }
    if (path.endsWith("/batch")) {
      const watermark = Math.max(...payload.events.map((event) => event.source_sequence));
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          accepted: payload.events.length,
          duplicate: 0,
          out_of_order: 0,
          ack_watermark: watermark,
        }),
      });
      return;
    }
    if (path.endsWith("/close")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          ack_watermark: payload.final_source_sequence,
          finalized: true,
          state: "sealed",
        }),
      });
      return;
    }
    await route.abort();
  });
  await openFixture(page);

  const result = await page.evaluate(async () => {
    const [{ PlaybackDiagnosticsWorkerClient }, { IndexedDbDiagnosticSpool }, constants] = await Promise.all([
      import("/src/lib/playbackDiagnostics/workerClient.js"),
      import("/src/lib/playbackDiagnostics/indexedDbSpool.js"),
      import("/src/lib/playbackDiagnostics/constants.js"),
    ]);
    await new Promise((resolve, reject) => {
      const deletion = indexedDB.deleteDatabase(constants.PLAYBACK_DIAGNOSTICS_DB_NAME);
      deletion.onsuccess = () => resolve();
      deletion.onerror = () => reject(deletion.error);
    });
    const health = [];
    const client = new PlaybackDiagnosticsWorkerClient({
      options: {
        playbackSessionId: "worker-contract-session",
        playbackAttemptId: "attempt-worker-contract",
        context: { playback_mode: "lite", stream_mode: "route2" },
        bootstrapContext: {
          platform: "linux",
          device_class: "desktop",
          hls_engine: "hls.js",
          client_timer_resolution_us: 100,
        },
      },
      onHealth: (entry) => health.push(entry),
    });
    const ready = await client.start();
    client.capture("playing", { payload: { state: "active" } });
    client.capture("waiting", { payload: { state: "waiting" } });
    client.capture("completed", {
      priority: "critical",
      terminalReason: "completed",
      payload: { state: "completed" },
    });
    const deadline = performance.now() + 10_000;
    while (!client.closed && performance.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    const spool = new IndexedDbDiagnosticSpool({ maxBytes: 64_000_000 });
    const recoveryRows = await spool.listRecoverySessions();
    spool.close();
    return { ready, closed: client.closed, recoveryRows, health };
  });

  expect(result.ready).toEqual({ worker: true, persistent: true });
  expect(result.closed).toBe(true);
  expect(result.recoveryRows).toEqual([]);
  expect(requests.some(({ path }) => path.endsWith("/bootstrap"))).toBe(true);
  expect(requests.some(({ path }) => path.endsWith("/batch"))).toBe(true);
  expect(requests.some(({ path }) => path.endsWith("/close"))).toBe(true);
});


test("browser lifecycle and capability reporting preserve semantics and clean up listeners", async ({ page }) => {
  await openFixture(page);

  const result = await page.evaluate(async () => {
    const [{ collectPlaybackDiagnosticCapabilities }, { PlaybackLifecycleDiagnosticObserver }] = await Promise.all([
      import("/src/lib/playbackDiagnostics/capabilities.js"),
      import("/src/lib/playbackDiagnostics/lifecycleObserver.js"),
    ]);
    const video = document.querySelector("#diagnostic-video");
    const capabilities = collectPlaybackDiagnosticCapabilities({ video });
    const absentCapabilities = collectPlaybackDiagnosticCapabilities({ video: {} });
    const records = [];
    const fakeDocument = new EventTarget();
    fakeDocument.visibilityState = "visible";
    fakeDocument.hasFocus = () => true;
    fakeDocument.fullscreenElement = null;
    const observer = new PlaybackLifecycleDiagnosticObserver({
      record: (name, detail) => records.push({ name, detail }),
      windowRef: window,
      documentRef: fakeDocument,
      navigatorRef: navigator,
    });
    observer.start();
    window.dispatchEvent(new Event("offline"));
    window.dispatchEvent(new Event("online"));
    fakeDocument.visibilityState = "hidden";
    fakeDocument.dispatchEvent(new Event("visibilitychange"));
    fakeDocument.visibilityState = "visible";
    fakeDocument.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new PageTransitionEvent("pagehide"));
    window.dispatchEvent(new PageTransitionEvent("pageshow"));
    const beforeStop = records.length;
    observer.stop();
    window.dispatchEvent(new Event("offline"));
    fakeDocument.dispatchEvent(new Event("visibilitychange"));
    return {
      capabilities,
      absentCapabilities,
      names: records.map((record) => record.name),
      hiddenEnd: records.find((record) => record.name === "page_hidden_ended")?.detail?.payload,
      beforeStop,
      afterStop: records.length,
    };
  });

  expect(["api_detected", "api_absent"]).toContain(result.capabilities.request_video_frame_callback);
  expect(["api_detected", "api_absent"]).toContain(result.capabilities.video_playback_quality);
  expect(result.absentCapabilities.request_video_frame_callback).toBe("api_absent");
  expect(result.absentCapabilities.video_playback_quality).toBe("api_absent");
  expect(result.names).toEqual(expect.arrayContaining([
    "page_lifecycle_started",
    "network_offline",
    "network_online",
    "page_hidden_started",
    "page_hidden_ended",
    "pagehide",
    "pageshow",
  ]));
  expect(result.hiddenEnd.hidden_duration_ms).toBeGreaterThanOrEqual(0);
  expect(result.beforeStop).toBe(result.afterStop);
});


test("HLS and media observers keep attempt identity and event semantics in a browser", async ({ page }) => {
  await openFixture(page);

  const result = await page.evaluate(async () => {
    const [{ HlsJsDiagnosticObserver }, { MediaElementDiagnosticObserver }] = await Promise.all([
      import("/src/lib/playbackDiagnostics/hlsObserver.js"),
      import("/src/lib/playbackDiagnostics/mediaObserver.js"),
    ]);
    class FakeHls {
      constructor() { this.listeners = new Map(); }
      on(name, handler) {
        if (!this.listeners.has(name)) this.listeners.set(name, new Set());
        this.listeners.get(name).add(handler);
      }
      off(name, handler) { this.listeners.get(name)?.delete(handler); }
      emit(name, data) { this.listeners.get(name)?.forEach((handler) => handler(name, data)); }
      listenerCount() {
        return [...this.listeners.values()].reduce((sum, handlers) => sum + handlers.size, 0);
      }
    }
    const hls = new FakeHls();
    const hlsRecords = [];
    const events = {
      MANIFEST_LOADING: "manifestLoading",
      FRAG_LOADING: "fragLoading",
      FRAG_LOADED: "fragLoaded",
      ERROR: "error",
    };
    const hlsObserver = new HlsJsDiagnosticObserver({
      hls,
      events,
      record: (name, detail) => hlsRecords.push({ name, detail }),
    });
    hlsObserver.start();
    hls.emit(events.MANIFEST_LOADING, { url: "/api/browser-playback/manifest.m3u8?token=secret" });
    const fragment = {
      url: "/api/browser-playback/segment/1.ts?token=secret",
      level: 2,
      sn: 7,
      start: 10,
      duration: 4,
      stats: { loaded: 1_024, loading: { start: 100, first: 120, end: 200 } },
    };
    hls.emit(events.FRAG_LOADING, { frag: fragment });
    hls.emit(events.FRAG_LOADED, { frag: fragment });
    const retryFragment = { ...fragment };
    hls.emit(events.FRAG_LOADING, { frag: retryFragment });
    const listenerCountBeforeStop = hls.listenerCount();
    hlsObserver.stop();
    const listenerCountAfterStop = hls.listenerCount();

    const video = document.querySelector("#diagnostic-video");
    const mediaRecords = [];
    const mediaObserver = new MediaElementDiagnosticObserver({
      video,
      record: (name, detail) => mediaRecords.push({ name, detail }),
      actionOrigin: () => "user",
    });
    mediaObserver.start();
    video.dispatchEvent(new Event("play"));
    video.dispatchEvent(new Event("playing"));
    video.dispatchEvent(new Event("pause"));
    mediaObserver.stop();

    const loads = hlsRecords.filter((record) => record.name === "hls_fragment_loading");
    const loaded = hlsRecords.find((record) => record.name === "hls_fragment_loaded");
    return {
      firstAttemptId: loads[0]?.detail?.payload?.request_attempt_id,
      loadedAttemptId: loaded?.detail?.payload?.request_attempt_id,
      firstAttempt: loads[0]?.detail?.payload?.request_attempt,
      retryAttempt: loads[1]?.detail?.payload?.request_attempt,
      normalizedRoute: loaded?.detail?.payload?.normalized_route,
      listenerCountBeforeStop,
      listenerCountAfterStop,
      mediaNames: mediaRecords.map((record) => record.name),
      playOrigin: mediaRecords.find((record) => record.name === "play_requested")?.detail?.payload?.action_origin,
    };
  });

  expect(result.firstAttemptId).toBeTruthy();
  expect(result.loadedAttemptId).toBe(result.firstAttemptId);
  expect(result.firstAttempt).toBe(1);
  expect(result.retryAttempt).toBe(2);
  expect(result.normalizedRoute).not.toContain("secret");
  expect(result.listenerCountBeforeStop).toBeGreaterThan(0);
  expect(result.listenerCountAfterStop).toBe(0);
  expect(result.mediaNames).toEqual(expect.arrayContaining([
    "media_play",
    "play_requested",
    "media_playing",
    "play_started",
    "media_pause",
    "pause_started",
  ]));
  expect(result.playOrigin).toBe("user");
});


test("hidden event-loop delay is classified as background throttle instead of foreground lag", async ({ page }) => {
  await openFixture(page);

  const result = await page.evaluate(async () => {
    const { PlaybackPerformanceDiagnosticObserver } = await import(
      "/src/lib/playbackDiagnostics/performanceObserver.js"
    );
    const callbacks = [];
    const cleared = [];
    let monotonicMs = 0;
    const records = [];
    const fakeWindow = {
      performance: { now: () => monotonicMs },
      setInterval: (callback) => {
        callbacks.push(callback);
        return callbacks.length;
      },
      clearInterval: (id) => cleared.push(id),
      PerformanceObserver: undefined,
    };
    const fakeDocument = { visibilityState: "hidden" };
    const observer = new PlaybackPerformanceDiagnosticObserver({
      record: (name, detail) => records.push({ name, detail }),
      windowRef: fakeWindow,
      documentRef: fakeDocument,
      navigatorRef: {},
    });
    observer.start();
    await Promise.resolve();
    monotonicMs = 1_000;
    callbacks[0]();
    fakeDocument.visibilityState = "visible";
    monotonicMs = 1_250;
    callbacks[0]();
    observer.stop();
    return {
      names: records.map((record) => record.name),
      throttle: records.find((record) => record.name === "background_timer_throttle")?.detail?.payload,
      foreground: records.find((record) => record.name === "event_loop_aggregate")?.detail?.payload,
      cleared,
    };
  });

  expect(result.names.filter((name) => name === "background_timer_throttle")).toHaveLength(1);
  expect(result.names.filter((name) => name === "event_loop_aggregate")).toHaveLength(1);
  expect(result.throttle.page_state).toBe("hidden");
  expect(result.foreground.page_state).toBe("visible");
  expect(result.cleared).toEqual(expect.arrayContaining([1, 2]));
});


test("transport survives a missing backend session, resumes with a new source, beacons, and closes in order", async ({ page }) => {
  await openFixture(page);

  const result = await page.evaluate(async () => {
    const [{ IndexedDbDiagnosticSpool }, { PlaybackDiagnosticsTransport }, constants] = await Promise.all([
      import("/src/lib/playbackDiagnostics/indexedDbSpool.js"),
      import("/src/lib/playbackDiagnostics/transport.js"),
      import("/src/lib/playbackDiagnostics/constants.js"),
    ]);
    const deleteDatabase = () => new Promise((resolve, reject) => {
      const request = indexedDB.deleteDatabase(constants.PLAYBACK_DIAGNOSTICS_DB_NAME);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("Diagnostic database deletion was blocked"));
    });
    await deleteDatabase();

    const sessionId = "transport-browser-contract";
    const spool = new IndexedDbDiagnosticSpool();
    const createQueuedEvent = (sequence) => ({
      event_id: `event-browser-${sequence}`,
      event_name: "media_aggregate",
      event_source: "client",
      source_sequence: sequence,
      event_sequence: sequence,
      playback_session_id: sessionId,
      priority: "normal",
      payload: { sequence },
    });
    const first = await spool.createAndEnqueue(sessionId, createQueuedEvent);
    const calls = [];
    const beaconCalls = [];
    let bootstrapCount = 0;
    let batchCount = 0;
    const response = (status, payload) => new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    });
    const fetchRef = async (url, options) => {
      const path = new URL(url, location.href).pathname;
      const body = JSON.parse(options.body);
      calls.push({ path, sequences: body.events?.map((event) => event.source_sequence) || [] });
      if (path.endsWith("/bootstrap")) {
        bootstrapCount += 1;
        return response(200, {
          enabled: true,
          diagnostics_session_id: sessionId,
          source_id: bootstrapCount === 1 ? "source-before-restart" : "source-after-restart",
          schema_version: "playback-diagnostics-event-v2",
          client_spool_max_bytes: 64_000_000,
          batch_max_events: 256,
          batch_max_bytes: 524_288,
          clock_algorithm: "monotonic-rtt-median-offset-v2",
          server_wall_time_ns: String(Date.now() * 1_000_000),
          server_monotonic_time_ns: "1",
          ack_watermark: 0,
        });
      }
      if (path.endsWith("/clock")) {
        const now = String(Date.now() * 1_000_000);
        return response(200, {
          sample_id: body.sample_id,
          client_send_wall_time_ms: body.client_send_wall_time_ms,
          client_send_monotonic_time_us: body.client_send_monotonic_time_us,
          server_receive_wall_time_ns: now,
          server_receive_monotonic_time_ns: "1",
          server_send_wall_time_ns: now,
          server_send_monotonic_time_ns: "2",
          monotonic_raw_time_ns: null,
        });
      }
      if (path.endsWith("/batch")) {
        batchCount += 1;
        if (batchCount === 1) return response(404, { detail: "session missing" });
        return response(200, {
          accepted: body.events.length,
          duplicate: 0,
          rejected: 0,
          out_of_order: 0,
          ack_watermark: Math.max(...body.events.map((event) => event.source_sequence)),
          capacity_state: "normal",
        });
      }
      if (path.endsWith("/close")) {
        return response(200, {
          accepted: true,
          ack_watermark: body.final_source_sequence,
          finalized: true,
          state: "sealed",
        });
      }
      return response(500, {});
    };
    const target = new EventTarget();
    const fakeWindow = {
      performance,
      addEventListener: target.addEventListener.bind(target),
      removeEventListener: target.removeEventListener.bind(target),
      dispatchEvent: target.dispatchEvent.bind(target),
      setInterval: () => 1,
      clearInterval: () => {},
      setTimeout: () => 1,
      clearTimeout: () => {},
    };
    const fakeNavigator = {
      sendBeacon: (url, body) => {
        beaconCalls.push({ url, size: body.size });
        return true;
      },
    };
    const transport = new PlaybackDiagnosticsTransport({
      playbackSessionId: sessionId,
      spool,
      bootstrapContext: { platform: "browser-test", capabilities: {} },
      fetchRef,
      windowRef: fakeWindow,
      documentRef: document,
      navigatorRef: fakeNavigator,
      randomRef: () => 0,
    });
    await transport.start();
    await transport.flush({ force: true });
    const afterMissingSession = await spool.stats(sessionId);
    await transport.flush({ force: true });
    const afterRecovery = await spool.stats(sessionId);

    const second = await spool.createAndEnqueue(sessionId, createQueuedEvent);
    transport.notePersistedEvent(second.event);
    fakeWindow.dispatchEvent(new Event("pagehide"));
    const third = await spool.createAndEnqueue(sessionId, createQueuedEvent);
    transport.notePersistedEvent(third.event);
    const closed = await transport.closeSession("completed", third.sequence);
    const queueAfterClose = await spool.stats(sessionId);
    transport.stop();
    spool.close();
    await deleteDatabase();
    return {
      firstSequence: first.sequence,
      afterMissingSession,
      afterRecovery,
      bootstrapCount,
      batchCount,
      beaconCalls,
      closed,
      queueAfterClose,
      operationPaths: calls
        .filter((call) => call.path.endsWith("/batch") || call.path.endsWith("/close"))
        .map((call) => ({ path: call.path.split("/").at(-1), sequences: call.sequences })),
    };
  });

  expect(result.firstSequence).toBe(1);
  expect(result.afterMissingSession.queueDepth).toBe(1);
  expect(result.afterRecovery.queueDepth).toBe(0);
  expect(result.bootstrapCount).toBe(2);
  expect(result.batchCount).toBe(3);
  expect(result.beaconCalls).toHaveLength(1);
  expect(result.beaconCalls[0].size).toBeGreaterThan(0);
  expect(result.closed).toBe(true);
  expect(result.queueAfterClose.queueDepth).toBe(0);
  expect(result.operationPaths.at(-1)).toEqual({ path: "close", sequences: [] });
  expect(result.operationPaths.at(-2)).toEqual({ path: "batch", sequences: [2, 3] });
});


test("pre-session play intent and early HLS observations survive recorder bootstrap failure", async ({ page }) => {
  await openFixture(page);

  const result = await page.evaluate(async () => {
    const [{ PlaybackDiagnosticRecorder }, { IndexedDbDiagnosticSpool }, constants] = await Promise.all([
      import("/src/lib/playbackDiagnostics/recorder.js"),
      import("/src/lib/playbackDiagnostics/indexedDbSpool.js"),
      import("/src/lib/playbackDiagnostics/constants.js"),
    ]);
    const deleteDatabase = () => new Promise((resolve, reject) => {
      const request = indexedDB.deleteDatabase(constants.PLAYBACK_DIAGNOSTICS_DB_NAME);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("Diagnostic database deletion was blocked"));
    });
    await deleteDatabase();
    const sessionId = "provisional-browser-contract";
    const recorder = new PlaybackDiagnosticRecorder({
      playbackSessionId: sessionId,
      video: document.querySelector("#diagnostic-video"),
      context: { hls_engine: "hls.js", device_class: "desktop" },
      provisionalEvents: [
        {
          eventName: "play_intent",
          options: { priority: "high", payload: { action_origin: "user" } },
        },
        {
          eventName: "hls_manifest_loading",
          options: { payload: { hls_event: "hls_manifest_loading" } },
        },
      ],
      fetchRef: async () => new Response(JSON.stringify({ detail: "temporarily unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }),
    });
    await recorder.start();
    const inspector = new IndexedDbDiagnosticSpool();
    const expected = new Set([
      "client_recorder_started",
      "play_intent",
      "hls_manifest_loading",
    ]);
    let names = [];
    const deadline = performance.now() + 5_000;
    while (performance.now() < deadline) {
      const batch = await inspector.readBatch(sessionId, {
        maxEvents: 256,
        maxBytes: 8_000_000,
      });
      names = batch.entries.map((entry) => entry.event.event_name);
      if ([...expected].every((name) => names.includes(name))) break;
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    inspector.close();
    recorder.stop();
    recorder.dataClient?.dispose();
    await deleteDatabase();
    return { names };
  });

  expect(result.names).toEqual(expect.arrayContaining([
    "client_recorder_started",
    "play_intent",
    "hls_manifest_loading",
  ]));
});
