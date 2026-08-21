import {
  PLAYBACK_DIAGNOSTICS_CRITICAL_RESERVE_RATIO,
  PLAYBACK_DIAGNOSTICS_DB_NAME,
  PLAYBACK_DIAGNOSTICS_DB_VERSION,
  PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES,
} from "./constants";
import { createDiagnosticId } from "./schema";

const EVENT_STORE = "events";
const META_STORE = "metadata";

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
  });
}

function transactionComplete(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("IndexedDB transaction failed"));
    transaction.onabort = () => reject(transaction.error || new Error("IndexedDB transaction aborted"));
  });
}

function encodedBytes(value) {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

function eventKey(sessionId, sequence) {
  return `${sessionId}:${String(sequence).padStart(16, "0")}`;
}

function sequenceMetaKey(sessionId) {
  return `sequence:${sessionId}`;
}

function clientMetaKey(sessionId) {
  return `client:${sessionId}`;
}

function usageMetaKey(sessionId) {
  return `usage:${sessionId}`;
}

export class IndexedDbDiagnosticSpool {
  constructor({
    indexedDBRef = globalThis.indexedDB,
    maxBytes = PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES,
  } = {}) {
    this.indexedDBRef = indexedDBRef;
    this.maxBytes = Math.max(1_000_000, Number(maxBytes) || PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES);
    this.database = null;
    this.available = Boolean(indexedDBRef);
    this.failureReason = this.available ? "" : "indexeddb_unavailable";
  }

  async open() {
    if (!this.indexedDBRef) {
      throw new Error("IndexedDB is unavailable");
    }
    if (this.database) return this;
    const request = this.indexedDBRef.open(
      PLAYBACK_DIAGNOSTICS_DB_NAME,
      PLAYBACK_DIAGNOSTICS_DB_VERSION,
    );
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(EVENT_STORE)) {
        const store = database.createObjectStore(EVENT_STORE, { keyPath: "key" });
        store.createIndex("by_session_sequence", ["session_id", "source_sequence"], { unique: true });
        store.createIndex("by_session_created", ["session_id", "created_at_ms"], { unique: false });
      }
      if (!database.objectStoreNames.contains(META_STORE)) {
        database.createObjectStore(META_STORE, { keyPath: "key" });
      }
    };
    try {
      this.database = await requestResult(request);
      this.database.onversionchange = () => {
        this.database?.close();
        this.database = null;
      };
      this.available = true;
      this.failureReason = "";
      return this;
    } catch (error) {
      this.available = false;
      this.failureReason = error?.name || "indexeddb_open_failed";
      throw error;
    }
  }

  setMaxBytes(value) {
    this.maxBytes = Math.max(1_000_000, Number(value) || this.maxBytes);
  }

  async getOrCreateClientInstanceId(sessionId) {
    await this.open();
    const transaction = this.database.transaction(META_STORE, "readwrite");
    const store = transaction.objectStore(META_STORE);
    const key = clientMetaKey(sessionId);
    const existing = await requestResult(store.get(key));
    const value = existing?.value || createDiagnosticId("client");
    if (!existing) store.put({ key, value });
    await transactionComplete(transaction);
    return value;
  }

  async reserveSequence(sessionId) {
    await this.open();
    const transaction = this.database.transaction(META_STORE, "readwrite");
    const store = transaction.objectStore(META_STORE);
    const key = sequenceMetaKey(sessionId);
    const existing = await requestResult(store.get(key));
    const sequence = Math.max(0, Number(existing?.value) || 0) + 1;
    store.put({ key, value: sequence });
    await transactionComplete(transaction);
    return sequence;
  }

  async enqueue(sessionId, event) {
    await this.open();
    const bytes = encodedBytes(event);
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readwrite");
    const eventStore = transaction.objectStore(EVENT_STORE);
    const metaStore = transaction.objectStore(META_STORE);
    const usageKey = usageMetaKey(sessionId);
    const usageRow = await requestResult(metaStore.get(usageKey));
    const usageBytes = Math.max(0, Number(usageRow?.bytes) || 0);
    const critical = event.priority === "critical";
    const normalLimit = Math.floor(
      this.maxBytes * (1 - PLAYBACK_DIAGNOSTICS_CRITICAL_RESERVE_RATIO),
    );
    const allowedLimit = critical ? this.maxBytes : normalLimit;
    if (usageBytes + bytes > allowedLimit) {
      transaction.abort();
      return {
        stored: false,
        reason: critical ? "client_spool_capacity_exhausted" : "client_spool_normal_capacity_reached",
        usageBytes,
        maxBytes: this.maxBytes,
      };
    }
    eventStore.put({
      key: eventKey(sessionId, event.source_sequence),
      session_id: sessionId,
      source_sequence: event.source_sequence,
      event,
      bytes,
      priority: event.priority,
      created_at_ms: Date.now(),
    });
    metaStore.put({ key: usageKey, bytes: usageBytes + bytes });
    await transactionComplete(transaction);
    return { stored: true, bytes, usageBytes: usageBytes + bytes, maxBytes: this.maxBytes };
  }

  async readBatch(sessionId, { maxEvents, maxBytes } = {}) {
    await this.open();
    const eventLimit = Math.max(1, Number(maxEvents) || 1);
    const byteLimit = Math.max(1, Number(maxBytes) || 1);
    const transaction = this.database.transaction(EVENT_STORE, "readonly");
    const index = transaction.objectStore(EVENT_STORE).index("by_session_sequence");
    const range = IDBKeyRange.bound([sessionId, 0], [sessionId, Number.MAX_SAFE_INTEGER]);
    const entries = [];
    let totalBytes = 0;
    await new Promise((resolve, reject) => {
      const request = index.openCursor(range, "next");
      request.onerror = () => reject(request.error || new Error("IndexedDB cursor failed"));
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor || entries.length >= eventLimit) {
          resolve();
          return;
        }
        const candidate = cursor.value;
        if (entries.length && totalBytes + candidate.bytes > byteLimit) {
          resolve();
          return;
        }
        entries.push(candidate);
        totalBytes += candidate.bytes;
        cursor.continue();
      };
    });
    await transactionComplete(transaction);
    return { entries, totalBytes };
  }

  async acknowledge(sessionId, watermark) {
    await this.open();
    const numericWatermark = Math.max(0, Number(watermark) || 0);
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readwrite");
    const eventStore = transaction.objectStore(EVENT_STORE);
    const index = eventStore.index("by_session_sequence");
    const range = IDBKeyRange.bound([sessionId, 0], [sessionId, numericWatermark]);
    let deletedBytes = 0;
    let deletedEvents = 0;
    await new Promise((resolve, reject) => {
      const request = index.openCursor(range, "next");
      request.onerror = () => reject(request.error || new Error("IndexedDB ACK cursor failed"));
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) {
          resolve();
          return;
        }
        deletedBytes += Math.max(0, Number(cursor.value?.bytes) || 0);
        deletedEvents += 1;
        cursor.delete();
        cursor.continue();
      };
    });
    const metaStore = transaction.objectStore(META_STORE);
    const usageKey = usageMetaKey(sessionId);
    const usageRow = await requestResult(metaStore.get(usageKey));
    const nextUsage = Math.max(0, (Number(usageRow?.bytes) || 0) - deletedBytes);
    metaStore.put({ key: usageKey, bytes: nextUsage });
    await transactionComplete(transaction);
    return { deletedEvents, deletedBytes, usageBytes: nextUsage };
  }

  async stats(sessionId) {
    await this.open();
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readonly");
    const eventIndex = transaction.objectStore(EVENT_STORE).index("by_session_created");
    const range = IDBKeyRange.bound([sessionId, 0], [sessionId, Number.MAX_SAFE_INTEGER]);
    const count = await requestResult(eventIndex.count(range));
    const usage = await requestResult(transaction.objectStore(META_STORE).get(usageMetaKey(sessionId)));
    let oldestEventAgeMs = 0;
    await new Promise((resolve, reject) => {
      const request = eventIndex.openCursor(range, "next");
      request.onerror = () => reject(request.error || new Error("IndexedDB stats cursor failed"));
      request.onsuccess = () => {
        oldestEventAgeMs = request.result
          ? Math.max(0, Date.now() - Number(request.result.value.created_at_ms || Date.now()))
          : 0;
        resolve();
      };
    });
    await transactionComplete(transaction);
    return {
      queueDepth: count,
      queueBytes: Math.max(0, Number(usage?.bytes) || 0),
      oldestEventAgeMs,
    };
  }

  close() {
    this.database?.close();
    this.database = null;
  }
}

