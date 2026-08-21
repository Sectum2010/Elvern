#!/usr/bin/env node

import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";
import { createServer as createViteServer } from "vite";


const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = path.resolve(FRONTEND_ROOT, "..");
const OUTPUT_PATH = path.resolve(
  process.argv[2] || path.join(PROJECT_ROOT, "tmp/playback-diagnostics-benchmark/client.json"),
);
const BENCHMARK_PATH = "/__elvern_playback_diagnostics_benchmark__";
const UPLOAD_PATH = "/__elvern_playback_diagnostics_upload__";


function percentile(values, fraction) {
  if (!values.length) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const position = (ordered.length - 1) * fraction;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return ordered[lower];
  return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
}


function benchmarkHtml() {
  return `<!doctype html>
<html lang="en">
  <head><meta charset="UTF-8"><title>Elvern diagnostics benchmark</title></head>
  <body>
    <script type="module">
      import {
        IndexedDbDiagnosticSpool,
        MemoryDiagnosticSpool,
      } from "/src/lib/playbackDiagnostics/indexedDbSpool.js";
      import { MediaElementDiagnosticObserver } from "/src/lib/playbackDiagnostics/mediaObserver.js";
      import { DiagnosticRingBuffer } from "/src/lib/playbackDiagnostics/ringBuffer.js";
      import { createPlaybackDiagnosticEvent } from "/src/lib/playbackDiagnostics/schema.js";
      import { PlaybackDiagnosticsWorkerClient } from "/src/lib/playbackDiagnostics/workerClient.js";

      const EVENT_COUNT = 1_800;
      const SAMPLE_RING_ENTRIES = 240;
      const FRAME_RING_ENTRIES = 7_200;
      const FRAME_RATE = 120;
      const SUSTAINED_PUSH_COUNT = 50_000;
      const SERIALIZATION_CHUNK_ENTRIES = 128;
      const WORKER_CAPTURE_EVENTS = 4_096;
      // Keep each synthetic task below the worker-pressure model. A 64-event
      // instantaneous burst represents roughly 16 seconds of 250 ms samples
      // arriving at once and correctly trips the production circuit breaker.
      const WORKER_CAPTURE_CHUNK_EVENTS = 16;
      const SESSION_ID = "session-synthetic-client-benchmark";

      function percentile(values, fraction) {
        if (!values.length) return null;
        const ordered = [...values].sort((left, right) => left - right);
        const position = (ordered.length - 1) * fraction;
        const lower = Math.floor(position);
        const upper = Math.ceil(position);
        if (lower === upper) return ordered[lower];
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower);
      }

      function summarized(values) {
        const total = values.reduce((sum, value) => sum + value, 0);
        return {
          count: values.length,
          total_ms: total,
          mean_ms: values.length ? total / values.length : null,
          p50_ms: percentile(values, 0.5),
          p95_ms: percentile(values, 0.95),
          p99_ms: percentile(values, 0.99),
          max_ms: values.length ? Math.max(...values) : null,
        };
      }

      async function waitForWorkerAcks(client, timeoutMs = 15_000) {
        const deadline = performance.now() + timeoutMs;
        while (client.pending.size > 0) {
          if (performance.now() >= deadline) {
            throw new Error("Timed out waiting for diagnostics Worker acknowledgements");
          }
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }

      function makeEvent(sequence) {
        return createPlaybackDiagnosticEvent({
          eventName: "media_aggregate",
          playbackSessionId: SESSION_ID,
          eventSequence: sequence,
          sourceSequence: sequence,
          context: {
            playback_attempt_id: "attempt-synthetic-client",
            attachment_id: "attachment-synthetic-client",
            playhead_ms: sequence * 1_000,
            duration_ms: 7_200_000,
            platform: "synthetic",
            device_class: "desktop",
            browser_family: "chromium",
            hls_engine: "hls.js",
            playback_mode: "lite",
            stream_mode: "route2",
            source_kind: "local",
            sample_window_ms: 1_000,
          },
          payload: {
            buffered_ahead_ms: 45_000 - (sequence % 10) * 250,
            buffered_behind_ms: 10_000,
            total_buffered_ms: 55_000,
            buffer_hole_count: 0,
            buffer_slope_ms_per_s: 1_000,
            playhead_slope_ms_per_s: 1_000,
            ready_state: 4,
            network_state: 1,
            queue_depth: 0,
            state: "playing",
          },
        });
      }

      async function deleteBenchmarkDatabase() {
        await new Promise((resolve) => {
          const request = indexedDB.deleteDatabase("elvern-playback-diagnostics-v1");
          request.onsuccess = () => resolve();
          request.onerror = () => resolve();
          request.onblocked = () => resolve();
        });
      }

      window.runElvernDiagnosticsBenchmark = async () => {
        globalThis.gc?.();
        const heapBefore = performance.memory?.usedJSHeapSize ?? null;
        const events = [];
        const creationLatencies = [];
        for (let sequence = 1; sequence <= EVENT_COUNT; sequence += 1) {
          const started = performance.now();
          events.push(makeEvent(sequence));
          creationLatencies.push(performance.now() - started);
        }

        const serializationLatencies = [];
        let serializedBytes = 0;
        const encoder = new TextEncoder();
        for (const event of events) {
          const started = performance.now();
          const encoded = encoder.encode(JSON.stringify(event));
          serializationLatencies.push(performance.now() - started);
          serializedBytes += encoded.byteLength;
        }

        const memorySpool = new MemoryDiagnosticSpool();
        const memoryEnqueueLatencies = [];
        for (const event of events) {
          const started = performance.now();
          const stored = await memorySpool.createAndEnqueue(
            SESSION_ID,
            (sequence) => ({ ...event, event_sequence: sequence, source_sequence: sequence }),
          );
          if (!stored.stored) throw new Error("Memory spool rejected the benchmark event");
          memoryEnqueueLatencies.push(performance.now() - started);
        }
        const memoryStats = await memorySpool.stats(SESSION_ID);

        await deleteBenchmarkDatabase();
        const indexedDbSpool = new IndexedDbDiagnosticSpool();
        const indexedDbOpenStarted = performance.now();
        await indexedDbSpool.open();
        const indexedDbOpenMs = performance.now() - indexedDbOpenStarted;
        const clientInstanceId = await indexedDbSpool.getOrCreateClientInstanceId(SESSION_ID);
        await indexedDbSpool.updateRecoveryState(SESSION_ID, {
          client_instance_id: clientInstanceId,
          source_id: "source-synthetic-client-benchmark",
          close_state: "open",
          last_durable_ack: 0,
        });
        const indexedDbEnqueueLatencies = [];
        for (const event of events) {
          const started = performance.now();
          const stored = await indexedDbSpool.createAndEnqueue(
            SESSION_ID,
            (sequence) => ({ ...event, event_sequence: sequence, source_sequence: sequence }),
          );
          if (!stored.stored) throw new Error("IndexedDB spool rejected the benchmark event");
          indexedDbEnqueueLatencies.push(performance.now() - started);
        }
        const indexedDbStats = await indexedDbSpool.stats(SESSION_ID);
        indexedDbSpool.close();

        const reloadOpenStarted = performance.now();
        const resumedSpool = new IndexedDbDiagnosticSpool();
        await resumedSpool.open();
        const reloadOpenMs = performance.now() - reloadOpenStarted;
        const recoveryAfterReload = await resumedSpool.getRecoveryState(SESSION_ID);
        const clientInstanceAfterReload = await resumedSpool.getOrCreateClientInstanceId(SESSION_ID);
        const statsAfterReload = await resumedSpool.stats(SESSION_ID);

        const readLatencies = [];
        let batch = null;
        for (let index = 0; index < 20; index += 1) {
          const started = performance.now();
          batch = await resumedSpool.readBatch(SESSION_ID, {
            maxEvents: 256,
            maxBytes: 524_288,
          });
          readLatencies.push(performance.now() - started);
        }

        const uploadLatencies = [];
        const uploadPayload = {
          diagnostics_session_id: SESSION_ID,
          source_id: "client-synthetic-client-benchmark",
          events: batch.entries.map((entry) => entry.event),
        };
        for (let index = 0; index < 20; index += 1) {
          const started = performance.now();
          const response = await fetch("${UPLOAD_PATH}", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(uploadPayload),
          });
          if (!response.ok) throw new Error("Synthetic upload endpoint failed");
          await response.json();
          uploadLatencies.push(performance.now() - started);
        }

        const ackStarted = performance.now();
        const ack = await resumedSpool.acknowledge(SESSION_ID, batch.entries.at(-1).source_sequence);
        const ackMs = performance.now() - ackStarted;
        const statsAfterAck = await resumedSpool.stats(SESSION_ID);

        const sampleRing = new DiagnosticRingBuffer(SAMPLE_RING_ENTRIES);
        for (let index = 0; index < SAMPLE_RING_ENTRIES; index += 1) {
          sampleRing.push({
            sample_monotonic_ms: index * 250,
            playhead_ms: index * 250,
            buffered_ahead_ms: 45_000,
            ready_state: 4,
            network_state: 1,
          });
        }
        const frameRing = new DiagnosticRingBuffer(FRAME_RING_ENTRIES);
        for (let index = 0; index < FRAME_RING_ENTRIES; index += 1) {
          frameRing.push({
            callback_monotonic_ms: index * (1_000 / FRAME_RATE),
            media_time_ms: index * (1_000 / FRAME_RATE),
            expected_display_time_ms: index * (1_000 / FRAME_RATE),
            presented_frames: index,
            processing_duration_ms: 1.2,
          });
        }

        const initialPushLatencies = [];
        const fullPushLatencies = [];
        const pushRing = new DiagnosticRingBuffer(FRAME_RING_ENTRIES);
        for (let index = 0; index < FRAME_RING_ENTRIES; index += 1) {
          const started = performance.now();
          pushRing.push({ callback_monotonic_ms: index, value: index });
          initialPushLatencies.push(performance.now() - started);
        }
        for (let index = 0; index < SUSTAINED_PUSH_COUNT; index += 1) {
          const started = performance.now();
          pushRing.push({ callback_monotonic_ms: FRAME_RING_ENTRIES + index, value: index });
          fullPushLatencies.push(performance.now() - started);
        }

        const incidentStarted = performance.now();
        const incidentCursor = frameRing.createSnapshotCursor();
        const incidentHandlerSynchronousMs = performance.now() - incidentStarted;
        const serializationTaskLatencies = [];
        const serializationChunkBytes = [];
        while (!incidentCursor.done) {
          const started = performance.now();
          const chunk = incidentCursor.read(SERIALIZATION_CHUNK_ENTRIES);
          const bytes = encoder.encode(JSON.stringify(chunk)).byteLength;
          serializationTaskLatencies.push(performance.now() - started);
          serializationChunkBytes.push(bytes);
        }

        const pushSource = DiagnosticRingBuffer.prototype.push.toString();
        const incidentSource = MediaElementDiagnosticObserver.prototype.persistIncidentPreWindow.toString();
        const structuralAssertions = {
          no_front_splice: !pushSource.includes(".splice(") && !pushSource.includes(".shift("),
          incident_uses_incremental_scheduler: !incidentSource.includes(".snapshot("),
          frame_ring_bounded: frameRing.length === FRAME_RING_ENTRIES,
          max_serialization_chunk_entries: SERIALIZATION_CHUNK_ENTRIES,
          max_serialization_chunk_bytes: Math.max(...serializationChunkBytes),
        };
        if (!structuralAssertions.no_front_splice) {
          throw new Error("Ring push regressed to front splice/shift");
        }
        if (!structuralAssertions.incident_uses_incremental_scheduler) {
          throw new Error("Incident handling regressed to synchronous full-ring snapshot");
        }
        if (!structuralAssertions.frame_ring_bounded) {
          throw new Error("The 60-second 120-fps frame ring is not bounded");
        }
        const sampleRingBytes = encoder.encode(JSON.stringify(sampleRing.snapshot())).byteLength;
        const frameRingBytes = encoder.encode(JSON.stringify(frameRing.snapshot())).byteLength;

        resumedSpool.close();
        await deleteBenchmarkDatabase();

        const longTasks = [];
        let longTaskObserver = null;
        if (typeof PerformanceObserver === "function"
            && PerformanceObserver.supportedEntryTypes?.includes("longtask")) {
          longTaskObserver = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              longTasks.push({ start_ms: entry.startTime, duration_ms: entry.duration });
            }
          });
          longTaskObserver.observe({ type: "longtask", buffered: false });
        }
        const workerClient = new PlaybackDiagnosticsWorkerClient({
          options: {
            playbackSessionId: "session-worker-capture-benchmark",
            playbackAttemptId: "attempt-worker-capture-benchmark",
            context: {
              platform: "synthetic",
              device_class: "desktop",
              browser_family: "chromium",
              playback_mode: "lite",
              stream_mode: "route2",
              source_kind: "local",
            },
            bootstrapContext: {
              client_timer_resolution_us: 5,
            },
          },
        });
        const workerReady = await workerClient.start();
        const workerCaptureLatencies = [];
        const workerCaptureTaskLatencies = [];
        let acceptedWorkerCaptures = 0;
        const workerCaptureWindowStart = performance.now();
        for (let chunkStart = 0; chunkStart < WORKER_CAPTURE_EVENTS;
          chunkStart += WORKER_CAPTURE_CHUNK_EVENTS) {
          const taskStarted = performance.now();
          const chunkEnd = Math.min(
            WORKER_CAPTURE_EVENTS,
            chunkStart + WORKER_CAPTURE_CHUNK_EVENTS,
          );
          for (let sequence = chunkStart; sequence < chunkEnd; sequence += 1) {
            const started = performance.now();
            const accepted = workerClient.capture("media_aggregate", {
              payload: {
                buffered_ahead_ms: 45_000 - (sequence % 10) * 250,
                ready_state: 4,
                network_state: 1,
                state: "playing",
              },
              playheadMs: sequence * 250,
              durationMs: 7_200_000,
              sampleWindowMs: 250,
            });
            workerCaptureLatencies.push(performance.now() - started);
            if (accepted) acceptedWorkerCaptures += 1;
          }
          workerCaptureTaskLatencies.push(performance.now() - taskStarted);
          await waitForWorkerAcks(workerClient);
        }
        const workerCaptureWindowEnd = performance.now();
        await new Promise((resolve) => setTimeout(resolve, 0));
        longTaskObserver?.disconnect();
        const attributableLongTasks = longTasks.filter((entry) => (
          entry.start_ms < workerCaptureWindowEnd
          && entry.start_ms + entry.duration_ms > workerCaptureWindowStart
        ));
        const workerCaptureSummary = summarized(workerCaptureLatencies);
        const workerTaskSummary = summarized(workerCaptureTaskLatencies);
        const workerCaptureAssertions = {
          all_captures_accepted: acceptedWorkerCaptures === WORKER_CAPTURE_EVENTS,
          p95_within_0_25_ms: workerCaptureSummary.p95_ms <= 0.25,
          p99_within_0_75_ms: workerCaptureSummary.p99_ms <= 0.75,
          ordinary_max_within_2_ms: workerCaptureSummary.max_ms <= 2,
          capture_task_max_below_10_ms: workerTaskSummary.max_ms < 10,
          no_observed_long_task: attributableLongTasks.length === 0,
        };
        if (Object.values(workerCaptureAssertions).some((value) => value !== true)) {
          throw new Error(
            "Diagnostics Worker capture budget failed: " + JSON.stringify(workerCaptureAssertions),
          );
        }
        workerClient.dispose();
        await new Promise((resolve) => setTimeout(resolve, 25));
        await deleteBenchmarkDatabase();
        globalThis.gc?.();
        const heapAfter = performance.memory?.usedJSHeapSize ?? null;
        return {
          benchmark_kind: "accelerated_synthetic_client",
          modeled_session_minutes: 30,
          synthetic_event_count: EVENT_COUNT,
          limitations: [
            "Chromium headless localhost benchmark; not a real Mac, iPhone, or network measurement.",
            "Batch upload latency is loopback-only and excludes production network latency.",
            "GC and heap values are Chromium diagnostics, not a cross-browser guarantee.",
          ],
          main_thread_event_creation: summarized(creationLatencies),
          serialization: {
            ...summarized(serializationLatencies),
            total_bytes: serializedBytes,
          },
          memory_spool_enqueue: summarized(memoryEnqueueLatencies),
          memory_spool_queue: memoryStats,
          indexeddb: {
            open_ms: indexedDbOpenMs,
            enqueue: summarized(indexedDbEnqueueLatencies),
            read_batch: summarized(readLatencies),
            ack_ms: ackMs,
            acked_events: ack.deletedEvents,
            queue_before_ack: indexedDbStats,
            queue_after_ack: statsAfterAck,
            reload_resume: {
              reopen_ms: reloadOpenMs,
              queue_after_reload: statsAfterReload,
              recovery_state_preserved: recoveryAfterReload?.source_id
                === "source-synthetic-client-benchmark",
              client_instance_preserved: clientInstanceAfterReload === clientInstanceId,
            },
          },
          loopback_batch_upload: {
            ...summarized(uploadLatencies),
            batch_events: batch.entries.length,
            batch_bytes: batch.totalBytes,
          },
          worker_capture_boundary: {
            ready: workerReady,
            requested_events: WORKER_CAPTURE_EVENTS,
            accepted_events: acceptedWorkerCaptures,
            chunk_events: WORKER_CAPTURE_CHUNK_EVENTS,
            synchronous_capture: workerCaptureSummary,
            synchronous_chunk_tasks: workerTaskSummary,
            attributable_long_tasks: attributableLongTasks,
            assertions: workerCaptureAssertions,
          },
          ring_buffers: {
            modeled_window_seconds: 60,
            sample_entries: SAMPLE_RING_ENTRIES,
            sample_bytes: sampleRingBytes,
            frame_rate: FRAME_RATE,
            frame_entries: FRAME_RING_ENTRIES,
            frame_bytes: frameRingBytes,
            total_bytes: sampleRingBytes + frameRingBytes,
            initial_push: summarized(initialPushLatencies),
            occupied_push: summarized(fullPushLatencies),
            incident_handler_synchronous_ms: incidentHandlerSynchronousMs,
            incremental_serialization_tasks: summarized(serializationTaskLatencies),
            longest_recorder_main_thread_task_ms: Math.max(
              incidentHandlerSynchronousMs,
              ...serializationTaskLatencies,
              ...fullPushLatencies,
            ),
            structural_assertions: structuralAssertions,
          },
          gc_pressure: {
            forced_gc_available: typeof globalThis.gc === "function",
            used_js_heap_before_bytes: heapBefore,
            used_js_heap_after_bytes: heapAfter,
            used_js_heap_delta_bytes: heapBefore == null || heapAfter == null
              ? null
              : heapAfter - heapBefore,
          },
        };
      };
      window.__elvernDiagnosticsBenchmarkReady = true;
    </script>
  </body>
</html>`;
}


