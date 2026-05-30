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
