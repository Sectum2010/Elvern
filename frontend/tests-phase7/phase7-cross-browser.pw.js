import { chromium, expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";


const PUBLIC_PROBES = [
  "https://www.cloudflare.com/cdn-cgi/trace",
  "https://api64.ipify.org/",
  "https://httpbin.org/status/204",
];
const LOCAL_FAULT_ORIGINS = new Set();
const NETWORK_GUARD_STATE = new WeakMap();
const MERIDIAN_DEMO_PATH = process.env.ELVERN_MERIDIAN_DEMO_PATH || "";


function asDataUrl(mimeType, contents) {
  return `data:${mimeType};base64,${Buffer.from(contents).toString("base64")}`;
}


async function installLocalMeridianDemoRuntime(context) {
  const reactUrl = "https://unpkg.com/react@18.3.1/umd/react.production.min.js";
  const reactDomUrl = "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js";
  await context.addInitScript(({ resources }) => {
    window.__resources = { ...(window.__resources || {}), ...resources };
  }, {
    resources: {
      [reactUrl]: asDataUrl(
        "text/javascript",
        readFileSync(resolve(process.cwd(), "node_modules/react/umd/react.production.min.js")),
      ),
      [reactDomUrl]: asDataUrl(
        "text/javascript",
        readFileSync(resolve(process.cwd(), "node_modules/react-dom/umd/react-dom.production.min.js")),
      ),
    },
  });
  const fontFaces = [
    ["Sora", "sora-latin-variable.woff2"],
    ["Archivo", "archivo-latin-variable.woff2"],
    ["Space Grotesk", "space-grotesk-latin-variable.woff2"],
  ].map(([family, filename]) => {
    const font = readFileSync(resolve(process.cwd(), "src/assets/fonts/control-center", filename));
    return `@font-face{font-family:'${family}';font-style:normal;font-weight:100 900;font-display:block;src:url(${asDataUrl("font/woff2", font)}) format('woff2')}`;
  }).join("\n");
  await context.route("https://fonts.googleapis.com/**", (route) => route.fulfill({
    status: 200,
    contentType: "text/css",
    body: fontFaces,
  }));
}


function requireMeridianDemoPath() {
  if (!MERIDIAN_DEMO_PATH || !existsSync(MERIDIAN_DEMO_PATH)) {
    throw new Error("ELVERN_MERIDIAN_DEMO_PATH must point to the private local Meridian demo.");
  }
}


async function captureControlCenterVisual(page, testInfo, name) {
  const screenshot = await page.screenshot({ fullPage: true });
  await testInfo.attach(name, {
    body: screenshot,
    contentType: "image/png",
  });
  const outputDirectory = process.env.ELVERN_CONTROL_CENTER_SCREENSHOT_DIR;
  if (outputDirectory) {
    await page.screenshot({
      fullPage: true,
      path: `${outputDirectory}/${testInfo.project.name}-${name}.png`,
    });
  }
}


async function comparePngPixels(page, reference, actual) {
  return page.evaluate(async ({ referenceBase64, actualBase64 }) => {
    function loadImage(base64) {
      return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = reject;
        image.src = `data:image/png;base64,${base64}`;
      });
    }
    const [referenceImage, actualImage] = await Promise.all([
      loadImage(referenceBase64),
      loadImage(actualBase64),
    ]);
    if (referenceImage.width !== actualImage.width || referenceImage.height !== actualImage.height) {
      return {
        dimensions_match: false,
        reference: [referenceImage.width, referenceImage.height],
        actual: [actualImage.width, actualImage.height],
      };
    }
    const canvas = document.createElement("canvas");
    canvas.width = referenceImage.width;
    canvas.height = referenceImage.height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(referenceImage, 0, 0);
    const referencePixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(actualImage, 0, 0);
    const actualPixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let changedPixels = 0;
    let channelDeltaTotal = 0;
    const diffPixels = context.createImageData(canvas.width, canvas.height);
    for (let index = 0; index < referencePixels.length; index += 4) {
      const delta = (
        Math.abs(referencePixels[index] - actualPixels[index])
        + Math.abs(referencePixels[index + 1] - actualPixels[index + 1])
        + Math.abs(referencePixels[index + 2] - actualPixels[index + 2])
      ) / 3;
      channelDeltaTotal += delta;
      if (delta > 32) {
        changedPixels += 1;
        diffPixels.data[index] = 220;
        diffPixels.data[index + 1] = 52;
        diffPixels.data[index + 2] = 68;
        diffPixels.data[index + 3] = 255;
      } else {
        const referenceLuminance = Math.round((
          referencePixels[index]
          + referencePixels[index + 1]
          + referencePixels[index + 2]
        ) / 3);
        const muted = Math.round(referenceLuminance * 0.18);
        diffPixels.data[index] = muted;
        diffPixels.data[index + 1] = muted;
        diffPixels.data[index + 2] = muted;
        diffPixels.data[index + 3] = 255;
      }
    }
    context.putImageData(diffPixels, 0, 0);
    const pixelCount = referencePixels.length / 4;
    return {
      dimensions_match: true,
      width: canvas.width,
      height: canvas.height,
      changed_pixel_ratio: changedPixels / pixelCount,
      mean_channel_delta: channelDeltaTotal / pixelCount,
      diff_base64: canvas.toDataURL("image/png").split(",")[1],
    };
  }, {
    referenceBase64: reference.toString("base64"),
    actualBase64: actual.toString("base64"),
  });
}


const MERIDIAN_STYLE_PROPERTIES = [
  "fontFamily",
  "fontSize",
  "lineHeight",
  "fontWeight",
  "color",
  "backgroundColor",
  "borderTopColor",
  "borderTopWidth",
  "borderRadius",
  "paddingTop",
  "paddingRight",
  "paddingBottom",
  "paddingLeft",
  "rowGap",
  "columnGap",
  "transitionDuration",
  "transitionTimingFunction",
];


function geometryDelta(reference, actual) {
  if (!reference || !actual) return null;
  return Object.fromEntries(["x", "y", "width", "height"].map((key) => [
    key,
    Math.abs(Number(reference[key]) - Number(actual[key])),
  ]));
}


function styleDifferences(reference, actual) {
  if (!reference || !actual) return null;
  return Object.fromEntries(MERIDIAN_STYLE_PROPERTIES.flatMap((property) => (
    reference[property] === actual[property]
      ? []
      : [[property, { reference: reference[property], actual: actual[property] }]]
  )));
}


async function collectMeridianEvidence(page, rootSelector, kind, stateName = "") {
  return page.evaluate(({ selector, pageKind, properties, state }) => {
    const root = Array.from(document.querySelectorAll(selector)).filter((element) => {
      const rect = element.getBoundingClientRect();
      const computed = getComputedStyle(element);
      return rect.width > 0
        && rect.height > 0
        && computed.display !== "none"
        && computed.visibility !== "hidden";
    }).at(-1);
    if (!root) throw new Error(`Missing Meridian ${pageKind} root ${selector}`);
    const rootRect = root.getBoundingClientRect();
    const box = (element) => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        x: Number((rect.x - rootRect.x).toFixed(3)),
        y: Number((rect.y - rootRect.y).toFixed(3)),
        width: Number(rect.width.toFixed(3)),
        height: Number(rect.height.toFixed(3)),
      };
    };
    const styles = (element) => {
      if (!element) return null;
      const computed = getComputedStyle(element);
      return Object.fromEntries(properties.map((property) => [property, computed[property]]));
    };
    const findDemoCard = () => Array.from(root.querySelectorAll("div")).find((element) => {
      const computed = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return computed.borderRadius === "14px" && computed.borderTopWidth === "1px" && rect.width > 500;
    }) || null;
    const findDemoControl = () => Array.from(root.querySelectorAll("div")).find((element) => {
      const computed = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return computed.cursor === "pointer" && rect.width >= 50 && rect.height >= 25 && rect.height <= 45;
    }) || null;
    const findDemoRail = () => Array.from(root.querySelectorAll("div")).find((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width >= 295
        && rect.width <= 305
        && rect.height >= rootRect.height - 10
        && rect.left >= rootRect.right - 310
        && rect.left < rootRect.right - 1;
    }) || null;
    const productionRail = root.querySelector(".control-center-status-rail");
    const visibleProductionRail = productionRail?.getBoundingClientRect().width > 1
      ? productionRail
      : null;
    const productionCardSelector = state.startsWith("admin-overview")
      || state.startsWith("admin-exposure")
      || state.startsWith("admin-system-status")
      || state.startsWith("admin-mixed-theme")
      || state.startsWith("admin-dark-theme")
      ? ".meridian-posture-card"
      : ".meridian-card";
    const visibleCardLandmarks = pageKind === "demo"
      ? Array.from(root.querySelectorAll("div")).filter((element) => {
        const computed = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return computed.borderRadius === "14px"
          && computed.borderTopWidth === "1px"
          && rect.width >= 300
          && rect.height > 30;
      })
      : Array.from(root.querySelectorAll(".meridian-card")).filter((element) => {
        const rect = element.getBoundingClientRect();
        const computed = getComputedStyle(element);
        return rect.width >= 300
          && rect.height > 30
          && computed.display !== "none"
          && computed.visibility !== "hidden";
      });
    const demoSidebar = root.children[0] || null;
    const demoWorkspace = root.children[1] || null;
    const demoContent = demoWorkspace?.firstElementChild || null;
    const nodes = pageKind === "demo" ? {
      root,
      sidebar: demoSidebar,
      workspace: demoWorkspace,
      content: demoContent,
      heading: demoContent?.children[0] || null,
      card: findDemoCard(),
      control: findDemoControl(),
      rail: findDemoRail(),
    } : {
      root,
      sidebar: root.querySelector(".meridian-sidebar"),
      workspace: root.querySelector(".meridian-workspace"),
      content: root.querySelector(".meridian-workspace__inner"),
      heading: root.querySelector(".meridian-page-header h1"),
      card: root.querySelector(productionCardSelector),
      control: root.querySelector(".meridian-sidebar__back"),
      rail: visibleProductionRail,
    };
    return {
      geometry: Object.fromEntries(Object.entries(nodes).map(([name, element]) => [name, box(element)])),
      computed_styles: Object.fromEntries(Object.entries(nodes).map(([name, element]) => [name, styles(element)])),
      card_landmarks: visibleCardLandmarks.map((element) => ({
        box: box(element),
        text: String(element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120),
      })),
      layout_landmarks: pageKind === "production"
        ? [
          ".meridian-content",
          ".meridian-admin-view",
          ".meridian-admin-stack",
          ".meridian-overview",
        ].map((layoutSelector) => ({
          box: box(root.querySelector(layoutSelector)),
          selector: layoutSelector,
        }))
        : Array.from(demoContent?.children || []).map((element, index) => ({
          box: box(element),
          selector: `demo-content-child-${index}`,
        })),
    };
  }, {
    selector: rootSelector,
    pageKind: kind,
    properties: MERIDIAN_STYLE_PROPERTIES,
    state: stateName,
  });
}


async function normalizeMeridianDemoFixture(page) {
  await page.evaluate(() => {
    const visibleRoot = Array.from(document.querySelectorAll("[data-mer]")).filter((element) => {
      const rect = element.getBoundingClientRect();
      const computed = getComputedStyle(element);
      return rect.width > 0
        && rect.height > 0
        && computed.display !== "none"
        && computed.visibility !== "hidden";
    }).at(-1);
    if (!visibleRoot) throw new Error("Missing visible Meridian demo root for fixture normalization.");
    const exactNames = new Map([
      ["admin", "demo-admin"],
      ["caleb", "demo-caleb"],
      ["helen", "demo-helen"],
      ["matthew", "demo-matthew"],
      ["hollender", "demo-hollender"],
      ["jazz", "demo-jazz"],
      ["codex1", "demo-codex1"],
      ["codex2", "demo-codex2"],
      ["codex3", "demo-codex3"],
      ["codex4", "demo-codex4"],
      ["codex5", "demo-codex5"],
      ["codex6", "demo-codex6"],
      ["quinn", "demo-quinn"],
      ["renee", "demo-renee"],
      ["AD", "DA"],
      ["CA", "DC"],
      ["HE", "DH"],
      ["MA", "DM"],
      ["HO", "DH"],
      ["JA", "DJ"],
    ]);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const trimmed = node.nodeValue.trim();
      if (exactNames.has(trimmed)) {
        node.nodeValue = node.nodeValue.replace(trimmed, exactNames.get(trimmed));
      }
      if (
        node.parentElement?.tagName === "B"
        && node.parentElement.parentElement?.textContent.includes("Connected as")
      ) {
        node.nodeValue = "demo@example.test";
      }
      node.nodeValue = node.nodeValue
        .replace(/\b[a-z0-9-]+\.tail[a-z0-9-]+\.ts\.net\b/gi, "elvern-test.example")
        .replace(/Elvern\s+\xb7\s+[^\s]+/g, "Elvern \xb7 elvern-test.example")
        .replace(/Connected as [^\xb7.]+(?=\s*\xb7)/g, "Connected as demo@example.test")
        .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}…[0-9a-f]{8}\b/gi, "00000000-0000-4000-8000-000000000001")
        .replace(/\/home\/[^/\s]+\/Videos\/Elvern Posters\/Movie Posters/g, "/srv/elvern-test/posters")
        .replace(/\/home\/[^/\s]+\/Videos\/Posters/g, "/srv/elvern-test/posters")
        .replace(/\/home\/[^/\s]+\/Videos/g, "/srv/elvern-test/media")
        .replaceAll("127.0.0.1", "192.0.2.10")
        .replace(/\b100\.(?:\d{1,3}\.){2}\d{1,3}\b/g, "192.0.2.20")
        .replace(/(^| · )admin(?= · |$)/g, "$1demo-admin")
        .replace(/(^| · )caleb(?= · |$)/g, "$1demo-caleb")
        .replace(/(^| · )helen(?= · |$)/g, "$1demo-helen")
        .replace(/(^| · )matthew(?= · |$)/g, "$1demo-matthew");
    }
    for (const field of visibleRoot.querySelectorAll("input, textarea")) {
      const normalized = String(field.value || "")
        .replace(/https:\/\/[a-z0-9-]+\.tail[a-z0-9-]+\.ts\.net/gi, "https://elvern-test.example")
        .replace(/\b\d+-[a-z0-9]+\.apps\.googleusercontent\.com\b/gi, "demo-client.apps.googleusercontent.com")
        .replace(/\/home\/[^/\s]+\/Videos\/Elvern Posters\/Movie Posters/g, "/srv/elvern-test/posters")
        .replace(/\/home\/[^/\s]+\/Videos\/Posters/g, "/srv/elvern-test/posters")
        .replace(/\/home\/[^/\s]+\/Videos/g, "/srv/elvern-test/media");
      if (normalized !== field.value) {
        const descriptor = Object.getOwnPropertyDescriptor(
          field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
          "value",
        );
        descriptor?.set?.call(field, normalized);
      }
    }
    for (const [label, value] of [["USERS", "8"], ["TITLES INDEXED", "110"]]) {
      const labelNode = Array.from(visibleRoot.querySelectorAll("div")).find((element) => (
        element.children.length === 0 && element.textContent.trim() === label
      ));
      if (labelNode?.parentElement?.children[1]) {
        labelNode.parentElement.children[1].textContent = value;
      }
    }
    const showAllUsers = Array.from(visibleRoot.querySelectorAll("div")).find((element) => (
      element.children.length === 0 && element.textContent.trim() === "Show all 14 users"
    ));
    if (showAllUsers) showAllUsers.textContent = "Show all 8 users";
    for (const [from, to] of [
      ["/dvmnbcbw/", "/elvern-test/"],
      ["May 9, 2026, 11:22 AM (83 days ago)", "May 9, 2026, 4:22 AM (83 days ago)"],
    ]) {
      const target = Array.from(visibleRoot.querySelectorAll("div")).find((element) => (
        element.children.length === 0 && element.textContent.trim() === from
      ));
      if (target) target.textContent = to;
    }
  });
}