export class MemoryDiagnosticSpool {
  constructor({ maxBytes = PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES } = {}) {
    this.maxBytes = maxBytes;
    this.entries = new Map();
    this.sequences = new Map();
    this.clientIds = new Map();
    this.available = true;
    this.failureReason = "indexeddb_unavailable_memory_fallback";
  }

  async open() { return this; }

  setMaxBytes(value) { this.maxBytes = Math.max(1_000_000, Number(value) || this.maxBytes); }

  async getOrCreateClientInstanceId(sessionId) {
    if (!this.clientIds.has(sessionId)) this.clientIds.set(sessionId, createDiagnosticId("client"));
    return this.clientIds.get(sessionId);
  }

  async reserveSequence(sessionId) {
    const next = (this.sequences.get(sessionId) || 0) + 1;
    this.sequences.set(sessionId, next);
    return next;
  }

  async enqueue(sessionId, event) {
    const bytes = encodedBytes(event);
    const stats = await this.stats(sessionId);
    const critical = event.priority === "critical";
    const limit = critical
      ? this.maxBytes
      : Math.floor(this.maxBytes * (1 - PLAYBACK_DIAGNOSTICS_CRITICAL_RESERVE_RATIO));
    if (stats.queueBytes + bytes > limit) {
      return { stored: false, reason: "client_spool_capacity_reached", ...stats };
    }
    this.entries.set(eventKey(sessionId, event.source_sequence), {
      session_id: sessionId,
      source_sequence: event.source_sequence,
      event,
      bytes,
      created_at_ms: Date.now(),
    });
    return { stored: true, bytes, usageBytes: stats.queueBytes + bytes };
  }

