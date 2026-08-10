import { readControlCenterTheme } from "./controlCenterSession.js";


export const CONTROL_CENTER_PAINT_CLASS = "elvern-control-center-paint";

const PAINT_COLORS = Object.freeze({
  light: "#f3e9d8",
  mixed: "#131110",
  dark: "#131110",
});


function documentPaintTargets(documentObject) {
  return [
    documentObject?.documentElement,
    documentObject?.body,
    documentObject?.getElementById?.("root"),
    documentObject?.getElementById?.("elvern-app-paint-floor"),
  ].filter(Boolean);
}


export function applyControlCenterPaint({
  active,
  documentObject = globalThis.document,
  theme = readControlCenterTheme(),
} = {}) {
  const normalizedTheme = Object.hasOwn(PAINT_COLORS, theme) ? theme : "light";
  for (const target of documentPaintTargets(documentObject)) {
    target.classList.toggle(CONTROL_CENTER_PAINT_CLASS, active === true);
    if (active === true) {
      target.dataset.elvernControlCenterTheme = normalizedTheme;
      target.style.setProperty("--elvern-control-center-paint", PAINT_COLORS[normalizedTheme]);
    } else {
      delete target.dataset.elvernControlCenterTheme;
      target.style.removeProperty("--elvern-control-center-paint");
    }
  }
}