async function normalizeMeridianProductionFixture(page) {
  await page.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      walker.currentNode.nodeValue = walker.currentNode.nodeValue
        .replaceAll("127.0.0.1", "elvern-test.example")
        .replaceAll("phase7-browser", "demo-admin");
    }
  });
}


async function writeMeridianParityArtifacts({
  actual,
  comparison,
  demoEvidence,
  name,
  productionEvidence,
  reference,
  testInfo,
}) {
  const outputRoot = process.env.ELVERN_CONTROL_CENTER_SCREENSHOT_DIR
    || resolve(process.cwd(), "../tmp/meridian-parity");
  const outputDirectory = resolve(outputRoot, name);
  mkdirSync(outputDirectory, { recursive: true });
  const geometryReport = {
    state: name,
    reference: demoEvidence.geometry,
    actual: productionEvidence.geometry,
    reference_card_landmarks: demoEvidence.card_landmarks,
    actual_card_landmarks: productionEvidence.card_landmarks,
    reference_layout_landmarks: demoEvidence.layout_landmarks,
    actual_layout_landmarks: productionEvidence.layout_landmarks,
    delta: Object.fromEntries(Object.keys(demoEvidence.geometry).map((key) => [
      key,
      geometryDelta(demoEvidence.geometry[key], productionEvidence.geometry[key]),
    ])),
  };
  const computedStyleReport = {
    state: name,
    properties: MERIDIAN_STYLE_PROPERTIES,
    reference: demoEvidence.computed_styles,
    actual: productionEvidence.computed_styles,
    differences: Object.fromEntries(Object.keys(demoEvidence.computed_styles).map((key) => [
      key,
      styleDifferences(demoEvidence.computed_styles[key], productionEvidence.computed_styles[key]),
    ])),
  };
  const diff = Buffer.from(comparison.diff_base64 || "", "base64");
  writeFileSync(resolve(outputDirectory, "demo-reference.png"), reference);
  writeFileSync(resolve(outputDirectory, "production-actual.png"), actual);
  writeFileSync(resolve(outputDirectory, "pixel-diff.png"), diff);
  writeFileSync(resolve(outputDirectory, "geometry-report.json"), `${JSON.stringify(geometryReport, null, 2)}\n`, "utf8");
  writeFileSync(resolve(outputDirectory, "computed-style-report.json"), `${JSON.stringify(computedStyleReport, null, 2)}\n`, "utf8");
  writeFileSync(
    resolve(outputDirectory, "pixel-report.json"),
    `${JSON.stringify({ ...comparison, diff_base64: undefined }, null, 2)}\n`,
    "utf8",
  );
  for (const [attachmentName, body, contentType] of [
    [`${name}-demo-reference`, reference, "image/png"],
    [`${name}-production-actual`, actual, "image/png"],
    [`${name}-pixel-diff`, diff, "image/png"],
    [`${name}-geometry-report`, Buffer.from(JSON.stringify(geometryReport, null, 2)), "application/json"],
    [`${name}-computed-style-report`, Buffer.from(JSON.stringify(computedStyleReport, null, 2)), "application/json"],
  ]) {
    await testInfo.attach(attachmentName, { body, contentType });
  }
  return { computedStyleReport, geometryReport, outputDirectory };
}


async function proxyControl(path, payload) {
  const controlOrigin = process.env.ELVERN_PHASE7_NETWORK_PROXY_CONTROL;
  const controlToken = process.env.ELVERN_PHASE7_NETWORK_PROXY_CONTROL_TOKEN;
  if (!controlOrigin || !controlToken) {
    throw new Error("The browser network authority control endpoint is unavailable.");
  }
  const response = await fetch(`${controlOrigin}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-Elvern-Network-Guard-Token": controlToken,
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Browser network authority control failed with ${response.status}.`);
  }
}


function classifyTestNetworkRequest(rawUrl, allowedOrigins) {
  const url = new URL(rawUrl);
  if (!["http:", "https:", "ws:", "wss:"].includes(url.protocol)) {
    return { allowed: true };
  }
  if (allowedOrigins.has(url.origin)) {
    return { allowed: true };
  }
  return {
    allowed: false,
    diagnostic: {
      scheme: url.protocol.replace(/:$/, ""),
      origin: url.origin,
      pathname_hash: createHash("sha256").update(url.pathname).digest("hex").slice(0, 12),
    },
  };
}


async function installExternalNetworkGuard(context, baseURL) {
  const state = {
    externalRequests: [],
    interceptedExternalUrls: new Set(),
  };
  const appOrigin = new URL(baseURL).origin;
  NETWORK_GUARD_STATE.set(context, state);
  await context.route("**/*", async (route) => {
    const allowedOrigins = new Set([appOrigin, ...LOCAL_FAULT_ORIGINS]);
    const result = classifyTestNetworkRequest(route.request().url(), allowedOrigins);
    if (result.allowed) {
      await route.fallback();
      return;
    }
    state.externalRequests.push(result.diagnostic);
    await route.abort("blockedbyclient");
  });
  context.on("request", (request) => {
    if (
      PUBLIC_PROBES.includes(request.url())
      || state.interceptedExternalUrls.has(request.url())
    ) {
      return;
    }
    const result = classifyTestNetworkRequest(
      request.url(),
      new Set([appOrigin, ...LOCAL_FAULT_ORIGINS]),
    );
    if (!result.allowed && !state.externalRequests.some(
      (entry) => entry.origin === result.diagnostic.origin
        && entry.pathname_hash === result.diagnostic.pathname_hash
    )) {
      state.externalRequests.push(result.diagnostic);
    }
  });
}


async function registerInterceptedExternalFixture(context, url, response) {
  const state = NETWORK_GUARD_STATE.get(context);
  if (!state) {
    throw new Error("External network guard must be installed before fixtures.");
  }
  state.interceptedExternalUrls.add(url);
  await context.route(url, (route) => route.fulfill(response));
}


function item(id, title, sourceKind = "local") {
  return {
    id,
    title,
    year: 2026,
    poster_url: null,
    source_kind: sourceKind,
    quality_rank: {
      key: "silver",
      label: "Silver",
      score: 7,
      description: "Good quality, highly watchable.",
      detected: ["1080p"],
      tooltip: "Good quality, highly watchable. Detected: 1080p.",
    },
    duration_seconds: 7200,
    progress_seconds: 0,
    progress_duration_seconds: 7200,
    completed: false,
  };
}


const ITEMS = [
  item(1, "Phase Seven Alpha", "local"),
  item(2, "Phase Seven Beta", "cloud"),
];


function opaqueToken(sequence, namespace = 0) {
  return (BigInt(namespace) * 1_000_000n + BigInt(sequence)).toString(16).padStart(64, "0");
}


function v2Summary(source = "all", state = {}) {
  const sourceItems = source === "all" ? ITEMS : ITEMS.filter((entry) => entry.source_kind === source);
  const visible = sourceItems.map((entry) => ({
    ...entry,
    title: `${entry.title}${state.titleSuffix || ""}`,
    poster_url: state.posterRecoveryEnabled
      ? `/api/posters/${entry.id}?cache_token=${opaqueToken(entry.id, 9)}`
      : entry.poster_url,
    ...(entry.id === 1 ? {
      progress_seconds: Number(state.progressSeconds || 0),
      progress_duration_seconds: Number(state.progressDuration || 7200),
      completed: Boolean(state.completed),
    } : {}),
  }));
  const continueWatchingItemIds = Number(state.progressSeconds || 0) > 0 && !state.completed
    ? visible.filter((entry) => entry.id === 1).map((entry) => entry.id)
    : [];
  return {
    schema_version: "library-summary-v2",
    revision: (source === "local" ? "b" : source === "cloud" ? "c" : "a").repeat(64),
    view: {
      category: "movies",
      source,
      genres: [],
      qualities: [],
      genre: null,
      quality: "all",
      sort: "smart",
    },
    items_by_id: Object.fromEntries(visible.map((entry) => [String(entry.id), entry])),
    sections: {
      item_ids: visible.map((entry) => entry.id),
      series_rails: [],
      cloud_series_rails: [],
      continue_watching_item_ids: continueWatchingItemIds,
      recently_added_item_ids: [],
    },
    available_genres: ["Action", "Drama", "Comedy"],
    total_items: visible.length,
    scan_in_progress: false,
  };
}


function v1SearchPayload(query) {
  const visible = ITEMS.filter((entry) => entry.title.toLowerCase().includes(query.toLowerCase()));
  return {
    items: visible.map((entry) => ({
      ...entry,
      quality_tier: "silver",
      quality_label: "Silver",
      source_label: entry.source_kind === "cloud" ? "Cloud" : "DGX",
      genres: [],
      genre_display: "Unknown",
      hidden_for_user: false,
      hidden_globally: false,
      file_size: 1024,
      width: 1920,
      height: 1080,
      video_codec: "h264",
      audio_codec: "aac",
      container: "mkv",
    })),
    series_rails: [],
    cloud_series_rails: [],
    continue_watching: [],
    recently_added: [],
    arrange: {
      source: "all",
      genres: [],
      qualities: [],
      genre: null,
      quality: "all",
      sort: "smart",
    },
    available_genres: [],
    scan_in_progress: false,
    total_items: visible.length,
  };
}


function desktopHelperStatus(state = {}) {
  return {
    device_id: state.desktopHelperDeviceId || "phase7-desktop",
    platform: "linux",
    helper_required: false,
    state: "helper_not_required",
    same_host: true,
    same_host_detection_source: "loopback_client_ip",
    last_seen_helper_version: null,
    vlc_detection_state: "installed",
    vlc_detection_path: "/usr/bin/vlc",
    vlc_detection_checked_at: "2026-07-22T00:00:00Z",
    runtime_included: true,
    latest_releases: [],
    notes: [],
    ...(state.desktopHelperStatus || {}),
  };
}


const MERIDIAN_SAFE_USER_NAMES = [
  "demo-admin",
  "demo-caleb",
  "demo-helen",
  "demo-matthew",
  "demo-hollender",
  "demo-jazz",
  "demo-codex1",
  "demo-codex2",
  "demo-codex3",
  "demo-codex4",
  "demo-codex5",
  "demo-codex6",
  "demo-quinn",
  "demo-renee",
];


function meridianSafeUsers() {
  return MERIDIAN_SAFE_USER_NAMES.map((username, index) => {
    const disabled = index >= 6 && index <= 11;
    const active = index === 0;
    return {
      id: index + 1,
      username,
      display_name: username,
      role: index === 0 ? "admin" : "standard_user",
      enabled: !disabled,
      active_sessions: active ? 1 : 0,
      last_login_at: `2026-07-${String(30 - (index % 8)).padStart(2, "0")}T${String(16 - (index % 6)).padStart(2, "0")}:25:00Z`,
      last_seen_at: active || index < 5 ? `2026-07-${String(31 - (index % 3)).padStart(2, "0")}T23:42:00Z` : null,
      last_activity_at: active || index < 5 ? `2026-07-${String(31 - (index % 3)).padStart(2, "0")}T23:42:00Z` : null,
      age_credential: 18,
      age_credential_display: "18+",
      status_label: active ? "Active now" : disabled ? "Disabled" : "Offline",
      status_color: active ? "#177A52" : disabled ? "#D64545" : "rgba(25,28,31,.3)",
      totp_enabled: index === 0,
      totp_enabled_at: index === 0 ? "2026-05-09T19:36:00Z" : null,
      totp_setup_prompt_enabled: false,
      assistant_beta_enabled: false,
    };
  });
}


function meridianSafeAuditEvents() {
  const baseEvents = [
    ["auth.login", "2026-07-30T23:25:00Z", "demo-admin"],
    ["auth.login", "2026-07-30T23:20:00Z", "demo-admin"],
    ["auth.logout", "2026-07-30T22:54:00Z", "demo-helen"],
    ["auth.login", "2026-07-30T22:53:00Z", "demo-helen"],
    ["auth.logout", "2026-07-30T07:38:00Z", "demo-admin"],
    ["admin.library.rescan", "2026-07-30T07:10:00Z", "demo-admin"],
    ["auth.login", "2026-07-30T04:34:00Z", "demo-admin"],
    ["auth.logout", "2026-07-30T00:47:00Z", "demo-admin"],
    ["auth.logout", "2026-07-29T19:08:00Z", "demo-caleb"],
    ["auth.login", "2026-07-29T19:06:00Z", "demo-caleb"],
  ];
  return Array.from({ length: 100 }, (_, index) => {
    const [action, createdAt, username] = baseEvents[index % baseEvents.length];
    return ({
    id: index + 1,
    action,
    created_at: createdAt,
    outcome: "success",
    username,
    ip_address: "192.0.2.10",
    target_type: null,
    target_id: null,
    media_item_id: null,
    details: {},
    });
  });
}


function meridianSafeExposureStatus() {
  return {
    active: {
      current_request_origin: "http://elvern-test.example",
      public_app_origin: "https://elvern-test.example",
      backend_origin: "http://192.0.2.20:8000",
      private_network_only: true,
      trusted_proxy_cidrs: ["192.0.2.10/8", "::1/128"],
      cookie_secure: false,
      url_prefix_present: true,
      maintenance_mode: { enabled: false },
      multiuser_enabled: true,
    },
    validation: { status: "ready", checks: [], errors: [], warnings: [] },
    plan: { env_suggestions: [], manual_steps: [], reverse_proxy_notes: [], activation_notes: [] },
    pending_draft: null,
    prepared_switch: null,
    finalized_profile: null,
  };
}


