import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const pagesDir = path.dirname(fileURLToPath(import.meta.url));
const adminPagePath = path.resolve(pagesDir, "AdminPage.jsx");
const libraryPagePath = path.resolve(pagesDir, "LibraryPage.jsx");
const refreshSweepButtonPath = path.resolve(pagesDir, "../components/RefreshSweepButton.jsx");
const stylesPath = path.resolve(pagesDir, "../styles.css");

function readAdminPage() {
  return fs.readFileSync(adminPagePath, "utf8");
}

function readLibraryPage() {
  return fs.readFileSync(libraryPagePath, "utf8");
}

function readRefreshSweepButton() {
  return fs.readFileSync(refreshSweepButtonPath, "utf8");
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
    expect(source).toContain("setInviteAssignedAge(18);");
    expect(source).toContain("setInviteAgeModalOpen(true);");
    expect(source).toContain("onSubmit={handleGenerateInviteCode}");
    expect(source).not.toContain("Show codes");
    expect(source).not.toContain("Hide codes");
    expect(source).not.toContain("admin-invite-code-header__chevron");
  });

  test("invite and user age credential controls post explicit age values", () => {
    const source = readAdminPage();

    expect(source).toContain("const AGE_CREDENTIAL_OPTIONS = Array.from({ length: 18 }");
    expect(source).toContain("const [inviteAssignedAge, setInviteAssignedAge] = useState(18)");
    expect(source).toContain("data: { assigned_age: Number(inviteAssignedAge || 18) }");
    expect(source).toContain("Age credential {inviteCode.assigned_age_display || formatAgeCredential(inviteCode.assigned_age)}");
    expect(source).toContain("age_credential: Number(createUserForm.ageCredential || 18)");
    expect(source).toContain("{ age_credential: Number(ageCredentialEditor.ageCredential || 18) }");
    expect(source).toContain("Assign age credential");
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

  test("refresh controls share the same one-way rounded border sweep", () => {
    const source = readAdminPage();
    const librarySource = readLibraryPage();
    const componentSource = readRefreshSweepButton();
    const styles = readStyles();

    expect(componentSource).toContain("export const REFRESH_SWEEP_MS = 1100");
    expect(componentSource).toContain("buildSweepGeometry");
    expect(componentSource).toContain("getBoundingClientRect");
    expect(componentSource).toContain("--refresh-sweep-length");
    expect(componentSource).not.toContain("pathLength=\"100\"");
    expect(source).toContain("handlePasswordHelpRefresh");
    expect(source).toContain("await loadPasswordHelpRequests();");
    expect(source).toContain("handleUrlPrefixRefreshStatus");
    expect(source).toContain("handleRecoveryRefresh");
    expect(source).toContain("RefreshSweepButton");
    expect(librarySource).toContain("RefreshSweepButton");
    expect(librarySource).toContain("Rescan library");
    expect(componentSource).toContain("refresh-status-sweep-button__sweep");
    expect(styles).toContain(".refresh-status-sweep-button__sweep path");
    expect(styles).toContain("@keyframes refresh-status-border-sweep");
    expect(styles).toContain("@media (prefers-reduced-motion: reduce)");
    const refreshSweepStart = styles.indexOf(".refresh-status-sweep-button__sweep path");
    const refreshSweepEnd = styles.indexOf("@media (prefers-reduced-motion: reduce)", refreshSweepStart);
    const refreshSweepStyles = styles.slice(refreshSweepStart, refreshSweepEnd);
    expect(refreshSweepStyles).toContain("stroke-width: 4.2");
    expect(refreshSweepStyles).toContain("stroke-dasharray: var(--refresh-sweep-length)");
    expect(refreshSweepStyles).toContain("stroke-dashoffset: var(--refresh-sweep-length)");
    expect(refreshSweepStyles).toContain("stroke-dashoffset: 0");
    expect(refreshSweepStyles).not.toContain("conic-gradient");
    expect(refreshSweepStyles).not.toContain("rotate(");
    expect(refreshSweepStyles).not.toContain("clip-path");
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