const benchmarkPlugin = {
  name: "elvern-playback-diagnostics-benchmark",
  configureServer(server) {
    server.middlewares.use((request, response, next) => {
      const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
      if (request.method === "GET" && requestUrl.pathname === BENCHMARK_PATH) {
        response.statusCode = 200;
        response.setHeader("Content-Type", "text/html; charset=utf-8");
        response.setHeader("Cache-Control", "no-store");
        response.end(benchmarkHtml());
        return;
      }
      if (request.method === "POST" && requestUrl.pathname === UPLOAD_PATH) {
        let received = 0;
        request.on("data", (chunk) => { received += chunk.length; });
        request.on("end", () => {
          response.statusCode = 200;
          response.setHeader("Content-Type", "application/json");
          response.setHeader("Cache-Control", "no-store");
          response.end(JSON.stringify({ accepted: true, received_bytes: received }));
        });
        return;
      }
      if (request.method === "POST"
          && requestUrl.pathname.startsWith("/api/playback-diagnostics/")) {
        const chunks = [];
        request.on("data", (chunk) => chunks.push(chunk));
        request.on("end", () => {
          let body = {};
          try {
            body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
          } catch {
            response.statusCode = 422;
            response.setHeader("Content-Type", "application/json");
            response.end(JSON.stringify({ detail: { code: "diagnostics_invalid_event" } }));
            return;
          }
          let payload;
          if (requestUrl.pathname.endsWith("/bootstrap")) {
            payload = {
              enabled: true,
              diagnostics_session_id: body.playback_session_id,
              source_id: "source-worker-capture-benchmark",
              schema_version: "playback-diagnostics-event-v2",
              client_spool_max_bytes: 64_000_000,
              batch_max_events: 256,
              batch_max_bytes: 524_288,
              clock_algorithm: "monotonic-rtt-median-offset-v2",
              server_wall_time_ns: String(Date.now() * 1_000_000),
              server_monotonic_time_ns: "1",
              ack_watermark: 0,
            };
          } else if (requestUrl.pathname.endsWith("/clock")) {
            const now = String(Date.now() * 1_000_000);
            payload = {
              sample_id: body.sample_id,
              client_send_wall_time_ms: body.client_send_wall_time_ms,
              client_send_monotonic_time_us: body.client_send_monotonic_time_us,
              server_receive_wall_time_ns: now,
              server_receive_monotonic_time_ns: "1",
              server_send_wall_time_ns: now,
              server_send_monotonic_time_ns: "2",
              monotonic_raw_time_ns: null,
            };
          } else if (requestUrl.pathname.endsWith("/batch")) {
            const sequences = (body.events || []).map((event) => Number(event.source_sequence) || 0);
            payload = {
              accepted: sequences.length,
              duplicate: 0,
              rejected: 0,
              out_of_order: 0,
              ack_watermark: sequences.length ? Math.max(...sequences) : 0,
              missing_sequences: [],
              durable_gap_ranges: [],
              source_state: "open",
            };
          } else if (requestUrl.pathname.endsWith("/close")) {
            payload = { sealed: true, state: "sealed", ack_watermark: body.final_source_sequence || 0 };
          } else {
            response.statusCode = 404;
            response.end();
            return;
          }
          response.statusCode = 200;
          response.setHeader("Content-Type", "application/json");
          response.setHeader("Cache-Control", "no-store");
          response.end(JSON.stringify(payload));
        });
        return;
      }
      next();
    });
  },
};