function meridianSafeFixtureState(overrides = {}) {
  return {
    role: "admin",
    userId: 1,
    username: "demo-admin",
    displayName: "demo-admin",
    posterAppearance: "modern",
    desktopHelperDeviceId: "00000000-0000-4000-8000-000000000001",
    desktopHelperStatus: {
      vlc_detection_state: "detection_unavailable",
      vlc_detection_path: null,
      vlc_detection_checked_at: "2026-08-01T04:00:00Z",
    },
    providerAuthStatus: {
      provider_auth_required: false,
      reconnect_required: false,
      providers: {
        google: {
          connected: true,
          account_email: "demo@example.test",
          account_display_name: "demo@example.test",
        },
      },
    },
    cloudLibraries: {
      google: {
        enabled: true,
        connected: true,
        account_email: "demo@example.test",
        account_display_name: "demo@example.test",
      },
      my_libraries: [],
      shared_libraries: [{
        id: 1,
        name: "Movies",
        item_count: 106,
        source_type: "folder",
        shared_by_admin: true,
        last_synced_at: "2026-07-30T00:10:00Z",
        hidden_for_user: false,
      }],
    },
    hiddenItems: [],
    globalHiddenItems: [
      "rileys first date-rmxtras",
      "our dads the filmmakers-rmxtras",
      "salt flats symphony-rmxtras",
      "harbor lights extended cut",
      "the last lighthouse keeper",
      "moths of the silver valley",
      "a winter in kodiak",
      "the cartographers dinner",
      "paper planes over prague",
      "midnight at the fun palace",
      "the orchard sessions",
    ].map((title, index) => ({
      id: 101 + index,
      title,
      year: null,
      hidden_at: `2026-07-${String(30 - index).padStart(2, "0")}T00:00:00Z`,
    })),
    googleDriveSetup: {
      configuration_state: "ready",
      configuration_label: "OAuth Ready",
      connected: true,
      account_email: "demo@example.test",
      account_name: "demo@example.test",
      https_origin: "https://elvern-test.example",
      client_id: "demo-client.apps.googleusercontent.com",
      client_id_configured: true,
      client_secret_configured: true,
      https_origin_configured: true,
      missing_fields: [],
      instructions: [],
    },
    mediaLibraryReference: {
      configured_value: "/srv/elvern-test/media",
      effective_value: "/srv/elvern-test/media",
      default_value: "/srv/elvern-test/media",
      configured_locations: ["/srv/elvern-test/media"],
      effective_locations: ["/srv/elvern-test/media"],
      category_summary: {
        movies: [{ name: "…/Elvern Media Root/Movies -M" }],
        tv: [{ name: "…/Elvern Media Root/TV Shows -TV" }],
        cartoon: [{ name: "…/Elvern Media Root/Cartoon -C" }],
        anime: [{ name: "…/Elvern Media Root/Anime -AN" }],
      },
      validation_rules: [
        "Leave blank to use the default library reference location: /srv/elvern-test/media",
        "Choose one or more parent folders where Elvern should look for media folders.",
        "Use one absolute Linux directory path or local file:// URI per line.",
        "System folders and Elvern data folders are not accepted — use a media folder such as /home/<user>/Videos, /mnt/media, or /srv/media.",
        "Elvern auto-discovers folders marked with -M, -TV, -AN, -C, -L, -S, and -X.",
        "Poster reference location stays manually configured below.",
      ],
    },
    posterReferenceLocation: {
      configured_value: "/srv/elvern-test/posters",
      effective_value: "/srv/elvern-test/posters",
      default_value: "/srv/elvern-test/posters",
      validation_rules: [
        "Leave blank to use the default Linux poster directory: /srv/elvern-test/posters",
        "Accepted: absolute Linux directory paths such as /srv/media/Posters",
        "Accepted: file:// URIs that resolve to an absolute local directory, such as file:///srv/media/Posters",
        "Rejected: relative paths, Windows paths, UNC/network authorities, and http/https URLs",
      ],
    },
    systemStatus: {
      total_media_items: 110,
      total_users: 8,
      scan: { running: false },
      last_scan: null,
      library: { total_items: 110 },
      security: { multiuser_enabled: true, session_ttl_hours: 8760 },
    },
    adminUsers: meridianSafeUsers().slice(0, 8),
    adminSessions: [{
      id: 1339,
      user_id: 1,
      username: "demo-admin",
      ip_address: "192.0.2.10",
      last_seen_at: "2026-08-01T06:46:00Z",
      last_activity_at: "2026-08-01T06:46:00Z",
      user_agent: "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0",
    }],
    adminAuditEvents: meridianSafeAuditEvents(),
    adminUrlPrefix: {
      prefix: "elvern-test",
      generated_at: "2026-05-09T11:22:00Z",
      days_old: 83,
      rotated_count: 0,
      rotation_reminder_due: false,
    },
    totpStatus: {
      enabled: true,
      enabled_at: "2026-05-09T19:36:00Z",
      setup_available: true,
      recovery_codes_remaining: 10,
    },
    adminInviteCodes: [],
    exposureStatus: meridianSafeExposureStatus(),
    ...overrides,
  };
}


async function installFixture(page, requests, state = {}) {
  for (const probe of PUBLIC_PROBES) {
    await page.route(probe, (route) => route.fulfill({ status: 204, body: "" }));
  }
  await page.route("**/_elvern/frontend-health", (route) => route.fulfill({
    status: 204, body: "", headers: { "X-Elvern-Frontend-Health": "1" },
  }));
  await page.route("**/health", async (route) => {
    state.healthRequestCount = Number(state.healthRequestCount || 0) + 1;
    const delay = Number(state.nextHealthDelayMs || 0);
    state.nextHealthDelayMs = 0;
    if (delay > 0) {
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
    const healthy = state.healthHealthy !== false
      || state.healthRequestCount <= Number(state.allowHealthyHealthRequests || 0);
    await route.fulfill({
      status: healthy ? 200 : 503,
      headers: healthy ? { "X-Elvern-Backend-Health": "1" } : {},
      contentType: "application/json",
      body: healthy ? '{"status":"ok"}' : '{"detail":"temporarily unavailable"}',
    });
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/[^/]+(?=\/api\/)/, "");
    requests.push(`${path}${url.search}`);
    state.pathRequestCounts ||= {};
    state.pathRequestCounts[path] = Number(state.pathRequestCounts[path] || 0) + 1;
    if (
      state.holdSettingsResources
      && [
        "/api/admin/google-drive-setup",
        "/api/admin/media-library-reference",
        "/api/admin/poster-reference-location",
      ].includes(path)
    ) {
      await new Promise((resolve) => {
        state.settingsResourceReleases ||= [];
        state.settingsResourceReleases.push(resolve);
      });
    }
    if (path.startsWith("/api/posters/")) {
      state.posterRequestCounts ||= {};
      state.posterRequestCounts[path] = Number(state.posterRequestCounts[path] || 0) + 1;
      if (
        state.posterRecoveryEnabled
        && state.posterFailureEnabled !== false
        && path === "/api/posters/1"
        && state.posterRequestCounts[path] === 1
      ) {
        if (state.waitBeforeFirstPosterFailure) {
          await new Promise((resolve) => {
            state.releaseFirstPosterFailure = resolve;
          });
        }
        await route.fulfill({ status: 503, body: "" });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="15"><rect width="10" height="15" fill="#222"/></svg>',
      });
      return;
    }
    let payload = {};
    let status = 200;
    if (path === "/api/auth/me") {
      payload = { user: {
        id: Number(state.userId || 7),
        username: state.username || "phase7-browser",
        display_name: state.displayName || "Phase 7 Browser",
        role: state.role || "standard_user",
        assistant_beta_enabled: Boolean(state.assistantEnabled),
        age_credential: 18,
      } };
    } else if (path === "/api/auth/heartbeat") {
      payload = { ok: true };
    } else if (path === "/api/user-settings") {
      payload = {
        hide_duplicate_movies: true,
        hide_recently_added: true,
        floating_library_search_enabled: state.floatingSearchEnabled !== false,
        desktop_floating_island_position: state.desktopIslandPosition || "top",
        poster_card_appearance: state.posterAppearance || "classic",
        poster_card_display_max_width: "1400",
      };
    } else if (path === "/api/provider-auth/status") {
      payload = state.providerAuthStatus || { provider_auth_required: false, reconnect_required: false };
    } else if (path === "/api/cloud-libraries") {
      payload = state.cloudLibraries || {
        google: { enabled: false, connected: false },
        my_libraries: [],
        shared_libraries: [],
      };
    } else if (path === "/api/user-hidden-items") {
      if (state.settingsAncillaryDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, state.settingsAncillaryDelayMs));
      }
      payload = { items: state.hiddenItems || [] };
    } else if (path === "/api/admin/global-hidden-items") {
      payload = { items: state.globalHiddenItems || [] };
    } else if (path === "/api/library/age-groups") {
      payload = { total: 0, items: [] };
    } else if (path === "/api/admin/google-drive-setup") {
      payload = state.googleDriveSetup || {
        configuration_state: "not_configured",
        configuration_label: "Not configured",
        connected: false,
        missing_fields: [],
        instructions: [],
      };
    } else if (path === "/api/auth/totp/status") {
      payload = state.totpStatus || { enabled: false, setup_available: false };
    } else if (path === "/api/admin/media-library-reference") {
      payload = state.mediaLibraryReference || {
        configured_value: "Configured",
        effective_value: "Configured",
        default_value: "Configured",
        configured_locations: ["Configured"],
        effective_locations: ["Configured"],
        category_summary: {},
        validation_rules: [],
      };
    } else if (path === "/api/admin/poster-reference-location") {
      payload = state.posterReferenceLocation || {
        configured_value: null,
        effective_value: "",
        default_value: "",
        validation_rules: [],
      };
    } else if (path === "/api/assistant/requests") {
      if (route.request().method() === "POST") {
        state.assistantRequestBodies ||= [];
        state.assistantRequestBodies.push(route.request().postData() || "");
        payload = { request: { request_number: "REQ-PHASE7" } };
      } else {
        payload = { requests: [] };
      }
    } else if (path === "/api/admin/assistant/requests") {
      payload = { requests: [] };
    } else if (/^\/api\/admin\/assistant\/requests\/\d+$/.test(path)) {
      payload = { request: null };
    } else if (path === "/api/system/status") {
      payload = state.systemStatus || {
        total_media_items: 0,
        total_users: 1,
        scan: { running: false },
        last_scan: null,
        library: { total_items: 0 },
        security: {
          multiuser_enabled: true,
          session_ttl_hours: 24,
        },
      };
    } else if (path === "/api/admin/users") {
      payload = { users: state.adminUsers || [] };
    } else if (path === "/api/admin/sessions") {
      payload = { sessions: state.adminSessions || [] };
    } else if (path === "/api/admin/audit") {
      payload = { events: state.adminAuditEvents || [] };
    } else if (path === "/api/admin/url-prefix") {
      payload = state.adminUrlPrefix || {};
    } else if (path === "/api/admin/playback-workers") {
      payload = { workers: [] };
    } else if (path === "/api/admin/invite-codes") {
      payload = { invite_codes: state.adminInviteCodes || [] };
    } else if (path === "/api/admin/exposure/status") {
      payload = state.exposureStatus || meridianSafeExposureStatus();
    } else if (path === "/api/library/v2/summary") {
      payload = v2Summary(url.searchParams.get("source") || "all", state);
    } else if (path === "/api/library/search") {
      payload = v1SearchPayload(url.searchParams.get("q") || "");
    } else if (path === "/api/library/v2/revision") {
      payload = {
        schema_version: "library-revision-v1",
        catalog: state.catalogToken || opaqueToken(1, 1),
        presentation: opaqueToken(1, 2),
        permission: opaqueToken(1, 3),
        user_overlay: opaqueToken(1, 4),
        progress: state.progressToken || opaqueToken(1, 5),
        combined_library: opaqueToken(1, 6),
        ...(state.revisionExtraFields || {}),
      };
    } else if (path === "/api/library/v2/progress-state") {
      status = Number(state.progressStateStatus || 200);
      payload = status === 200
        ? {
            schema_version: "library-progress-state-v1",
            progress_revision: state.progressToken || opaqueToken(1, 5),
            items: [{
              id: 1,
              progress_seconds: Number(state.progressSeconds || 0),
              progress_duration_seconds: Number(state.progressDuration || 7200),
              completed: Boolean(state.completed),
            }],
          }
        : { detail: state.progressStateErrorDetail || "Not found" };
    } else if (path === "/api/desktop-helper/status") {
      state.desktopStatusRequestCount = Number(state.desktopStatusRequestCount || 0) + 1;
      const delay = Number(
        state.desktopStatusRequestDelays?.[state.desktopStatusRequestCount - 1]
        || state.desktopStatusDelayMs
        || 0,
      );
      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
      payload = desktopHelperStatus(state);
    } else if (path === "/api/desktop-helper/verify") {
      const delay = Number(state.desktopVerifyDelayMs || 0);
      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
      payload = { status: desktopHelperStatus(state) };
    } else if (path === "/api/progress/1" && route.request().method() === "GET") {
      payload = {
        media_item_id: 1,
        position_seconds: Number(state.progressSeconds || 0),
        duration_seconds: Number(state.progressDuration || 7200),
        completed: Boolean(state.completed),
      };
    } else if (path === "/api/playback/1") {
      payload = {
        mode: "direct",
        transcode_status: "idle",
        manifest_complete: false,
      };
    } else if (path.startsWith("/api/desktop-playback/1")) {
      payload = {
        available: true,
        helper_required: false,
        vlc_available: true,
      };
    } else if (path === "/api/browser-playback/items/1/active") {
      payload = null;
    } else if (path === "/api/progress/1" && route.request().method() === "POST") {
      const progress = route.request().postDataJSON();
      state.progressSeconds = Number(progress.position_seconds || 0);
      state.progressDuration = Number(progress.duration_seconds || 7200);
      state.completed = Boolean(progress.completed);
      state.progressSequence = Number(state.progressSequence || 0) + 1;
      state.progressToken = opaqueToken(state.progressSequence, 5);
      payload = {
        media_item_id: 1,
        position_seconds: state.progressSeconds,
        duration_seconds: state.progressDuration,
        completed: state.completed,
      };
    } else if (path === "/api/library/rescan" && route.request().method() === "POST") {
      state.catalogSequence = Number(state.catalogSequence || 0) + 1;
      state.catalogToken = opaqueToken(state.catalogSequence, 1);
      state.titleSuffix = ` Scan ${state.catalogSequence}`;
      payload = {
        message: "Library scan completed.",
        running: false,
        job_id: null,
        cloud_sync: null,
      };
    } else if (/^\/api\/library\/item\/\d+$/.test(path)) {
      const itemId = Number(path.split("/").at(-1));
      const selected = ITEMS.find((entry) => entry.id === itemId);
      payload = {
        ...selected,
        source_kind: state.detailSourceKind || selected.source_kind,
        parsed_title: {
          display_title: selected.title,
          base_title: selected.title,
          edition_identity: "standard",
          parsed_year: selected.year,
          title_source: "title",
          parse_confidence: "high",
          warnings: [],
          parser_version: "test",
          suspicious_output: false,
        },
        original_filename: null,
        source_label: selected.source_kind === "cloud" ? "Cloud" : "DGX",
        quality_tier: "silver",
        quality_label: "Silver",
        genres: [],
        genre_display: "Unknown",
        hidden_for_user: false,
        hidden_globally: false,
        file_size: 1024,
        width: 1920,
        height: 1080,
        video_codec: "h264",
        audio_codec: "aac",
        container: "mkv",
        stream_url: `/api/stream/${itemId}`,
        resume_position_seconds: 0,
        subtitles: [],
        subtitle_tracks: [],
        audio_tracks: [],
        track_scan_status: "not_scanned",
        track_scan_error: "",
        track_scan_source: "not_scanned",
        audio_track_diagnostics: {},
        subtitle_track_diagnostics: {},
        age_requirement: null,
        age_requirement_display: "None",
        age_group_key: "",
        genre_group_key: "",
      };
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}


async function dispatchGenuinePageReturn(page) {
  await page.evaluate(() => {
    window.dispatchEvent(new Event("blur"));
    window.dispatchEvent(new Event("focus"));
  });
}


