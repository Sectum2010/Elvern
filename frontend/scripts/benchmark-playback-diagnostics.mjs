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
      import { DiagnosticRingBuffer } from "/src/lib/playbackDiagnostics/ringBuffer.js";
      import { createPlaybackDiagnosticEvent } from "/src/lib/playbackDiagnostics/schema.js";

      const EVENT_COUNT = 1_800;
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
          max_ms: values.length ? Math.max(...values) : null,
        };
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
          await memorySpool.enqueue(SESSION_ID, event);
          memoryEnqueueLatencies.push(performance.now() - started);
        }
        const memoryStats = await memorySpool.stats(SESSION_ID);

        await deleteBenchmarkDatabase();
        const indexedDbSpool = new IndexedDbDiagnosticSpool();
        const indexedDbOpenStarted = performance.now();
        await indexedDbSpool.open();
        const indexedDbOpenMs = performance.now() - indexedDbOpenStarted;
        const indexedDbEnqueueLatencies = [];
        for (const event of events) {
          const started = performance.now();
          await indexedDbSpool.enqueue(SESSION_ID, event);
          indexedDbEnqueueLatencies.push(performance.now() - started);
        }
        const indexedDbStats = await indexedDbSpool.stats(SESSION_ID);

        const readLatencies = [];
        let batch = null;
        for (let index = 0; index < 20; index += 1) {
          const started = performance.now();
          batch = await indexedDbSpool.readBatch(SESSION_ID, {
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
        const ack = await indexedDbSpool.acknowledge(SESSION_ID, batch.entries.at(-1).source_sequence);
        const ackMs = performance.now() - ackStarted;

        const sampleRing = new DiagnosticRingBuffer(240);
        for (let index = 0; index < 240; index += 1) {
          sampleRing.push({
            monotonic_ms: index * 250,
            playhead_ms: index * 250,
            buffered_ahead_ms: 45_000,
            ready_state: 4,
            network_state: 1,
          });
        }
        const frameRing = new DiagnosticRingBuffer(7_200);
        for (let index = 0; index < 7_200; index += 1) {
          frameRing.push({
            media_time_ms: index * (1_000 / 60),
            expected_display_time_ms: index * (1_000 / 60),
            presented_frames: index,
            processing_duration_ms: 1.2,
          });
        }
        const sampleRingBytes = encoder.encode(JSON.stringify(sampleRing.snapshot())).byteLength;
        const frameRingBytes = encoder.encode(JSON.stringify(frameRing.snapshot())).byteLength;

        indexedDbSpool.close();
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
          },
          loopback_batch_upload: {
            ...summarized(uploadLatencies),
            batch_events: batch.entries.length,
            batch_bytes: batch.totalBytes,
          },
          ring_buffers: {
            sample_entries: 240,
            sample_bytes: sampleRingBytes,
            frame_entries: 7_200,
            frame_bytes: frameRingBytes,
            total_bytes: sampleRingBytes + frameRingBytes,
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
    indexeddb_enqueue_p95_ms: report.indexeddb.enqueue.p95_ms,
    loopback_upload_p95_ms: report.loopback_batch_upload.p95_ms,
  };
  await fs.mkdir(path.dirname(OUTPUT_PATH), { recursive: true });
  await fs.writeFile(OUTPUT_PATH, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  await fs.chmod(OUTPUT_PATH, 0o600);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
} finally {
  await browser?.close();
  await vite?.close();
}