  async readBatch(sessionId, { maxEvents, maxBytes } = {}) {
    const ordered = [...this.entries.values()]
      .filter((entry) => entry.session_id === sessionId)
      .sort((left, right) => left.source_sequence - right.source_sequence);
    const entries = [];
    let totalBytes = 0;
    for (const entry of ordered) {
      if (entries.length >= maxEvents || (entries.length && totalBytes + entry.bytes > maxBytes)) break;
      entries.push(entry);
      totalBytes += entry.bytes;
    }
    return { entries, totalBytes };
  }

  async acknowledge(sessionId, watermark) {
    let deletedEvents = 0;
    let deletedBytes = 0;
    for (const [key, entry] of this.entries) {
      if (entry.session_id === sessionId && entry.source_sequence <= watermark) {
        this.entries.delete(key);
        deletedEvents += 1;
        deletedBytes += entry.bytes;
      }
    }
    const stats = await this.stats(sessionId);
    return { deletedEvents, deletedBytes, usageBytes: stats.queueBytes };
  }

  async stats(sessionId) {
    const entries = [...this.entries.values()].filter((entry) => entry.session_id === sessionId);
    return {
      queueDepth: entries.length,
      queueBytes: entries.reduce((sum, entry) => sum + entry.bytes, 0),
      oldestEventAgeMs: entries.length
        ? Math.max(0, Date.now() - Math.min(...entries.map((entry) => entry.created_at_ms)))
        : 0,
    };
  }

  close() {}
}

export async function createDiagnosticSpool(options = {}) {
  const persistent = new IndexedDbDiagnosticSpool(options);
  try {
    await persistent.open();
    return { spool: persistent, persistent: true, unavailableReason: null };
  } catch (error) {
    return {
      spool: new MemoryDiagnosticSpool(options),
      persistent: false,
      unavailableReason: error?.name || persistent.failureReason || "indexeddb_unavailable",
    };
  }
}