async function installFetchFaults(page, faults) {
  await page.addInitScript((faultDefinitions) => {
    const originalFetch = window.fetch.bind(window);
    const controls = Object.fromEntries(faultDefinitions.map((definition) => [
      definition.id,
      {
        ...definition,
        armed: definition.armed !== false,
        remaining: Number(definition.count || 1),
        triggered: 0,
      },
    ]));
    window.__elvernFetchFaults = controls;
    window.fetch = async (input, init = {}) => {
      const url = typeof input === "string" ? input : input?.url || "";
      const control = Object.values(controls).find(
        (candidate) => (
          candidate.armed
          && candidate.remaining > 0
          && url.includes(candidate.match)
        ),
      );
      if (!control) {
        return originalFetch(input, init);
      }
      control.remaining -= 1;
      control.triggered += 1;
      if (control.mode === "fetch") {
        throw new TypeError("NetworkError when attempting to fetch resource");
      }
      if (control.mode === "malformed_401") {
        return new Response("{", {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (control.mode === "body") {
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("{"));
            queueMicrotask(() => {
              controller.error(new TypeError("NetworkError when attempting to fetch resource"));
            });
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (control.mode === "body_network") {
        return originalFetch(control.faultUrl, {
          cache: "no-store",
          credentials: "omit",
          signal: init?.signal,
        });
      }
      if (control.mode === "pending_body") {
        const stream = new ReadableStream({
          start(controller) {
            const signal = init?.signal;
            const abort = () => controller.error(new DOMException("Aborted", "AbortError"));
            if (signal?.aborted) {
              abort();
              return;
            }
            signal?.addEventListener("abort", abort, { once: true });
          },
        });
        return new Response(stream, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return originalFetch(input, init);
    };
  }, faults);
}


async function startBodyFaultServer() {
  let requestCount = 0;
  const server = createServer((_request, response) => {
    requestCount += 1;
    response.writeHead(200, {
      "Access-Control-Allow-Origin": "*",
      "Content-Type": "application/json",
      "Transfer-Encoding": "chunked",
    });
    response.flushHeaders();
    response.write("{");
    setTimeout(() => response.socket?.destroy(), 30);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const origin = `http://127.0.0.1:${address.port}`;
  try {
    await proxyControl("/__elvern_network_guard_register", { origin });
  } catch (error) {
    await new Promise((resolve) => server.close(resolve));
    throw error;
  }
  LOCAL_FAULT_ORIGINS.add(origin);
  return {
    get requestCount() {
      return requestCount;
    },
    url: `${origin}/body-fault`,
    close: async () => {
      await proxyControl("/__elvern_network_guard_unregister", { origin });
      LOCAL_FAULT_ORIGINS.delete(origin);
      await new Promise((resolve) => server.close(resolve));
    },
  };
}


test.beforeEach(async ({ context, page, baseURL }) => {
  await context.clearCookies();
  await installExternalNetworkGuard(context, baseURL);
  await installFixture(page, []);
});

test.afterEach(async ({ context }) => {
  expect(NETWORK_GUARD_STATE.get(context)?.externalRequests || []).toEqual([]);
});


for (const scheme of ["http", "https", "ws", "wss"]) {
  test(`external network guard rejects unknown public ${scheme.toUpperCase()} without issuing it`, async ({
    baseURL,
  }) => {
    const result = classifyTestNetworkRequest(
      `${scheme}://unknown.invalid/private?token=must-not-be-recorded`,
      new Set([new URL(baseURL).origin]),
    );
    expect(result.allowed).toBe(false);
    expect(result.diagnostic).toEqual({
      scheme,
      origin: `${scheme}://unknown.invalid`,
      pathname_hash: expect.stringMatching(/^[0-9a-f]{12}$/),
    });
    expect(JSON.stringify(result)).not.toContain("token");
  });
}


async function expectPrimaryNavigationDoesNotOverlap(page) {
  const navigation = page.getByRole("navigation", { name: "Primary" });
  const controls = [
    ...await navigation.getByRole("link").all(),
    page.getByRole("button", { name: "Logout" }),
  ];
  const boxes = [];
  for (const control of controls) {
    await expect(control).toBeVisible();
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    boxes.push(box);
  }
  for (let leftIndex = 0; leftIndex < boxes.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < boxes.length; rightIndex += 1) {
      const left = boxes[leftIndex];
      const right = boxes[rightIndex];
      const overlaps = (
        left.x < right.x + right.width
        && left.x + left.width > right.x
        && left.y < right.y + right.height
        && left.y + left.height > right.y
      );
      expect(overlaps).toBe(false);
    }
  }
}


test("@assistant-navigation admin uses the desktop Avatar menu and standalone queue", async ({
  page,
  baseURL,
}) => {
  const state = { role: "admin", assistantEnabled: false };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], state);
  await page.goto("library");

  const navigation = page.getByRole("navigation", { name: "Library controls" });
  await expect(navigation).toBeVisible();
  await expect(page.getByText(/Assistant\s*\(Beta\)/i)).toHaveCount(0);
  await navigation.getByRole("button", { name: /Account:/ }).click();
  const accountMenu = page.getByRole("menu", { name: "Account menu" });
  await expect(accountMenu.getByLabel("Administrator")).toBeVisible();
  expect(await accountMenu.getByRole("menuitem").allTextContents()).toEqual([
    "Settings",
    "Admin Panel",
    "Assistant",
    "Sign out",
  ]);
  await accountMenu.getByRole("menuitem", { name: "Assistant" }).click();
  await expect(page).toHaveURL(`${baseURL}admin/assistant`);
  await expect(page.getByRole("heading", { name: "Request queue" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Library controls" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Back to Library" })).toBeVisible();

  await page.goto("admin/assistant/42");
  await expect(page.getByRole("link", { name: "Back to Assistant requests" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to Library" })).toHaveCount(0);

  await page.evaluate(() => {
    window.localStorage.setItem("elvern:admin-active-section", "assistant");
  });
  await page.goto("admin?section=assistant");
  await expect(page).toHaveURL(`${baseURL}admin/overview`);
  await expect.poll(() => page.evaluate(
    () => window.sessionStorage.getItem("elvern:control-center:admin-tab"),
  )).toBe("overview");
  const adminSections = page.getByRole("navigation", { name: "Admin sections" });
  await expect(adminSections.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await expect(adminSections.getByRole("link", { name: "Assistant" })).toHaveCount(0);
  await expect(page.getByText("Open request queue")).toHaveCount(0);
});


test("@assistant-navigation enabled regular user opens the request form without Admin", async ({
  page,
  baseURL,
}) => {
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], {
    role: "standard_user",
    assistantEnabled: true,
  });
  await page.goto("library");

  const navigation = page.getByRole("navigation", { name: "Library controls" });
  await navigation.getByRole("button", { name: /Account:/ }).click();
  const accountMenu = page.getByRole("menu", { name: "Account menu" });
  expect(await accountMenu.getByRole("menuitem").allTextContents()).toEqual([
    "Settings",
    "Assistant",
    "Sign out",
  ]);
  await expect(accountMenu.getByLabel("Administrator")).toHaveCount(0);
  await accountMenu.getByRole("menuitem", { name: "Assistant" }).click();
  await expect(page).toHaveURL(`${baseURL}assistant`);
  await expect(page.getByText("Submit a request", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to Library" })).toBeVisible();
});


test("@assistant-navigation attachment route keeps its safe nested back behavior", async ({
  page,
}) => {
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], {
    role: "standard_user",
    assistantEnabled: true,
  });
  await page.goto("attachments/42/view?name=report.txt");

  await expect(page.getByRole("navigation", { name: "Library controls" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Back to Library" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Back to/ })).toBeVisible();
});


test("@assistant-navigation revoked attachment route has no desktop Library Island", async ({
  page,
}) => {
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], {
    role: "standard_user",
    assistantEnabled: false,
  });
  await page.goto("attachments/42/view?name=report.txt");

  await expect(page.getByRole("navigation", { name: "Library controls" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Back to Library" })).toHaveCount(0);
});


test("@assistant-navigation strips Library query and hash from Assistant UI and payload", async ({
  page,
}) => {
  const state = {
    role: "standard_user",
    assistantEnabled: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], state);
  await page.goto("library?q=private-search#token-like-secret");
  await page.getByRole("button", { name: /Account:/ }).click();
  await page.getByRole("menuitem", { name: "Assistant" }).click();

  await page.getByText("Submit a request", { exact: true }).click();
  await expect(page.getByText("Page /library", { exact: true })).toBeVisible();
  await expect(page.getByText(/private-search|token-like-secret/)).toHaveCount(0);
  await page.getByLabel("Title").fill("Safe context");
  await page.getByLabel("Description").fill("Context should contain only the safe pathname.");
  await page.getByRole("button", { name: "Submit request" }).click();
  await expect.poll(() => state.assistantRequestBodies?.length || 0).toBe(1);

  const submittedBody = state.assistantRequestBodies[0];
  expect(submittedBody).toContain('name="page_context"');
  expect(submittedBody).toContain("/library");
  expect(submittedBody).not.toContain("private-search");
  expect(submittedBody).not.toContain("token-like-secret");
});


test("@assistant-navigation disabled regular user cannot see or directly open Assistant", async ({
  page,
  baseURL,
}) => {
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], {
    role: "standard_user",
    assistantEnabled: false,
  });
  await page.goto("assistant");

  await expect(page).toHaveURL(`${baseURL}library`);
  await page.getByRole("button", { name: /Account:/ }).click();
  const accountMenu = page.getByRole("menu", { name: "Account menu" });
  await expect(accountMenu.getByRole("menuitem", { name: "Assistant" })).toHaveCount(0);
  await expect(accountMenu.getByRole("menuitem", { name: "Admin Panel" })).toHaveCount(0);
});


for (const deviceCase of [
  {
    label: "phone",
    platform: "iPhone",
    userAgent: "iPhone",
    maxTouchPoints: 5,
    viewport: { width: 390, height: 844 },
  },
  {
    label: "tablet",
    platform: "MacIntel",
    userAgent: "Macintosh",
    maxTouchPoints: 5,
    viewport: { width: 820, height: 1180 },
  },
]) {
  test(`@assistant-navigation admin ${deviceCase.label} navigation remains usable and does not drag`, async ({
    page,
    baseURL,
  }) => {
    await page.addInitScript(({ nextPlatform, nextUserAgent, nextMaxTouchPoints }) => {
      Object.defineProperties(window.navigator, {
        platform: { configurable: true, get: () => nextPlatform },
        userAgent: { configurable: true, get: () => nextUserAgent },
        maxTouchPoints: { configurable: true, get: () => nextMaxTouchPoints },
      });
    }, {
      nextPlatform: deviceCase.platform,
      nextUserAgent: deviceCase.userAgent,
      nextMaxTouchPoints: deviceCase.maxTouchPoints,
    });
    await page.setViewportSize(deviceCase.viewport);
    await page.unrouteAll({ behavior: "wait" });
    await installFixture(page, [], { role: "admin" });
    await page.goto("library");

    await expectPrimaryNavigationDoesNotOverlap(page);
    const navigation = page.getByRole("navigation", { name: "Primary" });
    const libraryLink = navigation.getByRole("link", { name: "Library" });
    const assistantLink = navigation.getByRole("link", { name: "Assistant" });
    const libraryBox = await libraryLink.boundingBox();
    const assistantBox = await assistantLink.boundingBox();
    await libraryLink.dispatchEvent("pointerdown", {
      pointerId: 1,
      pointerType: "touch",
      clientX: libraryBox.x + libraryBox.width / 2,
      clientY: libraryBox.y + libraryBox.height / 2,
    });
    await libraryLink.dispatchEvent("pointermove", {
      pointerId: 1,
      pointerType: "touch",
      clientX: assistantBox.x + assistantBox.width / 2,
      clientY: assistantBox.y + assistantBox.height / 2,
    });
    await libraryLink.dispatchEvent("pointerup", {
      pointerId: 1,
      pointerType: "touch",
      clientX: assistantBox.x + assistantBox.width / 2,
      clientY: assistantBox.y + assistantBox.height / 2,
    });
    await expect(page).toHaveURL(`${baseURL}library`);
  });
}


test("@settings-navigation desktop sections use canonical nested routes and browser history", async ({
  page,
  baseURL,
}) => {
  await page.goto("settings?section=preferences");
  await expect(page).toHaveURL(`${baseURL}settings/appearance`);
  const navigation = page.getByRole("navigation", { name: "Settings sections" });
  await expect(navigation.getByRole("link", { name: "Appearance" })).toHaveAttribute("aria-current", "page");
  await navigation.getByRole("link", { name: "Hidden titles" }).click();
  await expect(page).toHaveURL(`${baseURL}settings/hidden-titles`);
  await page.goBack();
  await expect(page).toHaveURL(`${baseURL}settings/appearance`);
  await page.goForward();
  await expect(page).toHaveURL(`${baseURL}settings/hidden-titles`);
});


test("@settings-navigation delayed Libraries data survives the production StrictMode tree", async ({
  page,
}) => {
  const state = {
    settingsAncillaryDelayMs: 200,
    hiddenItems: [{
      id: 91,
      title: "Delayed Hidden Copy",
      year: 2026,
      edition_label: null,
      poster_url: null,
      hidden_at: "2026-07-28T00:00:00Z",
    }],
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], state);
  await page.goto("settings/hidden-titles");
  await expect(page.getByRole("radio", { name: /For me \(1\)/ })).toBeVisible();
  await expect(page.getByText("Delayed Hidden Copy")).toBeVisible();
  expect(state.pathRequestCounts?.["/api/user-hidden-items"] || 0).toBeGreaterThanOrEqual(1);
});


test("@settings-navigation legacy Hidden hash and Install routes replace canonically", async ({
  page,
  baseURL,
}) => {
  await page.goto("settings?other=1&section=hidden#hidden-list");
  await expect(page).toHaveURL(`${baseURL}settings/hidden-titles?other=1#hidden-list`);
  await expect(page.locator("#hidden-list")).toHaveCount(1);

  await page.goto("install?from=legacy#helper");
  await expect(page).toHaveURL(`${baseURL}settings/playback-apps?from=legacy#helper`);
  await expect(page.getByText("Elvern VLC Opener", { exact: true })).toBeVisible();

  await page.goto("settings?other=1&section=display#poster");
  await expect(page).toHaveURL(`${baseURL}settings/appearance?other=1#poster`);
  await expect(page.getByRole("link", { name: "Appearance" })).toHaveAttribute("aria-current", "page");

  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], { role: "admin" });
  await page.goto("settings?other=1&section=libraries#google-drive-oauth-setup");
  await expect(page).toHaveURL(
    `${baseURL}settings/server-storage?other=1#google-drive-oauth-setup`,
  );
  await expect(page.getByText("Google Drive OAuth Setup", { exact: true })).toBeVisible();
});


test("@settings-navigation Preferences and Advanced use the approved desktop information architecture", async ({
  page,
}) => {
  await page.goto("settings/appearance");
  await expect(page.getByRole("navigation", { name: "Settings sections" }).getByRole("link")).toHaveText([
    "Appearance",
    "Library",
    "Cloud & Sharing",
    "Hidden titles",
    "Playback & Apps",
  ]);
  await expect(page.getByText("Your account", { exact: true })).toHaveCount(0);

  const posterHeading = page.getByText("Poster appearance", { exact: true });
  const floatingHeading = page.getByText("Floating island position", { exact: true });
  const backgroundHeading = page.getByRole("heading", { name: "Background" });
  await expect(posterHeading).toBeVisible();
  await expect(floatingHeading).toBeVisible();
  await expect(backgroundHeading).toBeVisible();

  const posterCard = posterHeading.locator("xpath=ancestor::section[1]");
  await expect(posterCard.getByRole("combobox")).toHaveCount(0);
  await expect(posterCard.getByRole("radio", { name: "1400 px" })).toBeVisible();
  const posterBox = await posterHeading.boundingBox();
  const floatingBox = await floatingHeading.boundingBox();
  const backgroundBox = await backgroundHeading.boundingBox();
  expect(floatingBox.x).toBe(posterBox.x);
  expect(backgroundBox.x).toBe(posterBox.x);

  const libraryLink = page.getByRole("link", { name: "Library" });
  await libraryLink.click();
  await expect(page).toHaveURL(/\/settings\/library$/);
  await expect(libraryLink).toHaveAttribute("aria-current", "page");
  const libraryHeading = page.getByRole("heading", { name: "Library", exact: true });
  await expect(libraryHeading).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Hide.*Recently added/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Hide duplicate copies/ })).toBeVisible();
});


for (const deviceCase of [
  {
    label: "phone",
    platform: "iPhone",
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    viewport: { width: 390, height: 844 },
  },
  {
    label: "tablet",
    platform: "MacIntel",
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15",
    viewport: { width: 820, height: 1180 },
  },
]) {
  test(`@settings-navigation ${deviceCase.label} Preferences hides position and preserves card order`, async ({
    page,
  }, testInfo) => {
    await page.addInitScript(({ platform, userAgent }) => {
      Object.defineProperty(navigator, "platform", { configurable: true, get: () => platform });
      Object.defineProperty(navigator, "userAgent", { configurable: true, get: () => userAgent });
      Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, get: () => 5 });
    }, {
      platform: deviceCase.platform,
      userAgent: deviceCase.userAgent,
    });
    await page.setViewportSize(deviceCase.viewport);
    await page.goto("settings?section=preferences");

    await expect(page.getByRole("heading", { name: "Floating Island Position" })).toHaveCount(0);
    const posterBox = await page.getByRole("heading", { name: "Poster appearance" }).boundingBox();
    const backgroundBox = await page.getByRole("heading", { name: "Background" }).boundingBox();
    const libraryBox = await page.getByRole("heading", { name: "Library" }).boundingBox();
    expect(backgroundBox.y).toBeGreaterThan(posterBox.y);
    expect(libraryBox.y).toBeGreaterThan(backgroundBox.y);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await captureControlCenterVisual(page, testInfo, `${deviceCase.label}-settings`);
  });

  test(`@settings-navigation ${deviceCase.label} Admin keeps the legacy surface`, async ({
    page,
  }, testInfo) => {
    await page.addInitScript(({ platform, userAgent }) => {
      Object.defineProperty(navigator, "platform", { configurable: true, get: () => platform });
      Object.defineProperty(navigator, "userAgent", { configurable: true, get: () => userAgent });
      Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, get: () => 5 });
    }, {
      platform: deviceCase.platform,
      userAgent: deviceCase.userAgent,
    });
    await page.setViewportSize(deviceCase.viewport);
    await page.unrouteAll({ behavior: "wait" });
    await installFixture(page, [], { role: "admin" });
    await page.goto("admin/overview");

    await expect(page.locator(".control-center-desktop")).toHaveCount(0);
    await expect(page.getByLabel("Admin sections")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await captureControlCenterVisual(page, testInfo, `${deviceCase.label}-admin`);
  });
}


