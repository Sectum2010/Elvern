import { describe, expect, test } from "vitest";

import {
  canAccessAssistant,
  resolveAssistantNavigationTarget,
} from "./assistantAccess.js";


describe("Assistant access", () => {
  test.each([
    [{ role: "admin", assistant_beta_enabled: false }, true, "/admin/assistant"],
    [{ role: "admin", assistant_beta_enabled: true }, true, "/admin/assistant"],
    [{ role: "standard_user", assistant_beta_enabled: true }, true, "/assistant"],
    [{ role: "standard_user", assistant_beta_enabled: false }, false, null],
    [null, false, null],
  ])("resolves the approved access matrix for %j", (user, allowed, target) => {
    expect(canAccessAssistant(user)).toBe(allowed);
    expect(resolveAssistantNavigationTarget(user)).toBe(target);
  });
});
