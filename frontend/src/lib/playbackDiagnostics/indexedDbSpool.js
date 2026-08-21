import {
  PLAYBACK_DIAGNOSTICS_CRITICAL_RESERVE_RATIO,
  PLAYBACK_DIAGNOSTICS_DB_NAME,
  PLAYBACK_DIAGNOSTICS_DB_VERSION,
  PLAYBACK_DIAGNOSTICS_DEGRADED_SPOOL_MAX_BYTES,
  PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES,
  PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS,
} from "./constants";
import { createDiagnosticId } from "./schema";

const EVENT_STORE = "events";
const META_STORE = "metadata";
const GLOBAL_USAGE_KEY = "usage:global";
const OWNER_SCOPE_SALT_KEY = "owner-scope:salt";

function requestResult(request, { rejectOnBlocked = false } = {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    request.onsuccess = () => {
      if (settled) {
        request.result?.close?.();
        return;
      }
      settled = true;
      resolve(request.result);
    };
    request.onerror = () => {
      if (settled) return;
      settled = true;
      reject(request.error || new Error("IndexedDB request failed"));
    };
    if (rejectOnBlocked) {
      request.onblocked = () => {
        if (settled) return;
        settled = true;
        const error = new Error("IndexedDB open request was blocked");
        error.name = "IndexedDBBlockedError";
        reject(error);
      };
    }
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

async function ownerScopeDigest(salt, ownerUserId) {
  const bytes = new TextEncoder().encode(`${String(salt || "")}:${String(ownerUserId ?? "")}`);
  if (globalThis.crypto?.subtle?.digest) {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)]
      .map((entry) => entry.toString(16).padStart(2, "0"))
      .join("");
  }
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  bytes.forEach((entry) => {
    first = Math.imul(first ^ entry, 0x01000193) >>> 0;
    second = Math.imul(second ^ entry, 0x85ebca6b) >>> 0;
  });
  return `local-${first.toString(16).padStart(8, "0")}${second.toString(16).padStart(8, "0")}`;
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

function recoveryMetaKey(sessionId) {
  return `recovery:${sessionId}`;
}

function registryMetaKey(sessionId) {
  return `registry:${sessionId}`;
}

function numeric(value) {
  const result = Number(value);
  return Number.isFinite(result) && result >= 0 ? result : 0;
}

function capacityResult({ critical, globalUsageBytes, sessionUsageBytes, maxBytes }) {
  return {
    stored: false,
    reason: critical
      ? "client_spool_capacity_exhausted"
      : "client_spool_normal_capacity_reached",
    globalUsageBytes,
    usageBytes: sessionUsageBytes,
    maxBytes,
  };
}

function boundedPendingGaps(value) {
  return Array.isArray(value)
    ? value.slice(0, PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS)
    : [];
}

function normalizeGap(gap) {
  const start = Math.max(1, Number(gap?.start_sequence) || 0);
  const end = Math.max(start, Number(gap?.end_sequence) || start);
  return {
    start_sequence: start,
    end_sequence: end,
    reason_code: String(gap?.reason_code || "client_storage_failure"),
    rejected_event_name: gap?.rejected_event_name || null,
    rejected_event_hash: gap?.rejected_event_hash || null,
    created_at_ms: Number(gap?.created_at_ms) || Date.now(),
  };
}

export class IndexedDbDiagnosticSpool {
  constructor({
    indexedDBRef = globalThis.indexedDB,
    keyRangeRef = globalThis.IDBKeyRange,
    maxBytes = PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES,
  } = {}) {
    this.indexedDBRef = indexedDBRef;
    this.keyRangeRef = keyRangeRef;
    this.maxBytes = Math.max(1_000_000, Number(maxBytes) || PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES);
    this.database = null;
    this.available = Boolean(indexedDBRef);
    this.failureReason = this.available ? "" : "indexeddb_unavailable";
  }

  async open() {
    if (!this.indexedDBRef) throw new Error("IndexedDB is unavailable");
    if (this.database) return this;
    const request = this.indexedDBRef.open(
      PLAYBACK_DIAGNOSTICS_DB_NAME,
      PLAYBACK_DIAGNOSTICS_DB_VERSION,
    );
    request.onupgradeneeded = () => {
      const database = request.result;
      let eventStore;
      if (!database.objectStoreNames.contains(EVENT_STORE)) {
        eventStore = database.createObjectStore(EVENT_STORE, { keyPath: "key" });
        eventStore.createIndex("by_session_sequence", ["session_id", "source_sequence"], { unique: true });
        eventStore.createIndex("by_session_created", ["session_id", "created_at_ms"], { unique: false });
      } else {
        eventStore = request.transaction.objectStore(EVENT_STORE);
      }
      if (!eventStore.indexNames.contains("by_created")) {
        eventStore.createIndex("by_created", "created_at_ms", { unique: false });
      }
      if (!database.objectStoreNames.contains(META_STORE)) {
        database.createObjectStore(META_STORE, { keyPath: "key" });
      }
    };
    try {
      this.database = await requestResult(request, { rejectOnBlocked: true });
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
    const completed = transactionComplete(transaction);
    const store = transaction.objectStore(META_STORE);
    const key = clientMetaKey(sessionId);
    const existing = await requestResult(store.get(key));
    const value = existing?.value || createDiagnosticId("client");
    if (!existing) store.put({ key, value });
    await completed;
    return value;
  }

  async getOwnerScopeHash(ownerUserId) {
    await this.open();
    const transaction = this.database.transaction(META_STORE, "readwrite");
    const completed = transactionComplete(transaction);
    const store = transaction.objectStore(META_STORE);
    const existing = await requestResult(store.get(OWNER_SCOPE_SALT_KEY));
    const salt = existing?.value || createDiagnosticId("owner_scope_salt");
    if (!existing) store.put({ key: OWNER_SCOPE_SALT_KEY, value: salt });
    await completed;
    return ownerScopeDigest(salt, ownerUserId);
  }

  async createAndEnqueue(sessionId, eventFactory, { priority = "normal" } = {}) {
    await this.open();
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readwrite");
    const completed = transactionComplete(transaction);
    const eventStore = transaction.objectStore(EVENT_STORE);
    const metaStore = transaction.objectStore(META_STORE);
    const [sequenceRow, sessionUsageRow, globalUsageRow, registryRow] = await Promise.all([
      requestResult(metaStore.get(sequenceMetaKey(sessionId))),
      requestResult(metaStore.get(usageMetaKey(sessionId))),
      requestResult(metaStore.get(GLOBAL_USAGE_KEY)),
      requestResult(metaStore.get(registryMetaKey(sessionId))),
    ]);
    const sequence = numeric(sequenceRow?.value) + 1;
    let event;
    try {
      event = eventFactory(sequence);
    } catch (error) {
      transaction.abort();
      await completed.catch(() => {});
      throw error;
    }
    if (!event || Number(event.source_sequence) !== sequence) {
      transaction.abort();
      await completed.catch(() => {});
      throw new TypeError("Diagnostic event factory must use the allocated sequence");
    }
    const bytes = encodedBytes(event);
    const critical = priority === "critical" || event.priority === "critical";
    const sessionUsageBytes = numeric(sessionUsageRow?.bytes);
    const globalUsageBytes = numeric(globalUsageRow?.bytes);
    const normalLimit = Math.floor(
      this.maxBytes * (1 - PLAYBACK_DIAGNOSTICS_CRITICAL_RESERVE_RATIO),
    );
    const allowedLimit = critical ? this.maxBytes : normalLimit;
    if (globalUsageBytes + bytes > allowedLimit) {
      await completed;
      return capacityResult({
        critical,
        globalUsageBytes,
        sessionUsageBytes,
        maxBytes: this.maxBytes,
      });
    }
    const createdAtMs = Date.now();
    eventStore.add({
      key: eventKey(sessionId, sequence),
      session_id: sessionId,
      source_sequence: sequence,
      event,
      bytes,
      priority: event.priority,
      created_at_ms: createdAtMs,
    });
    metaStore.put({ key: sequenceMetaKey(sessionId), value: sequence });
    metaStore.put({ key: usageMetaKey(sessionId), bytes: sessionUsageBytes + bytes });
    metaStore.put({ key: GLOBAL_USAGE_KEY, bytes: globalUsageBytes + bytes });
    metaStore.put({
      ...(registryRow || {}),
      key: registryMetaKey(sessionId),
      session_id: sessionId,
      queue_bytes: sessionUsageBytes + bytes,
      last_sequence: sequence,
      updated_at_ms: createdAtMs,
    });
    await completed;
    return {
      stored: true,
      event,
      sequence,
      bytes,
      usageBytes: sessionUsageBytes + bytes,
      globalUsageBytes: globalUsageBytes + bytes,
      maxBytes: this.maxBytes,
    };
  }

  async replaceWithGap(sessionId, sequence, event) {
    await this.open();
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readwrite");
    const completed = transactionComplete(transaction);
    const eventStore = transaction.objectStore(EVENT_STORE);
    const metaStore = transaction.objectStore(META_STORE);
    const key = eventKey(sessionId, sequence);
    const existing = await requestResult(eventStore.get(key));
    if (!existing) {
      await completed;
      return false;
    }
    const bytes = encodedBytes(event);
    const delta = bytes - numeric(existing.bytes);
    const [sessionUsageRow, globalUsageRow] = await Promise.all([
      requestResult(metaStore.get(usageMetaKey(sessionId))),
      requestResult(metaStore.get(GLOBAL_USAGE_KEY)),
    ]);
    if (numeric(globalUsageRow?.bytes) + delta > this.maxBytes) {
      await completed;
      return false;
    }
    eventStore.put({ ...existing, event, bytes, priority: "critical" });
    metaStore.put({ key: usageMetaKey(sessionId), bytes: numeric(sessionUsageRow?.bytes) + delta });
    metaStore.put({ key: GLOBAL_USAGE_KEY, bytes: numeric(globalUsageRow?.bytes) + delta });
    await completed;
    return true;
  }

  async getRecoveryState(sessionId) {
    await this.open();
    const transaction = this.database.transaction(META_STORE, "readonly");
    const completed = transactionComplete(transaction);
    const row = await requestResult(transaction.objectStore(META_STORE).get(recoveryMetaKey(sessionId)));
    await completed;
    return row?.value || null;
  }

  async updateRecoveryState(sessionId, updates) {
    await this.open();
    const transaction = this.database.transaction(META_STORE, "readwrite");
    const completed = transactionComplete(transaction);
    const store = transaction.objectStore(META_STORE);
    const key = recoveryMetaKey(sessionId);
    const [row, registryRow] = await Promise.all([
      requestResult(store.get(key)),
      requestResult(store.get(registryMetaKey(sessionId))),
    ]);
    const value = { ...(row?.value || {}), ...updates, updated_at_ms: Date.now() };
    store.put({ key, value });
    store.put({
      ...(registryRow || {}),
      key: registryMetaKey(sessionId),
      session_id: sessionId,
      close_state: value.close_state || "open",
      source_id: value.source_id || null,
      client_instance_id: value.client_instance_id || null,
      owner_scope_hash: value.owner_scope_hash || null,
      final_source_sequence: value.final_source_sequence ?? null,
      last_durable_ack: numeric(value.last_durable_ack),
      pending_gap_count: boundedPendingGaps(value.pending_gaps).length,
      active_lease_id: value.active_lease_id || null,
      active_lease_expires_at_ms: numeric(value.active_lease_expires_at_ms),
      updated_at_ms: value.updated_at_ms,
    });
    await completed;
    return value;
  }

  async listRecoverySessions({ afterSessionId = "", limit = 64 } = {}) {
    await this.open();
    const transaction = this.database.transaction(META_STORE, "readonly");
    const completed = transactionComplete(transaction);
    const store = transaction.objectStore(META_STORE);
    const rows = [];
    const boundedLimit = Math.max(1, Math.min(256, Number(limit) || 64));
    const lowerKey = afterSessionId
      ? registryMetaKey(String(afterSessionId))
      : "registry:";
    await new Promise((resolve, reject) => {
      const range = this.keyRangeRef.bound(
        lowerKey,
        "registry:\uffff",
        Boolean(afterSessionId),
        false,
      );
      const request = store.openCursor(range, "next");
      request.onerror = () => reject(request.error || new Error("IndexedDB registry cursor failed"));
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor || rows.length >= boundedLimit) {
          resolve();
          return;
        }
        rows.push({ ...cursor.value });
        cursor.continue();
      };
    });
    await completed;
    return rows;
  }

  async queueGap(sessionId, gap, { sequence = null } = {}) {
    await this.open();
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readwrite");
    const completed = transactionComplete(transaction);
    const eventStore = transaction.objectStore(EVENT_STORE);
    const metaStore = transaction.objectStore(META_STORE);
    const [sequenceRow, recoveryRow, registryRow] = await Promise.all([
      requestResult(metaStore.get(sequenceMetaKey(sessionId))),
      requestResult(metaStore.get(recoveryMetaKey(sessionId))),
      requestResult(metaStore.get(registryMetaKey(sessionId))),
    ]);
    const nextSequence = sequence == null
      ? numeric(sequenceRow?.value) + 1
      : Math.max(1, Number(sequence) || 1);
    const normalized = normalizeGap({
      ...gap,
      start_sequence: gap?.start_sequence ?? nextSequence,
      end_sequence: gap?.end_sequence ?? nextSequence,
    });
    if (sequence != null) {
      const existing = await requestResult(eventStore.get(eventKey(sessionId, nextSequence)));
      if (!existing) {
        transaction.abort();
        await completed.catch(() => {});
        return null;
      }
    }
    const current = recoveryRow?.value || {};
    const pending = boundedPendingGaps(current.pending_gaps);
    const duplicate = pending.some((entry) => (
      Number(entry.start_sequence) === normalized.start_sequence
      && Number(entry.end_sequence) === normalized.end_sequence
      && entry.reason_code === normalized.reason_code
    ));
    if (!duplicate && pending.length >= PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS) {
      transaction.abort();
      await completed.catch(() => {});
      return null;
    }
    const pendingGaps = duplicate ? pending : [...pending, normalized];
    if (sequence == null) {
      metaStore.put({ key: sequenceMetaKey(sessionId), value: normalized.end_sequence });
    }
    const updatedAt = Date.now();
    const recovery = { ...current, pending_gaps: pendingGaps, updated_at_ms: updatedAt };
    metaStore.put({ key: recoveryMetaKey(sessionId), value: recovery });
    metaStore.put({
      ...(registryRow || {}),
      key: registryMetaKey(sessionId),
      session_id: sessionId,
      last_sequence: Math.max(numeric(registryRow?.last_sequence), normalized.end_sequence),
      close_state: recovery.close_state || "open",
      pending_gap_count: pendingGaps.length,
      updated_at_ms: updatedAt,
    });
    await completed;
    return normalized;
  }

  async pendingGaps(sessionId) {
    return boundedPendingGaps((await this.getRecoveryState(sessionId))?.pending_gaps);
  }

  async completeGap(sessionId, gap, watermark = 0) {
    await this.open();
    const normalized = normalizeGap(gap);
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readwrite");
    const completed = transactionComplete(transaction);
    const eventStore = transaction.objectStore(EVENT_STORE);
    const index = eventStore.index("by_session_sequence");
    const range = this.keyRangeRef.bound(
      [sessionId, normalized.start_sequence],
      [sessionId, normalized.end_sequence],
    );
    let deletedBytes = 0;
    await new Promise((resolve, reject) => {
      const request = index.openCursor(range, "next");
      request.onerror = () => reject(request.error || new Error("IndexedDB gap cursor failed"));
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) {
          resolve();
          return;
        }
        deletedBytes += numeric(cursor.value?.bytes);
        cursor.delete();
        cursor.continue();
      };
    });
    const metaStore = transaction.objectStore(META_STORE);
    const [recoveryRow, usageRow, globalUsageRow, registryRow] = await Promise.all([
      requestResult(metaStore.get(recoveryMetaKey(sessionId))),
      requestResult(metaStore.get(usageMetaKey(sessionId))),
      requestResult(metaStore.get(GLOBAL_USAGE_KEY)),
      requestResult(metaStore.get(registryMetaKey(sessionId))),
    ]);
    const current = recoveryRow?.value || {};
    const pending = boundedPendingGaps(current.pending_gaps).filter((entry) => !(
      Number(entry.start_sequence) === normalized.start_sequence
      && Number(entry.end_sequence) === normalized.end_sequence
      && entry.reason_code === normalized.reason_code
    ));
    const updatedAt = Date.now();
    const recovery = {
      ...current,
      pending_gaps: pending,
      last_durable_ack: Math.max(numeric(current.last_durable_ack), numeric(watermark)),
      updated_at_ms: updatedAt,
    };
    metaStore.put({ key: recoveryMetaKey(sessionId), value: recovery });
    metaStore.put({
      key: usageMetaKey(sessionId),
      bytes: Math.max(0, numeric(usageRow?.bytes) - deletedBytes),
    });
    metaStore.put({
      key: GLOBAL_USAGE_KEY,
      bytes: Math.max(0, numeric(globalUsageRow?.bytes) - deletedBytes),
    });
    metaStore.put({
      ...(registryRow || {}),
      key: registryMetaKey(sessionId),
      session_id: sessionId,
      pending_gap_count: pending.length,
      last_durable_ack: recovery.last_durable_ack,
      updated_at_ms: updatedAt,
    });
    await completed;
    return recovery;
  }

  async readBatch(sessionId, { maxEvents, maxBytes } = {}) {
    await this.open();
    const eventLimit = Math.max(1, Number(maxEvents) || 1);
    const byteLimit = Math.max(1, Number(maxBytes) || 1);
    const transaction = this.database.transaction(EVENT_STORE, "readonly");
    const completed = transactionComplete(transaction);
    const index = transaction.objectStore(EVENT_STORE).index("by_session_sequence");
    const range = this.keyRangeRef.bound([sessionId, 0], [sessionId, Number.MAX_SAFE_INTEGER]);
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
    await completed;
    return { entries, totalBytes };
  }

  async acknowledge(sessionId, watermark) {
    await this.open();
    const numericWatermark = numeric(watermark);
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readwrite");
    const completed = transactionComplete(transaction);
    const eventStore = transaction.objectStore(EVENT_STORE);
    const index = eventStore.index("by_session_sequence");
    const range = this.keyRangeRef.bound([sessionId, 0], [sessionId, numericWatermark]);
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
        deletedBytes += numeric(cursor.value?.bytes);
        deletedEvents += 1;
        cursor.delete();
        cursor.continue();
      };
    });
    const metaStore = transaction.objectStore(META_STORE);
    const [sessionUsageRow, globalUsageRow, recoveryRow, registryRow] = await Promise.all([
      requestResult(metaStore.get(usageMetaKey(sessionId))),
      requestResult(metaStore.get(GLOBAL_USAGE_KEY)),
      requestResult(metaStore.get(recoveryMetaKey(sessionId))),
      requestResult(metaStore.get(registryMetaKey(sessionId))),
    ]);
    const nextUsage = Math.max(0, numeric(sessionUsageRow?.bytes) - deletedBytes);
    const nextGlobalUsage = Math.max(0, numeric(globalUsageRow?.bytes) - deletedBytes);
    metaStore.put({ key: usageMetaKey(sessionId), bytes: nextUsage });
    metaStore.put({ key: GLOBAL_USAGE_KEY, bytes: nextGlobalUsage });
    metaStore.put({
      key: recoveryMetaKey(sessionId),
      value: {
        ...(recoveryRow?.value || {}),
        last_durable_ack: Math.max(numeric(recoveryRow?.value?.last_durable_ack), numericWatermark),
        updated_at_ms: Date.now(),
      },
    });
    metaStore.put({
      ...(registryRow || {}),
      key: registryMetaKey(sessionId),
      session_id: sessionId,
      queue_bytes: nextUsage,
      last_durable_ack: Math.max(
        numeric(registryRow?.last_durable_ack),
        numericWatermark,
      ),
      updated_at_ms: Date.now(),
    });
    await completed;
    return {
      deletedEvents,
      deletedBytes,
      usageBytes: nextUsage,
      globalUsageBytes: nextGlobalUsage,
    };
  }

  async markCloseState(sessionId, closeState) {
    return this.updateRecoveryState(sessionId, { close_state: closeState });
  }

  async cleanupSealedSession(sessionId) {
    const stats = await this.stats(sessionId);
    if (stats.queueDepth > 0) return false;
    const recovery = await this.getRecoveryState(sessionId);
    if (
      recovery?.close_state !== "sealed"
      || boundedPendingGaps(recovery?.pending_gaps).length > 0
    ) return false;
    const transaction = this.database.transaction(META_STORE, "readwrite");
    const completed = transactionComplete(transaction);
    const store = transaction.objectStore(META_STORE);
    [
      sequenceMetaKey(sessionId), clientMetaKey(sessionId), usageMetaKey(sessionId),
      recoveryMetaKey(sessionId), registryMetaKey(sessionId),
    ].forEach((key) => store.delete(key));
    await completed;
    return true;
  }

  async stats(sessionId) {
    await this.open();
    const transaction = this.database.transaction([EVENT_STORE, META_STORE], "readonly");
    const completed = transactionComplete(transaction);
    const eventIndex = transaction.objectStore(EVENT_STORE).index("by_session_created");
    const range = this.keyRangeRef.bound([sessionId, 0], [sessionId, Number.MAX_SAFE_INTEGER]);
    const count = await requestResult(eventIndex.count(range));
    const metaStore = transaction.objectStore(META_STORE);
    const [usage, globalUsage] = await Promise.all([
      requestResult(metaStore.get(usageMetaKey(sessionId))),
      requestResult(metaStore.get(GLOBAL_USAGE_KEY)),
    ]);
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
    await completed;
    return {
      queueDepth: count,
      queueBytes: numeric(usage?.bytes),
      globalQueueBytes: numeric(globalUsage?.bytes),
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
    this.maxBytes = Math.max(1_000, Number(maxBytes) || PLAYBACK_DIAGNOSTICS_DEFAULT_SPOOL_MAX_BYTES);
    this.entries = new Map();
    this.sequences = new Map();
    this.clientIds = new Map();
    this.recovery = new Map();
    this.ownerScopeSalt = createDiagnosticId("owner_scope_salt");
    this.available = true;
    this.failureReason = "indexeddb_unavailable_memory_fallback";
  }

  async open() { return this; }

  setMaxBytes(value) { this.maxBytes = Math.max(1_000, Number(value) || this.maxBytes); }

  async getOrCreateClientInstanceId(sessionId) {
    if (!this.clientIds.has(sessionId)) this.clientIds.set(sessionId, createDiagnosticId("client"));
    return this.clientIds.get(sessionId);
  }

  async getOwnerScopeHash(ownerUserId) {
    return ownerScopeDigest(this.ownerScopeSalt, ownerUserId);
  }

  async createAndEnqueue(sessionId, eventFactory, { priority = "normal" } = {}) {
    const sequence = (this.sequences.get(sessionId) || 0) + 1;
    const event = eventFactory(sequence);
    if (!event || Number(event.source_sequence) !== sequence) {
      throw new TypeError("Diagnostic event factory must use the allocated sequence");
    }
    const bytes = encodedBytes(event);
    const stats = await this.stats(sessionId);
    const critical = priority === "critical" || event.priority === "critical";
    const limit = critical
      ? this.maxBytes
      : Math.floor(this.maxBytes * (1 - PLAYBACK_DIAGNOSTICS_CRITICAL_RESERVE_RATIO));
    if (stats.globalQueueBytes + bytes > limit) {
      return capacityResult({
        critical,
        globalUsageBytes: stats.globalQueueBytes,
        sessionUsageBytes: stats.queueBytes,
        maxBytes: this.maxBytes,
      });
    }
    this.entries.set(eventKey(sessionId, sequence), {
      session_id: sessionId,
      source_sequence: sequence,
      event,
      bytes,
      priority: event.priority,
      created_at_ms: Date.now(),
    });
    this.sequences.set(sessionId, sequence);
    return {
      stored: true,
      event,
      sequence,
      bytes,
      usageBytes: stats.queueBytes + bytes,
      globalUsageBytes: stats.globalQueueBytes + bytes,
    };
  }

  async replaceWithGap(sessionId, sequence, event) {
    const key = eventKey(sessionId, sequence);
    const existing = this.entries.get(key);
    if (!existing) return false;
    this.entries.set(key, {
      ...existing,
      event,
      bytes: encodedBytes(event),
      priority: "critical",
    });
    return true;
  }

  async getRecoveryState(sessionId) { return this.recovery.get(sessionId) || null; }

  async updateRecoveryState(sessionId, updates) {
    const next = { ...(this.recovery.get(sessionId) || {}), ...updates, updated_at_ms: Date.now() };
    this.recovery.set(sessionId, next);
    return next;
  }

  async listRecoverySessions({ afterSessionId = "", limit = 64 } = {}) {
    const boundedLimit = Math.max(1, Math.min(256, Number(limit) || 64));
    return [...new Set([
      ...this.sequences.keys(),
      ...this.recovery.keys(),
      ...[...this.entries.values()].map((entry) => entry.session_id),
    ])]
      .map((sessionId) => String(sessionId))
      .filter((sessionId) => sessionId > String(afterSessionId || ""))
      .sort()
      .slice(0, boundedLimit)
      .map((sessionId) => {
      const state = this.recovery.get(sessionId) || {};
      const queueBytes = [...this.entries.values()]
        .filter((entry) => entry.session_id === sessionId)
        .reduce((total, entry) => total + numeric(entry.bytes), 0);
      return {
        session_id: sessionId,
        close_state: state.close_state || "open",
        source_id: state.source_id || null,
        owner_scope_hash: state.owner_scope_hash || null,
        final_source_sequence: state.final_source_sequence ?? null,
        pending_gap_count: boundedPendingGaps(state.pending_gaps).length,
        queue_bytes: queueBytes,
        active_lease_id: state.active_lease_id || null,
        active_lease_expires_at_ms: numeric(state.active_lease_expires_at_ms),
        updated_at_ms: state.updated_at_ms || 0,
      };
    });
  }

  async queueGap(sessionId, gap, { sequence = null } = {}) {
    const nextSequence = sequence == null
      ? (this.sequences.get(sessionId) || 0) + 1
      : Math.max(1, Number(sequence) || 1);
    if (sequence != null && !this.entries.has(eventKey(sessionId, nextSequence))) return null;
    const normalized = normalizeGap({
      ...gap,
      start_sequence: gap?.start_sequence ?? nextSequence,
      end_sequence: gap?.end_sequence ?? nextSequence,
    });
    const current = this.recovery.get(sessionId) || {};
    const pending = boundedPendingGaps(current.pending_gaps);
    const duplicate = pending.some((entry) => (
      Number(entry.start_sequence) === normalized.start_sequence
      && Number(entry.end_sequence) === normalized.end_sequence
      && entry.reason_code === normalized.reason_code
    ));
    if (!duplicate && pending.length >= PLAYBACK_DIAGNOSTICS_MAX_PENDING_GAPS) return null;
    if (!duplicate) pending.push(normalized);
    if (sequence == null) this.sequences.set(sessionId, normalized.end_sequence);
    this.recovery.set(sessionId, {
      ...current,
      pending_gaps: pending,
      updated_at_ms: Date.now(),
    });
    return normalized;
  }

  async pendingGaps(sessionId) {
    return boundedPendingGaps(this.recovery.get(sessionId)?.pending_gaps);
  }

  async completeGap(sessionId, gap, watermark = 0) {
    const normalized = normalizeGap(gap);
    for (let sequence = normalized.start_sequence; sequence <= normalized.end_sequence; sequence += 1) {
      this.entries.delete(eventKey(sessionId, sequence));
    }
    const current = this.recovery.get(sessionId) || {};
    const pending = boundedPendingGaps(current.pending_gaps).filter((entry) => !(
      Number(entry.start_sequence) === normalized.start_sequence
      && Number(entry.end_sequence) === normalized.end_sequence
      && entry.reason_code === normalized.reason_code
    ));
    const next = {
      ...current,
      pending_gaps: pending,
      last_durable_ack: Math.max(numeric(current.last_durable_ack), numeric(watermark)),
      updated_at_ms: Date.now(),
    };
    this.recovery.set(sessionId, next);
    return next;
  }

  async readBatch(sessionId, { maxEvents = 1, maxBytes = 1 } = {}) {
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
    const previous = this.recovery.get(sessionId) || {};
    this.recovery.set(sessionId, {
      ...previous,
      last_durable_ack: Math.max(numeric(previous.last_durable_ack), numeric(watermark)),
      updated_at_ms: Date.now(),
    });
    const stats = await this.stats(sessionId);
    return {
      deletedEvents,
      deletedBytes,
      usageBytes: stats.queueBytes,
      globalUsageBytes: stats.globalQueueBytes,
    };
  }

  async markCloseState(sessionId, closeState) {
    return this.updateRecoveryState(sessionId, { close_state: closeState });
  }

  async cleanupSealedSession(sessionId) {
    const stats = await this.stats(sessionId);
    const recovery = this.recovery.get(sessionId);
    if (
      stats.queueDepth
      || recovery?.close_state !== "sealed"
      || boundedPendingGaps(recovery?.pending_gaps).length > 0
    ) return false;
    this.sequences.delete(sessionId);
    this.clientIds.delete(sessionId);
    this.recovery.delete(sessionId);
    return true;
  }

  async stats(sessionId) {
    const all = [...this.entries.values()];
    const entries = all.filter((entry) => entry.session_id === sessionId);
    return {
      queueDepth: entries.length,
      queueBytes: entries.reduce((sum, entry) => sum + entry.bytes, 0),
      globalQueueBytes: all.reduce((sum, entry) => sum + entry.bytes, 0),
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
      spool: new MemoryDiagnosticSpool({
        ...options,
        maxBytes: Math.min(
          Number(options.degradedMaxBytes) || PLAYBACK_DIAGNOSTICS_DEGRADED_SPOOL_MAX_BYTES,
          PLAYBACK_DIAGNOSTICS_DEGRADED_SPOOL_MAX_BYTES,
        ),
      }),
      persistent: false,
      unavailableReason: error?.name || persistent.failureReason || "indexeddb_unavailable",
    };
  }
}
