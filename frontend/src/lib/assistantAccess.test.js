import { describe, expect, test } from "vitest";

import {
  canAccessAssistant,
  classifyPrimaryNavigationRoute,
  deriveAssistantContext,
  resolveAssistantNavigationTarget,
  sanitizeAssistantContextPath,
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

  test.each([
    ["/attachments/42/view", { role: "standard_user", assistant_beta_enabled: true }, "assistant"],
    ["/attachments/42/view", { role: "admin", assistant_beta_enabled: false }, "assistant"],
    ["/attachments/42/view", { role: "standard_user", assistant_beta_enabled: false }, null],
    ["/utility/unknown", { role: "standard_user", assistant_beta_enabled: true }, null],
    ["/library/42", { role: "standard_user", assistant_beta_enabled: true }, "library"],
    ["/settings", { role: "admin" }, "settings"],
    ["/admin/security", { role: "admin" }, "admin"],
  ])("classifies %s without defaulting unknown routes to Library", (path, user, expected) => {
    expect(classifyPrimaryNavigationRoute(path, user)).toBe(expected);
  });

  test.each([
    ["/library/local?genre=Action&q=private#results", "/library/local"],
    ["/library/cloud?sort=title", "/library/cloud"],
    ["/library/123?q=secret#player", "/library/123"],
    ["/settings?section=install&token=private", "/settings"],
    ["/library?q=private", "/library"],
    ["//evil", ""],
    ["https://external.example/library/1", ""],
    ["/unknown?token=private", ""],
  ])("keeps only an allowlisted pathname from %s", (value, expected) => {
    expect(sanitizeAssistantContextPath(value)).toBe(expected);
  });

  test("derives safe structured context without query, hash, or token values", () => {
    expect(deriveAssistantContext("/library/123?q=private#player")).toEqual({
      page_context: "/library/123",
      source_context: "library_detail",
      related_entity_type: "media_item",
      related_entity_id: "123",
    });
    expect(JSON.stringify(
      deriveAssistantContext("/settings?section=install&token=private"),
    )).not.toMatch(/section|token|private|#/);
    expect(deriveAssistantContext("//evil")).toEqual({
      page_context: null,
      source_context: null,
      related_entity_type: null,
      related_entity_id: null,
    });
  });
});
