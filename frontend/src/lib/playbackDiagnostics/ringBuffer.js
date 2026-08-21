function defaultTimestamp(value) {
  const candidate = Number(
    value?.sample_monotonic_ms
    ?? value?.callback_monotonic_ms
    ?? value?.timestamp_ms,
  );
  return Number.isFinite(candidate) ? candidate : null;
}

export class DiagnosticRingBuffer {
  constructor(maxEntries, { windowMs = null, timestamp = defaultTimestamp } = {}) {
    this.maxEntries = Math.max(1, Number(maxEntries) || 1);
    this.windowMs = Number.isFinite(Number(windowMs)) && Number(windowMs) > 0
      ? Number(windowMs)
      : null;
    this.timestamp = timestamp;
    this.buffer = new Array(this.maxEntries);
    this.firstSequence = 1;
    this.nextSequence = 1;
    this.latestTimestampMs = null;
    this.cursors = new Set();
  }

  push(value) {
    const timestampMs = this.timestamp(value);
    const sequence = this.nextSequence;
    const slot = (sequence - 1) % this.maxEntries;
    const overwritten = this.buffer[slot];
    if (overwritten) {
      this.cursors.forEach((cursor) => cursor.preserve(overwritten));
    }
    this.buffer[slot] = { sequence, value };
    this.nextSequence += 1;
    this.firstSequence = Math.max(this.firstSequence, this.nextSequence - this.maxEntries);
    if (timestampMs != null) this.latestTimestampMs = timestampMs;
    this.evictExpired(timestampMs);
  }

  evictExpired(latestTimestampMs = this.latestTimestampMs) {
    if (this.windowMs == null || latestTimestampMs == null) return;
    const cutoff = latestTimestampMs - this.windowMs;
    while (this.length > 1) {
      const oldestRecord = this.recordAt(this.firstSequence);
      const oldestTimestamp = this.timestamp(oldestRecord?.value);
      if (oldestTimestamp == null || oldestTimestamp >= cutoff) break;
      this.firstSequence += 1;
    }
  }

  snapshot() {
    const result = new Array(this.length);
    for (let index = 0; index < this.length; index += 1) {
      const entry = this.recordAt(this.firstSequence + index)?.value;
      result[index] = entry && typeof entry === "object" ? { ...entry } : entry;
    }
    return result;
  }

  recordAt(sequence) {
    const record = this.buffer[(sequence - 1) % this.maxEntries];
    return record?.sequence === sequence ? record : null;
  }

  createSnapshotCursor() {
    const cursor = new DiagnosticRingCursor(
      this,
      this.firstSequence,
      this.nextSequence - 1,
    );
    this.cursors.add(cursor);
    return cursor;
  }

  releaseCursor(cursor) {
    this.cursors.delete(cursor);
  }

  clear() {
    this.buffer.fill(undefined);
    this.firstSequence = 1;
    this.nextSequence = 1;
    this.latestTimestampMs = null;
    this.cursors.forEach((cursor) => cursor.cancel());
    this.cursors.clear();
  }

  get length() {
    return Math.max(0, this.nextSequence - this.firstSequence);
  }

  get complete() {
    if (!this.length) return false;
    if (this.windowMs == null) return this.length >= this.maxEntries;
    const oldestTimestamp = this.timestamp(this.recordAt(this.firstSequence)?.value);
    return oldestTimestamp != null
      && this.latestTimestampMs != null
      && this.latestTimestampMs - oldestTimestamp >= this.windowMs;
  }
}

class DiagnosticRingCursor {
  constructor(ring, firstSequence, lastSequence) {
    this.ring = ring;
    this.currentSequence = firstSequence;
    this.lastSequence = lastSequence;
    this.preserved = new Map();
    this.cancelled = false;
  }

  preserve(record) {
    if (
      record.sequence >= this.currentSequence
      && record.sequence <= this.lastSequence
      && !this.preserved.has(record.sequence)
    ) {
      this.preserved.set(record.sequence, record.value);
    }
  }

  read(maxEntries) {
    if (this.cancelled) return [];
    const result = [];
    const limit = Math.max(1, Number(maxEntries) || 1);
    while (this.currentSequence <= this.lastSequence && result.length < limit) {
      const sequence = this.currentSequence;
      const value = this.preserved.has(sequence)
        ? this.preserved.get(sequence)
        : this.ring.recordAt(sequence)?.value;
      this.preserved.delete(sequence);
      this.currentSequence += 1;
      if (value !== undefined) result.push(value);
    }
    if (this.done) this.release();
    return result;
  }

  release() {
    this.ring?.releaseCursor(this);
    this.ring = null;
    this.preserved.clear();
  }

  cancel() {
    this.cancelled = true;
    this.release();
  }

  get done() {
    return this.cancelled || this.currentSequence > this.lastSequence;
  }
}