test("@settings-navigation desktop Settings and Admin use their dock Back to Library control", async ({
  page,
  baseURL,
}) => {
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], { role: "admin" });
  const libraryUrl = `${baseURL}library?category=anime&source=cloud&sort=az&genre=Drama&quality=gold`;
  await page.goto("library?category=anime&source=cloud&sort=az&genre=Drama&quality=gold");
  await expect(page).toHaveURL(libraryUrl);

  await page.getByRole("button", { name: /Account:/ }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page.getByRole("navigation", { name: "Library controls" })).toHaveCount(0);
  const settingsDock = page.locator(".meridian-sidebar");
  await expect(settingsDock.getByRole("button", { name: "Library" })).toBeVisible();
  await settingsDock.getByRole("button", { name: "Library" }).click();
  await expect(page).toHaveURL(libraryUrl);

  await page.getByRole("button", { name: /Account:/ }).click();
  await page.getByRole("menuitem", { name: "Admin Panel" }).click();
  await expect(page.getByRole("navigation", { name: "Library controls" })).toHaveCount(0);
  const adminDock = page.locator(".meridian-sidebar");
  await expect(adminDock.getByRole("button", { name: "Library" })).toBeVisible();
  await adminDock.getByRole("button", { name: "Library" }).click();
  await expect(page).toHaveURL(libraryUrl);
});


