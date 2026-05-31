export const BACKGROUND_PRESETS = [
  {
    value: "neon",
    label: "Neon",
    swatch:
      "radial-gradient(circle at 18% 18%, rgba(214, 72, 176, 0.42), transparent 28%), linear-gradient(135deg, #74114f 0%, #432486 58%, #1b41b5 100%)",
  },
  {
    value: "basic",
    label: "Basic",
    swatch: "#3d4652",
  },
  {
    value: "midnight",
    label: "Midnight",
    swatch: "linear-gradient(135deg, #06111f 0%, #13283d 48%, #08111d 100%)",
  },
  {
    value: "aurora",
    label: "Aurora",
    swatch: "linear-gradient(135deg, #10463d 0%, #255b76 48%, #3a2a78 100%)",
  },
  {
    value: "rose",
    label: "Rose",
    swatch: "linear-gradient(135deg, #5a1732 0%, #843a5c 46%, #272355 100%)",
  },
  {
    value: "ocean",
    label: "Ocean",
    swatch: "linear-gradient(135deg, #05324a 0%, #146078 46%, #172c66 100%)",
  },
];

export const DEFAULT_BACKGROUND_SETTINGS = {
  background_mode: "preset",
  background_preset: "neon",
  background_gradient_start: "#74114f",
  background_gradient_end: "#1b41b5",
  background_gradient_accent: "#5c1867",
  background_solid_color: "#151a21",
  background_photo_url: null,
};

const PRESET_VALUES = new Set(BACKGROUND_PRESETS.map((preset) => preset.value));
const BACKGROUND_MODES = new Set(["preset", "gradient", "solid", "photo"]);
const SAFE_HEX_COLOR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

function clampColorChannel(value) {
  return Math.max(0, Math.min(255, Math.round(value)));
}

function normalizeHexColor(value, fallback) {
  const candidate = String(value || "").trim();
  if (!SAFE_HEX_COLOR_RE.test(candidate)) {
    return fallback;
  }
  if (candidate.length === 4) {
    return `#${candidate
      .slice(1)
      .split("")
      .map((character) => `${character}${character}`)
      .join("")}`.toLowerCase();
  }
  return candidate.toLowerCase();
}

function hexToRgb(hexColor) {
  const normalized = normalizeHexColor(hexColor, "#000000").slice(1);
  return {
    r: parseInt(normalized.slice(0, 2), 16),
    g: parseInt(normalized.slice(2, 4), 16),
    b: parseInt(normalized.slice(4, 6), 16),
  };
}

function rgbToHex({ r, g, b }) {
  return `#${[r, g, b]
    .map((channel) => clampColorChannel(channel).toString(16).padStart(2, "0"))
    .join("")}`;
}

function rgbToHsl({ r, g, b }) {
  const red = r / 255;
  const green = g / 255;
  const blue = b / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const lightness = (max + min) / 2;

  if (max === min) {
    return { h: 0, s: 0, l: lightness };
  }

  const delta = max - min;
  const saturation = lightness > 0.5 ? delta / (2 - max - min) : delta / (max + min);
  let hue;
  if (max === red) {
    hue = (green - blue) / delta + (green < blue ? 6 : 0);
  } else if (max === green) {
    hue = (blue - red) / delta + 2;
  } else {
    hue = (red - green) / delta + 4;
  }
  return { h: hue / 6, s: saturation, l: lightness };
}

function hueToRgbChannel(p, q, t) {
  let nextT = t;
  if (nextT < 0) {
    nextT += 1;
  }
  if (nextT > 1) {
    nextT -= 1;
  }
  if (nextT < 1 / 6) {
    return p + (q - p) * 6 * nextT;
  }
  if (nextT < 1 / 2) {
    return q;
  }
  if (nextT < 2 / 3) {
    return p + (q - p) * (2 / 3 - nextT) * 6;
  }
  return p;
}