let browser;
let vite;
try {
  vite = await createViteServer({
    root: FRONTEND_ROOT,
    configFile: false,
    logLevel: "error",
    plugins: [benchmarkPlugin],
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  await vite.listen();
  const address = vite.httpServer?.address();
  if (!address || typeof address === "string") {
    throw new Error("Unable to resolve the local benchmark server port");
  }
  browser = await chromium.launch({
    headless: true,
    args: ["--js-flags=--expose-gc"],
  });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${address.port}${BENCHMARK_PATH}`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(() => globalThis.__elvernDiagnosticsBenchmarkReady === true);
  const report = await page.evaluate(() => globalThis.runElvernDiagnosticsBenchmark());
  report.measured_at_utc = new Date().toISOString();
  report.browser_engine = "chromium";
  report.browser_version = browser.version();
  report.metric_summary = {
    creation_p95_ms: percentile(
      [report.main_thread_event_creation.p95_ms].filter(Number.isFinite),
      0.95,
    ),
    worker_capture_p95_ms: report.worker_capture_boundary.synchronous_capture.p95_ms,
    worker_capture_p99_ms: report.worker_capture_boundary.synchronous_capture.p99_ms,
    worker_capture_max_ms: report.worker_capture_boundary.synchronous_capture.max_ms,
    worker_capture_task_max_ms: report.worker_capture_boundary.synchronous_chunk_tasks.max_ms,
    indexeddb_enqueue_p95_ms: report.indexeddb.enqueue.p95_ms,
    loopback_upload_p95_ms: report.loopback_batch_upload.p95_ms,
    occupied_ring_push_p50_ms: report.ring_buffers.occupied_push.p50_ms,
    occupied_ring_push_p95_ms: report.ring_buffers.occupied_push.p95_ms,
    occupied_ring_push_max_ms: report.ring_buffers.occupied_push.max_ms,
    incident_handler_synchronous_ms: report.ring_buffers.incident_handler_synchronous_ms,
    longest_recorder_main_thread_task_ms:
      report.ring_buffers.longest_recorder_main_thread_task_ms,
    incremental_serialization_p95_ms:
      report.ring_buffers.incremental_serialization_tasks.p95_ms,
  };
  await fs.mkdir(path.dirname(OUTPUT_PATH), { recursive: true });
  await fs.writeFile(OUTPUT_PATH, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  await fs.chmod(OUTPUT_PATH, 0o600);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
} finally {
  await browser?.close();
  await vite?.close();
}
