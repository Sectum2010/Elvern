import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const pagesDir = path.dirname(fileURLToPath(import.meta.url));
const adminPagePath = path.resolve(pagesDir, "AdminPage.jsx");
const stylesPath = path.resolve(pagesDir, "../styles.css");

function readAdminPage() {
  return fs.readFileSync(adminPagePath, "utf8");
}

function readStyles() {
  return fs.readFileSync(stylesPath, "utf8");
}

describe("AdminPage invite code guards", () => {
  test("invite delete uses the in-app confirmation modal and revoke endpoint", () => {
    const source = readAdminPage();

    expect(source).not.toContain("window.confirm");
    expect(source).toContain("Delete invite code?");
    expect(source).toContain("This invite code will be revoked immediately and can no longer be used.");
    expect(source).toContain("/api/admin/invite-codes/${inviteCode.id}/revoke");
    expect(source).toContain("aria-label=\"Delete invite code\"");
  });

  test("generated invite code list collapses without making Generate toggle it", () => {
    const source = readAdminPage();

    expect(source).toContain("const [inviteCodesExpanded, setInviteCodesExpanded] = useState(true)");
    expect(source).toContain("setInviteCodesExpanded(true)");
    expect(source).toContain("aria-expanded={inviteCodesExpanded}");
    expect(source).toContain("inviteCodesExpanded ? (");
    expect(source).toContain("className=\"admin-invite-code-header__summary\"");
    expect(source).toContain("onClick={toggleInviteCodesExpanded}");
    expect(source).toContain("onClick={handleGenerateInviteCode}");
    expect(source).not.toContain("Show codes");
    expect(source).not.toContain("Hide codes");
    expect(source).not.toContain("admin-invite-code-header__chevron");
  });

  test("invite list spacing is outside the generated code card internals", () => {
    const styles = readStyles();

    expect(styles).toContain(".admin-list--dense.admin-invite-code-list");
    expect(styles).toMatch(/\.admin-list--dense\.admin-invite-code-list\s*\{[^}]*margin-top:\s*1rem;/s);
    expect(styles).toMatch(/\.admin-list--dense\.admin-invite-code-list\s*\{[^}]*width:\s*min\(100%,\s*37rem\);/s);
    expect(styles).not.toMatch(/\.admin-invite-code-row\s*\{[^}]*margin-top:/s);
  });

  test("invite delete modal actions use the compact modal row layout", () => {
    const styles = readStyles();

    expect(styles).toMatch(/\.browser-resume-modal__actions\.admin-confirm-modal__actions\s*\{[^}]*display:\s*flex;/s);
    expect(styles).toMatch(/\.browser-resume-modal__actions\.admin-confirm-modal__actions\s*\{[^}]*justify-content:\s*flex-end;/s);
    expect(styles).toMatch(/\.browser-resume-modal__actions\.admin-confirm-modal__actions > button\s*\{[^}]*flex:\s*0 0 auto;/s);
  });
});

describe("AdminPage password help request guards", () => {
  test("password help cards have list spacing without changing card padding", () => {
    const styles = readStyles();

    expect(styles).toMatch(/\.password-help-request-list\s*\{[^}]*margin-top:\s*1rem;/s);
    expect(styles).not.toMatch(/\.password-help-request-card\s*\{[^}]*padding:/s);
  });

  test("password help request cards expose inline request details from stored metadata", () => {
    const source = readAdminPage();

    expect(source).toContain("expandedPasswordHelpRequestId");
    expect(source).toContain("aria-label=\"Password request details\"");
    expect(source).toContain("password-help-request-card__info-glyph");
    expect(source).toContain("requestEntry.requester_ip_address");
    expect(source).toContain("requestEntry.requester_user_agent");
    expect(source).toContain("IP address");
    expect(source).toContain("Detected device");
    expect(source).toContain("Browser");
    expect(source).toContain("unknownIfEmpty");
    expect(source).toContain("detectPasswordHelpDevice");
    expect(source).toContain("detectPasswordHelpBrowser");
  });

  test("password help refresh keeps the same handler path with a transient border sweep", () => {
    const source = readAdminPage();
    const styles = readStyles();

    expect(source).toContain("handlePasswordHelpRefresh");
    expect(source).toContain("await loadPasswordHelpRequests();");
    expect(source).toContain("passwordHelpRefreshSweepActive");
    expect(source).toContain("PASSWORD_HELP_REFRESH_SWEEP_MS");
    expect(styles).toContain(".password-help-refresh-button--sweep::after");
    expect(styles).toContain("@keyframes password-help-refresh-border-sweep");
    expect(styles).toContain("@media (prefers-reduced-motion: reduce)");
    const refreshSweepStart = styles.indexOf(".password-help-refresh-button--sweep::after");
    const refreshSweepEnd = styles.indexOf("@media (prefers-reduced-motion: reduce)", refreshSweepStart);
    const refreshSweepStyles = styles.slice(refreshSweepStart, refreshSweepEnd);
    expect(refreshSweepStyles).toContain("border: 2px solid rgba(74, 222, 255, 0.98)");
    expect(refreshSweepStyles).toContain("clip-path: polygon");
    expect(refreshSweepStyles).not.toContain("conic-gradient");
    expect(refreshSweepStyles).not.toContain("rotate(");
  });

  test("password help section does not use native browser popups", () => {
    const source = readAdminPage();
    const start = source.indexOf("<h2>Password help requests</h2>");
    const end = source.indexOf("const logsSection =", start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    const sectionSource = source.slice(start, end);

    expect(sectionSource).not.toMatch(/window\.(?:alert|confirm|prompt)\b/);
    expect(sectionSource).not.toMatch(/\b(?:alert|confirm|prompt)\s*\(/);
  });
});