function hslToHex({ h, s, l }) {
  if (s === 0) {
    const channel = l * 255;
    return rgbToHex({ r: channel, g: channel, b: channel });
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return rgbToHex({
    r: hueToRgbChannel(p, q, h + 1 / 3) * 255,
    g: hueToRgbChannel(p, q, h) * 255,
    b: hueToRgbChannel(p, q, h - 1 / 3) * 255,
  });
}

function mixHexColors(left, right, ratio) {
  const leftRgb = hexToRgb(left);
  const rightRgb = hexToRgb(right);
  return rgbToHex({
    r: leftRgb.r + (rightRgb.r - leftRgb.r) * ratio,
    g: leftRgb.g + (rightRgb.g - leftRgb.g) * ratio,
    b: leftRgb.b + (rightRgb.b - leftRgb.b) * ratio,
  });
}

function clampUnit(value) {
  return Math.max(0, Math.min(1, value));
}

export function deriveGradientEndFromSingleColor(color) {
  const normalized = normalizeHexColor(color, DEFAULT_BACKGROUND_SETTINGS.background_gradient_start);
  const hsl = rgbToHsl(hexToRgb(normalized));
  return hslToHex({
    h: (hsl.h + 0.18) % 1,
    s: clampUnit(Math.max(0.58, hsl.s + 0.08)),
    l: clampUnit(Math.min(0.48, Math.max(0.22, hsl.l - 0.14))),
  });
}

export function deriveGradientColorsFromSingleColor(color) {
  const normalized = normalizeHexColor(color, DEFAULT_BACKGROUND_SETTINGS.background_gradient_start);
  const hsl = rgbToHsl(hexToRgb(normalized));
  const accent = hslToHex({
    h: (hsl.h + 0.08) % 1,
    s: clampUnit(Math.max(0.66, hsl.s + 0.1)),
    l: clampUnit(Math.min(0.58, Math.max(0.3, hsl.l + 0.02))),
  });
  const end = deriveGradientEndFromSingleColor(normalized);
  return {
    background_gradient_start: normalized,
    background_gradient_accent: accent,
    background_gradient_end: end,
  };
}

export function getBackgroundPickerPositionFromColor(color) {
  const normalized = normalizeHexColor(color, DEFAULT_BACKGROUND_SETTINGS.background_gradient_start);
  const hsl = rgbToHsl(hexToRgb(normalized));
  return {
    x: Math.max(0, Math.min(1, hsl.h)),
    y: Math.max(0, Math.min(1, (0.82 - hsl.l) / 0.58)),
  };
}

export function getBackgroundPickerColorAtPosition(x, y) {
  const hue = Math.max(0, Math.min(1, Number.isFinite(x) ? x : 0));
  const vertical = Math.max(0, Math.min(1, Number.isFinite(y) ? y : 0.5));
  const lightness = 0.82 - vertical * 0.58;
  const saturation = 0.78;
  return hslToHex({ h: hue, s: saturation, l: lightness });
}

export function normalizeUserBackgroundSettings(payload = {}) {
  const modeCandidate = String(payload.background_mode || DEFAULT_BACKGROUND_SETTINGS.background_mode).toLowerCase();
  let backgroundMode = BACKGROUND_MODES.has(modeCandidate) ? modeCandidate : "preset";
  const presetCandidate = String(payload.background_preset || DEFAULT_BACKGROUND_SETTINGS.background_preset).toLowerCase();
  const backgroundPreset = PRESET_VALUES.has(presetCandidate) ? presetCandidate : "neon";
  const photoUrl =
    typeof payload.background_photo_url === "string" && payload.background_photo_url.startsWith("/api/")
      ? payload.background_photo_url
      : null;
  if (backgroundMode === "photo" && !photoUrl) {
    backgroundMode = "preset";
  }
  const gradientStart = normalizeHexColor(
    payload.background_gradient_start,
    DEFAULT_BACKGROUND_SETTINGS.background_gradient_start,
  );
  const gradientEnd = normalizeHexColor(
    payload.background_gradient_end,
    deriveGradientEndFromSingleColor(gradientStart),
  );
  const gradientAccent = normalizeHexColor(
    payload.background_gradient_accent,
    mixHexColors(gradientStart, gradientEnd, 0.45),
  );
  const solidColor = normalizeHexColor(
    payload.background_solid_color,
    DEFAULT_BACKGROUND_SETTINGS.background_solid_color,
  );
  return {
    background_mode: backgroundMode,
    background_preset: backgroundPreset,
    background_gradient_start: gradientStart,
    background_gradient_end: gradientEnd,
    background_gradient_accent: gradientAccent,
    background_solid_color: solidColor,
    background_photo_url: photoUrl,
  };
}

export function buildBackgroundPreviewStyle(settings) {
  const normalized = normalizeUserBackgroundSettings(settings);
  if (normalized.background_mode === "solid") {
    return { background: normalized.background_solid_color };
  }
  if (normalized.background_mode === "gradient") {
    const gradientEnd =
      normalized.background_gradient_end === normalized.background_gradient_start
        ? deriveGradientEndFromSingleColor(normalized.background_gradient_start)
        : normalized.background_gradient_end;
    return {
      background: `linear-gradient(135deg, ${normalized.background_gradient_start} 0%, ${normalized.background_gradient_accent} 45%, ${gradientEnd} 100%)`,
    };
  }
  if (normalized.background_mode === "photo" && normalized.background_photo_url) {
    return {
      backgroundImage: `linear-gradient(135deg, rgba(5, 9, 14, 0.45), rgba(7, 10, 18, 0.62)), url("${normalized.background_photo_url}")`,
      backgroundPosition: "center",
      backgroundSize: "cover",
    };
  }
  const preset = BACKGROUND_PRESETS.find((entry) => entry.value === normalized.background_preset) || BACKGROUND_PRESETS[0];
  return { background: preset.swatch };
}

export function applyUserBackgroundTheme(
  settings,
  root = typeof document !== "undefined" ? document.documentElement : null,
) {
  const normalized = normalizeUserBackgroundSettings(settings);
  if (!root) {
    return normalized;
  }
  root.dataset.elvernBackgroundMode = normalized.background_mode;
  root.dataset.elvernBackgroundPreset = normalized.background_preset;
  root.style.setProperty("--app-background-gradient-start", normalized.background_gradient_start);
  root.style.setProperty("--app-background-gradient-end", normalized.background_gradient_end);
  root.style.setProperty("--app-background-gradient-accent", normalized.background_gradient_accent);
  root.style.setProperty("--app-background-solid-color", normalized.background_solid_color);
  if (normalized.background_photo_url) {
    root.style.setProperty("--app-background-photo", `url("${normalized.background_photo_url}")`);
  } else {
    root.style.removeProperty("--app-background-photo");
  }
  return normalized;
}

export function resetUserBackgroundTheme(root = typeof document !== "undefined" ? document.documentElement : null) {
  if (!root) {
    return;
  }
  delete root.dataset.elvernBackgroundMode;
  delete root.dataset.elvernBackgroundPreset;
  root.style.removeProperty("--app-background-gradient-start");
  root.style.removeProperty("--app-background-gradient-end");
  root.style.removeProperty("--app-background-gradient-accent");
  root.style.removeProperty("--app-background-solid-color");
  root.style.removeProperty("--app-background-photo");
}
