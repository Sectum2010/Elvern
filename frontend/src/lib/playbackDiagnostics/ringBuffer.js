export class DiagnosticRingBuffer {
  constructor(maxEntries) {
    this.maxEntries = Math.max(1, Number(maxEntries) || 1);
    this.entries = [];
  }

  push(value) {
    this.entries.push(value);
    if (this.entries.length > this.maxEntries) {
      this.entries.splice(0, this.entries.length - this.maxEntries);
    }
  }

  snapshot() {
    return this.entries.map((entry) => ({ ...entry }));
  }

  clear() {
    this.entries.length = 0;
  }

  get complete() {
    return this.entries.length >= this.maxEntries;
  }
}
