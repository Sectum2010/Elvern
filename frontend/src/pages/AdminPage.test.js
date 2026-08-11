import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const pagesDir = path.dirname(fileURLToPath(import.meta.url));
const adminPagePath = path.resolve(pagesDir, "AdminPage.jsx");
const libraryPagePath = path.resolve(pagesDir, "LibraryPage.jsx");
const refreshSweepButtonPath = path.resolve(pagesDir, "../components/RefreshSweepButton.jsx");
const controlCenterStylesPath = path.resolve(pagesDir, "../controlCenter.css");
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

function readControlCenterStyles() {
  return fs.readFileSync(controlCenterStylesPath, "utf8");
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

  test("recovery cards wrap long backup paths and fill the phone column", () => {
    const styles = readStyles();

    expect(styles).toMatch(/\.admin-recovery-card \.page-subnote,\s*\.admin-recovery-card strong\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(styles).toMatch(/\.admin-recovery-card \.page-subnote,\s*\.admin-recovery-card strong\s*\{[^}]*word-break:\s*break-word;/s);
    expect(styles).toMatch(/html\[data-device-shell="iphone"\] \.admin-recovery-grid,[\s\S]*html\[data-device-shell="iphone"\] \.admin-recovery-card\s*\{[^}]*inline-size:\s*100%;/s);
    expect(styles).toMatch(/html\[data-device-shell="iphone"\] \.admin-recovery-grid,[\s\S]*html\[data-device-shell="iphone"\] \.admin-recovery-card\s*\{[^}]*max-inline-size:\s*100%;/s);
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

describe("AdminPage exposure mode planner guards", () => {
  test("security card uses Exposure Mode wording and keeps planner form out of the default layout", () => {
    const source = readAdminPage();
    const start = source.indexOf("<section className=\"settings-card exposure-summary-card\">");
    const end = source.indexOf("urlPrefixStatus?.rotation_reminder_due", start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    const securityCardSource = source.slice(start, end);

    expect(securityCardSource).toContain("<StatusRow label=\"Exposure Mode\" value={exposureModeStatus} />");
    expect(securityCardSource).toContain("<StatusRow label=\"Finalized profile\" value={exposureFinalizedProfileStatus} />");
    expect(securityCardSource).toContain("<StatusRow label=\"Runtime posture\" value={exposureRuntimePostureStatus} />");
    expect(securityCardSource).toContain("<StatusRow label=\"Pending draft\" value={exposurePendingDraft ? \"Exists\" : \"None\"} />");
    expect(securityCardSource).toContain("<StatusRow label=\"Maintenance Mode\" value={exposureMaintenanceLockStatus} />");
    expect(securityCardSource).toContain("<StatusRow label=\"Prepared switch\" value={exposurePreparedSwitchStatus} />");
    expect(securityCardSource).toContain("<StatusRow label=\"Current request origin\"");
    expect(securityCardSource).toContain("exposure-maintenance-summary-control");
    expect(securityCardSource).toContain("className=\"exposure-maintenance-switch exposure-maintenance-switch--summary\"");
    expect(securityCardSource).toContain("Required to enable or disable Maintenance Mode.");
    expect(securityCardSource).toContain("Standalone server mode. Enabling logs out non-admin users and blocks non-admin logins without disabling accounts.");
    expect(securityCardSource).toContain("Manage");
    expect(securityCardSource).not.toContain("Private-only mode");
    expect(securityCardSource).not.toContain("Public Mode - Custom Domain");
    expect(securityCardSource).not.toContain("Public Mode - Direct IP (Not recommended)");
    expect(securityCardSource).not.toContain("Validate plan");
    expect(securityCardSource).not.toContain("Save pending draft");
  });

  test("manage button opens the planner modal path", () => {
    const source = readAdminPage();
    const handlerStart = source.indexOf("async function handleOpenExposurePlanner()");
    const handlerEnd = source.indexOf("function handleCloseExposurePlanner()", handlerStart);
    const modalStart = source.indexOf("const legacyExposurePlannerModal = exposurePlannerOpen ? (");
    const modalEnd = source.indexOf("const adminConfirmModalConfig", modalStart);

    expect(source).toContain("const [exposurePlannerOpen, setExposurePlannerOpen] = useState(false)");
    expect(handlerStart).toBeGreaterThan(0);
    expect(handlerEnd).toBeGreaterThan(handlerStart);
    const handlerSource = source.slice(handlerStart, handlerEnd);
    expect(handlerSource).toContain("setExposurePlannerOpen(true);");
    expect(handlerSource).toContain("await loadExposureStatus();");
    expect(handlerSource).not.toContain("if (!exposureStatus)");
    expect(source).toContain("onClick={handleOpenExposurePlanner}");
    expect(modalStart).toBeGreaterThan(0);
    expect(modalEnd).toBeGreaterThan(modalStart);
    const modalSource = source.slice(modalStart, modalEnd);
    expect(modalSource).toContain("Manage Exposure Mode");
    expect(modalSource).toContain("Draft only");
    expect(modalSource).toContain("Draft only — validation and pending drafts do not change runtime behavior, write env files, rotate the URL prefix, or disable users. Prepare only creates a manual plan and enables Maintenance Mode.");
    expect(modalSource).toContain("Current Status");
    expect(modalSource).toContain("Maintenance Mode");
    expect(modalSource).toContain("Prepare manual switch");
    expect(modalSource).toContain("Verify prepared switch");
    expect(modalSource).toContain("Finalize verified profile");
    expect(modalSource).toContain("Desired Mode");
    expect(modalSource).toContain("Confirmation");
    expect(modalSource).toContain("exposure-planner-modal-shell");
    expect(modalSource).toContain("exposure-results-stack");
  });

  test("planner modal includes modes, providers, Maintenance Mode actions, prepare actions, and no activation route", () => {
    const source = readAdminPage();
    const modalStart = source.indexOf("const legacyExposurePlannerModal = exposurePlannerOpen ? (");
    const modalEnd = source.indexOf("const adminConfirmModalConfig", modalStart);
    const modalSource = source.slice(modalStart, modalEnd);

    expect(source).toContain("/api/admin/exposure/status");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/validate\"");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/drafts\"");
    expect(source).toContain("apiRequest(\"/api/admin/maintenance-mode\"");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/prepare-switch\"");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/verify-prepared-switch\"");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/finalize-profile\"");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/prepared-switch\"");
    expect(source).toContain("current_admin_password = draft.currentAdminPassword");
    expect(source).toContain("const EXPOSURE_MODE_SEGMENTS = [");
    expect(source).toContain("{ value: \"private\", label: \"Private\" }");
    expect(source).toContain("{ value: \"public_custom_domain\", label: \"Public domain\" }");
    expect(source).toContain("{ value: \"public_direct_ip\", label: \"Direct IP ⚠\" }");
    expect(modalSource).toContain("className=\"exposure-mode-segmented\"");
    expect(modalSource).toContain("options={EXPOSURE_MODE_SEGMENTS}");
    expect(source).toContain("Public Mode - Direct IP (Not recommended)");
    expect(modalSource).toContain("Not recommended");
    expect(source).not.toContain("NOT RECOMMENDED");
    expect(modalSource).toContain("I understand direct public IP exposure is not recommended.");
    expect(modalSource).toContain("https://media.example.com");
    expect(modalSource).toContain("http://203.0.113.10:4173");
    expect(modalSource).toContain("Save pending draft");
    expect(modalSource).toContain("Clear pending draft");
    expect(source).toContain("const EXPOSURE_MAINTENANCE_SEGMENTS = [");
    expect(modalSource).toContain("className=\"exposure-maintenance-switch\"");
    expect(modalSource).toContain("className=\"primary-button exposure-maintenance-confirm-button\"");
    expect(modalSource).toContain("handleSetExposureMaintenanceLock(exposureMaintenanceTargetEnabled)");
    expect(source).toContain("const exposureMaintenanceActionLabel = exposureMaintenanceTargetEnabled ? \"Enable Maintenance Mode\" : \"Disable Maintenance Mode\";");
    expect(modalSource).not.toContain("disabled={exposurePending || exposureMaintenanceLock.enabled}");
    expect(modalSource).not.toContain("disabled={exposurePending || !exposureMaintenanceLock.enabled}");
    expect(modalSource).not.toContain("onClick={() => handleSetExposureMaintenanceLock(true)}");
    expect(modalSource).not.toContain("onClick={() => handleSetExposureMaintenanceLock(false)}");
    expect(modalSource).toContain("exposure-secret-field");
    expect(modalSource).toContain("Prepared for manual apply");
    expect(modalSource).toContain("Prepare manual switch");
    expect(modalSource).toContain("Verify prepared switch");
    expect(modalSource).toContain("Finalize verified profile");
    expect(modalSource).toContain("Clear prepared switch");
    expect(modalSource).toContain("Copy env suggestions");
    expect(modalSource).toContain("Env suggestions");
    expect(modalSource).toContain("Manual restart / reverse proxy checklist");
    expect(modalSource).toContain("URL prefix rotation");
    expect(modalSource).toContain("Manual only");
    expect(modalSource).toContain("Runtime effect");
    expect(modalSource).toContain("None yet");
    expect(modalSource).toContain("Takes effect");
    expect(modalSource).toContain("Activation");
    expect(modalSource).toContain("Not implemented");
    expect(source).toContain("I understand this logs out non-admin users and temporarily blocks non-admin logins without disabling their accounts.");
    expect(modalSource).toContain("{EXPOSURE_MAINTENANCE_ACKNOWLEDGEMENT}");
    expect(source).toContain("I understand this only prepares a manual switch plan. It does not write env files, restart Elvern, rotate the URL prefix, disable users, or activate public/private mode. It will enable Maintenance Mode and log out non-admin users.");
    expect(modalSource).toContain("{EXPOSURE_PREPARE_ACKNOWLEDGEMENT}");
    expect(source).toContain("I understand this only verifies the prepared manual switch. It does not release Maintenance Mode, write env files, restart Elvern, rotate the URL prefix, revoke sessions, disable users, or activate exposure mode.");
    expect(modalSource).toContain("{EXPOSURE_VERIFY_ACKNOWLEDGEMENT}");
    expect(source).toContain("I understand this records the verified exposure profile, clears the working draft/prepared state, and does not release Maintenance Mode or change runtime settings.");
    expect(modalSource).toContain("{EXPOSURE_FINALIZE_ACKNOWLEDGEMENT}");
    expect(modalSource).toContain("Required to enable or disable Maintenance Mode.");
    expect(modalSource).toContain("Required to prepare or clear a prepared switch.");
    expect(modalSource).toContain("Required to verify the prepared manual switch.");
    expect(modalSource).toContain("Required to finalize the verified exposure profile.");
    expect(modalSource).toContain("This standalone mode logs out non-admin users and blocks non-admin logins without disabling accounts.");
    expect(modalSource).toContain("Preparing will automatically enable Maintenance Mode and log out non-admin users. After manually applying env/reverse-proxy changes and restarting Elvern, return through the target address and verify in Phase 4.");
    expect(modalSource).toContain("Maintenance Mode remains under admin control.");
    expect(modalSource).toContain("Maintenance Mode remains on until an admin turns it off.");
    expect(modalSource).toContain("<StatusRow label=\"Maintenance Mode\" value={exposurePrepareMaintenanceStatus} />");
    expect(modalSource).toContain("<StatusRow label=\"Phase 4 verification\" value={exposurePhase4VerificationStatus} />");
    expect(source).toContain("caddy: \"Caddy\"");
    expect(source).toContain("nginx: \"Nginx\"");
    expect(source).toContain("cloudflare_tunnel: \"Cloudflare Tunnel\"");
    expect(source).toContain("manual_other: \"Manual/Other\"");
    expect(source).not.toContain("tailscale_funnel");
    expect(source).not.toContain("Tailscale Funnel");
    expect(source).not.toContain("/api/admin/exposure/activate");
    expect(source).not.toContain("/api/admin/exposure/switch-now");
    expect(source).not.toContain(">Activate<");
    expect(source).not.toContain("Activate plan");
    expect(source).not.toContain("Apply mode");
    expect(source).not.toContain(">Apply<");
    expect(source).not.toContain("Switch now");
    expect(source).not.toContain("Write env");
  });

  test("planner modal includes prepared switch verification without activation controls", () => {
    const source = readAdminPage();
    const modalStart = source.indexOf("const legacyExposurePlannerModal = exposurePlannerOpen ? (");
    const modalEnd = source.indexOf("const adminConfirmModalConfig", modalStart);
    const modalSource = source.slice(modalStart, modalEnd);

    expect(source).toContain("const DEFAULT_EXPOSURE_VERIFY_FORM = {");
    expect(source).toContain("const exposurePreparedSwitchStatus = !exposurePreparedSwitch");
    expect(source).toContain("verified_after_restart");
    expect(source).toContain("async function handleVerifyExposurePreparedSwitch()");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/verify-prepared-switch\"");
    expect(modalSource).toContain("<h3>Verify prepared switch</h3>");
    expect(modalSource).toContain("<StatusRow label=\"Prepared status\" value={exposurePreparedSwitchStatusDetail} />");
    expect(modalSource).toContain("<StatusRow label=\"Verification required after restart\"");
    expect(modalSource).toContain("<StatusRow label=\"Current request origin\"");
    expect(modalSource).toContain("<StatusRow label=\"Expected origin\"");
    expect(modalSource).toContain("<StatusRow label=\"Takes effect\" value=\"No\" />");
    expect(modalSource).toContain("Current access");
    expect(modalSource).toContain("Runtime settings");
    expect(modalSource).toContain("Safety state");
    expect(modalSource).toContain("Warnings");
    expect(source).toContain("Verified after restart.");
    expect(source).toContain("Verified with warnings.");
    expect(modalSource).toContain("disabled={exposurePending || !exposurePreparedSwitch}");
    expect(modalSource).not.toContain("Release Maintenance Mode");
    expect(modalSource).not.toContain("Rotate URL prefix now");
    expect(modalSource).not.toContain("Switch exposure mode");
  });

  test("planner modal includes finalized profile controls without release actions", () => {
    const source = readAdminPage();
    const modalStart = source.indexOf("const legacyExposurePlannerModal = exposurePlannerOpen ? (");
    const modalEnd = source.indexOf("const adminConfirmModalConfig", modalStart);
    const modalSource = source.slice(modalStart, modalEnd);

    expect(source).toContain("const DEFAULT_EXPOSURE_FINALIZE_FORM = {");
    expect(source).toContain("async function handleFinalizeExposureProfile()");
    expect(source).toContain("apiRequest(\"/api/admin/exposure/finalize-profile\"");
    expect(source).toContain("updateExposureFinalizedProfileState(payload.finalized_profile || null)");
    expect(modalSource).toContain("<h3>Finalize verified profile</h3>");
    expect(modalSource).toContain("<StatusRow label=\"Verified mode\" value={exposureFinalizeProfileLabel} />");
    expect(modalSource).toContain("<StatusRow label=\"Origin\" value={formatExposureValue(exposureFinalizeOrigin)} />");
    expect(modalSource).toContain("<StatusRow label=\"Verification status\" value={exposureFinalizeVerificationStatus} />");
    expect(modalSource).toContain("<StatusRow label=\"Maintenance Mode\" value=\"Remains under admin control\" />");
    expect(modalSource).toContain("<StatusRow label=\"Takes effect\" value=\"No runtime settings are changed by this record\" />");
    expect(modalSource).toContain("Verify a prepared switch first.");
    expect(modalSource).toContain("Finalize verified profile");
    expect(modalSource).not.toContain("Release users");
    expect(modalSource).not.toContain("Turn off Maintenance Mode");
  });

  test("planner modal renders validation details and grouped planning notes", () => {
    const source = readAdminPage();
    const modalStart = source.indexOf("const legacyExposurePlannerModal = exposurePlannerOpen ? (");
    const modalEnd = source.indexOf("const adminConfirmModalConfig", modalStart);
    const modalSource = source.slice(modalStart, modalEnd);

    expect(modalSource).toContain("<h3>Validation summary</h3>");
    expect(modalSource).toContain("<h3>Warnings / errors</h3>");
    expect(modalSource).toContain("<strong>Errors</strong>");
    expect(modalSource).toContain("<strong>Warnings</strong>");
    expect(modalSource).toContain("<h3>Checks</h3>");
    expect(modalSource).toContain("exposure-validation-summary");
    expect(modalSource).toContain("exposure-check-row");
    expect(modalSource).toContain("No blocking errors.");
    expect(modalSource).toContain("<h3>Prepared switch</h3>");
    expect(modalSource).toContain("Maintenance Mode auto-enabled");
    expect(modalSource).toContain("Verification required after restart");
    expect(modalSource).toContain("Checked in Phase 4");
    expect(modalSource).toContain("<h3>Manual plan</h3>");
    expect(modalSource).toContain("<summary>Manual Steps</summary>");
    expect(modalSource).toContain("<summary>Env Suggestions</summary>");
    expect(modalSource).toContain("<summary>Reverse Proxy Notes</summary>");
    expect(modalSource).toContain("<summary>Activation Notes</summary>");
  });
});