test("@settings-navigation admin Control Center shares state, status, and real routes", async ({
  page,
  baseURL,
}, testInfo) => {
  await page.unrouteAll({ behavior: "wait" });
  const requests = [];
  await installFixture(page, requests, { role: "admin" });
  await page.goto("settings/appearance");
  await expect(page.getByRole("heading", { name: "Appearance", exact: true })).toBeVisible();
  await captureControlCenterVisual(page, testInfo, "desktop-settings");
  await page.getByRole("button", { name: "System status" }).click();
  await expect(page.getByRole("complementary", { name: "System status" })).toBeVisible();
  await expect(page.getByText("Titles indexed")).toBeVisible();
  await expect(page.getByText("VLC on host")).toBeVisible();
  await captureControlCenterVisual(page, testInfo, "desktop-system-status");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("complementary", { name: "System status" })).toHaveCount(0);

  await page.getByRole("button", { name: /Switch to Admin Panel/ }).click();
  await expect(page).toHaveURL(`${baseURL}admin/overview`);
  await expect(page.getByText("92", { exact: true })).toBeVisible();
  await expect(page.getByText("PRIVATE", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await captureControlCenterVisual(page, testInfo, "desktop-admin");
  await page.goBack();
  await expect(page).toHaveURL(`${baseURL}settings/appearance`);
});


test("@settings-navigation @control-center-visual production Control Center matches the Meridian primary shell contract", async ({
  page,
}, testInfo) => {
  requireMeridianDemoPath();

  const demoBrowser = await chromium.launch({ headless: true });
  let referenceScreenshot;
  let referenceAdminScreenshot;
  let appearanceDemoEvidence;
  let adminDemoEvidence;
  try {
    const demoContext = await demoBrowser.newContext({
      viewport: { width: 1360, height: 880 },
      deviceScaleFactor: 1,
      locale: "en-US",
      timezoneId: "America/Los_Angeles",
      reducedMotion: "no-preference",
    });
    await installLocalMeridianDemoRuntime(demoContext);
    const demoPage = await demoContext.newPage();
    await demoPage.goto(pathToFileURL(MERIDIAN_DEMO_PATH).href, { waitUntil: "load" });
    const demoRoot = demoPage.locator("[data-mer]:visible").last();
    await expect(demoRoot).toBeVisible({ timeout: 30_000 });
    await demoPage.evaluate(() => document.fonts?.ready);
    await normalizeMeridianDemoFixture(demoPage);
    referenceScreenshot = await demoRoot.screenshot({ animations: "disabled" });
    appearanceDemoEvidence = await collectMeridianEvidence(demoPage, "[data-mer]", "demo");
    await demoPage.getByText("Admin Panel", { exact: true }).first().click();
    await expect(demoPage.getByText("Overview", { exact: true }).first()).toBeVisible();
    await demoPage.waitForTimeout(600);
    await normalizeMeridianDemoFixture(demoPage);
    referenceAdminScreenshot = await demoRoot.screenshot({ animations: "disabled" });
    adminDemoEvidence = await collectMeridianEvidence(demoPage, "[data-mer]", "demo");
    await demoContext.close();
  } finally {
    await demoBrowser.close();
  }

  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], meridianSafeFixtureState());
  await page.setViewportSize({ width: 1360, height: 880 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("settings/appearance");
  const productionRoot = page.locator(".meridian-control-center");
  await expect(productionRoot).toBeVisible();
  await page.evaluate(() => document.fonts?.ready);
  await normalizeMeridianProductionFixture(page);
  const productionScreenshot = await productionRoot.screenshot({ animations: "disabled" });
  const comparison = await comparePngPixels(page, referenceScreenshot, productionScreenshot);
  const appearanceProductionEvidence = await collectMeridianEvidence(
    page,
    ".meridian-control-center",
    "production",
  );
  const appearanceArtifacts = await writeMeridianParityArtifacts({
    actual: productionScreenshot,
    comparison,
    demoEvidence: appearanceDemoEvidence,
    name: "settings-appearance-primary-shell",
    productionEvidence: appearanceProductionEvidence,
    reference: referenceScreenshot,
    testInfo,
  });

  await page.goto("admin/overview");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await normalizeMeridianProductionFixture(page);
  const productionAdminScreenshot = await productionRoot.screenshot({ animations: "disabled" });
  const adminComparison = await comparePngPixels(
    page,
    referenceAdminScreenshot,
    productionAdminScreenshot,
  );
  const adminProductionEvidence = await collectMeridianEvidence(
    page,
    ".meridian-control-center",
    "production",
  );
  const adminArtifacts = await writeMeridianParityArtifacts({
    actual: productionAdminScreenshot,
    comparison: adminComparison,
    demoEvidence: adminDemoEvidence,
    name: "admin-overview-primary-shell",
    productionEvidence: adminProductionEvidence,
    reference: referenceAdminScreenshot,
    testInfo,
  });

  await testInfo.attach("meridian-demo-appearance", {
    body: referenceScreenshot,
    contentType: "image/png",
  });
  await testInfo.attach("meridian-production-appearance", {
    body: productionScreenshot,
    contentType: "image/png",
  });
  await testInfo.attach("meridian-appearance-pixel-report", {
    body: Buffer.from(JSON.stringify(comparison, null, 2)),
    contentType: "application/json",
  });
  await testInfo.attach("meridian-demo-admin-overview", {
    body: referenceAdminScreenshot,
    contentType: "image/png",
  });
  await testInfo.attach("meridian-production-admin-overview", {
    body: productionAdminScreenshot,
    contentType: "image/png",
  });
  await testInfo.attach("meridian-admin-overview-pixel-report", {
    body: Buffer.from(JSON.stringify(adminComparison, null, 2)),
    contentType: "application/json",
  });
  const screenshotDirectory = process.env.ELVERN_CONTROL_CENTER_SCREENSHOT_DIR;
  if (screenshotDirectory) {
    mkdirSync(screenshotDirectory, { recursive: true });
    writeFileSync(`${screenshotDirectory}/meridian-demo-appearance.png`, referenceScreenshot);
    writeFileSync(`${screenshotDirectory}/meridian-production-appearance.png`, productionScreenshot);
    writeFileSync(`${screenshotDirectory}/meridian-demo-admin-overview.png`, referenceAdminScreenshot);
    writeFileSync(`${screenshotDirectory}/meridian-production-admin-overview.png`, productionAdminScreenshot);
    writeFileSync(
      `${screenshotDirectory}/meridian-appearance-pixel-report.json`,
      `${JSON.stringify(comparison, null, 2)}\n`,
      "utf8",
    );
    writeFileSync(
      `${screenshotDirectory}/meridian-admin-overview-pixel-report.json`,
      `${JSON.stringify(adminComparison, null, 2)}\n`,
      "utf8",
    );
  }

  expect(comparison.dimensions_match).toBe(true);
  expect(comparison.changed_pixel_ratio).toBeLessThan(0.08);
  expect(comparison.mean_channel_delta).toBeLessThan(12);
  expect(adminComparison.dimensions_match).toBe(true);
  expect(adminComparison.changed_pixel_ratio).toBeLessThan(0.08);
  expect(adminComparison.mean_channel_delta).toBeLessThan(12);
  for (const artifacts of [appearanceArtifacts, adminArtifacts]) {
    for (const key of ["root", "sidebar", "workspace"]) {
      const delta = artifacts.geometryReport.delta[key];
      expect(delta, `${key} geometry evidence`).not.toBeNull();
      expect(Math.max(delta.x, delta.y, delta.width, delta.height), `${key} geometry`).toBeLessThanOrEqual(1);
    }
    for (const key of ["root", "sidebar"]) {
      const differences = artifacts.computedStyleReport.differences[key] || {};
      expect(differences.fontFamily, `${key} font family`).toBeUndefined();
      expect(differences.fontSize, `${key} font size`).toBeUndefined();
      expect(differences.lineHeight, `${key} line height`).toBeUndefined();
      expect(differences.backgroundColor, `${key} background`).toBeUndefined();
    }
  }
});


test("@settings-navigation @control-center-visual Meridian dedicated parity matrix emits strict evidence", async ({
  browserName,
  page,
}, testInfo) => {
  test.skip(browserName !== "chromium", "Meridian pixel parity uses one fixed Chromium executable for both reference and production.");
  test.setTimeout(90_000);
  requireMeridianDemoPath();

  const demoBrowser = await chromium.launch({ headless: true });
  const demoContext = await demoBrowser.newContext({
    viewport: { width: 1360, height: 880 },
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "America/Los_Angeles",
    reducedMotion: "no-preference",
  });
  await installLocalMeridianDemoRuntime(demoContext);
  const demoPage = await demoContext.newPage();
  const reports = [];
  const parityFailures = [];
  try {
    await demoPage.goto(pathToFileURL(MERIDIAN_DEMO_PATH).href, { waitUntil: "load" });
    const demoRoot = demoPage.locator("[data-mer]:visible").last();
    const productionRoot = page.locator(".meridian-control-center");
    await expect(demoRoot).toBeVisible({ timeout: 30_000 });

    await page.unrouteAll({ behavior: "wait" });
    const fixtureState = meridianSafeFixtureState({ holdSettingsResources: true });
    await installFixture(page, [], fixtureState);
    await page.setViewportSize({ width: 1360, height: 880 });
    await page.emulateMedia({ reducedMotion: "no-preference" });

    const clickDemoNav = async (label) => {
      const clicked = await demoPage.getByText(label, { exact: true }).evaluateAll((nodes) => {
        const target = nodes.find((node) => {
          const rect = node.getBoundingClientRect();
          return rect.left < 280 && rect.top >= 180 && rect.top < 520;
        });
        target?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        return Boolean(target);
      });
      expect(clicked, `Meridian demo navigation item ${label}`).toBe(true);
      await demoPage.waitForTimeout(700);
    };
    const clickDemoControl = async (label) => {
      const clicked = await demoPage.getByText(label, { exact: true }).evaluateAll((nodes) => {
        const target = nodes.find((node) => node.getBoundingClientRect().left >= 280);
        target?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        return Boolean(target);
      });
      expect(clicked, `Meridian demo control ${label}`).toBe(true);
      await demoPage.waitForTimeout(150);
    };
    const scrollControlsIntoView = async (label) => {
      const demoTarget = demoPage.getByText(label, { exact: true }).last();
      const productionTarget = page.getByText(label, { exact: true }).last();
      await Promise.all([
        demoTarget.evaluate((node) => node.scrollIntoView({ block: "center" })),
        productionTarget.evaluate((node) => node.scrollIntoView({ block: "center" })),
      ]);
      await Promise.all([demoPage.waitForTimeout(150), page.waitForTimeout(150)]);
    };
    const recordParityFailure = (condition, message) => {
      if (!condition) parityFailures.push(message);
    };
    const showDemoResourceSkeletons = async () => demoPage.evaluate(() => {
      const labels = [
        "Google Drive OAuth",
        "Library reference locations",
        "Poster reference location",
      ];
      const cards = labels.map((label) => {
        const labelNode = Array.from(document.querySelectorAll("div")).find((node) => node.textContent?.trim() === label);
        let card = labelNode;
        while (card) {
          const style = getComputedStyle(card);
          const rect = card.getBoundingClientRect();
          if (style.borderRadius === "14px" && style.borderTopWidth === "1px" && rect.width > 500) return card;
          card = card.parentElement;
        }
        return null;
      });
      if (cards.some((card) => !card)) throw new Error("Could not derive all Server resource cards from the Meridian demo.");
      cards.forEach((card) => {
        const height = card.getBoundingClientRect().height;
        card.replaceChildren(...["58%", "100%", "76%"].map((width) => {
          const line = document.createElement("i");
          Object.assign(line.style, {
            display: "block",
            width,
            height: "38px",
            borderRadius: "9px",
            background: getComputedStyle(card).getPropertyValue("--mer-surface") || "rgba(20,22,26,.08)",
          });
          return line;
        }));
        Object.assign(card.style, {
          boxSizing: "border-box",
          display: "grid",
          alignContent: "start",
          gap: "12px",
          minHeight: `${height}px`,
        });
      });
    });
    const compareState = async (name) => {
      await Promise.all([
        demoPage.evaluate(() => document.fonts?.ready),
        page.evaluate(() => document.fonts?.ready),
      ]);
      await normalizeMeridianDemoFixture(demoPage);
      await normalizeMeridianProductionFixture(page);
      const reference = await demoRoot.screenshot({ animations: "disabled" });
      const actual = await productionRoot.screenshot({ animations: "disabled" });
      const comparison = await comparePngPixels(page, reference, actual);
      const demoEvidence = await collectMeridianEvidence(demoPage, "[data-mer]", "demo", name);
      const productionEvidence = await collectMeridianEvidence(
        page,
        ".meridian-control-center",
        "production",
        name,
      );
      const artifacts = await writeMeridianParityArtifacts({
        actual,
        comparison,
        demoEvidence,
        name,
        productionEvidence,
        reference,
        testInfo,
      });
      reports.push({
        name,
        ...comparison,
        diff_base64: undefined,
        artifact_path: artifacts.outputDirectory,
      });
      recordParityFailure(comparison.dimensions_match, `${name}: dimensions differ`);
      recordParityFailure(
        comparison.changed_pixel_ratio < 0.08,
        `${name}: changed pixel ratio ${comparison.changed_pixel_ratio} is not below 0.08`,
      );
      recordParityFailure(
        comparison.mean_channel_delta < 12,
        `${name}: mean channel delta ${comparison.mean_channel_delta} is not below 12`,
      );
      for (const key of ["root", "sidebar", "workspace", "content", "heading", "card", "control", "rail"]) {
        const delta = artifacts.geometryReport.delta[key];
        const referenceGeometry = artifacts.geometryReport.reference[key];
        const actualGeometry = artifacts.geometryReport.actual[key];
        recordParityFailure(
          Boolean(referenceGeometry) === Boolean(actualGeometry),
          `${name}: ${key} geometry presence differs`,
        );
        if (delta) {
          recordParityFailure(
            Math.max(delta.x, delta.y, delta.width, delta.height) <= 1,
            `${name}: ${key} geometry delta is ${JSON.stringify(delta)}`,
          );
        }
      }
      for (const key of ["root", "sidebar", "workspace", "content", "heading", "card", "control", "rail"]) {
        const differences = artifacts.computedStyleReport.differences[key] || {};
        for (const property of MERIDIAN_STYLE_PROPERTIES) {
          recordParityFailure(
            differences[property] === undefined,
            `${name}: ${key} ${property} differs (${JSON.stringify(differences[property])})`,
          );
        }
      }
    };

    await page.goto("settings/appearance");
    await expect(page.getByRole("heading", { name: "Appearance", exact: true })).toBeVisible();
    await compareState("appearance-presets");
    for (const mode of ["Gradient", "Solid", "Photo"]) {
      await clickDemoControl(mode);
      await page.getByRole("radio", { name: mode, exact: true }).click();
      await compareState(`appearance-${mode.toLowerCase()}`);
    }

    for (const settingsCase of [
      ["Library", "settings/library"],
      ["Cloud & Sharing", "settings/cloud-sharing"],
    ]) {
      await clickDemoNav(settingsCase[0]);
      await page.goto(settingsCase[1]);
      await expect(page.getByRole("heading", { name: settingsCase[0], exact: true })).toBeVisible();
      await compareState(`settings-${settingsCase[1].split("/").at(-1)}`);
    }

    await clickDemoNav("Hidden titles");
    await page.goto("settings/hidden-titles");
    await clickDemoControl("For me (0)");
    await compareState("settings-hidden-empty");
    await clickDemoControl("For everyone (11)");
    await page.getByRole("radio", { name: "For everyone (11)" }).click();
    await compareState("settings-hidden-nonempty");

    await clickDemoNav("Playback & Apps");
    await page.goto("settings/playback-apps");
    await compareState("settings-playback-collapsed");
    await clickDemoControl("Diagnostics");
    await page.getByText("Diagnostics", { exact: true }).click();
    await compareState("settings-playback-diagnostics");

    await clickDemoNav("Server & Storage");
    await page.goto("settings/server-storage");
    await expect(page.getByLabel("Loading Google Drive OAuth setup")).toBeVisible();
    await expect(page.getByLabel("Loading library reference locations")).toBeVisible();
    await expect(page.getByLabel("Loading poster reference location")).toBeVisible();
    await showDemoResourceSkeletons();
    await compareState("settings-server-initial-skeleton");
    fixtureState.holdSettingsResources = false;
    for (const release of fixtureState.settingsResourceReleases || []) release();
    await expect(page.getByText("Google Drive OAuth Setup", { exact: true })).toBeVisible();
    await demoPage.reload({ waitUntil: "load" });
    await clickDemoNav("Server & Storage");
    await compareState("settings-server-oauth-step-1");
    for (const [label, stepNumber] of [["2 · Credentials", 2], ["3 · Register", 3], ["4 · Connect", 4]]) {
      await clickDemoControl(label);
      await page.getByRole("button", { name: label }).click();
      await compareState(`settings-server-oauth-step-${stepNumber}`);
    }
    await scrollControlsIntoView("Path rules");
    await compareState("settings-server-path-rules-closed");
    await clickDemoControl("Path rules");
    await page.getByRole("button", { name: /Path rules/ }).click();
    await compareState("settings-server-path-rules-open");
    await scrollControlsIntoView("Accepted paths");
    await compareState("settings-server-accepted-paths-closed");
    await clickDemoControl("Accepted paths");
    await page.getByRole("button", { name: "Accepted paths" }).click();
    await compareState("settings-server-accepted-paths-open");

    await clickDemoNav("Library");
    await page.goto("settings/library");
    await demoRoot.click({ position: { x: 1322, y: 38 } });
    await page.getByRole("button", { name: "System status" }).click();
    await expect(page.getByRole("complementary", { name: "System status" })).toBeVisible();
    await demoPage.waitForTimeout(500);
    await compareState("settings-system-status");
    await demoRoot.click({ position: { x: 1018, y: 38 } });
    await page.getByRole("button", { name: "System status" }).click();
    await demoPage.waitForTimeout(500);

    await clickDemoNav("Appearance");
    await page.goto("settings/appearance");
    await clickDemoControl("Presets");
    await page.getByRole("radio", { name: "Presets", exact: true }).click();
    await clickDemoControl("Aurora");
    await page.getByRole("radio", { name: "Aurora" }).click();
    await expect(page.getByRole("status")).toHaveText("Background preset saved.");
    await compareState("settings-toast-light");
    await page.getByRole("button", { name: /Theme:/ }).click();
    await demoRoot.click({ position: { x: 1320, y: 840 } });
    await compareState("settings-toast-mixed");
    await page.getByRole("button", { name: /Theme:/ }).click();
    await demoRoot.click({ position: { x: 1320, y: 840 } });
    await compareState("settings-toast-dark");
    await page.getByRole("button", { name: /Theme:/ }).click();
    await demoRoot.click({ position: { x: 1320, y: 840 } });

    await demoPage.getByText("Admin Panel", { exact: true }).first().click();
    await demoPage.waitForTimeout(700);
    for (const adminCase of [
      ["Overview", "admin/overview"],
      ["Users & Invites", "admin/users-invites"],
      ["Security", "admin/security"],
      ["Logs", "admin/logs"],
    ]) {
      if (adminCase[0] !== "Overview") await clickDemoNav(adminCase[0]);
      await page.goto(adminCase[1]);
      await expect(page.getByRole("heading", { name: adminCase[0], exact: true })).toBeVisible();
      await compareState(`admin-${adminCase[1].split("/").at(-1)}`);
    }

    await clickDemoNav("Overview");
    await page.goto("admin/overview");
    const demoExposureButton = demoPage.getByText("Manage Exposure Mode", { exact: true }).last();
    await demoExposureButton.scrollIntoViewIfNeeded();
    await demoExposureButton.click();
    await demoPage.waitForTimeout(150);
    await page.getByRole("button", { name: "Manage Exposure Mode" }).click();
    await expect(page.getByRole("dialog", { name: "Manage Exposure Mode" })).toBeVisible();
    await compareState("admin-exposure-phase-1");
    for (const [label, phaseNumber] of [["2 · Prepare", 2], ["3 · Verify", 3], ["4 · Finalize", 4]]) {
      await clickDemoControl(label);
      await page.getByRole("tab", { name: label }).click();
      await compareState(`admin-exposure-phase-${phaseNumber}`);
    }
    await demoRoot.click({ position: { x: 1080, y: 250 } });
    await expect(demoPage.getByText("Finalize verified profile", { exact: true })).toBeHidden();
    await page.getByRole("button", { name: "Close exposure mode manager" }).last().click();

    await clickDemoNav("Overview");
    await page.goto("admin/overview");
    await demoRoot.click({ position: { x: 1322, y: 38 } });
    await page.getByRole("button", { name: "System status" }).click();
    await expect(page.getByRole("complementary", { name: "System status" })).toBeVisible();
    await demoPage.waitForTimeout(500);
    await compareState("admin-system-status");

    await page.getByRole("button", { name: /Theme:/ }).click();
    await demoRoot.click({ position: { x: 1320, y: 840 } });
    await demoPage.waitForTimeout(500);
    await compareState("admin-mixed-theme");
    await page.getByRole("button", { name: /Theme:/ }).click();
    await demoRoot.click({ position: { x: 1320, y: 840 } });
    await demoPage.waitForTimeout(500);
    await compareState("admin-dark-theme");

    await testInfo.attach("meridian-page-theme-matrix-report", {
      body: Buffer.from(JSON.stringify(reports, null, 2)),
      contentType: "application/json",
    });
    const screenshotDirectory = process.env.ELVERN_CONTROL_CENTER_SCREENSHOT_DIR;
    if (screenshotDirectory) {
      mkdirSync(screenshotDirectory, { recursive: true });
      writeFileSync(
        `${screenshotDirectory}/meridian-page-theme-matrix-report.json`,
        `${JSON.stringify(reports, null, 2)}\n`,
        "utf8",
      );
    }
    expect(parityFailures, parityFailures.join("\n")).toEqual([]);
  } finally {
    await demoContext.close();
    await demoBrowser.close();
  }
});


for (const returnCase of [
  {
    label: "Root",
    listPath: "library?category=movies&sort=az",
    canonicalListPath: "library?category=movies&sort=az",
    title: "Phase Seven Alpha",
  },
  {
    label: "Local",
    listPath: "library/local?category=movies&sort=az",
    canonicalListPath: "library?category=movies&sort=az&source=local",
    title: "Phase Seven Alpha",
  },
  {
    label: "Cloud",
    listPath: "library/cloud?category=movies&sort=az",
    canonicalListPath: "library?category=movies&sort=az&source=cloud",
    title: "Phase Seven Beta",
  },
  {
    label: "formal search",
    listPath: "library?category=movies&q=Alpha",
    canonicalListPath: "library?category=movies&q=Alpha",
    title: "Phase Seven Alpha",
  },
]) {
  test(`@settings-navigation Floating Library returns to exact ${returnCase.label} list state`, async ({
    page,
    baseURL,
  }) => {
    await page.goto(returnCase.listPath);
    const expectedListUrl = `${baseURL}${returnCase.canonicalListPath}`;
    await expect(page).toHaveURL(expectedListUrl);
    const card = page.locator(".media-card").filter({ hasText: returnCase.title }).first();
    await expect(card).toBeVisible();
    await card.locator(".media-card__poster-link").click();
    await expect(page).toHaveURL(/\/library\/\d+$/);

    await page.getByRole("link", { name: "Back to library" }).click();
    await expect(page).toHaveURL(expectedListUrl);
    await expect(page.locator(".media-card").filter({ hasText: returnCase.title }).first())
      .toBeVisible();
  });
}


test("canonical Root Local and Cloud use the production v2 route", async ({ page, baseURL }) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);

  await page.goto("library/?category=movies#phase7");
  await expect(page).toHaveURL(`${baseURL}library?category=movies#phase7`);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  await page.goto("library/local/");
  await expect(page).toHaveURL(`${baseURL}library?source=local`);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  await page.goto("library/cloud/");
  await expect(page).toHaveURL(`${baseURL}library?source=cloud`);
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();

  const summaries = requests.filter((request) => request.startsWith("/api/library/v2/summary"));
  expect(summaries.some((request) => request.includes("source=local"))).toBe(true);
  expect(summaries.some((request) => request.includes("source=cloud"))).toBe(true);
  expect(requests.some((request) => request === "/api/library")).toBe(false);
});


test("desktop Island search is Enter-only, clears immediately, and survives Detail return", async ({
  page,
  baseURL,
}) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  const search = page.getByRole("searchbox", { name: "Search library" }).first();
  await search.fill("Beta");
  await page.waitForTimeout(200);
  expect(requests.filter((request) => request.startsWith("/api/library/search")).length).toBe(0);

  await search.press("Enter");
  await expect(page).toHaveURL(`${baseURL}library?category=movies&q=Beta`);
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
  expect(requests.filter((request) => {
    if (!request.startsWith("/api/library/search")) {
      return false;
    }
    return new URL(request, "https://elvern.invalid").searchParams.get("q") === "Beta";
  }).length).toBe(1);

  await page.locator(".media-card__poster-link").first().click();
  await expect(page).toHaveURL(`${baseURL}library/2`);
  await expect(page.getByRole("button", { name: "Open in VLC" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Lite Playback" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Full Playback" })).toBeVisible();
  await page.getByRole("link", { name: "Back to Library" }).click();
  await expect(page).toHaveURL(`${baseURL}library?category=movies&q=Beta`);
  await expect(page.getByRole("searchbox", { name: "Search library" }).first()).toHaveValue("Beta");
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();

  const returnedSearch = page.getByRole("searchbox", { name: "Search library" }).first();
  await returnedSearch.fill("");
  await expect(page).toHaveURL(`${baseURL}library?category=movies`);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect(page.getByText("2 indexed", { exact: true })).toBeVisible();
  await expect(page.getByTestId("library-index-controls")).toHaveCount(1);

  await returnedSearch.fill("NoSuchTitle");
  await page.waitForTimeout(200);
  await expect(page).toHaveURL(`${baseURL}library?category=movies`);
  await returnedSearch.press("Enter");
  await expect(page).toHaveURL(`${baseURL}library?category=movies&q=NoSuchTitle`);
  await expect(page.getByText("0 indexed", { exact: true })).toBeVisible();
  await expect(page.getByText("No matches yet", { exact: true })).toBeVisible();
  await expect(page.getByTestId("library-index-controls")).toHaveCount(1);
});


