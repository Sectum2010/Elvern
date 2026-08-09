import { describe, expect, test } from "vitest";

import { normalizeUserBackgroundSettings } from "./userBackground.js";

describe("normalizeUserBackgroundSettings legacy hue drafts", () => {
  test("derives legacy gradient and solid hue drafts from the saved HEX colors", () => {
    const settings = normalizeUserBackgroundSettings({
      background_custom_model: "legacy_v1",
      background_gradient_start: "#ff0000",
      background_gradient_end: "#00ff00",
      background_solid_color: "#0000ff",
      background_gradient_start_hue: 210,
      background_gradient_end_hue: 330,
      background_solid_hue: 210,
    });

    expect(settings.background_gradient_start_hue).toBe(0);
    expect(settings.background_gradient_end_hue).toBe(120);
    expect(settings.background_solid_hue).toBe(240);
    expect(settings.background_custom_model).toBe("legacy_v1");
  });

  test("preserves explicit hue_v2 values", () => {
    const settings = normalizeUserBackgroundSettings({
      background_custom_model: "hue_v2",
      background_gradient_start: "#ff0000",
      background_gradient_end: "#00ff00",
      background_solid_color: "#0000ff",
      background_gradient_start_hue: 17,
      background_gradient_end_hue: 219,
      background_solid_hue: 301,
    });

    expect(settings.background_gradient_start_hue).toBe(17);
    expect(settings.background_gradient_end_hue).toBe(219);
    expect(settings.background_solid_hue).toBe(301);
  });
});
