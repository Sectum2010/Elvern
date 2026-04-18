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
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object"
          ? detail.message || detail.title || "Request failed"
          : "Request failed";
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    error.detail = detail;
    throw error;
  }

  return payload;
}