test("revision change silently refreshes the active Library without a loading reset", async ({ page }) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1), titleSuffix: "" };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length).toBeGreaterThan(0);

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Updated";
  await dispatchGenuinePageReturn(page);

  await expect(page.getByText("Phase Seven Alpha Updated", { exact: true })).toBeVisible();
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("catalog plus progress membership revision performs one summary refresh", async ({ page }) => {
  const requests = [];
  const state = {
    catalogToken: opaqueToken(1, 1),
    progressToken: opaqueToken(1, 5),
    progressSeconds: 120,
    progressDuration: 7200,
    completed: false,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByRole("heading", { name: "Continue watching" })).toBeVisible();
  await expect(page.locator(".media-card__progress")).not.toHaveCount(0);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThan(0);
  const summaryCallsBefore = requests.filter((request) => request.startsWith("/api/library/v2/summary")).length;
  const progressCallsBefore = requests.filter((request) => request === "/api/library/v2/progress-state").length;

  state.catalogToken = opaqueToken(2, 1);
  state.progressToken = opaqueToken(2, 5);
  state.progressSeconds = 0;
  state.titleSuffix = " Single Refresh";
  await dispatchGenuinePageReturn(page);

  await expect(page.getByText("Phase Seven Alpha Single Refresh", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Continue watching" })).toHaveCount(0);
  await expect(page.locator(".media-card__progress")).toHaveCount(0);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/progress-state").length)
    .toBe(progressCallsBefore + 1);
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 1);
  await page.waitForTimeout(250);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore + 1);
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("progress-state 404 falls back to summary while later catalog revisions continue", async ({ page }) => {
  const requests = [];
  const state = {
    catalogToken: opaqueToken(1, 1),
    progressToken: opaqueToken(1, 5),
    progressSeconds: 0,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThan(0);
  const summaryCallsBefore = requests.filter((request) => request.startsWith("/api/library/v2/summary")).length;

  state.progressStateStatus = 404;
  state.progressToken = opaqueToken(2, 5);
  state.progressSeconds = 120;
  await dispatchGenuinePageReturn(page);
  await expect(page.getByRole("heading", { name: "Continue watching" })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/progress-state").length).toBe(1);
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 1);

  await dispatchGenuinePageReturn(page);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThanOrEqual(3);
  expect(requests.filter((request) => request === "/api/library/v2/progress-state")).toHaveLength(1);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore + 1);

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Catalog Still Syncs";
  await dispatchGenuinePageReturn(page);
  await expect(page.getByText("Phase Seven Alpha Catalog Still Syncs", { exact: true }).first()).toBeVisible();
  expect(requests.filter((request) => request === "/api/library/v2/progress-state")).toHaveLength(1);
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 2);
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("extra revision fields preserve old cache until a valid response arrives", async ({ page }) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1), titleSuffix: "" };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThan(0);
  const summaryCallsBefore = requests.filter((request) => request.startsWith("/api/library/v2/summary")).length;

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Validated";
  state.revisionExtraFields = { title: "must-be-rejected" };
  await dispatchGenuinePageReturn(page);
  await expect.poll(() => requests.filter((request) => request === "/api/library/v2/revision").length)
    .toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect(page.getByText("Phase Seven Alpha Validated", { exact: true })).toHaveCount(0);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(summaryCallsBefore);

  state.revisionExtraFields = null;
  await dispatchGenuinePageReturn(page);
  await expect(page.getByText("Phase Seven Alpha Validated", { exact: true })).toBeVisible();
  await expect.poll(() => requests.filter((request) => request.startsWith("/api/library/v2/summary")).length)
    .toBe(summaryCallsBefore + 1);
  await expect(page.getByText("Loading library...")).toHaveCount(0);
});


test("desktop poster context menu remains available", async ({ page }) => {
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await page.locator(".media-card").first().click({ button: "right" });
  await expect(page.getByText("Edit", { exact: true })).toBeVisible();
  await expect(page.getByText("Generate", { exact: true })).toBeVisible();
});


test("two independent same-account contexts apply progress reset and catalog revision silently", async ({ browser, baseURL }) => {
  const sharedState = {
    catalogToken: opaqueToken(0, 1),
    progressToken: opaqueToken(0, 5),
    progressSeconds: 0,
    progressDuration: 7200,
    completed: false,
  };
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const requestsA = [];
  const requestsB = [];
  await installFixture(pageA, requestsA, sharedState);
  await installFixture(pageB, requestsB, sharedState);
  try {
    await Promise.all([pageA.goto(`${baseURL}library`), pageB.goto(`${baseURL}library`)]);
    await expect(pageA.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect(pageB.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect.poll(() => requestsB.filter((request) => request === "/api/library/v2/revision").length)
      .toBeGreaterThan(0);

    await pageA.evaluate(async () => {
      await fetch(new URL("api/progress/1", document.baseURI), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position_seconds: 120, duration_seconds: 7200, completed: false }),
      });
    });
    await dispatchGenuinePageReturn(pageB);
    await expect(pageB.getByRole("heading", { name: "Continue watching" })).toBeVisible();
    await expect(pageB.locator(".media-card__progress")).not.toHaveCount(0);
    await expect(pageB.getByText("Loading library...")).toHaveCount(0);

    await pageA.evaluate(async () => {
      await fetch(new URL("api/progress/1", document.baseURI), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position_seconds: 0, duration_seconds: 7200, completed: false }),
      });
    });
    await dispatchGenuinePageReturn(pageB);
    await expect(pageB.getByRole("heading", { name: "Continue watching" })).toHaveCount(0);
    await expect(pageB.locator(".media-card__progress")).toHaveCount(0);
    await expect(pageB.getByText("Loading library...")).toHaveCount(0);

    await pageA.getByRole("button", { name: "Rescan" }).click();
    await dispatchGenuinePageReturn(pageB);
    await expect(pageB.getByText("Phase Seven Alpha Scan 1", { exact: true })).toBeVisible();
    await expect(pageB.getByText("Loading library...")).toHaveCount(0);
  } finally {
    await Promise.all([contextA.close(), contextB.close()]);
  }
});


test("two different identity contexts keep same-item progress UI isolated", async ({ browser, baseURL }) => {
  const stateA = {
    userId: 7,
    username: "phase7-user-a",
    progressToken: opaqueToken(1, 5),
    progressSeconds: 0,
  };
  const stateB = {
    userId: 8,
    username: "phase7-user-b",
    progressToken: opaqueToken(1, 5),
    progressSeconds: 0,
  };
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const requestsA = [];
  const requestsB = [];
  await installFixture(pageA, requestsA, stateA);
  await installFixture(pageB, requestsB, stateB);
  try {
    await Promise.all([pageA.goto(`${baseURL}library`), pageB.goto(`${baseURL}library`)]);
    await expect(pageA.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect(pageB.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    await expect.poll(() => requestsA.filter((request) => request === "/api/library/v2/revision").length)
      .toBeGreaterThan(0);
    await expect.poll(() => requestsB.filter((request) => request === "/api/library/v2/revision").length)
      .toBeGreaterThan(0);

    stateA.progressToken = opaqueToken(2, 5);
    stateA.progressSeconds = 120;
    await dispatchGenuinePageReturn(pageA);
    await expect(pageA.getByRole("heading", { name: "Continue watching" })).toBeVisible();
    await expect(pageA.locator(".media-card__progress")).not.toHaveCount(0);

    await dispatchGenuinePageReturn(pageB);
    await expect(pageB.getByRole("heading", { name: "Continue watching" })).toHaveCount(0);
    await expect(pageB.locator(".media-card__progress")).toHaveCount(0);
  } finally {
    await Promise.all([contextA.close(), contextB.close()]);
  }
});


test("Desktop Floating Island stays usable at desktop and narrow laptop widths", async ({ page }, testInfo) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1440, height: 900 },
    { width: 1180, height: 760 },
    { width: 1024, height: 768 },
    { width: 800, height: 700 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("library");
    await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
    const island = page.getByRole("navigation", { name: "Library controls" });
    await expect(island).toBeVisible();
    await expect(island.getByText("Elvern", { exact: true })).toBeVisible();
    await expect(island.getByRole("tab")).toHaveCount(4);
    await expect(island.getByRole("button", { name: "Arrange library" })).toBeVisible();
    await expect(island.getByRole("button", { name: /Account:/ })).toBeVisible();
    const search = page.getByRole("searchbox", { name: "Search library" });
    await expect(search).toHaveCount(1);
    await expect(search).toBeEnabled();
    await expect(page.getByRole("button", { name: "Clear search" })).toHaveCount(0);
    const collapsedWidth = (await search.boundingBox()).width;
    await search.click();
    await expect(search).toBeFocused();
    await expect.poll(async () => (await search.boundingBox()).width).toBeGreaterThan(collapsedWidth);
    const searchRequestCount = requests.filter((request) => request.startsWith("/api/library/search")).length;
    await search.fill("Beta");
    await page.waitForTimeout(200);
    expect(requests.filter((request) => request.startsWith("/api/library/search"))).toHaveLength(
      searchRequestCount,
    );
    await search.press("Enter");
    await expect(page).toHaveURL(/library\?category=movies&q=Beta$/);
    await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
    expect(requests.filter((request) => request.startsWith("/api/library/search"))).toHaveLength(
      searchRequestCount + 1,
    );
    expect((await island.boundingBox()).height).toBeGreaterThanOrEqual(63);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`library-root-${viewport.width}x${viewport.height}.png`),
      fullPage: true,
    });
  }
});


test("Desktop Island popovers stay solid, clamped, and directional at top and bottom", async ({
  page,
}) => {
  for (const position of ["top", "bottom"]) {
    await page.unrouteAll({ behavior: "wait" });
    await installFixture(page, [], { desktopIslandPosition: position });
    await page.setViewportSize({ width: 800, height: 700 });
    await page.goto("library");
    await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

    const island = page.getByTestId("desktop-library-island");
    await expect(island).toHaveClass(new RegExp(`desktop-library-island-wrap--${position}`));
    const islandBox = await island.boundingBox();

    await page.getByRole("button", { name: "Arrange library" }).click();
    const arrange = page.getByRole("dialog", { name: "Arrange library" });
    await expect(arrange).toHaveCSS("background-color", "rgb(17, 23, 34)");
    const arrangeBox = await arrange.boundingBox();
    expect(arrangeBox.x).toBeGreaterThanOrEqual(12);
    expect(arrangeBox.x + arrangeBox.width).toBeLessThanOrEqual(788);
    if (position === "top") {
      expect(arrangeBox.y).toBeGreaterThanOrEqual(islandBox.y + islandBox.height);
    } else {
      expect(arrangeBox.y + arrangeBox.height).toBeLessThanOrEqual(islandBox.y);
    }

    await page.getByRole("button", { name: /Account:/ }).click();
    await expect(arrange).toHaveCount(0);
    const accountMenu = page.getByRole("menu", { name: "Account menu" });
    await expect(accountMenu).toHaveCSS("background-color", "rgb(17, 23, 34)");
    const menuBox = await accountMenu.boundingBox();
    expect(menuBox.x).toBeGreaterThanOrEqual(12);
    expect(menuBox.x + menuBox.width).toBeLessThanOrEqual(788);
    if (position === "top") {
      expect(menuBox.y).toBeGreaterThanOrEqual(islandBox.y + islandBox.height);
    } else {
      expect(menuBox.y + menuBox.height).toBeLessThanOrEqual(islandBox.y);
    }
  }
});


test("Arrange keeps multi-filters draft-only and commits one canonical request", async ({ page, baseURL }) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  await page.setViewportSize({ width: 1180, height: 760 });
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  const summaryRequestCount = requests.filter(
    (request) => request.startsWith("/api/library/v2/summary"),
  ).length;
  await page.getByRole("button", { name: "Arrange library" }).click();
  const panel = page.getByRole("dialog", { name: "Arrange library" });
  await panel.getByRole("radio", { name: "Cloud" }).click();
  await panel.getByRole("button", { name: "Action" }).click();
  await panel.getByRole("button", { name: "Drama" }).click();
  await panel.getByRole("button", { name: "Diamond" }).click();
  await panel.getByRole("button", { name: "Gold" }).click();
  expect(requests.filter(
    (request) => request.startsWith("/api/library/v2/summary"),
  )).toHaveLength(summaryRequestCount);
  await expect(panel).toHaveCSS("background-color", "rgb(17, 23, 34)");
  await panel.getByRole("button", { name: "Done" }).click();
  await expect(page).toHaveURL(
    `${baseURL}library?category=movies&source=cloud&genre=Action&genre=Drama`
      + "&quality=diamond&quality=gold",
  );
  await expect.poll(() => requests.filter(
    (request) => request.startsWith("/api/library/v2/summary"),
  ).length).toBe(summaryRequestCount + 1);

  await page.getByRole("button", { name: "Arrange library" }).click();
  await panel.getByRole("button", { name: "Reset" }).click();
  await panel.getByRole("radio", { name: "Local" }).click();
  expect(requests.filter(
    (request) => request.startsWith("/api/library/v2/summary"),
  )).toHaveLength(summaryRequestCount + 1);
  await page.locator("main").click({ position: { x: 12, y: 220 } });
  await expect(page).toHaveURL(`${baseURL}library?category=movies&source=local`);
  await expect.poll(() => requests.filter(
    (request) => request.startsWith("/api/library/v2/summary"),
  ).length).toBe(summaryRequestCount + 2);
});


test("Legacy Local and Cloud redirects preserve source through formal search", async ({ page, baseURL }) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  for (const [source, query, title] of [
    ["local", "Alpha", "Phase Seven Alpha"],
    ["cloud", "Beta", "Phase Seven Beta"],
  ]) {
    await page.goto(`library/${source}`);
    await expect(page).toHaveURL(`${baseURL}library?source=${source}`);
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    const search = page.getByRole("searchbox", { name: "Search library" });
    const searchCount = requests.filter(
      (request) => request.startsWith("/api/library/search"),
    ).length;
    await search.fill(query);
    await page.waitForTimeout(200);
    expect(requests.filter(
      (request) => request.startsWith("/api/library/search"),
    )).toHaveLength(searchCount);
    await search.press("Enter");
    await expect(page).toHaveURL(
      `${baseURL}library?source=${source}&category=movies&q=${query}`,
    );
    await expect(page.getByText(title, { exact: true })).toBeVisible();
    expect(requests.filter(
      (request) => request.startsWith("/api/library/search"),
    )).toHaveLength(searchCount + 1);
  }
});


test("legacy floating-search setting does not disable the desktop Island", async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, [], { floatingSearchEnabled: false });
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  await expect(page.locator(".floating-library-search")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Library controls" })).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "Search library" })).toBeEnabled();
});


test("offline document uses the immediate explicit-offline Oops contract", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
  });
  await page.goto("offline.html");
  await expect(page.locator("#elvern-connection-shell")).toHaveAttribute("data-state", "unreachable");
  await expect(page.locator("[data-connection-oops-copy]")).toHaveText(
    "It looks like you're offline. Please check your connection and try again.",
  );
  await expect(page.locator("[data-connection-retry]")).toBeVisible();
});


test("third-party VLC stays in a separate tab while the Elvern route remains healthy", async ({
  context,
  page,
  baseURL,
}) => {
  const requests = [];
  const state = { desktopStatusDelayMs: 400 };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await registerInterceptedExternalFixture(
    context,
    "https://www.videolan.org/vlc/",
    {
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Mock VLC download</title><p>Mock VLC download</p>",
    },
  );

  await page.goto("install");
  const originalUrl = `${baseURL}settings/playback-apps`;
  await expect(page).toHaveURL(originalUrl);
  const vlcLink = page.getByRole("link", { name: "Download VLC" });
  await expect(vlcLink).toHaveAttribute("target", "_blank");
  await expect(vlcLink).toHaveAttribute("rel", /noopener/);
  await expect(vlcLink).toHaveAttribute("rel", /noreferrer/);

  const popupPromise = context.waitForEvent("page");
  await vlcLink.click();
  const popup = await popupPromise;
  await popup.waitForLoadState("domcontentloaded");
  await expect(popup).toHaveTitle("Mock VLC download");
  await expect(page).toHaveURL(originalUrl);
  await popup.close();

  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await page.locator(".media-card__poster-link").first().click();
  await expect(page).toHaveURL(`${baseURL}library/1`);
  await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
});


