export function readPersistedPanelState(storageKey, allowedValues, fallbackValue) {
  if (typeof window === "undefined") {
    return fallbackValue;
  }
  try {
    const storedValue = window.localStorage.getItem(storageKey);
    return allowedValues.includes(storedValue) ? storedValue : fallbackValue;
  } catch {
    return fallbackValue;
  }
}

export function writePersistedPanelState(storageKey, value, allowedValues) {
  if (typeof window === "undefined" || !allowedValues.includes(value)) {
    return;
  }
  try {
    window.localStorage.setItem(storageKey, value);
  } catch {
    // Local persistence is a convenience only; navigation should still work.
  }
}
