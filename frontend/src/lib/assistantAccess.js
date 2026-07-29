export function canAccessAssistant(user) {
  return Boolean(
    user
    && (
      user.role === "admin"
      || user.assistant_beta_enabled === true
    )
  );
}


export function resolveAssistantNavigationTarget(user) {
  if (!canAccessAssistant(user)) {
    return null;
  }
  return user.role === "admin" ? "/admin/assistant" : "/assistant";
}


export function classifyPrimaryNavigationRoute(pathname, user) {
  const path = typeof pathname === "string" ? pathname : "";
  if (
    path === "/library"
    || path === "/library/local"
    || path === "/library/cloud"
    || /^\/library\/\d+$/.test(path)
  ) {
    return "library";
  }
  if (path === "/settings") {
    return "settings";
  }
  if (
    canAccessAssistant(user)
    && (
      path === "/assistant"
      || path === "/admin/assistant"
      || path.startsWith("/admin/assistant/")
      || /^\/attachments\/\d+\/view$/.test(path)
    )
  ) {
    return "assistant";
  }
  if (
    user?.role === "admin"
    && (path === "/admin" || path.startsWith("/admin/"))
  ) {
    return "admin";
  }
  return null;
}


export function sanitizeAssistantContextPath(fromPath) {
  if (
    typeof fromPath !== "string"
    || !fromPath.startsWith("/")
    || fromPath.startsWith("//")
  ) {
    return "";
  }
  let pathname;
  try {
    pathname = new URL(fromPath, "https://elvern.invalid").pathname;
  } catch {
    return "";
  }
  if (/^\/library\/\d+$/.test(pathname)) {
    return pathname;
  }
  if (pathname === "/library/local" || pathname === "/library/cloud") {
    return pathname;
  }
  if (pathname === "/library") {
    return "/library";
  }
  if (pathname === "/settings") {
    return "/settings";
  }
  if (pathname === "/install") {
    return "/install";
  }
  return "";
}


export function deriveAssistantContext(fromPath) {
  const path = sanitizeAssistantContextPath(fromPath);
  const empty = {
    page_context: null,
    source_context: null,
    related_entity_type: null,
    related_entity_id: null,
  };
  if (!path || path === "/assistant") {
    return empty;
  }
  const detailMatch = path.match(/^\/library\/(\d+)$/);
  if (detailMatch) {
    return {
      page_context: path,
      source_context: "library_detail",
      related_entity_type: "media_item",
      related_entity_id: detailMatch[1],
    };
  }
  if (path === "/library/local") {
    return { ...empty, page_context: path, source_context: "library_local" };
  }
  if (path === "/library/cloud") {
    return { ...empty, page_context: path, source_context: "library_cloud" };
  }
  if (path === "/library") {
    return { ...empty, page_context: path, source_context: "library" };
  }
  if (path === "/settings") {
    return { ...empty, page_context: path, source_context: "settings" };
  }
  if (path === "/install") {
    return { ...empty, page_context: "/settings", source_context: "settings_install" };
  }
  return empty;
}