test("pagehide abort followed by persisted pageshow cannot let stale Helper status overwrite recovery", async ({
  page,
}) => {
  const requests = [];
  const state = {
    desktopStatusRequestDelays: [2_000, 0],
    desktopHelperStatus: {
      state: "up_to_date",
      helper_required: true,
      same_host: false,
      same_host_detection_source: "client_ip_not_local",
      vlc_detection_state: "installed",
    },
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect.poll(() => state.desktopStatusRequestCount || 0).toBe(1);

  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
  });

  await expect.poll(() => state.desktopStatusRequestCount || 0).toBe(2);
  await expect(page.getByText("Ready")).toBeVisible();
  await page.waitForTimeout(2_100);
  await expect(page.getByText("Ready")).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  expect(requests.filter((request) => request.startsWith("/api/desktop-helper/status"))).toHaveLength(2);
});


for (const faultMode of ["fetch", "body_network"]) {
  test(`a no-cache Library ${faultMode === "fetch" ? "fetch" : "body"}-stage failure recovers without an empty-state lie`, async ({
    page,
  }) => {
    const bodyFaultServer = faultMode === "body_network"
      ? await startBodyFaultServer()
      : null;
    const requests = [];
    const state = {};
    try {
      await page.unrouteAll({ behavior: "wait" });
      await installFixture(page, requests, state);
      await installFetchFaults(page, [{
        id: "library",
        match: "/api/library/v2/summary",
        mode: faultMode,
        faultUrl: bodyFaultServer?.url,
      }]);

      await page.goto("library?category=movies&quality=all");

      await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
      await expect(page.getByText("No media indexed yet")).toHaveCount(0);
      await expect(page.getByText("No matches yet")).toHaveCount(0);
      await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
      expect(await page.evaluate(() => window.__elvernFetchFaults.library.triggered)).toBe(1);
      expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(1);
      if (bodyFaultServer) {
        expect(bodyFaultServer.requestCount).toBe(1);
      }
    } finally {
      await bodyFaultServer?.close();
    }
  });
}


test("a no-cache Source failure recovers on the exact source URL without duplicate refetch", async ({
  page,
  baseURL,
}) => {
  const requests = [];
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests);
  await installFetchFaults(page, [{
    id: "source",
    match: "/api/library/v2/summary",
    mode: "fetch",
  }]);

  await page.goto("library?source=cloud&genre=Drama");

  await expect(page).toHaveURL(`${baseURL}library?source=cloud&genre=Drama`);
  await expect(page.getByText("Phase Seven Beta", { exact: true })).toBeVisible();
  await expect(page.getByText("No media indexed yet")).toHaveCount(0);
  await expect(page.getByText("No matches yet")).toHaveCount(0);
  expect(await page.evaluate(() => window.__elvernFetchFaults.source.triggered)).toBe(1);
  expect(requests.filter((request) => (
    request.startsWith("/api/library/v2/summary") && request.includes("source=cloud")
  ))).toHaveLength(1);
});


test("cached Library waits for a late recovery without clearing content or filters", async ({
  page,
  baseURL,
}) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1) };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "cachedLibrary",
    match: "/api/library/v2/summary",
    mode: "fetch",
    armed: false,
  }]);
  await page.goto("library?category=movies&quality=silver");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  state.healthHealthy = false;
  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Recovered";
  await page.evaluate(() => {
    window.__elvernFetchFaults.cachedLibrary.armed = true;
  });
  await dispatchGenuinePageReturn(page);

  await expect.poll(
    () => page.evaluate(() => window.__elvernFetchFaults.cachedLibrary.triggered),
  ).toBe(1);
  await expect(page).toHaveURL(`${baseURL}library?category=movies&quality=silver`);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect(page.getByText("Loading library...")).toHaveCount(0);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));

  await expect(page.getByText("Phase Seven Alpha Recovered", { exact: true })).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  expect(requests.filter((request) => request.startsWith("/api/library/v2/summary"))).toHaveLength(2);
});


test("an incident opened during an older health probe receives a second qualifying probe", async ({
  page,
}) => {
  const requests = [];
  const state = { catalogToken: opaqueToken(1, 1) };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "oldProbe",
    match: "/api/library/v2/summary",
    mode: "fetch",
    armed: false,
  }]);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();

  const healthCountBefore = state.healthRequestCount;
  state.nextHealthDelayMs = 700;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => state.healthRequestCount).toBe(healthCountBefore + 1);

  state.catalogToken = opaqueToken(2, 1);
  state.titleSuffix = " Qualified";
  await page.evaluate(() => {
    window.__elvernFetchFaults.oldProbe.armed = true;
  });
  await dispatchGenuinePageReturn(page);

  await expect(page.getByText("Phase Seven Alpha Qualified", { exact: true })).toBeVisible();
  await expect.poll(() => state.healthRequestCount).toBeGreaterThanOrEqual(healthCountBefore + 2);
  expect(await page.evaluate(() => window.__elvernFetchFaults.oldProbe.triggered)).toBe(1);
});


test("metadata recovery starts every applicable Detail auxiliary read", async ({ page }) => {
  const requests = [];
  const state = {
    role: "admin",
    detailSourceKind: "cloud",
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "metadata",
    match: "/api/library/item/1",
    mode: "fetch",
  }]);

  await page.goto("library/1");

  await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();
  for (const path of [
    "/api/progress/1",
    "/api/playback/1",
    "/api/desktop-playback/1",
    "/api/browser-playback/items/1/active",
    "/api/cloud-libraries",
    "/api/admin/media-library-reference",
  ]) {
    await expect.poll(() => state.pathRequestCounts?.[path] || 0).toBeGreaterThanOrEqual(1);
  }
  expect(await page.evaluate(() => window.__elvernFetchFaults.metadata.triggered)).toBe(1);
  await expect(page.getByText("Reconnecting…")).toHaveCount(0);
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
});


test("Detail selectively retries only transient progress and clears its old error", async ({ page }) => {
  const bodyFaultServer = await startBodyFaultServer();
  const requests = [];
  const state = {
    healthHealthy: false,
    allowHealthyHealthRequests: 1,
  };
  try {
    await page.unrouteAll({ behavior: "wait" });
    await installFixture(page, requests, state);
    await installFetchFaults(page, [{
      id: "progress",
      match: "/api/progress/1",
      mode: "body_network",
      faultUrl: bodyFaultServer.url,
    }]);

    await page.goto("library/1");
    await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();
    await expect(page.getByText("Reconnecting…")).toBeVisible();
    const metadataRequests = state.pathRequestCounts?.["/api/library/item/1"] || 0;
    const playbackRequests = state.pathRequestCounts?.["/api/playback/1"] || 0;

    state.healthHealthy = true;
    await page.evaluate(() => window.dispatchEvent(new Event("online")));

    await expect(page.getByText("Reconnecting…")).toHaveCount(0);
    expect(state.pathRequestCounts?.["/api/library/item/1"] || 0).toBe(metadataRequests);
    expect(state.pathRequestCounts?.["/api/playback/1"] || 0).toBe(playbackRequests);
    expect(state.pathRequestCounts?.["/api/progress/1"] || 0).toBe(1);
    expect(await page.evaluate(() => window.__elvernFetchFaults.progress.triggered)).toBe(1);
    expect(bodyFaultServer.requestCount).toBe(1);
    await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  } finally {
    await bodyFaultServer.close();
  }
});


test("shared settings observers make one production request", async ({ page }) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);

  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await page.locator(".media-card__poster-link").first().click();
  await expect(page.getByRole("heading", { name: "Phase Seven Alpha" })).toBeVisible();

  expect(state.pathRequestCounts?.["/api/user-settings"] || 0).toBe(1);
});


test("Helper verify coalesces resume and recovery into one follow-up status read", async ({
  page,
}) => {
  const requests = [];
  const state = {
    desktopVerifyDelayMs: 500,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "helperStatus",
    match: "/api/desktop-helper/status",
    mode: "fetch",
    armed: false,
  }]);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.__elvernFetchFaults.helperStatus.armed = true;
  });
  await dispatchGenuinePageReturn(page);
  await expect(page.getByText("Reconnecting…")).toBeVisible();

  await page.getByRole("button", { name: "Check VLC on this host" }).click();
  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await dispatchGenuinePageReturn(page);

  await expect.poll(() => state.pathRequestCounts?.["/api/desktop-helper/verify"] || 0).toBe(1);
  await expect.poll(() => state.pathRequestCounts?.["/api/desktop-helper/status"] || 0).toBe(2);
  await expect(page.getByText("Reconnecting…")).toHaveCount(0);
  await page.waitForTimeout(650);
  expect(state.pathRequestCounts?.["/api/desktop-helper/status"] || 0).toBe(2);
});


test("headers-received Helper status aborts cleanly on pagehide and resumes once", async ({
  page,
}) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "pendingBody",
    match: "/api/desktop-helper/status",
    mode: "pending_body",
  }]);

  await page.goto("install");
  await expect.poll(
    () => page.evaluate(() => window.__elvernFetchFaults.pendingBody.triggered),
  ).toBe(1);
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    window.dispatchEvent(new Event("focus"));
  });

  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  await expect(page.getByText(/NetworkError when attempting to fetch resource/i)).toHaveCount(0);
  expect(state.pathRequestCounts?.["/api/desktop-helper/status"] || 0).toBe(1);
});


test("a hidden page cancels a pending resume until the next visible return", async ({ page }) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  const revisionCount = state.pathRequestCounts?.["/api/library/v2/revision"] || 0;

  await page.evaluate(() => {
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    window.__setTestVisibility = (value) => {
      visibility = value;
      document.dispatchEvent(new Event("visibilitychange"));
    };
    window.dispatchEvent(new Event("blur"));
    window.dispatchEvent(new Event("focus"));
    window.__setTestVisibility("hidden");
  });
  await page.waitForTimeout(350);
  expect(state.pathRequestCounts?.["/api/library/v2/revision"] || 0).toBe(revisionCount);

  await page.evaluate(() => {
    window.__setTestVisibility("visible");
    window.dispatchEvent(new Event("focus"));
  });
  await expect.poll(
    () => state.pathRequestCounts?.["/api/library/v2/revision"] || 0,
  ).toBe(revisionCount + 1);
});


test("malformed 401 body clears protected Library cache without exposing parser text", async ({
  page,
}) => {
  const requests = [];
  const state = {};
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await installFetchFaults(page, [{
    id: "malformed401",
    match: "/api/desktop-helper/status",
    mode: "malformed_401",
    armed: false,
  }]);
  await page.goto("library");
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  expect(state.pathRequestCounts?.["/api/library/v2/summary"] || 0).toBe(1);

  await page.evaluate(() => {
    window.__elvernFetchFaults.malformed401.armed = true;
  });
  await page.getByRole("button", { name: /Account:/ }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await page.getByRole("link", { name: "Playback & Apps" }).click();
  await expect(page).toHaveURL(/\/settings\/playback-apps$/);
  await expect(page.getByText("Elvern received an unreadable response from the server.")).toBeVisible();
  await expect(page.getByText(/SyntaxError|Unexpected end|NetworkError when attempting/i)).toHaveCount(0);

  await page.locator(".meridian-sidebar")
    .getByRole("button", { name: "Library" })
    .click();
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  expect(state.pathRequestCounts?.["/api/library/v2/summary"] || 0).toBe(2);
});


test("a poster error before recovery retries exactly once", async ({ page }) => {
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  state.healthHealthy = false;
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  await page.locator(".meridian-sidebar")
    .getByRole("button", { name: "Library" })
    .click();
  await expect(page).toHaveURL(/\/library\?category=movies$/);
  await expect(page.getByText("Phase Seven Alpha", { exact: true })).toBeVisible();
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));

  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(2);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  await page.waitForTimeout(700);
  expect(state.posterRequestCounts["/api/posters/1"]).toBe(2);
});


test("a recovered attach-time poster incident still retries a late onError once", async ({ page }) => {
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
    waitBeforeFirstPosterFailure: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();
  state.healthHealthy = false;
  await page.evaluate(() => {
    window.__posterRecoveryEvents = 0;
    window.addEventListener("elvern:connectivity-recovered", () => {
      window.__posterRecoveryEvents += 1;
    });
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  await page.locator(".meridian-sidebar")
    .getByRole("button", { name: "Library" })
    .click();
  await expect(page).toHaveURL(/\/library\?category=movies$/);
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => page.evaluate(() => window.__posterRecoveryEvents)).toBe(1);
  state.releaseFirstPosterFailure();

  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(2);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  expect(state.posterRequestCounts["/api/posters/1"]).toBe(2);
});


test("consecutive poster incidents each retain their own single recovery retry", async ({
  page,
}) => {
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
    waitBeforeFirstPosterFailure: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.__posterRecoveryEvents = 0;
    window.addEventListener("elvern:connectivity-recovered", () => {
      window.__posterRecoveryEvents += 1;
    });
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  await page.locator(".meridian-sidebar")
    .getByRole("button", { name: "Library" })
    .click();
  await expect(page).toHaveURL(/\/library\?category=movies$/);
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => page.evaluate(() => window.__posterRecoveryEvents)).toBe(1);

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  state.releaseFirstPosterFailure();

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => page.evaluate(() => window.__posterRecoveryEvents)).toBe(2);
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(2);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  expect(state.posterRequestCounts["/api/posters/1"]).toBe(2);
});


test("poster recovery stays authoritative while no MediaCard subscriber exists", async ({ page }) => {
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
    posterFailureEnabled: false,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.__posterRecoveryEvents = 0;
    window.addEventListener("elvern:connectivity-recovered", () => {
      window.__posterRecoveryEvents += 1;
    });
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => page.evaluate(() => window.__posterRecoveryEvents)).toBe(1);

  await page.locator(".meridian-sidebar")
    .getByRole("button", { name: "Library" })
    .click();
  await expect(page).toHaveURL(/\/library\?category=movies$/);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  expect(state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);
});


test("an active poster incident remains recoverable beyond the former 30 second window", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const requests = [];
  const state = {
    posterRecoveryEnabled: true,
  };
  await page.unrouteAll({ behavior: "wait" });
  await installFixture(page, requests, state);
  await page.goto("install");
  await expect(page.getByText("Not required on this Elvern host")).toBeVisible();

  state.healthHealthy = false;
  await page.evaluate(() => {
    window.dispatchEvent(new CustomEvent("elvern:connectivity-failure", {
      detail: { classification: "transport", requestClass: "library" },
    }));
  });
  await page.locator(".meridian-sidebar")
    .getByRole("button", { name: "Library" })
    .click();
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(1);
  await page.waitForTimeout(30_100);

  state.healthHealthy = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => state.posterRequestCounts?.["/api/posters/1"] || 0).toBe(2);
  await expect(page.locator(".media-card__poster-image--loaded").first()).toBeVisible();
  expect(state.posterRequestCounts["/api/posters/1"]).toBe(2);
});
