const URL_PREFIX_PATTERN = /^[a-hjkmnp-z2-9]{8,24}$/;
const SPA_TOP_LEVEL_PATHS = new Set([
  "admin",
  "assistant",
  "attachments",
  "desktop",
  "forgot-password",
  "install",
  "library",
  "login",
  "new-user",
  "settings",
  "setup",
]);


function normalizeBasename(value = "/") {
  const basename = String(value || "/").trim().replace(/\/+$/g, "") || "/";
  return basename.startsWith("/") ? basename : `/${basename}`;
}


function splitPathFromBasename(pathname, basename) {
  if (basename === "/") {
    return pathname;
  }
  if (pathname === basename || pathname === `${basename}/`) {
    return "/";
  }
  if (!pathname.startsWith(`${basename}/`)) {
    return null;
  }
  return pathname.slice(basename.length) || "/";
}


function isSpaRelativePath(pathname) {
  const firstSegment = String(pathname || "").split("/").filter(Boolean)[0] || "";
  return SPA_TOP_LEVEL_PATHS.has(firstSegment);
}


export function detectSpaBasename(pathname = globalThis.window?.location?.pathname || "/") {
  const prefixCandidate = String(pathname || "").split("/").filter(Boolean)[0] || "";
  return URL_PREFIX_PATTERN.test(prefixCandidate) ? `/${prefixCandidate}` : "/";
}


export function canonicalizeSpaPathname(pathname = "/", { basename = "/" } = {}) {
  const sourcePath = String(pathname || "/");
  const normalizedBasename = normalizeBasename(basename);
  const relativePath = splitPathFromBasename(sourcePath, normalizedBasename);
  if (relativePath === null || relativePath === "/" || !isSpaRelativePath(relativePath)) {
    return sourcePath;
  }
  const canonicalRelativePath = relativePath.replace(/\/+$/g, "") || "/";
  if (normalizedBasename === "/") {
    return canonicalRelativePath;
  }
  return `${normalizedBasename}${canonicalRelativePath}`;
}


export function canonicalizeBrowserLocation(location, { basename = "/" } = {}) {
  const pathname = canonicalizeSpaPathname(location?.pathname || "/", { basename });
  const search = typeof location?.search === "string" ? location.search : "";
  const hash = typeof location?.hash === "string" ? location.hash : "";
  return {
    pathname,
    search,
    hash,
    href: `${pathname}${search}${hash}`,
    changed: pathname !== (location?.pathname || "/"),
  };
}


export function applyInitialSpaCanonicalization(browserWindow = globalThis.window, { basename } = {}) {
  if (!browserWindow?.history?.replaceState || !browserWindow?.location) {
    return false;
  }
  const resolvedBasename = basename || detectSpaBasename(browserWindow.location.pathname);
  const canonical = canonicalizeBrowserLocation(browserWindow.location, { basename: resolvedBasename });
  if (!canonical.changed) {
    return false;
  }
  browserWindow.history.replaceState(browserWindow.history.state, "", canonical.href);
  return true;
}


export function classifyLibrarySpaPath(pathname = "/", { basename = "/" } = {}) {
  const normalizedBasename = normalizeBasename(basename);
  const canonicalPath = canonicalizeSpaPathname(pathname, { basename: normalizedBasename });
  const libraryPath = splitPathFromBasename(canonicalPath, normalizedBasename) || canonicalPath;
  if (libraryPath === "/library") {
    return { kind: "root", pathname: canonicalPath };
  }
  if (libraryPath === "/library/local" || libraryPath === "/library/cloud") {
    return { kind: "source", pathname: canonicalPath };
  }
  if (/^\/library\/\d+$/.test(libraryPath)) {
    return { kind: "detail", pathname: canonicalPath };
  }
  return { kind: "other", pathname: canonicalPath };
}
