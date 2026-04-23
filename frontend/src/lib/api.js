function joinMessages(values) {
  const messages = values
    .map((value) => (typeof value === "string" ? value.trim() : ""))
    .filter(Boolean);
  return messages.length > 0 ? messages.join("; ") : null;
}

function extractDetailMessage(detail) {
  if (typeof detail === "string") {
    return detail.trim() || null;
  }
  if (Array.isArray(detail)) {
    return joinMessages(detail.map((entry) => {
      if (typeof entry === "string") {
        return entry;
      }
      if (entry && typeof entry === "object") {
        const field = Array.isArray(entry.loc)
          ? entry.loc.filter((part) => typeof part === "string" || typeof part === "number").join(".")
          : "";
        const message = typeof entry.msg === "string" ? entry.msg.trim() : "";
        if (field && message) {
          return `${field}: ${message}`;
        }
        return message || (typeof entry.message === "string" ? entry.message : "");
      }
      return "";
    }));
  }
  if (detail && typeof detail === "object") {
    return joinMessages([
      typeof detail.message === "string" ? detail.message : "",
      typeof detail.title === "string" ? detail.title : "",
      typeof detail.error === "string" ? detail.error : "",
      typeof detail.reason === "string" ? detail.reason : "",
    ]);
  }
  return null;
}

export function extractApiErrorMessage(payload, fallback = "Request failed") {
  const detail =
    typeof payload === "object" && payload && "detail" in payload
      ? payload.detail
      : null;
  const detailMessage = extractDetailMessage(detail);
  if (detailMessage) {
    return detailMessage;
  }
  if (typeof payload === "string") {
    const trimmed = payload.trim();
    if (trimmed) {
      return trimmed;
    }
    return fallback;
  }
  if (payload && typeof payload === "object") {
    return joinMessages([
      typeof payload.message === "string" ? payload.message : "",
      typeof payload.error === "string" ? payload.error : "",
      typeof payload.title === "string" ? payload.title : "",
    ]) || fallback;
  }
  return fallback;
}

export async function apiRequest(path, options = {}) {
  const { data, headers = {}, signal, method = "GET" } = options;
  const requestHeaders = { ...headers };

  let body;
  if (data !== undefined) {
    if (typeof FormData !== "undefined" && data instanceof FormData) {
      body = data;
    } else {
      requestHeaders["Content-Type"] = "application/json";
      body = JSON.stringify(data);
    }
  }

  const response = await fetch(path, {
    method,
    headers: requestHeaders,
    body,
    signal,
    credentials: "include",
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload && "detail" in payload
        ? payload.detail
        : null;
    const message = extractApiErrorMessage(payload);
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    error.detail = detail;
    throw error;
  }

  return payload;
}