describe("AdminPage desktop Control Center contracts", () => {
  test("uses one Meridian card system for Users, Invites, and Password Help", () => {
    const source = readAdminPage();

    expect(source).toContain('desktopControlCenter ? "meridian-card meridian-users-card" : "settings-card settings-card--wide"');
    expect(source).toContain('desktopControlCenter ? "meridian-card meridian-invite-card" : "settings-card"');
    expect(source).toContain('className={`${desktopControlCenter ? "meridian-card" : "settings-card settings-card--wide"} control-center-admin-password-help');
    expect(source).not.toContain('className="settings-card settings-card--wide meridian-card meridian-users-card"');
  });

  test("keeps active/background/pending status motion distinct and reduced-motion safe", () => {
    const styles = readControlCenterStyles();

    expect(styles).toContain(".user-status-indicator--green { animation: merUserActive");
    expect(styles).toContain(".user-status-indicator--yellow { animation: merUserBackground");
    expect(styles).toContain(".user-status-indicator--orange { animation: merUserPending");
    expect(styles).toMatch(/\.user-status-indicator--grey,[\s\S]*\.user-status-indicator--red\s*\{ animation: none; \}/);
    expect(styles).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*\.meridian-users-card \.user-status-indicator\s*\{[\s\S]*animation: none !important;/);
  });

  test("keeps the inline Invite age flow and dedicated Password Help resource states", () => {
    const source = readAdminPage();

    expect(source).toContain('className="meridian-invite-age-panel"');
    expect(source).toContain("INVITE CODE");
    expect(source).toContain("Assign age credential");
    expect(source).toContain("inviteAgeModalOpen && !desktopControlCenter");
    expect(source).toContain('aria-label="Loading password help requests"');
    expect(source).toContain("passwordHelpStatus.error");
    expect(source).toContain("passwordHelpStatus.loaded");
    expect(source).toContain("passwordHelpStatus.loading ? \"Refreshing…\" : \"Refresh\"");
  });

  test("long posture values retain full copy semantics without claiming unsupported clipboard success", () => {
    const source = readAdminPage();
    const styles = readControlCenterStyles();

    expect(source).toContain("function MeridianPostureValue({ label, value })");
    expect(source).toContain('title={String(value)}');
    expect(source).toContain('aria-label={`${copied ? "Copied" : "Copy"} ${label}: ${value}`}');
    expect(source).toContain("if (!navigator.clipboard?.writeText)");
    expect(source).toContain("await navigator.clipboard.writeText(String(value));");
    expect(styles).toContain("grid-template-columns: minmax(126px, 1.15fr) minmax(0, 1fr)");
    expect(styles).toContain(".meridian-posture-grid dt { overflow: hidden;");
  });

  test("keeps approved synthetic display isolated from real admin state", () => {
    const source = readAdminPage();

    expect(source).toContain("Math.round(92 * entryProgress)");
    expect(source).toContain('className="meridian-security-gauge__dial-wrap"');
    expect(source).toContain('className="meridian-security-gauge__dial-copy"');
    expect(source).toContain('className="meridian-security-gauge__mode">PRIVATE</span>');
    expect(source).toContain('r="64"');
    expect(source).toContain('className="meridian-security-gauge__value"');
    expect(source).toContain("ADMIN_LIVE_AUDIT_TICKER_LINE");
    expect(source).toContain('className="control-center-live-audit__viewport"');
    expect(source).toContain("statusPayload?.total_users");
    expect(source).toContain("sessionsPayload.length");
    expect(source).toContain("statusPayload?.total_media_items");
    expect(source).toContain("liveInviteCount");
    expect(source).toContain("Math.round(Number(value) * entryProgress)");
  });

  test("keeps real log and maintenance actions inside the demo-matched desktop structure", () => {
    const source = readAdminPage();

    expect(source).toContain('className="meridian-session-heading"');
    expect(source).toContain('className="ghost-button ghost-button--danger"');
    expect(source).toContain('className="meridian-maintenance-card__acknowledgement"');
    expect(source).toContain('className="meridian-maintenance-card__controls"');
    expect(source).toContain('onClick={() => handleSetExposureMaintenanceLock(exposureMaintenanceTargetEnabled)}');
    expect(source).toContain('onClick={() => handleRevokeSession(session)}');
  });

  test("places the users expander once after all playback worker groups", () => {
    const source = readAdminPage();
    const expander = source.indexOf("control-center-user-expander");
    const nativeWorkers = source.indexOf("workerGroup.nativeItems");
    const createUser = source.indexOf("admin-create-user-row", nativeWorkers);

    expect(source.match(/control-center-user-expander/g)).toHaveLength(1);
    expect(expander).toBeGreaterThan(nativeWorkers);
    expect(expander).toBeLessThan(createUser);
  });

  test("keeps desktop worker diagnostics in the Users header without changing their source", () => {
    const source = readAdminPage();

    expect(source).toContain("const playbackWorkerSummaryElement = playbackWorkerSummary.length > 0");
    expect(source).toContain('className="admin-workers-summary meridian-users-card__workers-summary"');
    expect(source).toContain("{desktopControlCenter ? playbackWorkerSummaryElement : null}");
    expect(source).toContain("{!desktopControlCenter ? playbackWorkerSummaryElement : null}");
  });

  test("uses route-owned loading and single polling owners", () => {
    const source = readAdminPage();
    const scanEffectStart = source.indexOf("if (!statusPayload?.scan?.running)");
    const workerEffectStart = source.indexOf("const ownsDesktopPoll");
    const streamEventsStart = source.indexOf("const ADMIN_STREAM_RELEVANT_EVENTS");

    expect(source).toContain("desktopAdminResourcesForTab(tab)");
    expect(source).toContain("loadDesktopAdminSection(desktopAdminTab");
    expect(source.slice(scanEffectStart, workerEffectStart)).toContain("loadSystemStatus({ silent: true })");
    expect(source.slice(scanEffectStart, workerEffectStart)).not.toContain("loadAdminData({ silent: true })");
    expect(source).toContain("shouldPollDesktopPlaybackWorkers");
    expect(source).toContain("document.visibilityState === \"visible\"");
    expect(source.slice(streamEventsStart, streamEventsStart + 500)).not.toContain("stream_connected");
    expect(source).toContain("...ADMIN_BACKUP_EVENT_TYPES");
    expect(source).toContain("dispatchAdminBackupEvent(event.type)");
  });

  test("protects dirty download drafts during Control Center navigation and closes deleted targets safely", () => {
    const source = readAdminPage();

    expect(source).toContain("CONTROL_CENTER_BEFORE_NAVIGATION_EVENT");
    expect(source).toContain('action: "route"');
    expect(source).toContain("pendingAction.proceed?.()");
    expect(source).toContain("is no longer available.");
  });

  test("uses in-app own-2FA dialogs and locks URL rotation while pending", () => {
    const source = readAdminPage();

    expect(source).toContain("const [ownTotpModal, setOwnTotpModal]");
    expect(source).toContain("own-totp-${ownTotpModal.mode}-password");
    expect(source).toContain("Save these replacement codes now. They are shown only once.");
    expect(source).not.toContain("window.prompt");
    expect(source).toContain("function closeUrlPrefixRotateModal()");
    expect(source).toContain("disabled={urlPrefixRotateModal.pending}");
  });

  test("password help can open the existing user actions flow", () => {
    const source = readAdminPage();

    expect(source).toContain("requestEntry.user_id");
    expect(source).toContain("Open user actions");
    expect(source).toContain("openUserActionsModal(requestUser)");
  });

  test("desktop Meridian exposure planner presents four phases through the existing handlers", () => {
    const source = readAdminPage();
    const componentStart = source.indexOf("function MeridianExposurePlannerModal({ model })");
    const componentEnd = source.indexOf("function exposurePayloadFromDraft", componentStart);
    const componentSource = source.slice(componentStart, componentEnd);

    expect(componentStart).toBeGreaterThan(0);
    expect(source).toContain('const MERIDIAN_EXPOSURE_PHASES = ["1 · Draft", "2 · Prepare", "3 · Verify", "4 · Finalize"]');
    expect(componentSource).toContain('role="tablist"');
    expect(componentSource).toContain('role="tab"');
    expect(componentSource).toContain("model.onValidate");
    expect(componentSource).toContain("model.onSaveDraft");
    expect(componentSource).toContain("model.onPrepare");
    expect(componentSource).toContain("model.onVerify");
    expect(componentSource).toContain("model.onFinalize");
    expect(source).toContain("desktopControlCenter ? (");
    expect(source).toContain(": legacyExposurePlannerModal;");
  });
});
