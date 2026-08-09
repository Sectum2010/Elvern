import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LoadingView } from "../components/LoadingView";
import { NonLoginSecretInput } from "../components/NonLoginSecretInput";
import { RefreshSweepButton } from "../components/RefreshSweepButton";
import { DesktopBackToLibraryButton } from "../components/DesktopBackToLibraryButton";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";
import {
  ADMIN_LIVE_AUDIT_TICKER_LINE,
  desktopAdminResourcesForTab,
  shouldPollDesktopPlaybackWorkers,
  shouldRefreshDesktopRealtimeResource,
} from "../lib/adminControlCenter.js";
import {
  buildPlaybackWorkerSummaryBubbles,
  buildPlaybackStatusDismissKey,
  buildPlaybackStatusDismissPrompt,
  buildPlaybackWorkerTerminatePrompt,
  buildPlaybackWorkersByUserId,
  buildWorkerPlaybackMetadataLabel,
  buildWorkerDisplayStatus,
  canDismissPlaybackStatus,
  canTerminatePlaybackWorker,
  formatCpuCoresUsage,
  formatMemoryGaugeValue,
  formatPreparedRanges,
  formatWorkerRuntime,
  shouldShowWorkerCleanupNotice,
  shortenDiagnosticId,
  workerStatusToneClass,
} from "../lib/adminPlaybackWorkers";
import { formatCompletedRescanWarning } from "../lib/cloudSyncStatus";
import { formatDate } from "../lib/format";
import {
  readPersistedPanelState,
  writePersistedPanelState,
} from "../lib/persistedPanelState";
import {
  classifyControlCenterPath,
  desktopAdminTabToLegacySection,
  isDesktopControlCenterDevice,
} from "../lib/controlCenterRoutes.js";
import {
  detectClientDeviceClass,
  detectClientPlatform,
} from "../lib/platformDetection.js";
import { fetchControlCenterResource } from "../lib/controlCenterQueries.js";


const SELF_DELETE_CONFIRM_DETAIL = "Confirm deletion before removing your own account";
const ADMIN_STREAM_RELEVANT_EVENTS = [
  "session_created",
  "session_ended",
  "session_revoked",
  "session_cleanup_confirmed",
  "session_status_changed",
  "user_disabled",
  "user_enabled",
  "user_deleted",
];
const ADMIN_SECTION_AUTO_COLLAPSE_MS = 15_000;
const PLAYBACK_WORKERS_POLL_MS = 4_000;
const RECOVERY_CHECKPOINT_LIMIT = 4;
const RECOVERY_WARNING_LIMIT = 4;
const RECOVERY_TRIGGER_LABELS = {
  auto_before_shared_local_path_update: "Auto · Shared local path update",
  auto_before_admin_rescan: "Auto · Admin rescan",
  manual_admin_ui: "Manual · Admin UI",
  manual_cli: "Manual · CLI",
};
const ADMIN_SECTIONS = [
  { key: "panel", label: "Admin Panel", icon: "panel" },
  { key: "security", label: "Security", icon: "security" },
  { key: "logs", label: "Logs", icon: "logs" },
  { key: "recovery", label: "Recovery", icon: "recovery" },
];
const ADMIN_SECTION_KEYS = ADMIN_SECTIONS.map((section) => section.key);
const ADMIN_ACTIVE_SECTION_STORAGE_KEY = "elvern:admin-active-section";
const AGE_CREDENTIAL_OPTIONS = Array.from({ length: 18 }, (_, index) => index + 1);
const EXPOSURE_PROVIDER_LABELS = {
  caddy: "Caddy",
  nginx: "Nginx",
  cloudflare_tunnel: "Cloudflare Tunnel",
  manual_other: "Manual/Other",
};
const EXPOSURE_MODE_SEGMENTS = [
  { value: "private", label: "Private" },
  { value: "public_custom_domain", label: "Public domain" },
  { value: "public_direct_ip", label: "Direct IP", badge: "Not recommended" },
];
const EXPOSURE_MAINTENANCE_SEGMENTS = [
  { value: "off", label: "Off" },
  { value: "on", label: "On" },
];
const DEFAULT_EXPOSURE_DRAFT = {
  selectedMode: "private",
  publicOrigin: "",
  privateOrigin: "",
  reverseProxyProvider: "caddy",
  acknowledgement: false,
  directIpNotRecommendedAcknowledgement: false,
  currentAdminPassword: "",
};
const DEFAULT_EXPOSURE_MAINTENANCE_FORM = {
  currentAdminPassword: "",
  acknowledgement: false,
};
const EXPOSURE_MAINTENANCE_ACKNOWLEDGEMENT =
  "I understand this logs out non-admin users and temporarily blocks non-admin logins without disabling their accounts.";
const DEFAULT_EXPOSURE_PREPARE_FORM = {
  currentAdminPassword: "",
  acknowledgement: false,
};
const EXPOSURE_PREPARE_ACKNOWLEDGEMENT =
  "I understand this only prepares a manual switch plan. It does not write env files, restart Elvern, rotate the URL prefix, disable users, or activate public/private mode. It will enable Maintenance Mode and log out non-admin users.";
const DEFAULT_EXPOSURE_VERIFY_FORM = {
  currentAdminPassword: "",
  acknowledgement: false,
};
const EXPOSURE_VERIFY_ACKNOWLEDGEMENT =
  "I understand this only verifies the prepared manual switch. It does not release Maintenance Mode, write env files, restart Elvern, rotate the URL prefix, revoke sessions, disable users, or activate exposure mode.";
const DEFAULT_EXPOSURE_FINALIZE_FORM = {
  currentAdminPassword: "",
  acknowledgement: false,
};
const EXPOSURE_FINALIZE_ACKNOWLEDGEMENT =
  "I understand this records the verified exposure profile, clears the working draft/prepared state, and does not release Maintenance Mode or change runtime settings.";

function formatAgeCredential(value) {
  const age = Number(value);
  if (!Number.isFinite(age)) {
    return "18+";
  }
  return age >= 18 ? "18+" : String(age);
}

function unknownIfEmpty(value) {
  const normalized = typeof value === "string" ? value.trim() : "";
  return normalized || "Unknown";
}


function detectPasswordHelpDevice(userAgent) {
  const normalized = String(userAgent || "").toLowerCase();
  if (!normalized) {
    return "Unknown";
  }
  if (normalized.includes("iphone") || normalized.includes("ipod")) {
    return "iPhone";
  }
  if (normalized.includes("ipad")) {
    return "iPad";
  }
  if (normalized.includes("android")) {
    return normalized.includes("mobile") ? "Android phone" : "Android tablet";
  }
  if (normalized.includes("windows nt")) {
    return "Windows PC";
  }
  if (normalized.includes("macintosh")) {
    return "Mac";
  }
  if (normalized.includes("linux")) {
    return "Linux desktop";
  }
  return "Unknown";
}


function detectPasswordHelpBrowser(userAgent) {
  const normalized = String(userAgent || "").toLowerCase();
  if (!normalized) {
    return "Unknown";
  }
  if (normalized.includes("edg/") || normalized.includes("edgios") || normalized.includes("edga/")) {
    return "Edge";
  }
  if (normalized.includes("crios") || normalized.includes("chrome/") || normalized.includes("chromium/")) {
    return "Chrome";
  }
  if (normalized.includes("fxios") || normalized.includes("firefox/")) {
    return "Firefox";
  }
  if (normalized.includes("safari/")) {
    return "Safari";
  }
  return "Unknown";
}


function StatusRow({ label, value }) {
  return (
    <div className="status-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}


function FeedbackBanner({ banner }) {
  if (!banner?.text) {
    return null;
  }
  return (
    <p
      className={banner.tone === "error" ? "feedback-banner feedback-banner--error" : "feedback-banner"}
      role={banner.tone === "error" ? "alert" : "status"}
    >
      {banner.text}
    </p>
  );
}


function InlineFeedback({ feedback }) {
  if (!feedback?.text) {
    return null;
  }
  return (
    <p
      className={feedback.tone === "error" ? "action-feedback action-feedback--error" : "action-feedback"}
      role={feedback.tone === "error" ? "alert" : "status"}
    >
      {feedback.text}
    </p>
  );
}


function ExposureSegmentedControl({ ariaLabel, className = "", options, value, onChange }) {
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === value));
  return (
    <div
      aria-label={ariaLabel}
      className={["settings-segmented-control exposure-segmented-control", className].filter(Boolean).join(" ")}
      role="radiogroup"
      style={{
        "--settings-segmented-count": options.length,
        "--settings-segmented-index": selectedIndex,
      }}
    >
      <span className="settings-segmented-control__indicator" aria-hidden="true" />
      {options.map((option) => {
        const isSelected = option.value === value;
        return (
          <button
            aria-checked={isSelected}
            className={[
              "settings-segmented-control__button exposure-segmented-control__button",
              isSelected ? "settings-segmented-control__button--active exposure-segmented-control__button--active" : "",
            ].filter(Boolean).join(" ")}
            key={option.value}
            onClick={() => onChange(option.value)}
            role="radio"
            type="button"
          >
            <span>{option.label}</span>
            {option.badge ? <small className="exposure-segmented-control__badge">{option.badge}</small> : null}
          </button>
        );
      })}
    </div>
  );
}


function ExposureVerificationCheckGroup({ title, checks, emptyText }) {
  return (
    <div className="exposure-checklist-group">
      <h4>{title}</h4>
      {checks.length > 0 ? (
        <div className="exposure-checks-list">
          {checks.map((entry) => (
            <div className="exposure-check-row" key={`verification-check-${title}-${entry.name || entry.detail}`}>
              <span className={`status-pill exposure-check-pill exposure-check-pill--${entry.status || "info"}`}>
                {entry.status || "info"}
              </span>
              <span>{entry.detail}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="page-subnote">{emptyText}</p>
      )}
    </div>
  );
}


function exposurePayloadFromDraft(draft, { includePassword = false } = {}) {
  const selectedMode = draft.selectedMode || "private";
  const isPublic = selectedMode !== "private";
  const isDirectIp = selectedMode === "public_direct_ip";
  const payload = {
    desired_mode: isPublic ? "public" : "private",
    public_entry_kind: isPublic ? (isDirectIp ? "direct_ip" : "custom_domain") : null,
    public_origin: isPublic ? draft.publicOrigin.trim() : null,
    private_origin: isPublic ? null : draft.privateOrigin.trim(),
    reverse_proxy_provider: isPublic ? (draft.reverseProxyProvider || "manual_other") : null,
    acknowledgement: Boolean(draft.acknowledgement),
    direct_ip_not_recommended_acknowledgement: Boolean(draft.directIpNotRecommendedAcknowledgement),
  };
  if (includePassword) {
    payload.current_admin_password = draft.currentAdminPassword;
  }
  return payload;
}


function exposureModeLabel(draft) {
  if (draft.selectedMode === "public_custom_domain") {
    return "Public Mode - Custom Domain";
  }
  if (draft.selectedMode === "public_direct_ip") {
    return "Public Mode - Direct IP (Not recommended)";
  }
  return "Private Mode";
}


function formatExposureProfileLabel(profile) {
  if (!profile) {
    return "None";
  }
  const mode = profile.mode || profile.desired_mode;
  const publicEntryKind = profile.public_entry_kind;
  if (mode === "public") {
    return publicEntryKind === "direct_ip" ? "Public direct IP" : "Public custom domain";
  }
  if (mode === "private") {
    return "Private";
  }
  return "Needs review";
}


function formatExposureModeStatus(active, pendingDraft, finalizedProfile = null) {
  if (finalizedProfile) {
    return formatExposureProfileLabel(finalizedProfile);
  }
  const desiredMode = pendingDraft?.desired?.desired_mode;
  if (desiredMode === "public") {
    return "Public draft pending";
  }
  if (desiredMode === "private") {
    return "Private draft pending";
  }
  if (typeof active?.private_network_only === "boolean") {
    return active.private_network_only ? "Private Mode" : "Public planning only";
  }
  return "Unknown / Needs review";
}


function formatExposureValue(value) {
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(", ") : "None";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (value === null || value === undefined || value === "") {
    return "Not set";
  }
  return String(value);
}


function formatExposureSuggestion(suggestion) {
  if (!suggestion || typeof suggestion !== "object") {
    return "";
  }
  const name = suggestion.name || "Setting";
  const value = suggestion.value ?? "";
  const effect = suggestion.effect ? ` - ${suggestion.effect}` : "";
  return `${name}=${value}${effect}`;
}


function formatBytes(value) {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const decimals = unitIndex === 0 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(decimals)} ${units[unitIndex]}`;
}


function formatRecoveryCheckpointTime(value) {
  if (!value) {
    return "Unknown time";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value));
}


function formatRecoveryTriggerLabel(trigger) {
  if (typeof trigger !== "string" || !trigger.trim()) {
    return "Unknown trigger";
  }
  return RECOVERY_TRIGGER_LABELS[trigger] || trigger;
}


function formatRecoveryCheckpointId(checkpointId) {
  if (typeof checkpointId !== "string" || !checkpointId.trim()) {
    return "ID unavailable";
  }
  return checkpointId.length > 18 ? `...${checkpointId.slice(-12)}` : checkpointId;
}

function formatBackupProtectionLabel(checkpoint) {
  if (checkpoint?.backup_encrypted && checkpoint?.backup_key_source === "passphrase") {
    return "passphrase-protected";
  }
  if (checkpoint?.backup_encrypted) {
    return "auto-encrypted (tied to server secret)";
  }
  return "legacy plaintext";
}


function UserStatusIndicator({ color, label }) {
  return (
    <span className="user-status-pill" title={label}>
      <span aria-hidden="true" className={`user-status-indicator user-status-indicator--${color}`} />
      <span className="user-status-pill__label">{label}</span>
    </span>
  );
}

function AdminCrownIcon() {
  return (
    <span aria-label="Admin" className="admin-user-crown" role="img" title="Admin">
      <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
        <path d="M4.4 18.4h15.2l1.1-10.1-5.2 3.7L12 4.8 8.5 12 3.3 8.3l1.1 10.1Z" />
      </svg>
    </span>
  );
}


function createDestructiveGateToken() {
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const values = new Uint32Array(2);
    crypto.getRandomValues(values);
    return `${values[0].toString(36)}${values[1].toString(36)}`;
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}


function InviteCodeEyeIcon({ struck = false }) {
  return (
    <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
      <path
        d="M2.25 12S5.625 5.25 12 5.25 21.75 12 21.75 12 18.375 18.75 12 18.75 2.25 12 2.25 12Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
      <path
        d="M15 12A3 3 0 1 1 9 12A3 3 0 0 1 15 12Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
      {struck ? (
        <path
          d="M4.5 19.5L19.5 4.5"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.7"
        />
      ) : null}
    </svg>
  );
}

function AdminConfirmModal({
  confirmLabel = "Confirm",
  danger = false,
  error = "",
  onCancel,
  onConfirm,
  open,
  pending = false,
  title,
  body,
}) {
  if (!open) {
    return null;
  }
  return (
    <div
      aria-labelledby="admin-confirm-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={pending ? undefined : onCancel}
      />
      <div className="browser-resume-modal__card detail-info-modal__card admin-confirm-modal">
        <div className="detail-info-modal__copy">
          <h2 id="admin-confirm-modal-title" className="detail-info-modal__title">{title}</h2>
          <p className="page-subnote">{body}</p>
          {error ? <p className="form-error">{error}</p> : null}
        </div>
        <div className="browser-resume-modal__actions admin-confirm-modal__actions">
          <button className="ghost-button" disabled={pending} onClick={onCancel} type="button">
            Cancel
          </button>
          <button
            className={danger ? "ghost-button ghost-button--danger" : "primary-button"}
            disabled={pending}
            onClick={onConfirm}
            type="button"
          >
            {pending ? `${confirmLabel}...` : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}


function getUserAvatarInitials(username) {
  if (typeof username !== "string") {
    return "U";
  }
  const trimmed = username.trim();
  if (!trimmed) {
    return "U";
  }
  const parts = trimmed.split(/[^a-zA-Z0-9]+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase();
}

function PlaybackResourceGauge({
  label,
  valueLabel,
  gaugePercent = null,
  tone = "cpu",
}) {
  const isActive = Number.isFinite(gaugePercent);
  const displayValueLabel = valueLabel === "—" ? `${label} —` : valueLabel;
  return (
    <div className={["playback-resource-gauge", !isActive ? "playback-resource-gauge--inactive" : ""].filter(Boolean).join(" ")}>
      <span className="playback-resource-gauge__value">
        {displayValueLabel}
      </span>
      <div
        className={[
          "playback-resource-gauge__circle",
          `playback-resource-gauge__circle--${tone}`,
          !isActive ? "playback-resource-gauge__circle--inactive" : "",
        ].filter(Boolean).join(" ")}
        style={isActive ? { "--playback-gauge-progress": `${gaugePercent}%` } : undefined}
      >
        <span>{label}</span>
      </div>
    </div>
  );
}

function AdminSectionIcon({ name }) {
  if (name === "security") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M12 3l7 3v5c0 4.7-2.7 8.9-7 10-4.3-1.1-7-5.3-7-10V6l7-3z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
      </svg>
    );
  }
  if (name === "logs") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M6 4h9l3 3v13H6z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        <path d="M15 4v4h4M9 12h6M9 16h6" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
      </svg>
    );
  }
  if (name === "recovery") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M8 7a7 7 0 0 1 11 2" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        <path d="M19 5v4h-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        <path d="M16 17a7 7 0 0 1-11-2" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        <path d="M5 19v-4h4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
      <path d="M4 5h7v6H4zM13 5h7v6h-7zM4 13h7v6H4zM13 13h7v6h-7z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}


export function AdminPage() {
  const { user, refreshAuth } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [statusPayload, setStatusPayload] = useState(null);
  const [usersPayload, setUsersPayload] = useState([]);
  const [sessionsPayload, setSessionsPayload] = useState([]);
  const [auditPayload, setAuditPayload] = useState([]);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState(null);
  const [recoveryFeedback, setRecoveryFeedback] = useState(null);
  const [statusRefreshPending, setStatusRefreshPending] = useState(false);
  const [createPending, setCreatePending] = useState(false);
  const [createBackupPending, setCreateBackupPending] = useState(false);
  const [recoveryLoading, setRecoveryLoading] = useState(false);
  const [recoveryLoaded, setRecoveryLoaded] = useState(false);
  const [backupsDirectory, setBackupsDirectory] = useState("");
  const [backupsPayload, setBackupsPayload] = useState([]);
  const [selectedCheckpointId, setSelectedCheckpointId] = useState("");
  const [inspectPending, setInspectPending] = useState(false);
  const [inspectPayload, setInspectPayload] = useState(null);
  const [restorePlanPending, setRestorePlanPending] = useState(false);
  const [restorePlanPayload, setRestorePlanPayload] = useState(null);
  const [backupCreateModal, setBackupCreateModal] = useState({
    open: false,
    passphrase: "",
    confirmPassphrase: "",
    error: "",
  });
  const [backupPassphraseModal, setBackupPassphraseModal] = useState({
    mode: null,
    checkpointId: "",
    passphrase: "",
    error: "",
  });
  const [showAllRecoveryCheckpoints, setShowAllRecoveryCheckpoints] = useState(false);
  const [showAllRecoveryWarnings, setShowAllRecoveryWarnings] = useState(false);
  const [userActionPending, setUserActionPending] = useState(null);
  const [sessionActionPending, setSessionActionPending] = useState(null);
  const [showAllSessions, setShowAllSessions] = useState(false);
  const [showAllAudit, setShowAllAudit] = useState(false);
  const [showAllUsers, setShowAllUsers] = useState(false);
  const [showAllCreateUserAges, setShowAllCreateUserAges] = useState(false);
  const [userFeedback, setUserFeedback] = useState({});
  const [userActionsModalUserId, setUserActionsModalUserId] = useState(null);
  const [createUserExpanded, setCreateUserExpanded] = useState(false);
  const [deleteUserState, setDeleteUserState] = useState({
    userId: null,
    username: "",
    currentAdminPassword: "",
    fieldToken: createDestructiveGateToken(),
    pending: false,
    error: "",
  });
  const [inviteCodes, setInviteCodes] = useState([]);
  const [invitePending, setInvitePending] = useState(false);
  const [revealedInviteIds, setRevealedInviteIds] = useState({});
  const [inviteCodesExpanded, setInviteCodesExpanded] = useState(true);
  const [inviteAgeModalOpen, setInviteAgeModalOpen] = useState(false);
  const [inviteAssignedAge, setInviteAssignedAge] = useState(18);
  const [passwordHelpRequests, setPasswordHelpRequests] = useState([]);
  const [passwordHelpPendingId, setPasswordHelpPendingId] = useState(null);
  const [expandedPasswordHelpRequestId, setExpandedPasswordHelpRequestId] = useState(null);
  const [adminConfirmModal, setAdminConfirmModal] = useState({
    type: null,
    payload: null,
    pending: false,
    error: "",
  });
  const [urlPrefixStatus, setUrlPrefixStatus] = useState(null);
  const [urlPrefixPending, setUrlPrefixPending] = useState(false);
  const [urlPrefixReminderDismissed, setUrlPrefixReminderDismissed] = useState(false);
  const [urlPrefixRotateModal, setUrlPrefixRotateModal] = useState({
    open: false,
    currentAdminPassword: "",
    pending: false,
    error: "",
  });
  const [exposureStatus, setExposureStatus] = useState(null);
  const [exposurePlan, setExposurePlan] = useState(null);
  const [exposureFeedback, setExposureFeedback] = useState(null);
  const [exposurePending, setExposurePending] = useState(false);
  const [exposureDraft, setExposureDraft] = useState(DEFAULT_EXPOSURE_DRAFT);
  const [exposureMaintenanceForm, setExposureMaintenanceForm] = useState(DEFAULT_EXPOSURE_MAINTENANCE_FORM);
  const [exposureMaintenanceFeedback, setExposureMaintenanceFeedback] = useState(null);
  const [exposureMaintenanceTargetMode, setExposureMaintenanceTargetMode] = useState(null);
  const [exposurePrepareForm, setExposurePrepareForm] = useState(DEFAULT_EXPOSURE_PREPARE_FORM);
  const [exposurePrepareFeedback, setExposurePrepareFeedback] = useState(null);
  const [exposureVerifyForm, setExposureVerifyForm] = useState(DEFAULT_EXPOSURE_VERIFY_FORM);
  const [exposureVerifyFeedback, setExposureVerifyFeedback] = useState(null);
  const [exposureVerificationResult, setExposureVerificationResult] = useState(null);
  const [exposureFinalizeForm, setExposureFinalizeForm] = useState(DEFAULT_EXPOSURE_FINALIZE_FORM);
  const [exposureFinalizeFeedback, setExposureFinalizeFeedback] = useState(null);
  const [exposurePlannerOpen, setExposurePlannerOpen] = useState(false);
  const [totpStatus, setTotpStatus] = useState(null);
  const [ownTotpModal, setOwnTotpModal] = useState({
    mode: null,
    password: "",
    code: "",
    pending: false,
    error: "",
    recoveryCodes: [],
    copyFeedback: "",
  });
  const [totpPromptPendingUserId, setTotpPromptPendingUserId] = useState(null);
  const [totpDisableUserModal, setTotpDisableUserModal] = useState({
    userId: null,
    username: "",
    currentAdminPassword: "",
    pending: false,
    error: "",
  });
  const [downloadAccessState, setDownloadAccessState] = useState({
    userId: null,
    loading: false,
    saving: false,
    accessMode: "none",
    selectedItems: [],
    searchQuery: "",
    searchResults: [],
    searchPending: false,
    feedback: "",
    error: "",
  });
  const [playbackWorkersPayload, setPlaybackWorkersPayload] = useState(null);
  const [playbackWorkersWarning, setPlaybackWorkersWarning] = useState("");
  const [playbackWorkersFeedback, setPlaybackWorkersFeedback] = useState(null);
  const [terminateWorkerPending, setTerminateWorkerPending] = useState("");
  const [terminateWorkerModal, setTerminateWorkerModal] = useState(null);
  const [dismissedPlaybackStatusKeys, setDismissedPlaybackStatusKeys] = useState({});
  const [dismissPlaybackStatusModal, setDismissPlaybackStatusModal] = useState(null);
  const [collapsedWorkerUserIds, setCollapsedWorkerUserIds] = useState({});
  const [diagnosticIdModal, setDiagnosticIdModal] = useState(null);
  const [selfDeleteState, setSelfDeleteState] = useState({
    open: false,
    password: "",
    armed: false,
    pending: false,
    error: "",
  });
  const [roleConfirm, setRoleConfirm] = useState({
    userId: null,
    username: "",
    nextRole: "standard_user",
    currentAdminPassword: "",
  });
  const [passwordEditor, setPasswordEditor] = useState({
    userId: null,
    username: "",
    newPassword: "",
    currentAdminPassword: "",
  });
  const [createUserForm, setCreateUserForm] = useState({
    username: "",
    password: "",
    role: "standard_user",
    ageCredential: 18,
  });
  const [ageCredentialEditor, setAgeCredentialEditor] = useState({
    userId: null,
    ageCredential: 18,
  });
  const cloudSyncWarningRef = useRef("");
  const scanRunningRef = useRef(false);
  const adminStreamRef = useRef(null);
  const adminStreamReconnectTimerRef = useRef(null);
  const adminStreamReconnectDelayRef = useRef(3000);
  const realtimeRefreshInFlightRef = useRef(false);
  const realtimeRefreshQueuedRef = useRef(false);
  const desktopRequestGenerationRef = useRef(0);
  const desktopRequestControllerRef = useRef(null);
  const activeDesktopAdminTabRef = useRef("");
  const sectionCollapseTimerRef = useRef(0);
  const ownTotpDialogRef = useRef(null);
  const ownTotpReturnFocusRef = useRef(null);
  const ownTotpPendingRef = useRef(false);
  ownTotpPendingRef.current = ownTotpModal.pending;
  const initialControlCenterPath = classifyControlCenterPath(location.pathname);
  const desktopControlCenter = isDesktopControlCenterDevice(
    detectClientDeviceClass(),
    detectClientPlatform(),
  ) && initialControlCenterPath.area === "admin" && Boolean(initialControlCenterPath.tab);
  const desktopAdminTab = desktopControlCenter ? initialControlCenterPath.tab : "";
  activeDesktopAdminTabRef.current = desktopAdminTab;
  const [activeSection, setActiveSection] = useState(() => (
    initialControlCenterPath.area === "admin" && initialControlCenterPath.tab
      ? desktopAdminTabToLegacySection(initialControlCenterPath.tab)
      : readPersistedPanelState(ADMIN_ACTIVE_SECTION_STORAGE_KEY, ADMIN_SECTION_KEYS, "panel")
  ));
  const [expandedSection, setExpandedSection] = useState(null);
  const [documentVisible, setDocumentVisible] = useState(() => (
    typeof document === "undefined" || document.visibilityState === "visible"
  ));

  useEffect(() => {
    try {
      if (window.localStorage.getItem(ADMIN_ACTIVE_SECTION_STORAGE_KEY) === "assistant") {
        writePersistedPanelState(
          ADMIN_ACTIVE_SECTION_STORAGE_KEY,
          "panel",
          ADMIN_SECTION_KEYS,
        );
      }
    } catch {
      // Section persistence is optional; the in-memory default remains usable.
    }
  }, []);

  useEffect(() => {
    const controlCenterPath = classifyControlCenterPath(location.pathname);
    if (controlCenterPath.area === "admin" && controlCenterPath.tab) {
      const nestedSection = desktopAdminTabToLegacySection(controlCenterPath.tab);
      setActiveSection(nestedSection);
      setExpandedSection(nestedSection);
      return;
    }
    const params = new URLSearchParams(location.search);
    const requestedSection = params.get("section");
    if (!requestedSection) {
      return;
    }
    if (requestedSection === "assistant") {
      params.set("section", "panel");
      writePersistedPanelState(ADMIN_ACTIVE_SECTION_STORAGE_KEY, "panel", ADMIN_SECTION_KEYS);
      setActiveSection("panel");
      setExpandedSection("panel");
      navigate(`${location.pathname}?${params.toString()}${location.hash || ""}`, {
        replace: true,
        state: location.state,
      });
      return;
    }
    const isKnownSection = ADMIN_SECTIONS.some((section) => section.key === requestedSection);
    if (!isKnownSection) {
      params.set("section", "panel");
      writePersistedPanelState(ADMIN_ACTIVE_SECTION_STORAGE_KEY, "panel", ADMIN_SECTION_KEYS);
      setActiveSection("panel");
      setExpandedSection("panel");
      navigate(`${location.pathname}?${params.toString()}${location.hash || ""}`, {
        replace: true,
        state: location.state,
      });
      return;
    }
    writePersistedPanelState(ADMIN_ACTIVE_SECTION_STORAGE_KEY, requestedSection, ADMIN_SECTION_KEYS);
    setActiveSection(requestedSection);
    setExpandedSection(requestedSection);
  }, [location.hash, location.pathname, location.search, location.state, navigate]);

  const hasAnotherEnabledAdmin = useMemo(
    () => usersPayload.some((entry) => entry.id !== user?.id && entry.role === "admin" && entry.enabled),
    [usersPayload, user?.id],
  );
  const selectedUserActionsEntry = useMemo(
    () => usersPayload.find((entry) => entry.id === userActionsModalUserId) || null,
    [usersPayload, userActionsModalUserId],
  );
  const playbackWorkersByUserId = useMemo(
    () => buildPlaybackWorkersByUserId(playbackWorkersPayload),
    [playbackWorkersPayload],
  );

  async function loadPlaybackWorkers({ silent = false } = {}) {
    try {
      const payload = await apiRequest("/api/admin/playback-workers");
      setPlaybackWorkersPayload(payload);
      setPlaybackWorkersWarning("");
    } catch (requestError) {
      if (!silent) {
        console.error("Failed to load playback worker status", requestError);
      }
      setPlaybackWorkersWarning(requestError.message || "Playback worker status is temporarily unavailable.");
    }
  }

  function applySystemStatus(status) {
    if (scanRunningRef.current && !status.scan.running) {
      const completionText = cloudSyncWarningRef.current
        ? formatCompletedRescanWarning(cloudSyncWarningRef.current)
        : (status.last_scan?.message || "Library scan completed.");
      setBanner({
        tone: cloudSyncWarningRef.current ? "error" : "success",
        text: completionText,
      });
    }
    scanRunningRef.current = Boolean(status.scan.running);
    setStatusPayload(status);
  }

  async function loadSystemStatus({ silent = false, signal } = {}) {
    try {
      const status = desktopControlCenter
        ? await desktopAdminResourceRequest("system", { force: true })
        : await apiRequest("/api/system/status", { signal });
      applySystemStatus(status);
      return true;
    } catch (requestError) {
      if (requestError.name === "AbortError") {
        return false;
      }
      if (!silent) {
        setBanner({ tone: "error", text: requestError.message || "Failed to load system status" });
      }
      return false;
    }
  }

  function applyDesktopAdminResource(resource, payload) {
    if (resource === "system") {
      applySystemStatus(payload);
    } else if (resource === "users") {
      setUsersPayload(payload.users || []);
    } else if (resource === "sessions") {
      setSessionsPayload(payload.sessions || []);
    } else if (resource === "audit") {
      setAuditPayload(payload.events || []);
    } else if (resource === "urlPrefix") {
      setUrlPrefixStatus(payload);
    } else if (resource === "ownTotp") {
      setTotpStatus(payload);
    } else if (resource === "invites") {
      setInviteCodes(payload.invite_codes || []);
    } else if (resource === "passwordHelp") {
      setPasswordHelpRequests(payload.requests || []);
    } else if (resource === "exposure") {
      setExposureStatus(payload);
      setExposurePlan(payload);
      setExposureVerificationResult(payload.prepared_switch?.verification || null);
    } else if (resource === "backups") {
      const checkpoints = Array.isArray(payload.checkpoints) ? payload.checkpoints : [];
      setBackupsDirectory(typeof payload.backups_dir === "string" ? payload.backups_dir : "");
      setBackupsPayload(checkpoints);
      setRecoveryLoaded(true);
      setSelectedCheckpointId((current) => (
        current && checkpoints.some((entry) => entry.checkpoint_id === current)
          ? current
          : checkpoints[0]?.checkpoint_id || ""
      ));
    }
  }

  function desktopAdminResourceRequest(resource, { force = false } = {}) {
    return fetchControlCenterResource({
      userId: user?.id,
      role: user?.role,
      resource,
      force,
    });
  }

  async function loadDesktopAdminSection(tab, { signal, force = false } = {}) {
    const generation = desktopRequestGenerationRef.current + 1;
    desktopRequestGenerationRef.current = generation;
    setLoading(true);
    const resources = desktopAdminResourcesForTab(tab);
    const results = await Promise.allSettled(
      resources.map((resource) => desktopAdminResourceRequest(resource, { force })),
    );
    if (signal?.aborted || generation !== desktopRequestGenerationRef.current) {
      return;
    }
    const failures = [];
    results.forEach((result, index) => {
      if (result.status === "fulfilled") {
        applyDesktopAdminResource(resources[index], result.value);
        return;
      }
      if (result.reason?.name !== "AbortError") {
        failures.push(result.reason?.message || `Failed to load ${resources[index]}`);
      }
    });
    if (failures.length > 0) {
      setBanner({ tone: "error", text: failures[0] });
    }
    setLoading(false);
  }

  async function refreshAdminUsersResource() {
    if (!desktopControlCenter) {
      await loadAdminData({ silent: true });
      return;
    }
    const payload = await desktopAdminResourceRequest("users", { force: true });
    applyDesktopAdminResource("users", payload);
  }

  async function loadAdminData({ silent = false } = {}) {
    if (!silent) {
      setLoading(true);
    }
    try {
      const [status, users, sessions, audit, urlPrefix, ownTotpStatus] = await Promise.all([
        apiRequest("/api/system/status"),
        apiRequest("/api/admin/users"),
        apiRequest("/api/admin/sessions"),
        apiRequest("/api/admin/audit?limit=100"),
        apiRequest("/api/admin/url-prefix"),
        apiRequest("/api/auth/totp/status"),
      ]);
      applySystemStatus(status);
      setUsersPayload(users.users);
      setSessionsPayload(sessions.sessions);
      setAuditPayload(audit.events);
      setUrlPrefixStatus(urlPrefix);
      setTotpStatus(ownTotpStatus);
      return true;
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to load admin data",
      });
      return false;
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }

  async function loadInviteCodes() {
    try {
      const payload = await apiRequest("/api/admin/invite-codes");
      setInviteCodes(payload.invite_codes || []);
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to load invite codes",
      });
    }
  }

  async function loadPasswordHelpRequests() {
    try {
      const payload = await apiRequest("/api/admin/password-help-requests");
      setPasswordHelpRequests(payload.requests || []);
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to load password help requests",
      });
    }
  }

  async function handlePasswordHelpRefresh() {
    await loadPasswordHelpRequests();
  }

  async function handleUrlPrefixRefreshStatus() {
    await loadUrlPrefixStatus();
  }

  async function handleRecoveryRefresh() {
    await loadRecoveryData();
  }

  async function loadUrlPrefixStatus() {
    setUrlPrefixPending(true);
    try {
      const payload = await apiRequest("/api/admin/url-prefix");
      setUrlPrefixStatus(payload);
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to load URL prefix status",
      });
    } finally {
      setUrlPrefixPending(false);
    }
  }

  async function loadExposureStatus({ signal } = {}) {
    try {
      const payload = await apiRequest("/api/admin/exposure/status", { signal });
      setExposureStatus(payload);
      setExposureVerificationResult(payload.prepared_switch?.verification || null);
      setExposurePlan(payload);
    } catch (requestError) {
      if (requestError.name === "AbortError") {
        return;
      }
      setExposureFeedback({
        tone: "error",
        text: requestError.message || "Failed to load exposure planner status.",
      });
    }
  }

  async function handleValidateExposurePlan() {
    setExposurePending(true);
    setExposureFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/exposure/validate", {
        method: "POST",
        data: exposurePayloadFromDraft(exposureDraft),
      });
      setExposurePlan(payload);
      setExposureStatus(payload);
      setExposureFeedback({
        tone: payload.validation?.status === "blocked" ? "error" : "success",
        text: payload.validation?.status === "blocked"
          ? "Exposure plan has blocking validation errors."
          : "Exposure plan validated. Review warnings before saving a pending draft.",
      });
    } catch (requestError) {
      setExposureFeedback({
        tone: "error",
        text: requestError.message || "Failed to validate exposure plan.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  async function handleSaveExposureDraft() {
    if (!exposureDraft.currentAdminPassword.trim()) {
      setExposureFeedback({ tone: "error", text: "Enter your current admin password to save a pending draft." });
      return;
    }
    if (!exposureDraft.acknowledgement) {
      setExposureFeedback({ tone: "error", text: "Acknowledge the Phase 1 limitations before saving." });
      return;
    }
    if (exposureDraft.selectedMode === "public_direct_ip" && !exposureDraft.directIpNotRecommendedAcknowledgement) {
      setExposureFeedback({
        tone: "error",
        text: "Confirm that direct public IP exposure is not recommended before saving.",
      });
      return;
    }
    setExposurePending(true);
    setExposureFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/exposure/drafts", {
        method: "POST",
        data: exposurePayloadFromDraft(exposureDraft, { includePassword: true }),
      });
      setExposurePlan(payload);
      setExposureStatus(payload);
      setExposureDraft((current) => ({ ...current, currentAdminPassword: "" }));
      setExposureFeedback({ tone: "success", text: "Pending exposure draft saved. It does not take effect yet." });
    } catch (requestError) {
      setExposureFeedback({
        tone: "error",
        text: requestError.message || "Failed to save pending exposure draft.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  async function handleClearExposureDraft() {
    setExposurePending(true);
    setExposureFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/exposure/drafts", { method: "DELETE" });
      setExposurePlan(payload);
      setExposureStatus(payload);
      setExposureFeedback({ tone: "success", text: "Pending exposure draft cleared. Runtime behavior was unchanged." });
    } catch (requestError) {
      setExposureFeedback({
        tone: "error",
        text: requestError.message || "Failed to clear pending exposure draft.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  function updateExposureMaintenanceLockState(lockState) {
    setExposureStatus((current) => (
      current
        ? { ...current, active: { ...(current.active || {}), maintenance_lock: lockState, maintenance_mode: lockState } }
        : current
    ));
    setExposurePlan((current) => (
      current
        ? { ...current, active: { ...(current.active || {}), maintenance_lock: lockState, maintenance_mode: lockState } }
        : current
    ));
  }

  function updateExposurePreparedSwitchState(preparedSwitch, verification = undefined) {
    setExposureStatus((current) => (
      current
        ? { ...current, prepared_switch: preparedSwitch || null, takes_effect: false }
        : current
    ));
    setExposurePlan((current) => (
      current
        ? { ...current, prepared_switch: preparedSwitch || null, takes_effect: false }
        : current
    ));
    if (verification !== undefined) {
      setExposureVerificationResult(verification || null);
    } else if (preparedSwitch?.verification) {
      setExposureVerificationResult(preparedSwitch.verification);
    } else if (!preparedSwitch) {
      setExposureVerificationResult(null);
    }
  }

  function updateExposureFinalizedProfileState(finalizedProfile) {
    setExposureStatus((current) => (
      current
        ? {
            ...current,
            finalized_profile: finalizedProfile || null,
            pending_draft: null,
            prepared_switch: null,
            takes_effect: false,
          }
        : current
    ));
    setExposurePlan((current) => (
      current
        ? {
            ...current,
            finalized_profile: finalizedProfile || null,
            pending_draft: null,
            prepared_switch: null,
            takes_effect: false,
          }
        : current
    ));
    setExposureVerificationResult(null);
  }

  async function handleSetExposureMaintenanceLock(enabled) {
    if (!exposureMaintenanceForm.currentAdminPassword.trim()) {
      setExposureMaintenanceFeedback({
        tone: "error",
        text: "Enter your current admin password to update Maintenance Mode.",
      });
      return;
    }
    if (enabled && !exposureMaintenanceForm.acknowledgement) {
      setExposureMaintenanceFeedback({
        tone: "error",
        text: "Acknowledge the Maintenance Mode impact before enabling it.",
      });
      return;
    }
    setExposurePending(true);
    setExposureMaintenanceFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/maintenance-mode", {
        method: enabled ? "POST" : "DELETE",
        data: {
          current_admin_password: exposureMaintenanceForm.currentAdminPassword,
          acknowledgement: enabled ? exposureMaintenanceForm.acknowledgement : false,
        },
      });
      updateExposureMaintenanceLockState(payload);
      setExposureMaintenanceTargetMode(enabled ? "on" : "off");
      setExposureMaintenanceForm(DEFAULT_EXPOSURE_MAINTENANCE_FORM);
      setExposureMaintenanceFeedback({
        tone: "success",
        text: enabled
          ? `Maintenance Mode enabled. ${payload.revoked_non_admin_sessions || 0} non-admin session(s) logged out.`
          : "Maintenance Mode disabled.",
      });
    } catch (requestError) {
      setExposureMaintenanceFeedback({
        tone: "error",
        text: requestError.message || "Failed to update Maintenance Mode.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  async function handlePrepareExposureManualSwitch() {
    if (!exposurePrepareForm.currentAdminPassword.trim()) {
      setExposurePrepareFeedback({
        tone: "error",
        text: "Enter your current admin password to prepare a manual switch.",
      });
      return;
    }
    if (!exposurePrepareForm.acknowledgement) {
      setExposurePrepareFeedback({
        tone: "error",
        text: "Acknowledge that this only prepares a manual switch plan before continuing.",
      });
      return;
    }
    setExposurePending(true);
    setExposurePrepareFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/exposure/prepare-switch", {
        method: "POST",
        data: {
          current_admin_password: exposurePrepareForm.currentAdminPassword,
          acknowledgement: exposurePrepareForm.acknowledgement,
        },
      });
      updateExposurePreparedSwitchState(payload.prepared_switch || null, null);
      setExposurePrepareForm(DEFAULT_EXPOSURE_PREPARE_FORM);
      setExposurePrepareFeedback({ tone: "success", text: "Prepared for manual apply." });
    } catch (requestError) {
      setExposurePrepareFeedback({
        tone: "error",
        text: requestError.message || "Failed to prepare manual switch.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  async function handleClearExposurePreparedSwitch() {
    if (!exposurePrepareForm.currentAdminPassword.trim()) {
      setExposurePrepareFeedback({
        tone: "error",
        text: "Enter your current admin password to clear the prepared switch.",
      });
      return;
    }
    setExposurePending(true);
    setExposurePrepareFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/exposure/prepared-switch", {
        method: "DELETE",
        data: {
          current_admin_password: exposurePrepareForm.currentAdminPassword,
          acknowledgement: false,
        },
      });
      updateExposurePreparedSwitchState(payload.prepared_switch || null, null);
      setExposurePrepareForm(DEFAULT_EXPOSURE_PREPARE_FORM);
      setExposurePrepareFeedback({ tone: "success", text: "Prepared switch cleared." });
    } catch (requestError) {
      setExposurePrepareFeedback({
        tone: "error",
        text: requestError.message || "Failed to clear prepared switch.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  async function handleVerifyExposurePreparedSwitch() {
    if (!exposurePreparedSwitch) {
      setExposureVerifyFeedback({
        tone: "error",
        text: "Prepare and manually apply a switch before verification.",
      });
      return;
    }
    if (!exposureVerifyForm.currentAdminPassword.trim()) {
      setExposureVerifyFeedback({
        tone: "error",
        text: "Enter your current admin password to verify the prepared switch.",
      });
      return;
    }
    if (!exposureVerifyForm.acknowledgement) {
      setExposureVerifyFeedback({
        tone: "error",
        text: "Acknowledge that this only verifies the prepared manual switch before continuing.",
      });
      return;
    }
    setExposurePending(true);
    setExposureVerifyFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/exposure/verify-prepared-switch", {
        method: "POST",
        data: {
          current_admin_password: exposureVerifyForm.currentAdminPassword,
          acknowledgement: exposureVerifyForm.acknowledgement,
        },
      });
      updateExposurePreparedSwitchState(payload.prepared_switch || null, payload.verification || null);
      setExposureVerifyForm(DEFAULT_EXPOSURE_VERIFY_FORM);
      setExposureVerifyFeedback({
        tone: "success",
        text: payload.verification?.status === "warnings" ? "Verified with warnings." : "Verified after restart.",
      });
    } catch (requestError) {
      const verification = requestError.detail?.verification;
      if (verification) {
        setExposureVerificationResult(verification);
      }
      setExposureVerifyFeedback({
        tone: "error",
        text: requestError.message || "Failed to verify prepared switch.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  async function handleFinalizeExposureProfile() {
    if (exposurePreparedSwitch?.status !== "verified_after_restart") {
      setExposureFinalizeFeedback({
        tone: "error",
        text: "Verify a prepared switch first.",
      });
      return;
    }
    if (!exposureFinalizeForm.currentAdminPassword.trim()) {
      setExposureFinalizeFeedback({
        tone: "error",
        text: "Enter your current admin password to finalize the verified profile.",
      });
      return;
    }
    if (!exposureFinalizeForm.acknowledgement) {
      setExposureFinalizeFeedback({
        tone: "error",
        text: "Acknowledge what finalization records before continuing.",
      });
      return;
    }
    setExposurePending(true);
    setExposureFinalizeFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/exposure/finalize-profile", {
        method: "POST",
        data: {
          current_admin_password: exposureFinalizeForm.currentAdminPassword,
          acknowledgement: exposureFinalizeForm.acknowledgement,
        },
      });
      updateExposureFinalizedProfileState(payload.finalized_profile || null);
      setExposureFinalizeForm(DEFAULT_EXPOSURE_FINALIZE_FORM);
      setExposureFinalizeFeedback({
        tone: "success",
        text: "Finalized verified profile. Maintenance Mode remains on until an admin turns it off.",
      });
    } catch (requestError) {
      setExposureFinalizeFeedback({
        tone: "error",
        text: requestError.message || "Failed to finalize verified profile.",
      });
    } finally {
      setExposurePending(false);
    }
  }

  async function handleOpenExposurePlanner() {
    setExposurePlannerOpen(true);
    setExposureMaintenanceFeedback(null);
    setExposurePrepareFeedback(null);
    setExposureVerifyFeedback(null);
    setExposureFinalizeFeedback(null);
    setExposureMaintenanceTargetMode(null);
    await loadExposureStatus();
  }

  function handleCloseExposurePlanner() {
    if (!exposurePending) {
      setExposurePlannerOpen(false);
    }
  }

  async function handleRotateUrlPrefix(event) {
    event.preventDefault();
    if (!urlPrefixRotateModal.currentAdminPassword.trim()) {
      setUrlPrefixRotateModal((current) => ({
        ...current,
        error: "Enter your current admin password to rotate the URL prefix.",
      }));
      return;
    }
    setUrlPrefixRotateModal((current) => ({ ...current, pending: true, error: "" }));
    try {
      const payload = await apiRequest("/api/admin/url-prefix/rotate", {
        method: "POST",
        data: { current_admin_password: urlPrefixRotateModal.currentAdminPassword },
      });
      const nextPrefix = payload.new_prefix;
      window.alert(`New URL prefix: /${nextPrefix}/. Logging out...`);
      window.location.assign(`${window.location.origin}/${nextPrefix}/login`);
    } catch (requestError) {
      setUrlPrefixRotateModal((current) => ({
        ...current,
        pending: false,
        error: requestError.message || "Failed to rotate URL prefix.",
      }));
    }
  }

  function closeUrlPrefixRotateModal() {
    if (urlPrefixRotateModal.pending) {
      return;
    }
    setUrlPrefixRotateModal({
      open: false,
      currentAdminPassword: "",
      pending: false,
      error: "",
    });
  }

  async function handleToggleUserTotpRequirement(entry) {
    const currentlyEnabled = Boolean(entry.totp_setup_prompt_enabled || entry.totp_enabled);
    const nextEnabled = !currentlyEnabled;
    if (!nextEnabled) {
      setTotpDisableUserModal({
        userId: entry.id,
        username: entry.username,
        currentAdminPassword: "",
        pending: false,
        error: "",
      });
      return;
    }
    setTotpPromptPendingUserId(entry.id);
    try {
      await apiRequest(`/api/admin/users/${entry.id}/2fa/setup-prompt`, {
        method: "PATCH",
        data: { enabled: true },
      });
      setBanner({
        tone: "success",
        text: `Enabled 2FA for ${entry.username}.`,
      });
      await refreshAdminUsersResource();
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || `Failed to update 2FA for ${entry.username}.`,
      });
    } finally {
      setTotpPromptPendingUserId(null);
    }
  }

  function closeTotpDisableUserModal() {
    if (totpDisableUserModal.pending) {
      return;
    }
    setTotpDisableUserModal({
      userId: null,
      username: "",
      currentAdminPassword: "",
      pending: false,
      error: "",
    });
  }

  async function handleConfirmDisableUserTotp(event) {
    event.preventDefault();
    if (!totpDisableUserModal.userId) {
      return;
    }
    if (!totpDisableUserModal.currentAdminPassword.trim()) {
      setTotpDisableUserModal((current) => ({
        ...current,
        error: "Enter your current admin password to disable 2FA.",
      }));
      return;
    }
    const userId = totpDisableUserModal.userId;
    const username = totpDisableUserModal.username;
    setTotpDisableUserModal((current) => ({ ...current, pending: true, error: "" }));
    setTotpPromptPendingUserId(userId);
    try {
      await apiRequest(`/api/admin/users/${userId}/2fa/disable`, {
        method: "POST",
        data: { current_admin_password: totpDisableUserModal.currentAdminPassword },
      });
      setBanner({
        tone: "success",
        text: `Disabled 2FA for ${username}.`,
      });
      setTotpDisableUserModal({
        userId: null,
        username: "",
        currentAdminPassword: "",
        pending: false,
        error: "",
      });
      await refreshAdminUsersResource();
    } catch (requestError) {
      setTotpDisableUserModal((current) => ({
        ...current,
        pending: false,
        error: requestError.message || `Failed to disable 2FA for ${username}.`,
      }));
    } finally {
      setTotpPromptPendingUserId(null);
    }
  }

  function openOwnTotpModal(mode) {
    ownTotpReturnFocusRef.current = document.activeElement;
    setOwnTotpModal({
      mode,
      password: "",
      code: "",
      pending: false,
      error: "",
      recoveryCodes: [],
      copyFeedback: "",
    });
  }

  function closeOwnTotpModal() {
    if (ownTotpModal.pending) {
      return;
    }
    setOwnTotpModal({
      mode: null,
      password: "",
      code: "",
      pending: false,
      error: "",
      recoveryCodes: [],
      copyFeedback: "",
    });
  }

  async function handleRegenerateOwnRecoveryCodes(event) {
    event?.preventDefault?.();
    if (!ownTotpModal.password.trim() || !/^\d{6}$/.test(ownTotpModal.code.trim())) {
      setOwnTotpModal((current) => ({
        ...current,
        error: "Enter your current password and a current 6-digit authenticator code.",
      }));
      return;
    }
    setOwnTotpModal((current) => ({ ...current, pending: true, error: "" }));
    try {
      const payload = await apiRequest("/api/auth/recovery-codes/regenerate", {
        method: "POST",
        data: { password: ownTotpModal.password, totp_code: ownTotpModal.code.trim() },
      });
      setOwnTotpModal((current) => ({
        ...current,
        password: "",
        code: "",
        pending: false,
        recoveryCodes: payload.recovery_codes || [],
      }));
      const status = await apiRequest("/api/auth/totp/status");
      setTotpStatus(status);
    } catch (requestError) {
      setOwnTotpModal((current) => ({
        ...current,
        password: "",
        code: "",
        pending: false,
        error: requestError.message || "Failed to regenerate recovery codes.",
      }));
    }
  }

  async function handleDisableOwnTotp(event) {
    event?.preventDefault?.();
    if (!ownTotpModal.password.trim() || !ownTotpModal.code.trim()) {
      setOwnTotpModal((current) => ({
        ...current,
        error: "Enter your current password and an authenticator or recovery code.",
      }));
      return;
    }
    setOwnTotpModal((current) => ({ ...current, pending: true, error: "" }));
    try {
      await apiRequest("/api/auth/totp/disable", {
        method: "POST",
        data: { password: ownTotpModal.password, totp_or_recovery: ownTotpModal.code.trim() },
      });
      setBanner({ tone: "success", text: "Two-factor authentication disabled." });
      const status = await apiRequest("/api/auth/totp/status");
      setTotpStatus(status);
      closeOwnTotpModal();
    } catch (requestError) {
      setOwnTotpModal((current) => ({
        ...current,
        password: "",
        code: "",
        pending: false,
        error: requestError.message || "Failed to disable 2FA.",
      }));
    }
  }

  async function handleCopyOwnRecoveryCodes() {
    try {
      await navigator.clipboard.writeText(ownTotpModal.recoveryCodes.join("\n"));
      setOwnTotpModal((current) => ({ ...current, copyFeedback: "Copied" }));
    } catch {
      setOwnTotpModal((current) => ({ ...current, copyFeedback: "Could not copy. Select the codes manually." }));
    }
  }

  async function loadRecoveryData({ silent = false, preserveFeedback = false, preferredCheckpointId = "" } = {}) {
    if (!silent) {
      setRecoveryLoading(true);
    }
    if (!preserveFeedback) {
      setRecoveryFeedback(null);
    }
    try {
      const payload = await apiRequest("/api/admin/backups");
      const checkpoints = Array.isArray(payload.checkpoints) ? payload.checkpoints : [];
      setBackupsDirectory(typeof payload.backups_dir === "string" ? payload.backups_dir : "");
      setBackupsPayload(checkpoints);
      setRecoveryLoaded(true);
      setSelectedCheckpointId((current) => {
        if (preferredCheckpointId && checkpoints.some((entry) => entry.checkpoint_id === preferredCheckpointId)) {
          return preferredCheckpointId;
        }
        if (current && checkpoints.some((entry) => entry.checkpoint_id === current)) {
          return current;
        }
        return checkpoints[0]?.checkpoint_id || "";
      });
    } catch (requestError) {
      setRecoveryFeedback({
        tone: "error",
        text: requestError.message || "Failed to load backup checkpoints.",
      });
    } finally {
      if (!silent) {
        setRecoveryLoading(false);
      }
    }
  }

  async function loadAdminRealtimeState() {
    if (realtimeRefreshInFlightRef.current) {
      realtimeRefreshQueuedRef.current = true;
      return;
    }
    realtimeRefreshInFlightRef.current = true;
    try {
      const activeTab = activeDesktopAdminTabRef.current;
      const refreshUsers = !desktopControlCenter
        || shouldRefreshDesktopRealtimeResource(activeTab, "users");
      const refreshSessions = !desktopControlCenter
        || shouldRefreshDesktopRealtimeResource(activeTab, "sessions");
      const requests = [];
      if (refreshUsers) {
        requests.push(["users", desktopControlCenter
          ? desktopAdminResourceRequest("users", { force: true })
          : apiRequest("/api/admin/users")]);
      }
      if (refreshSessions) {
        requests.push(["sessions", desktopControlCenter
          ? desktopAdminResourceRequest("sessions", { force: true })
          : apiRequest("/api/admin/sessions")]);
      }
      const results = await Promise.all(requests.map(([, request]) => request));
      results.forEach((payload, index) => {
        if (requests[index][0] === "users") {
          setUsersPayload(payload.users || []);
        } else {
          setSessionsPayload(payload.sessions || []);
        }
      });
      if (requests.length === 0) {
        return;
      }
    } catch (requestError) {
      if (requestError.status !== 401 && requestError.status !== 403) {
        console.error("Failed to refresh admin realtime data", requestError);
      }
    } finally {
      realtimeRefreshInFlightRef.current = false;
      if (realtimeRefreshQueuedRef.current) {
        realtimeRefreshQueuedRef.current = false;
        window.setTimeout(() => {
          loadAdminRealtimeState();
        }, 0);
      }
    }
  }

  useEffect(() => {
    if (!desktopControlCenter) {
      loadAdminData();
    }
  }, [desktopControlCenter]);

  useEffect(() => {
    if (!desktopControlCenter || !desktopAdminTab) {
      return undefined;
    }
    desktopRequestControllerRef.current?.abort();
    const controller = new AbortController();
    desktopRequestControllerRef.current = controller;
    loadDesktopAdminSection(desktopAdminTab, { signal: controller.signal });
    return () => {
      controller.abort();
      if (desktopRequestControllerRef.current === controller) {
        desktopRequestControllerRef.current = null;
      }
    };
  }, [desktopAdminTab, desktopControlCenter, user?.id, user?.role]);

  useEffect(() => {
    if (desktopControlCenter || activeSection !== "recovery" || recoveryLoaded || recoveryLoading) {
      return;
    }
    loadRecoveryData();
  }, [activeSection, desktopControlCenter, recoveryLoaded, recoveryLoading]);

  useEffect(() => {
    if (desktopControlCenter || user?.role !== "admin" || activeSection !== "panel") {
      return;
    }
    loadInviteCodes();
  }, [activeSection, desktopControlCenter, user?.role]);

  useEffect(() => {
    if (desktopControlCenter || user?.role !== "admin" || activeSection !== "security") {
      return;
    }
    loadExposureStatus();
    loadPasswordHelpRequests();
  }, [activeSection, desktopControlCenter, user?.role]);

  useEffect(() => {
    function handleVisibilityChange() {
      setDocumentVisible(document.visibilityState === "visible");
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useEffect(() => {
    if (!ownTotpModal.mode) {
      return undefined;
    }
    const dialog = ownTotpDialogRef.current;
    const focusableSelector = [
      "button:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[href]",
      "[tabindex]:not([tabindex='-1'])",
    ].join(",");
    dialog?.querySelector(focusableSelector)?.focus();

    function handleDialogKeyDown(event) {
      if (event.key === "Escape") {
        if (!ownTotpPendingRef.current) {
          event.preventDefault();
          closeOwnTotpModal();
        }
        return;
      }
      if (event.key !== "Tab" || !dialog) {
        return;
      }
      const focusable = [...dialog.querySelectorAll(focusableSelector)];
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleDialogKeyDown);
    return () => {
      document.removeEventListener("keydown", handleDialogKeyDown);
      ownTotpReturnFocusRef.current?.focus?.();
    };
  }, [ownTotpModal.mode]);

  useEffect(() => {
    if (!selectedUserActionsEntry) {
      setDownloadAccessState((current) => ({
        ...current,
        userId: null,
        accessMode: "none",
        selectedItems: [],
        searchQuery: "",
        searchResults: [],
        feedback: "",
        error: "",
      }));
      return;
    }
    loadDownloadAccessForUser(selectedUserActionsEntry);
  }, [selectedUserActionsEntry?.id]);

  useEffect(() => {
    if (!selectedUserActionsEntry || downloadAccessState.accessMode !== "selected") {
      return undefined;
    }
    const query = downloadAccessState.searchQuery.trim();
    if (!query) {
      setDownloadAccessState((current) => ({ ...current, searchResults: [], searchPending: false }));
      return undefined;
    }
    const controller = new AbortController();
    const timerId = window.setTimeout(async () => {
      setDownloadAccessState((current) => ({ ...current, searchPending: true }));
      try {
        const payload = await apiRequest(`/api/library/search?q=${encodeURIComponent(query)}`, {
          signal: controller.signal,
        });
        setDownloadAccessState((current) => ({
          ...current,
          searchPending: false,
          searchResults: (payload.items || []).filter(
            (item) => !current.selectedItems.some((selected) => selected.id === item.id),
          ),
        }));
      } catch (requestError) {
        if (requestError.name !== "AbortError") {
          setDownloadAccessState((current) => ({
            ...current,
            searchPending: false,
            error: requestError.message || "Movie search failed",
          }));
        }
      }
    }, 250);
    return () => {
      controller.abort();
      window.clearTimeout(timerId);
    };
  }, [
    downloadAccessState.accessMode,
    downloadAccessState.searchQuery,
    selectedUserActionsEntry,
  ]);

  useEffect(() => () => {
    if (typeof window !== "undefined" && sectionCollapseTimerRef.current) {
      window.clearTimeout(sectionCollapseTimerRef.current);
    }
  }, []);

  useEffect(() => {
    if (!statusPayload?.scan?.running) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      loadSystemStatus({ silent: true });
    }, 2500);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [statusPayload?.scan?.running]);

  useEffect(() => {
    const ownsDesktopPoll = desktopControlCenter
      && shouldPollDesktopPlaybackWorkers(desktopAdminTab, documentVisible ? "visible" : "hidden");
    const ownsLegacyPoll = !desktopControlCenter
      && user?.role === "admin"
      && activeSection === "panel"
      && documentVisible;
    if (user?.role !== "admin" || (!ownsDesktopPoll && !ownsLegacyPoll)) {
      return undefined;
    }
    loadPlaybackWorkers({ silent: true });
    const intervalId = window.setInterval(() => {
      loadPlaybackWorkers({ silent: true });
    }, PLAYBACK_WORKERS_POLL_MS);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [activeSection, desktopAdminTab, desktopControlCenter, documentVisible, user?.role]);

  useEffect(() => {
    if (user?.role !== "admin") {
      return undefined;
    }

    let disposed = false;

    function clearReconnectTimer() {
      if (adminStreamReconnectTimerRef.current) {
        window.clearTimeout(adminStreamReconnectTimerRef.current);
        adminStreamReconnectTimerRef.current = null;
      }
    }

    function scheduleReconnect() {
      clearReconnectTimer();
      const delay = adminStreamReconnectDelayRef.current;
      adminStreamReconnectTimerRef.current = window.setTimeout(() => {
        if (disposed) {
          return;
        }
        loadAdminRealtimeState();
        connectStream();
      }, delay);
      adminStreamReconnectDelayRef.current = Math.min(delay * 2, 30000);
    }

    function closeStream() {
      if (adminStreamRef.current) {
        adminStreamRef.current.close();
        adminStreamRef.current = null;
      }
    }

    function handleAdminStreamEvent() {
      loadAdminRealtimeState();
    }

    function connectStream() {
      closeStream();
      const stream = new EventSource("/api/admin/events/stream");
      adminStreamRef.current = stream;
      ADMIN_STREAM_RELEVANT_EVENTS.forEach((eventName) => {
        stream.addEventListener(eventName, handleAdminStreamEvent);
      });
      stream.onopen = () => {
        adminStreamReconnectDelayRef.current = 3000;
      };
      stream.onerror = () => {
        if (disposed) {
          return;
        }
        closeStream();
        scheduleReconnect();
      };
    }

    connectStream();

    return () => {
      disposed = true;
      clearReconnectTimer();
      closeStream();
    };
  }, [user?.role]);

  function setFeedbackForUser(userId, tone, text) {
    setUserFeedback((current) => ({
      ...current,
      [userId]: { tone, text },
    }));
  }

  function clearUserEditors(userId) {
    setRoleConfirm((current) => (current.userId === userId
      ? {
          userId: null,
          username: "",
          nextRole: "standard_user",
          currentAdminPassword: "",
        }
      : current));
    setPasswordEditor((current) => (current.userId === userId
      ? {
          userId: null,
          username: "",
          newPassword: "",
          currentAdminPassword: "",
        }
      : current));
    setDeleteUserState((current) => (current.userId === userId
      ? {
          userId: null,
          username: "",
          currentAdminPassword: "",
          fieldToken: createDestructiveGateToken(),
          pending: false,
          error: "",
        }
      : current));
    setAgeCredentialEditor((current) => (current.userId === userId
      ? {
          userId: null,
          ageCredential: 18,
        }
      : current));
  }

  function openUserActionsModal(entry) {
    clearUserEditors(userActionsModalUserId);
    setAgeCredentialEditor({
      userId: entry.id,
      ageCredential: Number(entry.age_credential || 18),
    });
    setUserActionsModalUserId(entry.id);
  }

  function closeUserActionsModal() {
    if (userActionsModalUserId != null) {
      clearUserEditors(userActionsModalUserId);
    }
    setSelfDeleteState({
      open: false,
      password: "",
      armed: false,
      pending: false,
      error: "",
    });
    setUserActionsModalUserId(null);
  }

  function openTerminateWorkerModal(worker) {
    setPlaybackWorkersFeedback(null);
    setTerminateWorkerModal({
      workerId: worker.worker_id,
      sessionId: worker.session_id,
      title: worker.title || "this playback worker",
    });
  }

  function openDismissPlaybackStatusModal(worker) {
    setDismissPlaybackStatusModal({
      dismissKey: buildPlaybackStatusDismissKey(worker),
      title: worker.title || "this playback status",
    });
  }

  function closeDismissPlaybackStatusModal() {
    setDismissPlaybackStatusModal(null);
  }

  function handleDismissPlaybackStatusConfirm() {
    if (!dismissPlaybackStatusModal?.dismissKey) {
      return;
    }
    setDismissedPlaybackStatusKeys((current) => ({
      ...current,
      [dismissPlaybackStatusModal.dismissKey]: true,
    }));
    setDismissPlaybackStatusModal(null);
  }

  function closeTerminateWorkerModal() {
    if (terminateWorkerPending) {
      return;
    }
    setTerminateWorkerModal(null);
  }

  function toggleWorkerGroupCollapsed(userId) {
    setCollapsedWorkerUserIds((current) => ({
      ...current,
      [userId]: !current[userId],
    }));
  }

  function openDiagnosticIdModal(label, value) {
    if (typeof value !== "string" || !value.trim()) {
      return;
    }
    setDiagnosticIdModal({
      label,
      value: value.trim(),
    });
  }

  function closeDiagnosticIdModal() {
    setDiagnosticIdModal(null);
  }

  async function handleTerminateWorkerConfirm() {
    if (!terminateWorkerModal?.workerId || terminateWorkerPending) {
      return;
    }
    setTerminateWorkerPending(terminateWorkerModal.workerId);
    setPlaybackWorkersFeedback(null);
    try {
      await apiRequest(`/api/admin/playback-workers/${encodeURIComponent(terminateWorkerModal.workerId)}/terminate`, {
        method: "POST",
      });
      const terminatedTitle = terminateWorkerModal.title;
      setTerminateWorkerModal(null);
      await loadPlaybackWorkers({ silent: true });
      setPlaybackWorkersFeedback({
        tone: "success",
        text: `${terminatedTitle} terminated.`,
      });
    } catch (requestError) {
      setPlaybackWorkersFeedback({
        tone: "error",
        text: requestError.message || `Failed to terminate ${terminateWorkerModal.title}.`,
      });
    } finally {
      setTerminateWorkerPending("");
    }
  }

  async function handleRefreshStatus() {
    if (statusRefreshPending) {
      return;
    }
    setStatusRefreshPending(true);
    setBanner(null);
    try {
      if (desktopControlCenter) {
        await loadDesktopAdminSection(desktopAdminTab, { force: true });
      } else {
        await loadAdminData({ silent: true });
      }
      const shouldRefreshWorkers = user?.role === "admin" && (
        desktopControlCenter ? desktopAdminTab === "users-invites" : activeSection === "panel"
      );
      if (shouldRefreshWorkers) {
        await loadPlaybackWorkers({ silent: true });
      }
      if (statusPayload) {
        setBanner({ tone: "success", text: "Admin status refreshed." });
      }
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to refresh admin status",
      });
    } finally {
      setStatusRefreshPending(false);
    }
  }

  function handleCheckpointSelection(checkpointId) {
    setSelectedCheckpointId(checkpointId);
    setInspectPayload(null);
    setRestorePlanPayload(null);
    setShowAllRecoveryWarnings(false);
    setBackupPassphraseModal({ mode: null, checkpointId: "", passphrase: "", error: "" });
  }

  function openCreateBackupModal() {
    setBackupCreateModal({
      open: true,
      passphrase: "",
      confirmPassphrase: "",
      error: "",
    });
  }

  function closeCreateBackupModal() {
    if (createBackupPending) {
      return;
    }
    setBackupCreateModal({
      open: false,
      passphrase: "",
      confirmPassphrase: "",
      error: "",
    });
  }

  async function handleCreateBackupNow(passphrase) {
    if (createBackupPending) {
      return;
    }
    setCreateBackupPending(true);
    setRecoveryFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/backups", {
        method: "POST",
        data: { passphrase },
      });
      const checkpoint = payload.checkpoint || {};
      const checkpointId = checkpoint.checkpoint_id || "";
      setInspectPayload(null);
      setRestorePlanPayload(null);
      setBackupCreateModal({
        open: false,
        passphrase: "",
        confirmPassphrase: "",
        error: "",
      });
      await loadRecoveryData({
        silent: true,
        preserveFeedback: true,
        preferredCheckpointId: checkpointId,
      });
      setRecoveryFeedback({
        tone: "success",
        text: [
          payload.message || "Backup checkpoint created.",
          checkpointId ? `Checkpoint: ${checkpointId}.` : "",
          checkpoint.path ? `Path: ${checkpoint.path}.` : "",
          checkpoint.created_at_utc ? `Created: ${checkpoint.created_at_utc}.` : "",
          payload.warning || "",
        ].filter(Boolean).join(" "),
      });
    } catch (requestError) {
      setRecoveryFeedback({
        tone: "error",
        text: requestError.message || "Failed to create backup checkpoint.",
      });
    } finally {
      setCreateBackupPending(false);
    }
  }

  function handleConfirmCreateBackup() {
    const passphrase = backupCreateModal.passphrase;
    if (passphrase.length < 12) {
      setBackupCreateModal((current) => ({
        ...current,
        error: "Use at least 12 characters.",
      }));
      return;
    }
    if (passphrase !== backupCreateModal.confirmPassphrase) {
      setBackupCreateModal((current) => ({
        ...current,
        error: "Passphrases do not match.",
      }));
      return;
    }
    handleCreateBackupNow(passphrase);
  }

  function selectedCheckpointRequiresPassphrase() {
    return selectedCheckpoint?.backup_encrypted && selectedCheckpoint?.backup_key_source === "passphrase";
  }

  function openBackupPassphraseModal(mode) {
    setBackupPassphraseModal({
      mode,
      checkpointId: selectedCheckpointId,
      passphrase: "",
      error: "",
    });
  }

  function closeBackupPassphraseModal() {
    if (inspectPending || restorePlanPending) {
      return;
    }
    setBackupPassphraseModal({ mode: null, checkpointId: "", passphrase: "", error: "" });
  }

  async function handleInspectCheckpoint(passphrase = null) {
    if (!selectedCheckpointId || inspectPending) {
      return;
    }
    setInspectPending(true);
    setRecoveryFeedback(null);
    try {
      const endpoint = `/api/admin/backups/${encodeURIComponent(selectedCheckpointId)}/inspect`;
      const payload = passphrase
        ? await apiRequest(endpoint, { method: "POST", data: { passphrase } })
        : await apiRequest(endpoint);
      setInspectPayload(payload);
      setBackupPassphraseModal({ mode: null, checkpointId: "", passphrase: "", error: "" });
    } catch (requestError) {
      if (passphrase) {
        setBackupPassphraseModal((current) => ({
          ...current,
          error: requestError.message || "Wrong backup passphrase.",
        }));
      }
      setRecoveryFeedback({
        tone: "error",
        text: requestError.message || "Failed to inspect checkpoint.",
      });
    } finally {
      setInspectPending(false);
    }
  }

  async function handleGenerateRestorePlan(passphrase = null) {
    if (!selectedCheckpointId || restorePlanPending) {
      return;
    }
    setRestorePlanPending(true);
    setRecoveryFeedback(null);
    setShowAllRecoveryWarnings(false);
    try {
      const endpoint = `/api/admin/backups/${encodeURIComponent(selectedCheckpointId)}/restore-plan`;
      const payload = passphrase
        ? await apiRequest(endpoint, { method: "POST", data: { passphrase } })
        : await apiRequest(endpoint);
      setRestorePlanPayload(payload);
      setBackupPassphraseModal({ mode: null, checkpointId: "", passphrase: "", error: "" });
    } catch (requestError) {
      if (passphrase) {
        setBackupPassphraseModal((current) => ({
          ...current,
          error: requestError.message || "Wrong backup passphrase.",
        }));
      }
      setRecoveryFeedback({
        tone: "error",
        text: requestError.message || "Failed to build recovery preview.",
      });
    } finally {
      setRestorePlanPending(false);
    }
  }

  function handleProtectedInspectClick() {
    if (selectedCheckpointRequiresPassphrase()) {
      openBackupPassphraseModal("inspect");
      return;
    }
    handleInspectCheckpoint();
  }

  function handleProtectedRestorePlanClick() {
    if (selectedCheckpointRequiresPassphrase()) {
      openBackupPassphraseModal("restore-plan");
      return;
    }
    handleGenerateRestorePlan();
  }

  function handleSubmitBackupPassphrase() {
    const passphrase = backupPassphraseModal.passphrase;
    if (!passphrase) {
      setBackupPassphraseModal((current) => ({
        ...current,
        error: "Enter the backup passphrase.",
      }));
      return;
    }
    if (backupPassphraseModal.mode === "inspect") {
      handleInspectCheckpoint(passphrase);
    } else if (backupPassphraseModal.mode === "restore-plan") {
      handleGenerateRestorePlan(passphrase);
    }
  }

  function closeCreateUserForm() {
    setCreateUserForm({ username: "", password: "", role: "standard_user", ageCredential: 18 });
    setShowAllCreateUserAges(false);
    setCreateUserExpanded(false);
  }

  function toggleCreateUserForm() {
    if (createUserExpanded) {
      closeCreateUserForm();
      return;
    }
    setCreateUserExpanded(true);
  }

  async function handleCreateUser(event) {
    event.preventDefault();
    setCreatePending(true);
    setBanner(null);
    try {
      await apiRequest("/api/admin/users", {
        method: "POST",
        data: {
          username: createUserForm.username.trim(),
          password: createUserForm.password,
          role: createUserForm.role,
          enabled: true,
          age_credential: Number(createUserForm.ageCredential || 18),
        },
      });
      closeCreateUserForm();
      setBanner({ tone: "success", text: "User created." });
      await refreshAdminUsersResource();
    } catch (requestError) {
      setCreateUserForm((current) => ({ ...current, password: "" }));
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to create user",
      });
    } finally {
      setCreatePending(false);
    }
  }

  async function handleUpdateUser(targetUser, updates, successText) {
    setUserActionPending(targetUser.id);
    setFeedbackForUser(targetUser.id, "success", "");
    try {
      const payload = await apiRequest(`/api/admin/users/${targetUser.id}`, {
        method: "PATCH",
        data: updates,
      });
      clearUserEditors(targetUser.id);
      setFeedbackForUser(targetUser.id, "success", successText || `Updated ${payload.username}.`);
      await refreshAdminUsersResource();
    } catch (requestError) {
      if (updates && Object.prototype.hasOwnProperty.call(updates, "current_admin_password")) {
        setRoleConfirm((current) => (current.userId === targetUser.id
          ? { ...current, currentAdminPassword: "" }
          : current));
      }
      setFeedbackForUser(
        targetUser.id,
        "error",
        requestError.message || `Failed to update ${targetUser.username}`,
      );
    } finally {
      setUserActionPending(null);
    }
  }

  async function handleAssistantAccessToggle(entry) {
    setUserActionPending(entry.id);
    setFeedbackForUser(entry.id, "success", "");
    try {
      await apiRequest(`/api/admin/users/${entry.id}/assistant-access`, {
        method: "PATCH",
        data: {
          assistant_beta_enabled: !entry.assistant_beta_enabled,
        },
      });
      setFeedbackForUser(
        entry.id,
        "success",
        `${entry.username} ${entry.assistant_beta_enabled ? "lost" : "gained"} Assistant access.`,
      );
      await refreshAdminUsersResource();
    } catch (requestError) {
      setPasswordEditor((current) => (current.userId === entry.id
        ? { ...current, newPassword: "", currentAdminPassword: "" }
        : current));
      setFeedbackForUser(
        entry.id,
        "error",
        requestError.message || `Failed to update Assistant access for ${entry.username}`,
      );
    } finally {
      setUserActionPending(null);
    }
  }

  async function handleSubmitRoleChange(entry) {
    if (!roleConfirm.currentAdminPassword.trim()) {
      setFeedbackForUser(entry.id, "error", "Enter your current admin password to change roles.");
      return;
    }
    const nextRoleLabel = roleConfirm.nextRole === "admin" ? "admin" : "standard user";
    await handleUpdateUser(
      entry,
      {
        role: roleConfirm.nextRole,
        current_admin_password: roleConfirm.currentAdminPassword,
      },
      `${entry.username} is now ${nextRoleLabel}.`,
    );
  }

  async function handleSubmitPassword(entry) {
    if (passwordEditor.newPassword.trim().length < 8) {
      setFeedbackForUser(entry.id, "error", "New password must be at least 8 characters.");
      return;
    }
    if (!passwordEditor.currentAdminPassword.trim()) {
      setFeedbackForUser(entry.id, "error", "Enter your current admin password to update passwords.");
      return;
    }
    setUserActionPending(entry.id);
    setFeedbackForUser(entry.id, "success", "");
    try {
      const payload = await apiRequest(`/api/admin/users/${entry.id}/password`, {
        method: "POST",
        data: {
          new_password: passwordEditor.newPassword,
          current_admin_password: passwordEditor.currentAdminPassword,
        },
      });
      clearUserEditors(entry.id);
      setFeedbackForUser(entry.id, "success", payload.message || `Password updated for ${entry.username}.`);
    } catch (requestError) {
      setFeedbackForUser(
        entry.id,
        "error",
        requestError.message || `Failed to update password for ${entry.username}`,
      );
    } finally {
      setUserActionPending(null);
    }
  }

  function armDeleteUser(entry) {
    setDeleteUserState({
      userId: entry.id,
      username: entry.username,
      currentAdminPassword: "",
      fieldToken: createDestructiveGateToken(),
      pending: false,
      error: "",
    });
  }

  async function handleConfirmDeleteUser(entry) {
    if (deleteUserState.pending) {
      return;
    }
    if (!deleteUserState.currentAdminPassword.trim()) {
      setDeleteUserState((current) => ({
        ...current,
        error: "Enter your current admin password before deleting this user.",
      }));
      return;
    }
    setDeleteUserState((current) => ({ ...current, pending: true, error: "" }));
    try {
      await apiRequest(`/api/admin/users/${entry.id}`, {
        method: "DELETE",
        data: {
          confirm: true,
          current_admin_password: deleteUserState.currentAdminPassword,
        },
      });
      setUserActionsModalUserId(null);
      setDeleteUserState({
        userId: null,
        username: "",
        currentAdminPassword: "",
        fieldToken: createDestructiveGateToken(),
        pending: false,
        error: "",
      });
      setBanner({ tone: "success", text: `Deleted user ${entry.username}.` });
      await refreshAdminUsersResource();
    } catch (requestError) {
      setDeleteUserState((current) => ({
        ...current,
        currentAdminPassword: "",
        fieldToken: createDestructiveGateToken(),
        pending: false,
        error: requestError.message || `Failed to delete ${entry.username}`,
      }));
    }
  }

  async function handleGenerateInviteCode(event) {
    event?.preventDefault?.();
    setInvitePending(true);
    setBanner(null);
    try {
      const payload = await apiRequest("/api/admin/invite-codes", {
        method: "POST",
        data: { assigned_age: Number(inviteAssignedAge || 18) },
      });
      setInviteCodes((current) => [
        payload,
        ...current.filter((inviteCode) => inviteCode.id !== payload.id),
      ]);
      setRevealedInviteIds((current) => ({ ...current, [payload.id]: false }));
      setInviteCodesExpanded(true);
      setInviteAgeModalOpen(false);
      setBanner({ tone: "success", text: "Invite code generated." });
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to generate invite code",
      });
    } finally {
      setInvitePending(false);
    }
  }

  async function handleSaveUserAgeCredential(entry) {
    await handleUpdateUser(
      entry,
      { age_credential: Number(ageCredentialEditor.ageCredential || 18) },
      `${entry.username} age credential is ${formatAgeCredential(ageCredentialEditor.ageCredential)}.`,
    );
  }

  function openInviteCodeDeleteModal(inviteCode) {
    setAdminConfirmModal({
      type: "invite-delete",
      payload: inviteCode,
      pending: false,
      error: "",
    });
  }

  function openPasswordHelpDismissModal(requestEntry) {
    setAdminConfirmModal({
      type: "password-help-dismiss",
      payload: requestEntry,
      pending: false,
      error: "",
    });
  }

  function togglePasswordHelpDetails(requestId) {
    setExpandedPasswordHelpRequestId((current) => (current === requestId ? null : requestId));
  }

  function closeAdminConfirmModal() {
    if (adminConfirmModal.pending) {
      return;
    }
    setAdminConfirmModal({
      type: null,
      payload: null,
      pending: false,
      error: "",
    });
  }

  async function confirmInviteCodeDelete(inviteCode) {
    try {
      await apiRequest(`/api/admin/invite-codes/${inviteCode.id}/revoke`, { method: "POST" });
      setInviteCodes((current) => current.filter((entry) => entry.id !== inviteCode.id));
      setRevealedInviteIds((current) => {
        const next = { ...current };
        delete next[inviteCode.id];
        return next;
      });
      setAdminConfirmModal({
        type: null,
        payload: null,
        pending: false,
        error: "",
      });
      setBanner({ tone: "success", text: "Invite code deleted and revoked." });
    } catch (requestError) {
      setAdminConfirmModal((current) => ({
        ...current,
        pending: false,
        error: requestError.message || "Failed to delete invite code",
      }));
    }
  }

  async function confirmPasswordHelpDismiss(requestEntry) {
    setPasswordHelpPendingId(requestEntry.id);
    try {
      await apiRequest(`/api/admin/password-help-requests/${requestEntry.id}/dismiss`, {
        method: "POST",
        data: { confirm: true },
      });
      await loadPasswordHelpRequests();
      setAdminConfirmModal({
        type: null,
        payload: null,
        pending: false,
        error: "",
      });
      setBanner({ tone: "success", text: "Password help request dismissed." });
    } catch (requestError) {
      setAdminConfirmModal((current) => ({
        ...current,
        pending: false,
        error: requestError.message || "Failed to dismiss password help request",
      }));
    } finally {
      setPasswordHelpPendingId(null);
    }
  }

  async function handleConfirmAdminModal() {
    if (!adminConfirmModal.type || !adminConfirmModal.payload) {
      return;
    }
    setAdminConfirmModal((current) => ({ ...current, pending: true, error: "" }));
    if (adminConfirmModal.type === "invite-delete") {
      await confirmInviteCodeDelete(adminConfirmModal.payload);
      return;
    }
    if (adminConfirmModal.type === "password-help-dismiss") {
      await confirmPasswordHelpDismiss(adminConfirmModal.payload);
    }
  }

  function toggleInviteCodesExpanded() {
    setInviteCodesExpanded((current) => !current);
  }

  async function loadDownloadAccessForUser(entry) {
    if (!entry) {
      return;
    }
    setDownloadAccessState((current) => ({
      ...current,
      userId: entry.id,
      loading: true,
      error: "",
      feedback: "",
      searchQuery: "",
      searchResults: [],
    }));
    try {
      const payload = await apiRequest(`/api/admin/users/${entry.id}/download-access`);
      setDownloadAccessState((current) => ({
        ...current,
        userId: entry.id,
        loading: false,
        accessMode: payload.access_mode || "none",
        selectedItems: payload.selected_items || [],
      }));
    } catch (requestError) {
      setDownloadAccessState((current) => ({
        ...current,
        loading: false,
        error: requestError.message || "Failed to load download access",
      }));
    }
  }

  function updateDownloadAccessMode(accessMode) {
    setDownloadAccessState((current) => ({
      ...current,
      accessMode,
      feedback: "",
      error: "",
    }));
  }

  function addDownloadAccessMovie(item) {
    setDownloadAccessState((current) => {
      if (current.selectedItems.some((selected) => selected.id === item.id)) {
        return current;
      }
      return {
        ...current,
        selectedItems: [...current.selectedItems, item],
        searchQuery: "",
        searchResults: [],
      };
    });
  }

  function removeDownloadAccessMovie(itemId) {
    setDownloadAccessState((current) => ({
      ...current,
      selectedItems: current.selectedItems.filter((item) => item.id !== itemId),
    }));
  }

  async function saveDownloadAccess(entry) {
    setDownloadAccessState((current) => ({ ...current, saving: true, error: "", feedback: "" }));
    try {
      const payload = await apiRequest(`/api/admin/users/${entry.id}/download-access`, {
        method: "PUT",
        data: {
          access_mode: downloadAccessState.accessMode,
          media_item_ids: downloadAccessState.selectedItems.map((item) => item.id),
        },
      });
      setDownloadAccessState((current) => ({
        ...current,
        saving: false,
        accessMode: payload.access_mode || "none",
        selectedItems: payload.selected_items || [],
        feedback: "Download access saved.",
      }));
    } catch (requestError) {
      setDownloadAccessState((current) => ({
        ...current,
        saving: false,
        error: requestError.message || "Failed to save download access",
      }));
    }
  }

  async function handleRevokeSession(session) {
    setSessionActionPending(session.id);
    setBanner(null);
    try {
      await apiRequest(`/api/admin/sessions/${session.id}/revoke`, { method: "POST" });
      const authenticatedUser = await refreshAuth({ notifyOnFailure: true });
      if (!authenticatedUser) {
        navigate("/login", { replace: true });
        return;
      }
      const sessions = await apiRequest("/api/admin/sessions");
      setSessionsPayload(sessions.sessions || []);
      setBanner({ tone: "success", text: `Session ${session.id} revoked.` });
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to revoke session",
      });
    } finally {
      setSessionActionPending(null);
    }
  }

  async function handleSelfDeletePrecheck(event) {
    event.preventDefault();
    if (!hasAnotherEnabledAdmin) {
      setSelfDeleteState((current) => ({
        ...current,
        error: "Create another enabled admin before deleting your own account.",
      }));
      return;
    }
    if (!selfDeleteState.password.trim()) {
      setSelfDeleteState((current) => ({
        ...current,
        error: "Enter your current admin password first.",
      }));
      return;
    }
    setSelfDeleteState((current) => ({ ...current, pending: true, error: "" }));
    try {
      await apiRequest("/api/admin/self-delete", {
        method: "POST",
        data: {
          current_admin_password: selfDeleteState.password,
          confirm: false,
        },
      });
    } catch (requestError) {
      if (requestError.message === SELF_DELETE_CONFIRM_DETAIL) {
        setSelfDeleteState((current) => ({
          ...current,
          pending: false,
          armed: true,
          error: "",
        }));
        return;
      }
      setSelfDeleteState((current) => ({
        ...current,
        password: "",
        pending: false,
        error: requestError.message || "Unable to verify your password.",
      }));
      return;
    }
    setSelfDeleteState((current) => ({
      ...current,
      pending: false,
      armed: true,
      error: "",
    }));
  }

  async function handleSelfDeleteConfirm() {
    setSelfDeleteState((current) => ({ ...current, pending: true, error: "" }));
    try {
      await apiRequest("/api/admin/self-delete", {
        method: "POST",
        data: {
          current_admin_password: selfDeleteState.password,
          confirm: true,
        },
      });
      await refreshAuth();
      navigate("/login", { replace: true });
    } catch (requestError) {
      setSelfDeleteState((current) => ({
        ...current,
        password: "",
        armed: false,
        pending: false,
        error: requestError.message || "Failed to delete your admin account.",
      }));
    }
  }

  const visibleSessions = showAllSessions ? sessionsPayload : sessionsPayload.slice(0, 8);
  const visibleAuditEvents = showAllAudit ? auditPayload : auditPayload.slice(0, 10);
  const visibleUsers = showAllUsers ? usersPayload : usersPayload.slice(0, 6);
  const liveInviteCount = inviteCodes.filter((entry) => {
    if (entry.used_at) {
      return false;
    }
    const expiresAt = Date.parse(entry.expires_at);
    return !Number.isFinite(expiresAt) || expiresAt > Date.now();
  }).length;
  const recentBackupWarnings = useMemo(
    () =>
      auditPayload.filter((event) => event?.details?.auto_backup_status === "failed").slice(0, 6),
    [auditPayload],
  );
  const selectedCheckpoint = useMemo(
    () => backupsPayload.find((entry) => entry.checkpoint_id === selectedCheckpointId) || null,
    [backupsPayload, selectedCheckpointId],
  );
  const visibleRecoveryCheckpoints = showAllRecoveryCheckpoints
    ? backupsPayload
    : backupsPayload.slice(0, RECOVERY_CHECKPOINT_LIMIT);
  const recoveryCheckpointSummary = backupsPayload.length > 0
    ? (
      showAllRecoveryCheckpoints || backupsPayload.length <= RECOVERY_CHECKPOINT_LIMIT
        ? `Showing all ${backupsPayload.length} checkpoints.`
        : `Showing ${visibleRecoveryCheckpoints.length} of ${backupsPayload.length} checkpoints.`
    )
    : "";
  const restorePlanWarnings = Array.isArray(restorePlanPayload?.warnings) ? restorePlanPayload.warnings : [];
  const visibleRestorePlanWarnings = showAllRecoveryWarnings
    ? restorePlanWarnings
    : restorePlanWarnings.slice(0, RECOVERY_WARNING_LIMIT);
  const playbackWorkerSummary = useMemo(
    () => buildPlaybackWorkerSummaryBubbles(playbackWorkersPayload),
    [playbackWorkersPayload],
  );

  useEffect(() => {
    if (userActionsModalUserId == null || selectedUserActionsEntry) {
      return;
    }
    setUserActionsModalUserId(null);
  }, [selectedUserActionsEntry, userActionsModalUserId]);

  useEffect(() => {
    if ((!selectedUserActionsEntry && !terminateWorkerModal && !dismissPlaybackStatusModal && !diagnosticIdModal) || typeof document === "undefined") {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    function handleKeyDown(event) {
      if (event.key === "Escape") {
        if (diagnosticIdModal) {
          closeDiagnosticIdModal();
          return;
        }
        if (terminateWorkerModal) {
          closeTerminateWorkerModal();
          return;
        }
        if (dismissPlaybackStatusModal) {
          closeDismissPlaybackStatusModal();
          return;
        }
        closeUserActionsModal();
      }
    }
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [selectedUserActionsEntry, terminateWorkerModal, terminateWorkerPending, dismissPlaybackStatusModal, diagnosticIdModal]);

  if (loading && !statusPayload) {
    return <LoadingView label="Loading admin tools..." />;
  }

  function clearSectionCollapseTimer() {
    if (typeof window === "undefined" || !sectionCollapseTimerRef.current) {
      return;
    }
    window.clearTimeout(sectionCollapseTimerRef.current);
    sectionCollapseTimerRef.current = 0;
  }

  function scheduleSectionCollapse() {
    if (typeof window === "undefined") {
      return;
    }
    clearSectionCollapseTimer();
    sectionCollapseTimerRef.current = window.setTimeout(() => {
      setExpandedSection(null);
      sectionCollapseTimerRef.current = 0;
    }, ADMIN_SECTION_AUTO_COLLAPSE_MS);
  }

  function handleSectionClick(sectionKey) {
    writePersistedPanelState(ADMIN_ACTIVE_SECTION_STORAGE_KEY, sectionKey, ADMIN_SECTION_KEYS);
    setActiveSection(sectionKey);
    setExpandedSection((current) => {
      if (current === sectionKey) {
        clearSectionCollapseTimer();
        return null;
      }
      scheduleSectionCollapse();
      return sectionKey;
    });
  }

  const usersCard = (
    <section className="settings-card settings-card--wide">
      <div className="settings-inline-header">
        <div>
          <h2>Users</h2>
          <p className="page-subnote">Role changes and password updates require your current admin password.</p>
        </div>
      </div>
      {playbackWorkerSummary.length > 0 ? (
        <div className="admin-workers-summary" aria-label="Playback worker summary">
          {playbackWorkerSummary.map((entry) => (
            <span className="admin-workers-summary__pill" key={entry}>{entry}</span>
          ))}
        </div>
      ) : null}
      {playbackWorkersWarning ? (
        <p className="page-subnote admin-workers-summary__warning">
          Playback workers warning: {playbackWorkersWarning}
        </p>
      ) : null}
      {playbackWorkersFeedback?.text ? (
        <p
          className={playbackWorkersFeedback.tone === "error" ? "action-feedback action-feedback--error" : "action-feedback"}
          role={playbackWorkersFeedback.tone === "error" ? "alert" : "status"}
        >
          {playbackWorkersFeedback.text}
        </p>
      ) : null}
      <div className="admin-list">
        {visibleUsers.map((entry) => {
          const isSelf = entry.id === user?.id;
          const isActionsModalOpen = userActionsModalUserId === entry.id;
          const workerGroup = playbackWorkersByUserId.get(entry.id) || null;
          const isWorkerGroupCollapsed = collapsedWorkerUserIds[entry.id] === true;
          return (
            <div className="admin-list__row admin-user-row" key={entry.id}>
              <button
                aria-expanded={isActionsModalOpen}
                aria-haspopup="dialog"
                aria-label={`Open user actions for ${entry.username}`}
                className="user-avatar-button"
                onClick={() => openUserActionsModal(entry)}
                type="button"
              >
                <span aria-hidden="true" className="user-avatar-button__initials">
                  {getUserAvatarInitials(entry.username)}
                </span>
              </button>

              <div className="admin-user-row__summary">
                <div className="admin-user-heading">
                  <strong>{entry.username}</strong>
                  {entry.role === "admin" ? <AdminCrownIcon /> : null}
                  <UserStatusIndicator color={entry.status_color} label={entry.status_label} />
                </div>
                <p className="page-subnote">
                  {entry.active_sessions} live session{entry.active_sessions === 1 ? "" : "s"} · last login {formatDate(entry.last_login_at)}
                </p>
                <p className="page-subnote">Age credential {entry.age_credential_display || formatAgeCredential(entry.age_credential)}</p>
                {entry.last_seen_at ? <p className="page-subnote">Last heartbeat {formatDate(entry.last_seen_at)}{entry.last_activity_at ? ` · last activity ${formatDate(entry.last_activity_at)}` : ""}</p> : null}
                {isSelf ? (
                  <p className="page-subnote">Your own admin account cannot be disabled. Use Delete account inside Account actions if needed.</p>
                ) : null}
                {!isActionsModalOpen ? <InlineFeedback feedback={userFeedback[entry.id]} /> : null}
              </div>

              <div className="admin-user-row__priority">
                {!isSelf ? (
                  <button
                    className="ghost-button"
                    disabled={userActionPending === entry.id}
                    onClick={() =>
                      handleUpdateUser(
                        entry,
                        { enabled: !entry.enabled },
                        `${entry.username} ${entry.enabled ? "disabled" : "enabled"}.`,
                      )
                    }
                    type="button"
                  >
                    {entry.enabled ? "Disable" : "Enable"}
                  </button>
                ) : null}
              </div>

              {workerGroup && workerGroup.totalPlaybackItems > 0 ? (
                <div className="admin-user-row__workers">
                  <div className="admin-user-workers">
                    <button
                      aria-expanded={!isWorkerGroupCollapsed}
                      className="admin-user-workers__header admin-user-workers__header-button"
                      onClick={() => toggleWorkerGroupCollapsed(entry.id)}
                      type="button"
                    >
                      <div className="admin-user-workers__copy">
                        <strong>Playback status</strong>
                        <p className="page-subnote">
                          Route2 workers and native playback sessions for this user.
                        </p>
                      </div>
                      {workerGroup.hasRunningWorkers ? (
                        <div className="admin-user-workers__gauges">
                          <PlaybackResourceGauge
                            gaugePercent={workerGroup.cpuGaugePercent}
                            label="CPU"
                            tone="cpu"
                            valueLabel={formatCpuCoresUsage(workerGroup.cpuCoresUsed, workerGroup.allocatedCpuCores)}
                          />
                          <PlaybackResourceGauge
                            gaugePercent={workerGroup.memoryGaugePercent}
                            label="RAM"
                            tone="memory"
                            valueLabel={formatMemoryGaugeValue(workerGroup.memoryBytes)}
                          />
                        </div>
                      ) : null}
                    </button>

                    <div className="admin-user-workers__stats">
                      <span className="admin-workers-summary__pill">{workerGroup.allocatedCpuCores ?? workerGroup.allocated_budget_cores ?? 0} allocated cores</span>
                      <span className="admin-workers-summary__pill">{workerGroup.running_workers} running</span>
                      <span className="admin-workers-summary__pill">{workerGroup.queued_workers} queued</span>
                      <span className="admin-workers-summary__pill">{workerGroup.totalWorkers} total</span>
                      {workerGroup.totalNativePlaybacks > 0 ? (
                        <span className="admin-workers-summary__pill">{workerGroup.totalNativePlaybacks} native</span>
                      ) : null}
                    </div>

                    {isWorkerGroupCollapsed ? (
                      <p className="page-subnote admin-user-workers__collapsed-note">
                        Playback cards hidden for this user.
                      </p>
                    ) : (
                      <div className="admin-user-workers__list">
                        {workerGroup.items
                          .filter((worker) => !dismissedPlaybackStatusKeys[buildPlaybackStatusDismissKey(worker)])
                          .map((worker) => {
                          const preparedRanges = formatPreparedRanges(worker.prepared_ranges);
                          const sessionDiagnosticId = shortenDiagnosticId(worker.session_id);
                          const workerDiagnosticId = shortenDiagnosticId(worker.worker_id);
                          const epochDiagnosticId = shortenDiagnosticId(worker.epoch_id);
                          const hasTargetPosition = Number.isFinite(worker.target_position_seconds);
                          const displayStatus = buildWorkerDisplayStatus(worker);
                          const canTerminateWorker = canTerminatePlaybackWorker(worker.state);
                          const canDismissWorkerStatus = canDismissPlaybackStatus(worker);
                          return (
                            <div className="admin-worker-card" key={worker.worker_id}>
                              <div className="admin-worker-card__header">
                                <div className="admin-worker-card__copy">
                                  <strong>{worker.title || "Untitled media item"}</strong>
                                  <p className="page-subnote">
                                    {buildWorkerPlaybackMetadataLabel(worker)}
                                  </p>
                                </div>
                                <div className="admin-worker-card__actions">
                                  <span
                                    className={[
                                      "admin-worker-state",
                                      workerStatusToneClass(displayStatus),
                                    ].join(" ")}
                                    onClick={(event) => event.stopPropagation()}
                                    title={displayStatus.reason || undefined}
                                  >
                                    {displayStatus.label}
                                  </span>
                                  {canTerminateWorker ? (
                                    <button
                                      className="ghost-button admin-worker-card__terminate"
                                      disabled={terminateWorkerPending === worker.worker_id}
                                      onClick={() => openTerminateWorkerModal(worker)}
                                      type="button"
                                    >
                                      Terminate
                                    </button>
                                  ) : null}
                                  {canDismissWorkerStatus ? (
                                    <button
                                      className="ghost-button admin-worker-card__terminate"
                                      onClick={() => openDismissPlaybackStatusModal(worker)}
                                      type="button"
                                    >
                                      Dismiss
                                    </button>
                                  ) : null}
                                </div>
                              </div>

                              <div className="admin-worker-card__meta">
                                <span>Runtime {formatWorkerRuntime(worker.runtime_seconds)}</span>
                                {worker.pid ? <span>PID {worker.pid}</span> : null}
                                {worker.assigned_threads ? <span>{worker.assigned_threads} threads</span> : null}
                                {hasTargetPosition ? <span>Target {Math.round(worker.target_position_seconds)}s</span> : null}
                                {worker.replacement_count ? <span>{worker.replacement_count} replacements</span> : null}
                                {worker.failure_count ? <span>{worker.failure_count} failures</span> : null}
                              </div>

                              {preparedRanges ? (
                                <p className="page-subnote">Prepared ranges {preparedRanges}</p>
                              ) : null}

                              {sessionDiagnosticId || workerDiagnosticId || epochDiagnosticId ? (
                                <div className="admin-worker-card__diagnostics">
                                  {sessionDiagnosticId ? (
                                    <button
                                      className="admin-diagnostic-id-button"
                                      onClick={() => openDiagnosticIdModal("session", worker.session_id)}
                                      type="button"
                                    >
                                      session {sessionDiagnosticId}
                                    </button>
                                  ) : null}
                                  {workerDiagnosticId ? (
                                    <button
                                      className="admin-diagnostic-id-button"
                                      onClick={() => openDiagnosticIdModal("worker", worker.worker_id)}
                                      type="button"
                                    >
                                      worker {workerDiagnosticId}
                                    </button>
                                  ) : null}
                                  {epochDiagnosticId ? (
                                    <button
                                      className="admin-diagnostic-id-button"
                                      onClick={() => openDiagnosticIdModal("epoch", worker.epoch_id)}
                                      type="button"
                                    >
                                      epoch {epochDiagnosticId}
                                    </button>
                                  ) : null}
                                </div>
                              ) : null}

                              {worker.failure_reason || worker.non_retryable_error ? (
                                <p className="action-feedback action-feedback--error">{worker.failure_reason || worker.non_retryable_error}</p>
                              ) : null}

                              {shouldShowWorkerCleanupNotice(worker) ? (
                                <p className="page-subnote">Backend cleanup is taking longer than expected.</p>
                              ) : null}
                            </div>
                          );
		        })}
        {usersPayload.length > 6 ? (
          <button
            className="ghost-button ghost-button--inline control-center-user-expander"
            onClick={() => setShowAllUsers((current) => !current)}
            type="button"
          >
            {showAllUsers ? "Show fewer" : `Show all ${usersPayload.length}`}
          </button>
        ) : null}
                        {workerGroup.nativeItems
                          .filter((nativePlayback) => !dismissedPlaybackStatusKeys[buildPlaybackStatusDismissKey(nativePlayback)])
                          .map((nativePlayback) => {
                          const displayStatus = buildWorkerDisplayStatus(nativePlayback);
                          const sessionDiagnosticId = shortenDiagnosticId(nativePlayback.session_id);
                          const positionSeconds = Number(nativePlayback.last_position_seconds);
                          const durationSeconds = Number(nativePlayback.last_duration_seconds);
                          const hasPosition = Number.isFinite(positionSeconds) && positionSeconds >= 0;
                          const hasDuration = Number.isFinite(durationSeconds) && durationSeconds >= 0;
                          const canDismissNativeStatus = canDismissPlaybackStatus(nativePlayback);
                          return (
                            <div className="admin-worker-card admin-native-playback-card" key={`native-${nativePlayback.session_id}`}>
                              <div className="admin-worker-card__header">
                                <div className="admin-worker-card__copy">
                                  <strong>{nativePlayback.title || "Untitled media item"}</strong>
                                  <p className="page-subnote">
                                    {buildWorkerPlaybackMetadataLabel(nativePlayback)}
                                  </p>
                                </div>
                                <div className="admin-worker-card__actions">
                                  <span
                                    className={[
                                      "admin-worker-state",
                                      workerStatusToneClass(displayStatus),
                                    ].join(" ")}
                                    onClick={(event) => event.stopPropagation()}
                                    title={displayStatus.reason || undefined}
                                  >
                                    {displayStatus.label}
                                  </span>
                                  {canDismissNativeStatus ? (
                                    <button
                                      className="ghost-button admin-worker-card__terminate"
                                      onClick={() => openDismissPlaybackStatusModal(nativePlayback)}
                                      type="button"
                                    >
                                      Dismiss
                                    </button>
                                  ) : null}
                                </div>
                              </div>

                              <div className="admin-worker-card__meta">
                                {nativePlayback.client_name ? <span>Client {nativePlayback.client_name}</span> : null}
                                {hasPosition ? <span>Position {formatWorkerRuntime(positionSeconds)}</span> : null}
                                {hasDuration ? <span>Duration {formatWorkerRuntime(durationSeconds)}</span> : null}
                                {nativePlayback.last_stream_activity_at ? <span>Last stream {formatDate(nativePlayback.last_stream_activity_at)}</span> : null}
                                {nativePlayback.expires_at ? <span>Expires {formatDate(nativePlayback.expires_at)}</span> : null}
                                <span>{nativePlayback.auth_session_coupled ? "Auth coupled" : "Auth decoupled"}</span>
                              </div>

                              {sessionDiagnosticId ? (
                                <div className="admin-worker-card__diagnostics">
                                  <button
                                    className="admin-diagnostic-id-button"
                                    onClick={() => openDiagnosticIdModal("native session", nativePlayback.session_id)}
                                    type="button"
                                  >
                                    session {sessionDiagnosticId}
                                  </button>
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              ) : null}
            </div>
          );
	        })}
        <div
          className={[
            "admin-list__row admin-user-row admin-create-user-row",
            createUserExpanded ? "admin-create-user-row--expanded" : "",
          ].filter(Boolean).join(" ")}
        >
          <button
            aria-expanded={createUserExpanded}
            aria-label="Create user"
            className="user-avatar-button user-avatar-button--create"
            onClick={toggleCreateUserForm}
            type="button"
          >
            <span aria-hidden="true" className="user-avatar-button__initials">+</span>
          </button>
          <div className="admin-user-row__summary">
            <button
              className="admin-create-user-row__button"
              onClick={toggleCreateUserForm}
              type="button"
            >
              Create user
            </button>
            {createUserExpanded ? (
              <form className="admin-form admin-create-user-inline-form" onSubmit={handleCreateUser}>
                <label>
                  Username
                  <input
                    autoComplete="off"
                    name="admin-create-user-username"
                    onChange={(event) => setCreateUserForm((current) => ({ ...current, username: event.target.value }))}
                    required
                    type="text"
                    value={createUserForm.username}
                  />
                </label>
                <label>
                  Password
                  <NonLoginSecretInput
                    autoComplete="new-password"
                    onChange={(event) => setCreateUserForm((current) => ({ ...current, password: event.target.value }))}
                    purpose="admin-create-user-secret"
                    required
                    value={createUserForm.password}
                  />
                </label>
                <label>
                  Role
                  <select
                    className="admin-select"
                    onChange={(event) => setCreateUserForm((current) => ({ ...current, role: event.target.value }))}
                    value={createUserForm.role}
                  >
                    <option value="standard_user">Standard user</option>
                    <option value="admin">Admin</option>
                  </select>
                </label>
                <fieldset className="control-center-age-choices">
                  <legend>Age credential</legend>
                  <div className="control-center-age-choices__quick">
                    {[18, 16, 13].map((age) => (
                      <button
                        aria-pressed={createUserForm.ageCredential === age}
                        className="ghost-button"
                        key={age}
                        onClick={() => setCreateUserForm((current) => ({ ...current, ageCredential: age }))}
                        type="button"
                      >
                        {formatAgeCredential(age)}
                      </button>
                    ))}
                    <button
                      aria-expanded={showAllCreateUserAges}
                      className="ghost-button"
                      onClick={() => setShowAllCreateUserAges((current) => !current)}
                      type="button"
                    >
                      More ages
                    </button>
                  </div>
                  {showAllCreateUserAges ? (
                    <select
                      aria-label="All age credentials"
                      className="admin-select"
                      onChange={(event) => setCreateUserForm((current) => ({ ...current, ageCredential: Number(event.target.value) }))}
                      value={createUserForm.ageCredential}
                    >
                      {AGE_CREDENTIAL_OPTIONS.map((age) => (
                        <option key={age} value={age}>{formatAgeCredential(age)}</option>
                      ))}
                    </select>
                  ) : null}
                </fieldset>
                <div className="admin-form__actions">
                  <button className="primary-button" disabled={createPending} type="submit">
                    {createPending ? "Creating..." : "Create user"}
                  </button>
                  <button
                    className="ghost-button"
                    disabled={createPending}
                    onClick={closeCreateUserForm}
                    type="button"
                  >
                    Close
                  </button>
                </div>
              </form>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );

  const userActionsModal = selectedUserActionsEntry ? (
    <div
      aria-labelledby="admin-user-actions-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={closeUserActionsModal}
      />
      <div className="browser-resume-modal__card detail-info-modal__card admin-user-actions-modal">
        <div className="detail-info-modal__header admin-user-actions-modal__header">
          <div className="detail-info-modal__copy">
            <p className="eyebrow detail-info-modal__eyebrow">User actions</p>
            <div className="admin-user-actions-modal__title-row">
              <div className="user-avatar-button user-avatar-button--static" aria-hidden="true">
                <span className="user-avatar-button__initials">
                  {getUserAvatarInitials(selectedUserActionsEntry.username)}
                </span>
              </div>
              <div className="admin-user-actions-modal__title-copy">
                <h2 id="admin-user-actions-modal-title" className="detail-info-modal__title">
                  {selectedUserActionsEntry.username}
                  {selectedUserActionsEntry.role === "admin" ? <AdminCrownIcon /> : null}
                </h2>
                <div className="admin-user-actions-modal__subtitle">
                  <UserStatusIndicator
                    color={selectedUserActionsEntry.status_color}
                    label={selectedUserActionsEntry.status_label}
                  />
                  <span>
                    {selectedUserActionsEntry.active_sessions} live session
                    {selectedUserActionsEntry.active_sessions === 1 ? "" : "s"}
                  </span>
                </div>
              </div>
            </div>
            <p className="page-subnote">
              Last login {formatDate(selectedUserActionsEntry.last_login_at)}
              {selectedUserActionsEntry.last_seen_at ? ` · last heartbeat ${formatDate(selectedUserActionsEntry.last_seen_at)}` : ""}
              {selectedUserActionsEntry.last_activity_at ? ` · last activity ${formatDate(selectedUserActionsEntry.last_activity_at)}` : ""}
            </p>
          </div>
          <button
            className="ghost-button detail-info-modal__close"
            onClick={closeUserActionsModal}
            type="button"
          >
            Close
          </button>
        </div>
        <div className="detail-info-modal__body admin-user-actions-modal__body">
          <section className="admin-user-actions-modal__section">
            <div className="admin-user-actions-modal__section-header">
              <h3>Account actions</h3>
              <p className="page-subnote">
                Role changes and password updates still require your current admin password.
              </p>
            </div>
            <div className="admin-list__actions">
              {selectedUserActionsEntry.id !== user?.id ? (
                <button
                  className="ghost-button"
                  disabled={userActionPending === selectedUserActionsEntry.id}
                  onClick={() => {
                    setPasswordEditor({
                      userId: null,
                      username: "",
                      newPassword: "",
                      currentAdminPassword: "",
                    });
                    setRoleConfirm({
                      userId: selectedUserActionsEntry.id,
                      username: selectedUserActionsEntry.username,
                      nextRole: selectedUserActionsEntry.role === "admin" ? "standard_user" : "admin",
                      currentAdminPassword: "",
                    });
                  }}
                  type="button"
                >
                  Make {selectedUserActionsEntry.role === "admin" ? "standard" : "admin"}
                </button>
              ) : null}

              <button
                className="ghost-button"
                disabled={userActionPending === selectedUserActionsEntry.id}
                onClick={() => {
                  setRoleConfirm({
                    userId: null,
                    username: "",
                    nextRole: "standard_user",
                    currentAdminPassword: "",
                  });
                  setPasswordEditor({
                    userId: selectedUserActionsEntry.id,
                    username: selectedUserActionsEntry.username,
                    newPassword: "",
                    currentAdminPassword: "",
                  });
                }}
                type="button"
	              >
	                Update password
	              </button>
              {selectedUserActionsEntry.id !== user?.id ? (
                <button
                  className="ghost-button ghost-button--danger"
                  disabled={userActionPending === selectedUserActionsEntry.id || deleteUserState.pending}
                  onClick={() => armDeleteUser(selectedUserActionsEntry)}
                  type="button"
                >
                  Delete user
                </button>
              ) : null}
              {selectedUserActionsEntry.id === user?.id ? (
                <button
                  className="ghost-button ghost-button--danger"
                  disabled={!hasAnotherEnabledAdmin || userActionPending === selectedUserActionsEntry.id}
                  onClick={() =>
                    setSelfDeleteState({
                      open: true,
                      password: "",
                      armed: false,
                      pending: false,
                      error: "",
                    })
                  }
                  type="button"
                >
                  Delete account
                </button>
              ) : null}
	            </div>

            {deleteUserState.userId === selectedUserActionsEntry.id ? (
              <div className="admin-danger-block">
                <p className="form-error">
                  Warning: deleting {selectedUserActionsEntry.username} revokes auth, native playback, Route2 sessions, and download sessions.
                </p>
                <NonLoginSecretInput
                  className="admin-delete-confirm-secret"
                  key={deleteUserState.fieldToken}
                  onChange={(event) =>
                    setDeleteUserState((current) => ({
                      ...current,
                      currentAdminPassword: event.target.value,
                      error: "",
                    }))
                  }
                  placeholder="Required to delete this user"
                  purpose={`delete-user-reauth-${deleteUserState.fieldToken}`}
                  value={deleteUserState.currentAdminPassword}
                />
                {deleteUserState.error ? <p className="form-error">{deleteUserState.error}</p> : null}
                <div className="admin-list__actions">
                  <button
                    className="ghost-button ghost-button--danger"
                    disabled={deleteUserState.pending}
                    onClick={() => handleConfirmDeleteUser(selectedUserActionsEntry)}
                    type="button"
                  >
                    {deleteUserState.pending ? "Deleting..." : "Confirm delete user"}
                  </button>
	                  <button
	                    className="ghost-button"
	                    disabled={deleteUserState.pending}
	                    onClick={() =>
                      setDeleteUserState({
                        userId: null,
                        username: "",
                        currentAdminPassword: "",
                        fieldToken: createDestructiveGateToken(),
                        pending: false,
                        error: "",
                      })
	                    }
                    type="button"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : null}

            <div className="admin-inline-form admin-age-credential-editor">
              <label>
                Age credential
                <select
                  className="admin-select"
                  disabled={userActionPending === selectedUserActionsEntry.id}
                  onChange={(event) =>
                    setAgeCredentialEditor({
                      userId: selectedUserActionsEntry.id,
                      ageCredential: Number(event.target.value),
                    })
                  }
                  value={
                    ageCredentialEditor.userId === selectedUserActionsEntry.id
                      ? ageCredentialEditor.ageCredential
                      : Number(selectedUserActionsEntry.age_credential || 18)
                  }
                >
                  {AGE_CREDENTIAL_OPTIONS.map((age) => (
                    <option key={age} value={age}>{formatAgeCredential(age)}</option>
                  ))}
                </select>
              </label>
              <div className="admin-list__actions">
                <button
                  className="primary-button"
                  disabled={
                    userActionPending === selectedUserActionsEntry.id
                    || Number(ageCredentialEditor.ageCredential || 18) === Number(selectedUserActionsEntry.age_credential || 18)
                  }
                  onClick={() => handleSaveUserAgeCredential(selectedUserActionsEntry)}
                  type="button"
                >
                  Save age credential
                </button>
              </div>
            </div>

            {selectedUserActionsEntry.id === user?.id ? (
              <p className="page-subnote">
                Your own admin account cannot be disabled from the main row.
                {!hasAnotherEnabledAdmin ? " Create another enabled admin before deleting your own account." : ""}
              </p>
            ) : null}

            {selectedUserActionsEntry.id === user?.id && selfDeleteState.open ? (
              <div className="admin-danger-block">
                {!selfDeleteState.armed ? (
                  <form className="admin-inline-form" onSubmit={handleSelfDeletePrecheck}>
                    <p className="page-subnote">
                      Enter your current admin password first. You will see one final destructive confirmation before anything is deleted.
                    </p>
                    <NonLoginSecretInput
                      autoComplete="new-password"
                      onChange={(event) =>
                        setSelfDeleteState((current) => ({
                          ...current,
                          password: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
                      purpose="self-delete-reauth"
                      value={selfDeleteState.password}
                    />
                    {selfDeleteState.error ? <p className="form-error">{selfDeleteState.error}</p> : null}
                    <div className="admin-list__actions">
                      <button className="ghost-button ghost-button--danger" disabled={selfDeleteState.pending} type="submit">
                        {selfDeleteState.pending ? "Checking..." : "Continue"}
                      </button>
                      <button
                        className="ghost-button"
                        onClick={() =>
                          setSelfDeleteState({
                            open: false,
                            password: "",
                            armed: false,
                            pending: false,
                            error: "",
                          })
                        }
                        type="button"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="admin-inline-form">
                    <p className="form-error">
                      Final warning: deleting your own admin account ends your current session immediately.
                    </p>
                    {selfDeleteState.error ? <p className="form-error">{selfDeleteState.error}</p> : null}
                    <div className="admin-list__actions">
                      <button
                        className="ghost-button ghost-button--danger"
                        disabled={selfDeleteState.pending}
                        onClick={handleSelfDeleteConfirm}
                        type="button"
                      >
                        {selfDeleteState.pending ? "Deleting..." : "Delete my admin account"}
                      </button>
                      <button
                        className="ghost-button"
                        onClick={() =>
                          setSelfDeleteState({
                            open: false,
                            password: "",
                            armed: false,
                            pending: false,
                            error: "",
                          })
                        }
                        type="button"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ) : null}

            <InlineFeedback feedback={userFeedback[selectedUserActionsEntry.id]} />

            {roleConfirm.userId === selectedUserActionsEntry.id ? (
              <form
                className="admin-inline-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  handleSubmitRoleChange(selectedUserActionsEntry);
                }}
              >
                <p className="page-subnote">
                  Confirm making {selectedUserActionsEntry.username} {roleConfirm.nextRole === "admin" ? "an admin" : "a standard user"}.
                </p>
                <NonLoginSecretInput
                  autoComplete="new-password"
                  onChange={(event) =>
                    setRoleConfirm((current) => ({
                      ...current,
                      currentAdminPassword: event.target.value,
                    }))
                  }
                  placeholder="Current admin password"
                  purpose="role-change-reauth"
                  value={roleConfirm.currentAdminPassword}
                />
                <div className="admin-list__actions">
                  <button className="primary-button" disabled={userActionPending === selectedUserActionsEntry.id} type="submit">
                    Confirm role change
                  </button>
                  <button
                    className="ghost-button"
                    onClick={() => clearUserEditors(selectedUserActionsEntry.id)}
                    type="button"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : null}

            {passwordEditor.userId === selectedUserActionsEntry.id ? (
              <form
                className="admin-inline-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  handleSubmitPassword(selectedUserActionsEntry);
                }}
              >
                <NonLoginSecretInput
                  autoComplete="new-password"
                  onChange={(event) =>
                    setPasswordEditor((current) => ({
                      ...current,
                      newPassword: event.target.value,
                    }))
                  }
                  placeholder="New password"
                  purpose="admin-update-new-secret"
                  value={passwordEditor.newPassword}
                />
                <NonLoginSecretInput
                  autoComplete="new-password"
                  onChange={(event) =>
                    setPasswordEditor((current) => ({
                      ...current,
                      currentAdminPassword: event.target.value,
                    }))
                  }
                  placeholder="Current admin password"
                  purpose="admin-update-reauth"
                  value={passwordEditor.currentAdminPassword}
                />
                <div className="admin-list__actions">
                  <button className="primary-button" disabled={userActionPending === selectedUserActionsEntry.id} type="submit">
                    Save password
                  </button>
                  <button
                    className="ghost-button"
                    onClick={() => clearUserEditors(selectedUserActionsEntry.id)}
                    type="button"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : null}
          </section>

	          <section className="admin-user-actions-modal__section">
	            <div className="admin-user-actions-modal__section-header">
	              <h3>Assistant</h3>
	              <p className="page-subnote">Secondary access only for the safe structured request form.</p>
	            </div>
            {selectedUserActionsEntry.role === "standard_user" ? (
              <div className="assistant-access-toggle assistant-access-toggle--modal">
                <div>
                  <strong>{selectedUserActionsEntry.assistant_beta_enabled ? "Enabled" : "Disabled"}</strong>
	                  <p className="page-subnote">
	                    {selectedUserActionsEntry.assistant_beta_enabled
	                      ? "This user can access the Assistant request flow."
	                      : "This user cannot access the Assistant request flow."}
	                  </p>
                </div>
                <button
                  className={selectedUserActionsEntry.assistant_beta_enabled ? "ghost-button" : "primary-button"}
                  disabled={userActionPending === selectedUserActionsEntry.id}
                  onClick={() => handleAssistantAccessToggle(selectedUserActionsEntry)}
                  type="button"
                >
                  {selectedUserActionsEntry.assistant_beta_enabled ? "Disable Assistant" : "Enable Assistant"}
                </button>
              </div>
            ) : (
	              <p className="page-subnote">
	                Admins always have Assistant access. The account switch is only configurable for standard users.
	              </p>
	            )}
	          </section>

          <section className="admin-user-actions-modal__section">
            <div className="admin-user-actions-modal__section-header">
              <h3>Download Access (Beta)</h3>
              <p className="page-subnote">Download grants are separate from playback access.</p>
            </div>
            {downloadAccessState.loading && downloadAccessState.userId === selectedUserActionsEntry.id ? (
              <p className="page-subnote">Loading download access...</p>
            ) : (
              <div className="download-access-card">
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>No download access</strong>
                    <small>Hide download actions for this user.</small>
                  </span>
                  <input
                    checked={downloadAccessState.accessMode === "none"}
                    name="download-access-mode"
                    onChange={() => updateDownloadAccessMode("none")}
                    type="radio"
                  />
                </label>
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>Enable access to all movies</strong>
                    <small>Allow downloading every visible movie.</small>
                  </span>
                  <input
                    checked={downloadAccessState.accessMode === "all"}
                    name="download-access-mode"
                    onChange={() => updateDownloadAccessMode("all")}
                    type="radio"
                  />
                </label>
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>Select available movies</strong>
                    <small>Grant individual movies one at a time.</small>
                  </span>
                  <input
                    checked={downloadAccessState.accessMode === "selected"}
                    name="download-access-mode"
                    onChange={() => updateDownloadAccessMode("selected")}
                    type="radio"
                  />
                </label>

                {downloadAccessState.accessMode === "selected" ? (
                  <div className="download-access-picker">
                    <label className="search-field">
                      <span className="sr-only">Search movies for download access</span>
                      <input
                        autoComplete="off"
                        onChange={(event) =>
                          setDownloadAccessState((current) => ({ ...current, searchQuery: event.target.value }))
                        }
                        placeholder="Search movies to add"
                        type="search"
                        value={downloadAccessState.searchQuery}
                      />
                    </label>
                    {downloadAccessState.searchQuery.trim() ? (
                      <div className="download-access-results">
                        {downloadAccessState.searchPending ? <p className="page-subnote">Searching...</p> : null}
                        {!downloadAccessState.searchPending && downloadAccessState.searchResults.length === 0 ? (
                          <p className="page-subnote">No matching movies.</p>
                        ) : null}
                        {downloadAccessState.searchResults.slice(0, 8).map((item) => (
                          <button
                            className="download-access-result"
                            key={item.id}
                            onClick={() => addDownloadAccessMovie(item)}
                            type="button"
                          >
                            <strong>{item.title}</strong>
                            <span>{formatBytes(item.file_size)}</span>
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {downloadAccessState.selectedItems.length > 0 ? (
                      <div className="download-access-selected">
                        {downloadAccessState.selectedItems.map((item) => (
                          <span className="download-access-chip" key={item.id}>
                            {item.title}
                            <button
                              aria-label={`Remove ${item.title}`}
                              onClick={() => removeDownloadAccessMovie(item.id)}
                              type="button"
                            >
                              X
                            </button>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="page-subnote">No selected movies yet.</p>
                    )}
                  </div>
                ) : null}
                {downloadAccessState.error ? <p className="form-error">{downloadAccessState.error}</p> : null}
                {downloadAccessState.feedback ? <p className="action-feedback">{downloadAccessState.feedback}</p> : null}
                <button
                  className="primary-button"
                  disabled={downloadAccessState.saving}
                  onClick={() => saveDownloadAccess(selectedUserActionsEntry)}
                  type="button"
                >
                  {downloadAccessState.saving ? "Saving..." : "Save download access"}
                </button>
              </div>
            )}
          </section>
	        </div>
	      </div>
	    </div>
  ) : null;

  const terminateWorkerConfirmationModal = terminateWorkerModal ? (
    <div
      aria-labelledby="admin-terminate-worker-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={closeTerminateWorkerModal}
      />
      <div className="browser-resume-modal__card detail-info-modal__card admin-playback-worker-modal">
        <div className="detail-info-modal__copy">
          <p className="eyebrow detail-info-modal__eyebrow">PLAYBACK WORKER</p>
          <p id="admin-terminate-worker-modal-title" className="detail-info-modal__title admin-playback-worker-modal__prompt">
            {buildPlaybackWorkerTerminatePrompt(terminateWorkerModal.title)}
          </p>
        </div>
        <div className="browser-resume-modal__actions admin-playback-worker-modal__actions">
          <button
            className="primary-button admin-playback-worker-modal__cancel"
            disabled={terminateWorkerPending === terminateWorkerModal.workerId}
            onClick={closeTerminateWorkerModal}
            type="button"
          >
            No
          </button>
          <button
            className="ghost-button ghost-button--danger admin-playback-worker-modal__confirm"
            disabled={terminateWorkerPending === terminateWorkerModal.workerId}
            onClick={handleTerminateWorkerConfirm}
            type="button"
          >
            Yes
          </button>
        </div>
      </div>
    </div>
  ) : null;

  const dismissPlaybackStatusConfirmationModal = dismissPlaybackStatusModal ? (
    <div
      aria-labelledby="admin-dismiss-playback-status-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={closeDismissPlaybackStatusModal}
      />
      <div className="browser-resume-modal__card detail-info-modal__card admin-playback-worker-modal">
        <div className="detail-info-modal__copy">
          <p className="eyebrow detail-info-modal__eyebrow">PLAYBACK STATUS</p>
          <p id="admin-dismiss-playback-status-modal-title" className="detail-info-modal__title admin-playback-worker-modal__prompt">
            {buildPlaybackStatusDismissPrompt(dismissPlaybackStatusModal.title)}
          </p>
          <p className="page-subnote">This only hides the non-running card in this admin page session.</p>
        </div>
        <div className="browser-resume-modal__actions admin-playback-worker-modal__actions">
          <button
            className="primary-button admin-playback-worker-modal__cancel"
            onClick={closeDismissPlaybackStatusModal}
            type="button"
          >
            Cancel
          </button>
          <button
            className="ghost-button admin-playback-worker-modal__confirm"
            onClick={handleDismissPlaybackStatusConfirm}
            type="button"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  ) : null;

  const diagnosticIdTitle = diagnosticIdModal ? `${diagnosticIdModal.label} id` : "";
  const diagnosticIdPopup = diagnosticIdModal ? (
    <div
      aria-labelledby="admin-diagnostic-id-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={closeDiagnosticIdModal}
      />
      <div className="browser-resume-modal__card detail-info-modal__card admin-diagnostic-id-modal">
        <div className="admin-diagnostic-id-modal__header">
          <p id="admin-diagnostic-id-modal-title" className="detail-info-modal__title admin-diagnostic-id-modal__title">
            {diagnosticIdTitle}
          </p>
          <button
            aria-label="Close"
            className="ghost-button detail-info-modal__close admin-diagnostic-id-modal__close"
            onClick={closeDiagnosticIdModal}
            type="button"
          >
            X
          </button>
        </div>
        <code className="admin-diagnostic-id-modal__value">{diagnosticIdModal.value}</code>
      </div>
    </div>
  ) : null;

  const exposureDisplay = exposurePlan || exposureStatus || {};
  const exposureActive = exposureDisplay.active || {};
  const exposureValidation = exposureDisplay.validation || {};
  const exposureChecks = Array.isArray(exposureValidation.checks) ? exposureValidation.checks : [];
  const exposurePlanDetails = exposureDisplay.plan || {};
  const exposurePendingDraft = exposureDisplay.pending_draft || null;
  const exposureFinalizedProfile = exposureDisplay.finalized_profile || null;
  const exposureEnvSuggestions = Array.isArray(exposurePlanDetails.env_suggestions)
    ? exposurePlanDetails.env_suggestions
    : [];
  const exposureManualSteps = Array.isArray(exposurePlanDetails.manual_steps) ? exposurePlanDetails.manual_steps : [];
  const exposureReverseProxyNotes = Array.isArray(exposurePlanDetails.reverse_proxy_notes)
    ? exposurePlanDetails.reverse_proxy_notes
    : [];
  const exposureActivationNotes = Array.isArray(exposurePlanDetails.activation_notes)
    ? exposurePlanDetails.activation_notes
    : [];
  const exposureErrors = Array.isArray(exposureValidation.errors) ? exposureValidation.errors : [];
  const exposureWarnings = Array.isArray(exposureValidation.warnings) ? exposureValidation.warnings : [];
  const exposureModeStatus = formatExposureModeStatus(exposureActive, exposurePendingDraft, exposureFinalizedProfile);
  const exposureMaintenanceLock = exposureActive.maintenance_mode || exposureActive.maintenance_lock || {};
  const exposureMaintenanceLockStatus = exposureMaintenanceLock.enabled ? "On" : "Off";
  const exposureMaintenanceTargetValue = exposureMaintenanceTargetMode || (exposureMaintenanceLock.enabled ? "on" : "off");
  const exposureMaintenanceTargetEnabled = exposureMaintenanceTargetValue === "on";
  const exposureMaintenanceTargetMatchesCurrent = exposureMaintenanceTargetEnabled === Boolean(exposureMaintenanceLock.enabled);
  const exposureMaintenanceActionLabel = exposureMaintenanceTargetEnabled ? "Enable Maintenance Mode" : "Disable Maintenance Mode";
  const exposurePrepareMaintenanceStatus = exposureMaintenanceLock.enabled ? "Already On" : "Will be enabled automatically on prepare";
  const exposurePreparedSwitch = exposureDisplay.prepared_switch || null;
  const exposurePreparedSwitchStatus = !exposurePreparedSwitch
    ? "None"
    : exposurePreparedSwitch.status === "verified_after_restart"
      ? "Verified"
      : "Prepared";
  const exposurePreparedSwitchStatusDetail = !exposurePreparedSwitch
    ? "None"
    : exposurePreparedSwitch.status === "verified_after_restart"
      ? "Verified after restart"
      : "Prepared for manual apply";
  const exposureValidationStatus = exposureValidation.status || "ready";
  const exposureValidationStatusLabel = exposureValidationStatus === "blocked"
    ? "Blocked"
    : exposureValidationStatus === "warnings"
      ? "Warnings"
      : "Ready";
  const exposureValidationSummary = exposureValidationStatus === "blocked"
    ? "Blocking errors must be fixed before saving a pending draft."
    : exposureValidationStatus === "warnings"
      ? "Warnings need review before saving a pending draft."
      : "No blocking errors.";
  const exposureCurrentOriginMatchCheck = exposureChecks.find((entry) => entry.name === "current_origin_match") || null;
  const exposureCurrentOriginMatchStatus = exposureCurrentOriginMatchCheck
    ? formatExposureValue(exposureCurrentOriginMatchCheck.status)
    : exposurePendingDraft?.desired?.desired_mode === "public"
      ? "Required"
      : "Not required";
  const exposurePhase4VerificationStatus = exposurePreparedSwitch?.verification_required
    ? exposurePreparedSwitch.status === "verified_after_restart"
      ? "Verified after restart"
      : "Required after restart"
    : exposurePendingDraft?.desired?.desired_mode === "public"
      ? "Required in Phase 4"
      : "Not required for private draft";
  const exposurePreparedManualSteps = Array.isArray(exposurePreparedSwitch?.manual_steps)
    ? exposurePreparedSwitch.manual_steps
    : [];
  const exposurePreparedEnvBlock = exposurePreparedSwitch?.env_block || "";
  const exposurePreparedDesired = exposurePreparedSwitch?.desired || {};
  const exposureExpectedOrigin = exposurePreparedDesired.desired_mode === "public"
    ? exposurePreparedDesired.public_origin
    : exposurePreparedDesired.private_origin || "Not required";
  const exposureVerification = exposureVerificationResult || exposurePreparedSwitch?.verification || {};
  const exposureVerificationStatus = exposureVerification.status || (exposurePreparedSwitch ? "not_verified" : "not_ready");
  const exposureVerificationStatusLabel = exposureVerificationStatus === "passed"
    ? "Passed"
    : exposureVerificationStatus === "warnings"
      ? "Warnings"
      : exposureVerificationStatus === "blocked"
        ? "Blocked"
        : exposureVerificationStatus === "not_verified"
          ? "Not verified"
          : "Not ready";
  const exposureVerificationErrors = Array.isArray(exposureVerification.errors) ? exposureVerification.errors : [];
  const exposureVerificationWarnings = Array.isArray(exposureVerification.warnings) ? exposureVerification.warnings : [];
  const exposureVerificationChecks = Array.isArray(exposureVerification.checks) ? exposureVerification.checks : [];
  const exposureCurrentAccessChecks = exposureVerificationChecks.filter((entry) => (
    ["current_origin_match", "server_side_origin_probe"].includes(entry.name)
  ));
  const exposureRuntimeSettingChecks = exposureVerificationChecks.filter((entry) => (
    ["private_network_only", "public_app_origin", "backend_origin", "cookie_secure", "url_prefix_present"].includes(entry.name)
  ));
  const exposureSafetyStateChecks = exposureVerificationChecks.filter((entry) => (
    ["prepared_switch_status", "maintenance_mode", "trusted_proxy_cidrs"].includes(entry.name)
  ));
  const exposureWarningChecks = exposureVerificationChecks.filter((entry) => (
    entry.status === "warn" || entry.status === "block" || entry.name === "direct_ip_not_recommended"
  ));
  const exposureFinalizedProfileStatus = formatExposureProfileLabel(exposureFinalizedProfile);
  const exposureFinalizedVerificationStatus = exposureFinalizedProfile?.verification?.status || "";
  const exposureRuntimePostureStatus = exposureFinalizedVerificationStatus === "passed"
    ? "Verified"
    : exposureFinalizedVerificationStatus === "warnings"
      ? "Verified with warnings"
      : exposurePreparedSwitch?.status === "verified_after_restart"
        ? exposureVerificationStatus === "warnings" ? "Verified with warnings" : "Verified"
        : exposurePendingDraft || exposurePreparedSwitch
          ? "Needs review"
          : "None";
  const exposureCanFinalizeProfile = exposurePreparedSwitch?.status === "verified_after_restart";
  const exposureFinalizeProfileSource = exposurePreparedDesired.desired_mode ? exposurePreparedDesired : exposureFinalizedProfile;
  const exposureFinalizeProfileLabel = formatExposureProfileLabel(exposureFinalizeProfileSource);
  const exposureFinalizeOrigin = exposureFinalizeProfileSource?.desired_mode === "public" || exposureFinalizeProfileSource?.mode === "public"
    ? exposureFinalizeProfileSource?.public_origin
    : exposureFinalizeProfileSource?.private_origin || "Not required";
  const exposureFinalizeVerificationStatus = exposureVerificationStatusLabel;
  const adminOverviewSummary = desktopControlCenter ? (
    <div className="control-center-overview-summary">
      <section className="control-center-score-card" aria-label="Security score 92, private">
        <span className="control-center-score-card__eyebrow">Security score</span>
        <strong>92</strong>
        <span className="control-center-score-card__label">PRIVATE</span>
        <p>Presentation score only. Operational controls below remain authoritative.</p>
      </section>
      <section className="settings-card control-center-overview-facts">
        <h2>Current posture</h2>
        <StatusRow label="Users" value={String(statusPayload?.total_users ?? usersPayload.length)} />
        <StatusRow label="Active auth sessions" value={String(sessionsPayload.length)} />
        <StatusRow label="Titles indexed" value={String(statusPayload?.total_media_items ?? 0)} />
        <StatusRow label="Multi-user" value={statusPayload?.security?.multiuser_enabled ? "Enabled" : "Disabled"} />
      </section>
    </div>
  ) : null;
  const adminSecurityKpis = desktopControlCenter ? (
    <div className="control-center-admin-kpis" aria-label="Security summary">
      {[
        ["Users", statusPayload?.total_users ?? usersPayload.length],
        ["Active sessions", sessionsPayload.length],
        ["Titles indexed", statusPayload?.total_media_items ?? 0],
        ["Live invites", liveInviteCount],
      ].map(([label, value]) => (
        <section className="control-center-admin-kpi" key={label}>
          <span>{label}</span>
          <strong>{String(value)}</strong>
        </section>
      ))}
    </div>
  ) : null;
  const securitySection = statusPayload ? (
    <div className="admin-section-grid">
      <section className="settings-card control-center-admin-library-status">
        <h2>Library status</h2>
        <StatusRow label="Indexed movies" value={String(statusPayload.total_media_items)} />
        <StatusRow
          label="Scan"
          value={statusPayload.scan.running ? "Running" : statusPayload.last_scan?.finished_at ? "Idle" : "Ready"}
        />
        <StatusRow label="Files seen" value={String(statusPayload.last_scan?.files_seen ?? 0)} />
        <StatusRow label="Changed" value={String(statusPayload.last_scan?.files_changed ?? 0)} />
        <StatusRow label="Removed" value={String(statusPayload.last_scan?.files_removed ?? 0)} />
      </section>

	      <section className="settings-card exposure-summary-card">
	        <div className="settings-inline-header">
	          <div>
	            <h2>Security</h2>
	            <p className="page-subnote">Compact account and exposure posture.</p>
	          </div>
	          <button className="ghost-button ghost-button--inline" onClick={handleOpenExposurePlanner} type="button">
	            Manage Exposure Mode
	          </button>
	        </div>
	        <StatusRow label="Exposure Mode" value={exposureModeStatus} />
	        <StatusRow label="Finalized profile" value={exposureFinalizedProfileStatus} />
	        <StatusRow label="Runtime posture" value={exposureRuntimePostureStatus} />
	        <StatusRow label="Pending draft" value={exposurePendingDraft ? "Exists" : "None"} />
	        <StatusRow label="Maintenance Mode" value={exposureMaintenanceLockStatus} />
	        <StatusRow label="Prepared switch" value={exposurePreparedSwitchStatus} />
	        <StatusRow label="Current request origin" value={formatExposureValue(exposureActive.current_request_origin)} />
	        <StatusRow label="Multi-user" value={statusPayload.security.multiuser_enabled ? "Enabled" : "Disabled"} />
	        <StatusRow label="Users" value={String(statusPayload.total_users)} />
	        <StatusRow label="Active auth sessions" value={String(sessionsPayload.length)} />
	        <StatusRow label="Session TTL" value={`${statusPayload.security.session_ttl_hours} hour(s)`} />
          <div className="exposure-maintenance-summary-control">
            <div className="settings-field">
              <span>
                <strong>Maintenance Mode</strong>
                <small>Standalone server mode. Enabling logs out non-admin users and blocks non-admin logins without disabling accounts.</small>
              </span>
              <ExposureSegmentedControl
                ariaLabel="Maintenance Mode target"
                className="exposure-maintenance-switch exposure-maintenance-switch--summary"
                onChange={setExposureMaintenanceTargetMode}
                options={EXPOSURE_MAINTENANCE_SEGMENTS}
                value={exposureMaintenanceTargetValue}
              />
            </div>
            <label className="settings-toggle settings-toggle--compact">
              <span>
                <strong>Maintenance Mode acknowledgement</strong>
                <small>{EXPOSURE_MAINTENANCE_ACKNOWLEDGEMENT}</small>
              </span>
              <input
                checked={exposureMaintenanceForm.acknowledgement}
                onChange={(event) =>
                  setExposureMaintenanceForm((current) => ({
                    ...current,
                    acknowledgement: event.target.checked,
                  }))
                }
                type="checkbox"
              />
            </label>
            <label className="settings-field">
              <span>
                <strong>Current admin password</strong>
                <small>Required to enable or disable Maintenance Mode.</small>
              </span>
              <div className="exposure-secret-field">
                <NonLoginSecretInput
                  autoComplete="new-password"
                  onChange={(event) =>
                    setExposureMaintenanceForm((current) => ({
                      ...current,
                      currentAdminPassword: event.target.value,
                    }))
                  }
                  placeholder="Current admin password"
                  value={exposureMaintenanceForm.currentAdminPassword}
                />
              </div>
            </label>
            <InlineFeedback feedback={exposureMaintenanceFeedback} />
            <div className="admin-list__actions exposure-maintenance-actions">
              <button
                className="primary-button exposure-maintenance-confirm-button"
                disabled={exposurePending || exposureMaintenanceTargetMatchesCurrent}
                onClick={() => handleSetExposureMaintenanceLock(exposureMaintenanceTargetEnabled)}
                type="button"
              >
                {exposureMaintenanceActionLabel}
              </button>
              <span className="page-subnote exposure-maintenance-actions__hint">
                {exposureMaintenanceTargetMatchesCurrent ? "Already matches the current state." : "Password confirmation required."}
              </span>
            </div>
          </div>
	      </section>

      {urlPrefixStatus?.rotation_reminder_due && !urlPrefixReminderDismissed ? (
        <section className="settings-card settings-card--wide control-center-admin-url-reminder">
          <div className="settings-inline-header">
            <div>
              <h2>URL prefix reminder</h2>
              <p className="page-subnote">
                URL prefix is {urlPrefixStatus.days_old} days old. Consider rotating if you've widely shared the URL.
              </p>
            </div>
            <div className="admin-list__actions">
              <button
                className="primary-button"
                onClick={() =>
                  setUrlPrefixRotateModal({
                    open: true,
                    currentAdminPassword: "",
                    pending: false,
                    error: "",
                  })
                }
                type="button"
              >
                Rotate now
              </button>
              <button className="ghost-button" onClick={() => setUrlPrefixReminderDismissed(true)} type="button">
                Dismiss
              </button>
            </div>
          </div>
        </section>
      ) : null}

      <section className="settings-card control-center-admin-url-prefix">
        <div className="settings-inline-header">
          <div>
            <h2>URL Prefix</h2>
            <p className="page-subnote">
              Random path prefix that hides the app from automated scanners. Rotating revokes all sessions.
            </p>
          </div>
        </div>
        <StatusRow label="Current prefix" value={urlPrefixStatus?.prefix ? `/${urlPrefixStatus.prefix}/` : "Unknown"} />
        <StatusRow label="Generated" value={urlPrefixStatus?.generated_at ? `${formatDate(urlPrefixStatus.generated_at)} (${urlPrefixStatus.days_old} days ago)` : "Manual override"} />
        <StatusRow label="Manual rotations" value={String(urlPrefixStatus?.rotated_count ?? 0)} />
        <div className="admin-list__actions admin-url-prefix-actions">
          <button
            className="primary-button"
            onClick={() =>
              setUrlPrefixRotateModal({
                open: true,
                currentAdminPassword: "",
                pending: false,
                error: "",
              })
            }
            type="button"
          >
            Rotate prefix...
          </button>
          <RefreshSweepButton
            className="ghost-button"
            disabled={urlPrefixPending}
            onClick={handleUrlPrefixRefreshStatus}
            type="button"
          >
            {urlPrefixPending ? "Refreshing..." : "Refresh status"}
          </RefreshSweepButton>
        </div>
      </section>

      <section className="settings-card admin-totp-summary-card">
        <div className="settings-inline-header">
          <div>
            <h2>Two-factor authentication</h2>
            <p className="page-subnote">Admins are prompted to enable authenticator-based 2FA.</p>
          </div>
          <div className="admin-list__actions">
            {totpStatus?.enabled ? (
              <button className="ghost-button" onClick={() => openOwnTotpModal("regenerate")} type="button">
                Regenerate recovery codes
              </button>
            ) : (
              <Link className="primary-button" to="/setup/totp">
                Enable 2FA
              </Link>
            )}
          </div>
        </div>
        <StatusRow label="Status" value={totpStatus?.enabled ? `Enabled${totpStatus.enabled_at ? ` since ${formatDate(totpStatus.enabled_at)}` : ""}` : "Not enabled"} />
        <StatusRow label="Recovery codes remaining" value={String(totpStatus?.recovery_codes_remaining ?? 0)} />
        {totpStatus?.enabled ? (
          <button className="ghost-button ghost-button--danger" onClick={() => openOwnTotpModal("disable")} type="button">
            Disable 2FA
          </button>
        ) : null}
      </section>

      <section className="settings-card settings-card--wide control-center-admin-manage-user-totp">
        <div className="settings-inline-header">
          <div>
            <h2>Manage user 2FA</h2>
            <p className="page-subnote">Enable requires setup on next login. Disable removes pairing and revokes that user's active sessions.</p>
          </div>
        </div>
        <div className="admin-list admin-list--dense">
          {usersPayload.map((entry) => {
            const totpRequirementEnabled = Boolean(entry.totp_setup_prompt_enabled || entry.totp_enabled);
            const totpLabel = entry.totp_enabled
              ? `2FA set up${entry.totp_enabled_at ? ` · last enabled ${formatDate(entry.totp_enabled_at)}` : ""}`
              : entry.totp_setup_prompt_enabled
                ? "2FA required · setup not complete"
                : "2FA not required";
            return (
              <div className="admin-list__row admin-list__row--card admin-totp-user-row" key={`totp-${entry.id}`}>
                <div className="admin-totp-user-row__copy">
                  <span className="admin-totp-user-row__name">
                    <strong>{entry.username}</strong>
                    {entry.role === "admin" ? <AdminCrownIcon /> : null}
                  </span>
                  <p className="page-subnote">{totpLabel}</p>
                </div>
                <div className="admin-totp-user-row__actions">
                  <button
                    className={totpRequirementEnabled ? "ghost-button ghost-button--danger" : "ghost-button"}
                    disabled={totpPromptPendingUserId === entry.id}
                    onClick={() => handleToggleUserTotpRequirement(entry)}
                    type="button"
                  >
                    {totpPromptPendingUserId === entry.id
                      ? "Saving..."
                      : totpRequirementEnabled
                        ? "Disable"
                        : "Enable"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="settings-card settings-card--wide control-center-admin-password-help">
        <div className="settings-inline-header">
          <div>
            <h2>Password help requests</h2>
            <p className="page-subnote">Pending requests stay visible for 30 days unless dismissed.</p>
          </div>
          <RefreshSweepButton
            className="ghost-button ghost-button--inline"
            onClick={handlePasswordHelpRefresh}
            type="button"
          >
            Refresh
          </RefreshSweepButton>
        </div>
        <div className="admin-list admin-list--dense password-help-request-list">
          {passwordHelpRequests.length > 0 ? (
	            passwordHelpRequests.map((requestEntry) => {
	              const detailsOpen = expandedPasswordHelpRequestId === requestEntry.id;
	              const requestUser = usersPayload.find((entry) => entry.id === requestEntry.user_id) || null;
              const requesterIpAddress = unknownIfEmpty(requestEntry.requester_ip_address);
              const requesterDevice = detectPasswordHelpDevice(requestEntry.requester_user_agent);
              const requesterBrowser = detectPasswordHelpBrowser(requestEntry.requester_user_agent);
              return (
                <div
                  className={[
                    "admin-list__row",
                    "admin-list__row--card",
                    "password-help-request-card",
                    detailsOpen ? "password-help-request-card--details-open" : "",
                  ].filter(Boolean).join(" ")}
                  key={requestEntry.id}
                >
                  <button
                    aria-expanded={detailsOpen}
                    aria-label="Password request details"
                    className="password-help-request-card__info-button"
                    onClick={() => togglePasswordHelpDetails(requestEntry.id)}
                    type="button"
                  >
                    <span aria-hidden="true" className="password-help-request-card__info-glyph">i</span>
                  </button>
                  <div>
                    <strong>{requestEntry.username_snapshot}</strong>
                    <p className="page-subnote">
                      Requested {formatDate(requestEntry.created_at)} · expires {formatDate(requestEntry.expires_at)}
                    </p>
                  </div>
                  {detailsOpen ? (
                    <div className="password-help-request-card__details">
                      <div>
                        <span>IP address</span>
                        <strong>{requesterIpAddress}</strong>
                      </div>
                      <div>
                        <span>Detected device</span>
                        <strong>{requesterDevice}</strong>
                      </div>
                      <div>
                        <span>Browser</span>
                        <strong>{requesterBrowser}</strong>
                      </div>
                    </div>
                  ) : null}
                  <div className="admin-list__actions">
                    <button
                      className="ghost-button"
                      disabled={!requestUser}
                      onClick={() => requestUser && openUserActionsModal(requestUser)}
                      type="button"
                    >
                      Open user actions
                    </button>
                    <button
                      className="ghost-button ghost-button--danger"
                      disabled={passwordHelpPendingId === requestEntry.id}
                      onClick={() => openPasswordHelpDismissModal(requestEntry)}
                      type="button"
                    >
                      {passwordHelpPendingId === requestEntry.id ? "Dismissing..." : "Dismiss"}
                    </button>
                  </div>
                </div>
              );
            })
          ) : (
            <p className="page-subnote">No pending password help requests.</p>
          )}
        </div>
      </section>
	    </div>
	  ) : null;

  const logsSection = (
    <>
      {desktopControlCenter ? (
        <div className="control-center-live-audit" aria-label="Live audit design ticker">
          <span className="control-center-live-audit__label"><i aria-hidden="true" />LIVE AUDIT</span>
          <div className="control-center-live-audit__track">
            <span>{ADMIN_LIVE_AUDIT_TICKER_LINE}</span>
            <span aria-hidden="true">{ADMIN_LIVE_AUDIT_TICKER_LINE}</span>
          </div>
        </div>
      ) : null}
      <div className="admin-activity-grid">
      <section className="settings-card admin-activity-card">
        <div className="settings-inline-header">
          <div>
            <h2>Active sessions</h2>
            <p className="page-subnote">
              Showing {visibleSessions.length} of {sessionsPayload.length} sessions. Revoke ends that specific auth session and its session-linked playback or VLC handoff access. It does not remove the device record.
            </p>
          </div>
          {sessionsPayload.length > 8 ? (
            <button
              className="ghost-button ghost-button--inline"
              onClick={() => setShowAllSessions((current) => !current)}
              type="button"
            >
              {showAllSessions ? "Show recent only" : "Show all"}
            </button>
          ) : null}
        </div>
        <div className="admin-list admin-list--dense">
          {visibleSessions.length > 0 ? (
            visibleSessions.map((session) => (
              <div className="admin-list__row admin-list__row--card" key={session.id}>
                <div>
                  <strong>{session.username}</strong>
                  <p className="page-subnote">
                    session #{session.id} · {session.ip_address || "unknown IP"} · last seen {formatDate(session.last_seen_at)}
                  </p>
                  {session.last_activity_at ? <p className="page-subnote">Last activity {formatDate(session.last_activity_at)}</p> : null}
                  {session.user_agent ? <p className="page-subnote">{session.user_agent}</p> : null}
                </div>
                <div className="admin-list__actions">
                  <button
                    className="ghost-button"
                    disabled={sessionActionPending === session.id}
                    onClick={() => handleRevokeSession(session)}
                    type="button"
                  >
                    Revoke
                  </button>
                </div>
              </div>
            ))
          ) : (
            <p className="page-subnote">No active sessions found.</p>
          )}
        </div>
      </section>

      <section className="settings-card admin-activity-card">
        <div className="settings-inline-header">
          <div>
            <h2>Recent audit log</h2>
            <p className="page-subnote">Showing {visibleAuditEvents.length} of {auditPayload.length} events.</p>
          </div>
          {auditPayload.length > 10 ? (
            <button
              className="ghost-button ghost-button--inline"
              onClick={() => setShowAllAudit((current) => !current)}
              type="button"
            >
              {showAllAudit ? "Show recent only" : "Show all"}
            </button>
          ) : null}
        </div>
        <div className="admin-list admin-list--dense">
          {visibleAuditEvents.length > 0 ? (
            visibleAuditEvents.map((event) => (
              <div className="admin-list__row admin-list__row--card" key={event.id}>
                <div>
                  <strong>{event.action}</strong>
                  <p className="page-subnote">
                    {formatDate(event.created_at)} · {event.outcome} · {event.username || "unknown user"} · {event.ip_address || "unknown IP"}
                  </p>
                  {event.target_type || event.target_id || event.media_item_id ? (
                    <p className="page-subnote">
                      {event.target_type || "target"} {event.target_id || event.media_item_id || "n/a"}
                    </p>
                  ) : null}
                </div>
              </div>
            ))
          ) : (
            <p className="page-subnote">No audit events recorded yet.</p>
          )}
        </div>
      </section>
      </div>
    </>
  );

  const recoverySection = (
    <div className="admin-section-stack admin-recovery-section">
      <section className="settings-card settings-card--wide">
        <p className="eyebrow">Admin-only</p>
        <h2>Backup &amp; Recovery</h2>
        <p className="page-subnote">
          Backups protect Elvern runtime state. They do not include movie files, poster libraries, or playback/transcode cache.
        </p>
        <p className="form-error">
          Manual backups may contain secrets such as env values, OAuth tokens, session-related secrets, and database contents. Do not commit or share them.
        </p>
        <p className="page-subnote">
          Auto encrypted backups are protected by a key derived from ELVERN_SESSION_SECRET. If that secret is lost or rotated, old auto-key backups may not be recoverable. For long-term/off-machine recovery, use a manual passphrase backup.
        </p>
        <div className="admin-list__actions">
          <button
            className="primary-button"
            disabled={createBackupPending}
            onClick={openCreateBackupModal}
            type="button"
          >
            {createBackupPending ? "Creating backup..." : "Create encrypted backup"}
          </button>
          <RefreshSweepButton
            className="ghost-button"
            disabled={recoveryLoading}
            onClick={handleRecoveryRefresh}
            type="button"
          >
            {recoveryLoading ? "Refreshing..." : "Refresh"}
          </RefreshSweepButton>
        </div>
        <FeedbackBanner banner={recoveryFeedback} />
      </section>

      <div className="admin-activity-grid admin-recovery-grid admin-recovery-grid--compact settings-grid--compact-columns">
        <div className="settings-grid__column admin-recovery-grid__column">
          <section className="settings-card admin-activity-card admin-recovery-card">
            <div className="settings-inline-header">
              <div>
                <h2>Recent checkpoints</h2>
                <p className="page-subnote">
                  Select a checkpoint, then inspect it or preview recovery. Automatic checkpoints are best-effort and stay server-local in this stage.
                </p>
              </div>
            </div>
            {backupsPayload.length > 0 ? (
              <div className="admin-recovery__toolbar">
                <p className="page-subnote">{recoveryCheckpointSummary}</p>
                {backupsPayload.length > RECOVERY_CHECKPOINT_LIMIT ? (
                  <button
                    className="ghost-button ghost-button--inline"
                    onClick={() => setShowAllRecoveryCheckpoints((current) => !current)}
                    type="button"
                  >
                    {showAllRecoveryCheckpoints ? "Show less" : "Show all"}
                  </button>
                ) : null}
              </div>
            ) : null}
            <div className="admin-list admin-list--dense">
              {backupsPayload.length > 0 ? (
                visibleRecoveryCheckpoints.map((checkpoint) => {
                  const selected = checkpoint.checkpoint_id === selectedCheckpointId;
                  return (
                    <div
                      className={selected
                        ? "admin-list__row admin-list__row--card admin-recovery__checkpoint-card admin-recovery__checkpoint-card--selected"
                        : "admin-list__row admin-list__row--card admin-recovery__checkpoint-card"}
                      key={checkpoint.checkpoint_id}
                    >
                      <div>
                        <strong>{formatRecoveryTriggerLabel(checkpoint.backup_trigger)}</strong>
                        <p className="page-subnote">
                          {formatRecoveryCheckpointTime(checkpoint.created_at_utc)} · {checkpoint.auto_checkpoint ? "Automatic checkpoint" : "Manual checkpoint"}
                        </p>
                        <p className="page-subnote admin-recovery__mono" title={checkpoint.checkpoint_id}>
                          ID {formatRecoveryCheckpointId(checkpoint.checkpoint_id)}
                        </p>
                        <p className="page-subnote">
                          {formatBackupProtectionLabel(checkpoint)} · {checkpoint.contains_secrets ? "Contains secrets" : "No secrets flagged"} · DB integrity {checkpoint.db_integrity_check_result || "unknown"} · {formatBytes(checkpoint.total_size_bytes)} · {checkpoint.file_count} files
                        </p>
                        <p className="page-subnote">
                          Inspect {checkpoint.inspect_valid ? "valid" : "invalid"}{checkpoint.inspect_error ? ` · ${checkpoint.inspect_error}` : ""}
                        </p>
                      </div>
                      <div className="admin-list__actions">
                        <button
                          className={selected ? "primary-button" : "ghost-button"}
                          onClick={() => handleCheckpointSelection(checkpoint.checkpoint_id)}
                          type="button"
                        >
                          {selected ? "Selected" : "Select"}
                        </button>
                      </div>
                    </div>
                  );
                })
              ) : (
                <p className="page-subnote">
                  {recoveryLoading ? "Loading checkpoints..." : "No checkpoints found yet."}
                </p>
              )}
            </div>
          </section>

          <section className="settings-card admin-activity-card admin-recovery-card admin-recovery-card--result">
            <div className="settings-inline-header">
              <div>
                <h2>Recovery preview</h2>
                <p className="page-subnote">
                  This only checks what this checkpoint could recover. It does not restore or change anything.
                </p>
              </div>
              <button
                className="ghost-button ghost-button--inline"
                disabled={!selectedCheckpointId || restorePlanPending}
                onClick={handleProtectedRestorePlanClick}
                type="button"
              >
                {restorePlanPending ? "Previewing..." : "Preview recovery"}
              </button>
            </div>
            {selectedCheckpoint ? (
              <div className="admin-recovery__selection-note">
                <p className="page-subnote">
                  Selected checkpoint: {formatRecoveryTriggerLabel(selectedCheckpoint.backup_trigger)} · {formatRecoveryCheckpointTime(selectedCheckpoint.created_at_utc)}
                </p>
                <p className="page-subnote admin-recovery__mono" title={selectedCheckpoint.checkpoint_id}>
                  ID {selectedCheckpoint.checkpoint_id}
                </p>
              </div>
            ) : (
              <p className="page-subnote">Select a checkpoint first.</p>
            )}
            {restorePlanPayload ? (
              <div className="admin-list">
                <div className="admin-list__row admin-list__row--card admin-recovery__result-card">
                  <div>
                    <strong>{restorePlanPayload.checkpoint_valid ? "Recovery preview ready" : "Recovery preview has blocking errors"}</strong>
                    <p className="page-subnote">
                      {formatRecoveryTriggerLabel(restorePlanPayload.backup_trigger)} · {restorePlanPayload.contains_secrets ? "Contains secrets" : "No secrets flagged"}
                    </p>
                    {restorePlanPayload.blocking_errors?.length > 0 ? (
                      <>
                        <p className="form-error">Blocking errors:</p>
                        <ul className="page-subnote">
                          {restorePlanPayload.blocking_errors.map((entry) => (
                            <li key={entry}>{entry}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {restorePlanWarnings.length > 0 ? (
                      <>
                        <div className="admin-recovery__list-header">
                          <p className="page-subnote">Warnings:</p>
                          {restorePlanWarnings.length > RECOVERY_WARNING_LIMIT ? (
                            <button
                              className="ghost-button ghost-button--inline"
                              onClick={() => setShowAllRecoveryWarnings((current) => !current)}
                              type="button"
                            >
                              {showAllRecoveryWarnings ? "Show fewer warnings" : "Show all warnings"}
                            </button>
                          ) : null}
                        </div>
                        <ul className="page-subnote">
                          {visibleRestorePlanWarnings.map((entry) => (
                            <li key={entry}>{entry}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    <p className="page-subnote">
                      Scope: DB snapshot {restorePlanPayload.restore_scope?.db_snapshot_available ? "available" : "missing"} · env {restorePlanPayload.restore_scope?.env_snapshot_available ? "available" : "missing"} · helper releases {restorePlanPayload.restore_scope?.helper_releases_available ? "available" : "missing"} · assistant uploads {restorePlanPayload.restore_scope?.assistant_uploads_available ? "available" : "missing"}
                    </p>
                    <p className="page-subnote">
                      Current vs backup: project root {restorePlanPayload.comparison?.same_project_root ? "same" : "different"} · DB path {restorePlanPayload.comparison?.same_db_path ? "same" : "different"} · public origin {restorePlanPayload.comparison?.same_public_app_origin ? "same" : "different"} · backend origin {restorePlanPayload.comparison?.same_backend_origin ? "same" : "different"} · media root {restorePlanPayload.comparison?.same_media_root_path ? "same" : "different"}
                    </p>
                    {restorePlanPayload.not_included?.length > 0 ? (
                      <>
                        <p className="page-subnote">Not included:</p>
                        <ul className="page-subnote">
                          {restorePlanPayload.not_included.map((entry) => (
                            <li key={entry}>{entry}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {restorePlanPayload.required_pre_restore_steps?.length > 0 ? (
                      <>
                        <p className="page-subnote">Required pre-restore steps:</p>
                        <ul className="page-subnote">
                          {restorePlanPayload.required_pre_restore_steps.map((entry) => (
                            <li key={entry}>{entry}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {restorePlanPayload.manual_restore_outline?.length > 0 ? (
                      <>
                        <p className="page-subnote">Manual restore outline:</p>
                        <ol className="page-subnote">
                          {restorePlanPayload.manual_restore_outline.map((entry) => (
                            <li key={entry}>{entry}</li>
                          ))}
                        </ol>
                      </>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : (
              <p className="page-subnote">
                Preview recovery to compare the checkpoint against the current live environment.
              </p>
            )}
          </section>
        </div>

        <div className="settings-grid__column admin-recovery-grid__column">
          <section className="settings-card admin-activity-card admin-recovery-card admin-recovery-card--result">
            <div className="settings-inline-header">
              <div>
                <h2>Inspect checkpoint</h2>
                <p className="page-subnote">
                  Compact checkpoint validation only. No manifest secrets or raw database contents are shown here.
                </p>
              </div>
              <button
                className="ghost-button ghost-button--inline"
                disabled={!selectedCheckpointId || inspectPending}
                onClick={handleProtectedInspectClick}
                type="button"
              >
                {inspectPending ? "Inspecting..." : "Inspect"}
              </button>
            </div>
            {selectedCheckpoint ? (
              <div className="admin-recovery__selection-note">
                <p className="page-subnote">
                  Selected checkpoint: {formatRecoveryTriggerLabel(selectedCheckpoint.backup_trigger)} · {formatRecoveryCheckpointTime(selectedCheckpoint.created_at_utc)}
                </p>
                <p className="page-subnote admin-recovery__mono" title={selectedCheckpoint.checkpoint_id}>
                  ID {selectedCheckpoint.checkpoint_id}
                </p>
              </div>
            ) : (
              <p className="page-subnote">Select a checkpoint first.</p>
            )}
            {inspectPayload ? (
              <div className="admin-list">
                <div className="admin-list__row admin-list__row--card admin-recovery__result-card">
                  <div>
                    <strong>{inspectPayload.valid ? "Checkpoint valid" : "Checkpoint invalid"}</strong>
                    <p className="page-subnote">
                      DB integrity {inspectPayload.db_integrity_check_result || "unknown"} · {formatBytes(inspectPayload.total_size_bytes)} · {inspectPayload.file_count} files · {inspectPayload.files_verified} verified
                    </p>
                    {inspectPayload.warning ? <p className="page-subnote">{inspectPayload.warning}</p> : null}
                    {inspectPayload.errors?.length > 0 ? (
                      <ul className="page-subnote">
                        {inspectPayload.errors.map((error) => (
                          <li key={error}>{error}</li>
                        ))}
                      </ul>
                    ) : null}
                    {inspectPayload.missing_files?.length > 0 ? (
                      <>
                        <p className="page-subnote">Missing files:</p>
                        <ul className="page-subnote">
                          {inspectPayload.missing_files.map((entry) => (
                            <li key={entry}>{entry}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {inspectPayload.hash_mismatches?.length > 0 ? (
                      <>
                        <p className="page-subnote">Hash mismatches:</p>
                        <ul className="page-subnote">
                          {inspectPayload.hash_mismatches.map((entry) => (
                            <li key={entry.relative_path}>{entry.relative_path}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}
          </section>

          <div className="admin-recovery-side-stack">
            <section className="settings-card admin-activity-card admin-recovery-card">
              <h2>Off-host protection</h2>
              <p className="page-subnote">
                Server-local checkpoints protect against bad scans, bad settings changes, and app mistakes. They do not protect against drive failure.
              </p>
              <p className="page-subnote">
                Copy checkpoint folders from <strong>{backupsDirectory || "backend/data/backups/"}</strong> to an external drive, NAS, or secure storage for off-host protection.
              </p>
              {selectedCheckpoint?.path ? (
                <p className="page-subnote">
                  Selected checkpoint path: <strong>{selectedCheckpoint.path}</strong>
                </p>
              ) : null}
            </section>

            <section className="settings-card admin-activity-card admin-recovery-card">
              <h2>Recent backup warnings</h2>
              {recentBackupWarnings.length > 0 ? (
                <div className="admin-list">
                  {recentBackupWarnings.map((event) => (
                    <div className="admin-list__row admin-list__row--card admin-recovery__warning-card" key={event.id}>
                      <div>
                        <strong>{event.action}</strong>
                        <p className="page-subnote">
                          {formatDate(event.created_at)} · {event.username || "unknown user"}
                        </p>
                        <p className="page-subnote">
                          {event.details?.auto_backup_error || "Backup warning recorded in the audit log."}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="page-subnote">No recent backup warnings are visible in the loaded audit log.</p>
              )}
            </section>
          </div>
        </div>
      </div>
    </div>
  );

  const exposurePlannerModal = exposurePlannerOpen ? (
    <div
      aria-labelledby="admin-exposure-planner-modal-title"
      aria-modal="true"
      className="browser-resume-modal exposure-planner-modal-shell"
      role="dialog"
    >
      <button
        aria-label="Close exposure mode manager"
        className="browser-resume-modal__backdrop exposure-planner-modal__backdrop"
        disabled={exposurePending}
        onClick={handleCloseExposurePlanner}
        type="button"
      />
      <div className="browser-resume-modal__card detail-info-modal__card exposure-planner-modal">
        <div className="detail-info-modal__header exposure-planner-modal__header">
          <div className="detail-info-modal__copy">
            <p className="detail-info-modal__eyebrow">Security</p>
            <h2 className="detail-info-modal__title" id="admin-exposure-planner-modal-title">
              Manage Exposure Mode
            </h2>
            <p className="page-subnote">
              Plan private or public exposure without changing the running server.
            </p>
          </div>
          <div className="admin-list__actions exposure-planner-modal__header-actions">
            <span className="status-pill">Draft only</span>
            <button
              aria-label="Close exposure mode manager"
              className="detail-info-modal__close"
              disabled={exposurePending}
              onClick={handleCloseExposurePlanner}
              type="button"
            >
              X
            </button>
          </div>
        </div>
        <div className="detail-info-modal__body exposure-planner-modal__body">
          <p className="form-error exposure-planner-modal__banner">
            Draft only — validation and pending drafts do not change runtime behavior, write env files, rotate the URL prefix, or disable users. Prepare only creates a manual plan and enables Maintenance Mode.
          </p>
          <div className="exposure-planner-grid">
            <div className="settings-card-stack">
              <section className="exposure-planner-modal__section">
                <h3>Current Status</h3>
                <div className="exposure-planner-status">
                  <StatusRow label="Exposure Mode" value={exposureModeStatus} />
                  <StatusRow label="Current request origin" value={formatExposureValue(exposureActive.current_request_origin)} />
                  <StatusRow label="Current public_app_origin" value={formatExposureValue(exposureActive.public_app_origin)} />
                  <StatusRow label="Current backend_origin" value={formatExposureValue(exposureActive.backend_origin)} />
                  <StatusRow label="Private-network flag" value={exposureActive.private_network_only ? "Enabled" : "Disabled"} />
                  <StatusRow label="Trusted proxy CIDRs" value={formatExposureValue(exposureActive.trusted_proxy_cidrs)} />
                  <StatusRow label="Cookie secure" value={formatExposureValue(exposureActive.cookie_secure)} />
                  <StatusRow label="URL prefix present" value={formatExposureValue(exposureActive.url_prefix_present)} />
                  <StatusRow label="Finalized profile" value={exposureFinalizedProfileStatus} />
                  <StatusRow label="Runtime posture" value={exposureRuntimePostureStatus} />
                  <StatusRow label="Pending draft" value={exposurePendingDraft ? "Exists" : "None"} />
                  <StatusRow label="Maintenance Mode" value={exposureMaintenanceLockStatus} />
                  <StatusRow label="Prepared switch" value={exposurePreparedSwitchStatus} />
                </div>
              </section>
              <section className="exposure-planner-modal__section">
                <h3>Maintenance Mode</h3>
                <div className="exposure-planner-status">
                  <StatusRow label="Status" value={exposureMaintenanceLockStatus} />
                  <StatusRow
                    label="Message shown to standard users"
                    value={formatExposureValue(exposureMaintenanceLock.message)}
                  />
                  <StatusRow label="Created by" value={formatExposureValue(exposureMaintenanceLock.created_by_username)} />
                  <StatusRow label="Created at" value={formatExposureValue(exposureMaintenanceLock.created_at)} />
                </div>
                <p className="page-subnote">
                  This standalone mode logs out non-admin users and blocks non-admin logins without disabling accounts.
                </p>
                <div className="settings-field exposure-maintenance-target">
                  <span>
                    <strong>Target state</strong>
                    <small>Select the intended Maintenance Mode state, then confirm with your current admin password.</small>
                  </span>
                  <ExposureSegmentedControl
                    ariaLabel="Maintenance Mode target"
                    className="exposure-maintenance-switch"
                    onChange={setExposureMaintenanceTargetMode}
                    options={EXPOSURE_MAINTENANCE_SEGMENTS}
                    value={exposureMaintenanceTargetValue}
                  />
                </div>
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>Maintenance Mode acknowledgement</strong>
                    <small>{EXPOSURE_MAINTENANCE_ACKNOWLEDGEMENT}</small>
                  </span>
                  <input
                    checked={exposureMaintenanceForm.acknowledgement}
                    onChange={(event) =>
                      setExposureMaintenanceForm((current) => ({
                        ...current,
                        acknowledgement: event.target.checked,
                      }))
                    }
                    type="checkbox"
                  />
                </label>
                <label className="settings-field">
                  <span>
                    <strong>Current admin password</strong>
                    <small>Required to enable or disable Maintenance Mode.</small>
                  </span>
                  <div className="exposure-secret-field">
                    <NonLoginSecretInput
                      autoComplete="new-password"
                      onChange={(event) =>
                        setExposureMaintenanceForm((current) => ({
                          ...current,
                          currentAdminPassword: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
                      value={exposureMaintenanceForm.currentAdminPassword}
                    />
                  </div>
                </label>
                <InlineFeedback feedback={exposureMaintenanceFeedback} />
                <div className="admin-list__actions exposure-maintenance-actions">
                  <button
                    className="primary-button exposure-maintenance-confirm-button"
                    disabled={exposurePending || exposureMaintenanceTargetMatchesCurrent}
                    onClick={() => handleSetExposureMaintenanceLock(exposureMaintenanceTargetEnabled)}
                    type="button"
                  >
                    {exposureMaintenanceActionLabel}
                  </button>
                  <span className="page-subnote exposure-maintenance-actions__hint">
                    {exposureMaintenanceTargetMatchesCurrent ? "Already matches the current state." : "Password confirmation required."}
                  </span>
                </div>
              </section>
              <section className="exposure-planner-modal__section">
                <h3>Prepare manual switch</h3>
                <div className="exposure-planner-status">
                  <StatusRow label="Prepared status" value={exposurePreparedSwitch ? "Prepared for manual apply" : "None"} />
                  <StatusRow label="Pending draft" value={exposurePendingDraft ? "Exists" : "Missing"} />
                  <StatusRow label="Maintenance Mode" value={exposurePrepareMaintenanceStatus} />
                  <StatusRow label="Validation status" value={formatExposureValue(exposureValidation.status || "ready")} />
                  <StatusRow label="Phase 4 verification" value={exposurePhase4VerificationStatus} />
                  <StatusRow label="URL prefix rotation" value="Manual only" />
                  <StatusRow label="Env writing" value="Manual only" />
                  <StatusRow label="Runtime effect" value="None yet" />
                  <StatusRow label="Takes effect" value="No" />
                  <StatusRow label="Activation" value="Not implemented" />
                  <StatusRow label="Prepared by" value={formatExposureValue(exposurePreparedSwitch?.prepared_by_username)} />
                  <StatusRow label="Prepared at" value={formatExposureValue(exposurePreparedSwitch?.prepared_at)} />
                </div>
                <p className="page-subnote">
                  Preparing will automatically enable Maintenance Mode and log out non-admin users. After manually applying env/reverse-proxy changes and restarting Elvern, return through the target address and verify in Phase 4.
                </p>
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>Prepare acknowledgement</strong>
                    <small>{EXPOSURE_PREPARE_ACKNOWLEDGEMENT}</small>
                  </span>
                  <input
                    checked={exposurePrepareForm.acknowledgement}
                    onChange={(event) =>
                      setExposurePrepareForm((current) => ({
                        ...current,
                        acknowledgement: event.target.checked,
                      }))
                    }
                    type="checkbox"
                  />
                </label>
                <label className="settings-field">
                  <span>
                    <strong>Current admin password</strong>
                    <small>Required to prepare or clear a prepared switch.</small>
                  </span>
                  <div className="exposure-secret-field">
                    <NonLoginSecretInput
                      autoComplete="new-password"
                      onChange={(event) =>
                        setExposurePrepareForm((current) => ({
                          ...current,
                          currentAdminPassword: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
                      value={exposurePrepareForm.currentAdminPassword}
                    />
                  </div>
                </label>
                <InlineFeedback feedback={exposurePrepareFeedback} />
                <div className="admin-list__actions">
                  <button
                    className="primary-button"
                    disabled={exposurePending}
                    onClick={handlePrepareExposureManualSwitch}
                    type="button"
                  >
                    Prepare manual switch
                  </button>
                  <button
                    className="ghost-button"
                    disabled={exposurePending || !exposurePreparedSwitch}
                    onClick={handleClearExposurePreparedSwitch}
                    type="button"
                  >
                    Clear prepared switch
                  </button>
                </div>
              </section>
              <section className="exposure-planner-modal__section">
                <h3>Verify prepared switch</h3>
                <div className="exposure-planner-status">
                  <StatusRow label="Prepared status" value={exposurePreparedSwitchStatusDetail} />
                  <StatusRow label="Verification required after restart" value={exposurePreparedSwitch?.verification_required ? "Yes" : "No"} />
                  <StatusRow label="Maintenance Mode" value={exposureMaintenanceLockStatus} />
                  <StatusRow label="Current request origin" value={formatExposureValue(exposureActive.current_request_origin)} />
                  <StatusRow label="Expected origin" value={formatExposureValue(exposureExpectedOrigin)} />
                  <StatusRow label="Verification status" value={exposureVerificationStatusLabel} />
                  <StatusRow label="Takes effect" value="No" />
                </div>
                <p className="page-subnote">
                  Run this only after manually applying env/proxy/DNS changes, manually restarting Elvern, and returning through the intended target address. Maintenance Mode remains under admin control.
                </p>
                <div className="exposure-verification-checklist">
                  <ExposureVerificationCheckGroup
                    checks={exposureCurrentAccessChecks}
                    emptyText="No current-access verification checks have run yet."
                    title="Current access"
                  />
                  <ExposureVerificationCheckGroup
                    checks={exposureRuntimeSettingChecks}
                    emptyText="No runtime setting verification checks have run yet."
                    title="Runtime settings"
                  />
                  <ExposureVerificationCheckGroup
                    checks={exposureSafetyStateChecks}
                    emptyText="No safety-state verification checks have run yet."
                    title="Safety state"
                  />
                  <ExposureVerificationCheckGroup
                    checks={exposureWarningChecks}
                    emptyText="No blocking checks or warnings from the last verification."
                    title="Warnings"
                  />
                </div>
                {exposureVerificationErrors.length > 0 ? (
                  <div className="exposure-message-group">
                    <strong>Verification errors</strong>
                    <ul>
                      {exposureVerificationErrors.map((entry) => <li key={`verification-error-${entry}`}>{entry}</li>)}
                    </ul>
                  </div>
                ) : null}
                {exposureVerificationWarnings.length > 0 ? (
                  <div className="exposure-message-group">
                    <strong>Verification warnings</strong>
                    <ul>
                      {exposureVerificationWarnings.map((entry) => <li key={`verification-warning-${entry}`}>{entry}</li>)}
                    </ul>
                  </div>
                ) : null}
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>Verification acknowledgement</strong>
                    <small>{EXPOSURE_VERIFY_ACKNOWLEDGEMENT}</small>
                  </span>
                  <input
                    checked={exposureVerifyForm.acknowledgement}
                    disabled={!exposurePreparedSwitch}
                    onChange={(event) =>
                      setExposureVerifyForm((current) => ({
                        ...current,
                        acknowledgement: event.target.checked,
                      }))
                    }
                    type="checkbox"
                  />
                </label>
                <label className="settings-field">
                  <span>
                    <strong>Current admin password</strong>
                    <small>Required to verify the prepared manual switch.</small>
                  </span>
                  <div className="exposure-secret-field">
                    <NonLoginSecretInput
                      autoComplete="new-password"
                      disabled={!exposurePreparedSwitch}
                      onChange={(event) =>
                        setExposureVerifyForm((current) => ({
                          ...current,
                          currentAdminPassword: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
                      value={exposureVerifyForm.currentAdminPassword}
                    />
                  </div>
                </label>
                <InlineFeedback feedback={exposureVerifyFeedback} />
                <div className="admin-list__actions">
                  <button
                    className="primary-button"
                    disabled={exposurePending || !exposurePreparedSwitch}
                    onClick={handleVerifyExposurePreparedSwitch}
                    type="button"
                  >
                    Verify prepared switch
                  </button>
                  {!exposurePreparedSwitch ? (
                    <span className="page-subnote">Prepare a manual switch before verification.</span>
                  ) : null}
                </div>
              </section>
              <section className="exposure-planner-modal__section">
                <h3>Finalize verified profile</h3>
                <div className="exposure-planner-status">
                  <StatusRow label="Verified mode" value={exposureFinalizeProfileLabel} />
                  <StatusRow label="Origin" value={formatExposureValue(exposureFinalizeOrigin)} />
                  <StatusRow label="Verification status" value={exposureFinalizeVerificationStatus} />
                  <StatusRow label="Maintenance Mode" value="Remains under admin control" />
                  <StatusRow label="Takes effect" value="No runtime settings are changed by this record" />
                </div>
                <p className="page-subnote">Maintenance Mode remains on until an admin turns it off.</p>
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>Finalize acknowledgement</strong>
                    <small>{EXPOSURE_FINALIZE_ACKNOWLEDGEMENT}</small>
                  </span>
                  <input
                    checked={exposureFinalizeForm.acknowledgement}
                    disabled={!exposureCanFinalizeProfile}
                    onChange={(event) =>
                      setExposureFinalizeForm((current) => ({
                        ...current,
                        acknowledgement: event.target.checked,
                      }))
                    }
                    type="checkbox"
                  />
                </label>
                <label className="settings-field">
                  <span>
                    <strong>Current admin password</strong>
                    <small>Required to finalize the verified exposure profile.</small>
                  </span>
                  <div className="exposure-secret-field">
                    <NonLoginSecretInput
                      autoComplete="new-password"
                      disabled={!exposureCanFinalizeProfile}
                      onChange={(event) =>
                        setExposureFinalizeForm((current) => ({
                          ...current,
                          currentAdminPassword: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
                      value={exposureFinalizeForm.currentAdminPassword}
                    />
                  </div>
                </label>
                <InlineFeedback feedback={exposureFinalizeFeedback} />
                <div className="admin-list__actions">
                  <button
                    className="primary-button"
                    disabled={exposurePending || !exposureCanFinalizeProfile}
                    onClick={handleFinalizeExposureProfile}
                    type="button"
                  >
                    Finalize verified profile
                  </button>
                  {!exposureCanFinalizeProfile ? (
                    <span className="page-subnote">Verify a prepared switch first.</span>
                  ) : null}
                </div>
              </section>
              <section className="exposure-planner-modal__section">
                <h3>Desired Mode</h3>
                <div className="settings-field exposure-mode-selector">
                  <span>
                    <strong>Planner selector</strong>
                    <small>{exposureModeLabel(exposureDraft)}. This selector does not switch runtime exposure.</small>
                  </span>
                  <ExposureSegmentedControl
                    ariaLabel="Exposure planner mode"
                    className="exposure-mode-segmented"
                    onChange={(nextMode) =>
                      setExposureDraft((current) => ({
                        ...current,
                        selectedMode: nextMode,
                        reverseProxyProvider: nextMode === "public_direct_ip" ? "manual_other" : current.reverseProxyProvider,
                      }))
                    }
                    options={EXPOSURE_MODE_SEGMENTS}
                    value={exposureDraft.selectedMode}
                  />
                </div>
                {exposureDraft.selectedMode === "private" ? (
                  <label className="settings-field">
                    <span>
                      <strong>Private origin</strong>
                      <small>Optional tailnet, LAN, or private DNS address.</small>
                    </span>
                    <input
                      onChange={(event) => setExposureDraft((current) => ({ ...current, privateOrigin: event.target.value }))}
                      placeholder="https://machine.tailnet-name.ts.net or http://192.168.1.10:4173"
                      type="text"
                      value={exposureDraft.privateOrigin}
                    />
                  </label>
                ) : (
                  <>
                    <label className="settings-field">
                      <span>
                        <strong>Public origin</strong>
                        <small>
                          {exposureDraft.selectedMode === "public_direct_ip"
                            ? "Direct public IP is allowed but not recommended."
                            : "Use a purchased domain with HTTPS. No public Tailscale option is offered."}
                        </small>
                      </span>
                      <input
                        onChange={(event) => setExposureDraft((current) => ({ ...current, publicOrigin: event.target.value }))}
                        placeholder={exposureDraft.selectedMode === "public_direct_ip" ? "http://203.0.113.10:4173" : "https://media.example.com"}
                        type="text"
                        value={exposureDraft.publicOrigin}
                      />
                    </label>
                    <label className="settings-field">
                      <span>
                        <strong>Provider</strong>
                        <small>Manual planning notes only; Elvern will not configure the proxy.</small>
                      </span>
                      <select
                        onChange={(event) => setExposureDraft((current) => ({ ...current, reverseProxyProvider: event.target.value }))}
                        value={exposureDraft.reverseProxyProvider}
                      >
                        {Object.entries(EXPOSURE_PROVIDER_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
              </section>
              <section className="exposure-planner-modal__section">
                <h3>Confirmation</h3>
                {exposureDraft.selectedMode === "public_direct_ip" ? (
                  <label className="settings-toggle settings-toggle--compact exposure-planner-warning">
                    <span>
                      <strong>Not recommended</strong>
                      <small>I understand direct public IP exposure is not recommended.</small>
                    </span>
                    <input
                      checked={exposureDraft.directIpNotRecommendedAcknowledgement}
                      onChange={(event) =>
                        setExposureDraft((current) => ({
                          ...current,
                          directIpNotRecommendedAcknowledgement: event.target.checked,
                        }))
                      }
                      type="checkbox"
                    />
                  </label>
                ) : null}
                <label className="settings-toggle settings-toggle--compact">
                  <span>
                    <strong>Phase 1 acknowledgement</strong>
                    <small>This pending draft does not take effect until a later activation phase.</small>
                  </span>
                  <input
                    checked={exposureDraft.acknowledgement}
                    onChange={(event) => setExposureDraft((current) => ({ ...current, acknowledgement: event.target.checked }))}
                    type="checkbox"
                  />
                </label>
                <label className="settings-field">
                  <span>
                    <strong>Current admin password</strong>
                    <small>Required only to save a pending draft.</small>
                  </span>
                  <div className="exposure-secret-field">
                    <NonLoginSecretInput
                      autoComplete="new-password"
                      onChange={(event) => setExposureDraft((current) => ({ ...current, currentAdminPassword: event.target.value }))}
                      placeholder="Current admin password"
                      value={exposureDraft.currentAdminPassword}
                    />
                  </div>
                </label>
                <InlineFeedback feedback={exposureFeedback} />
                <div className="admin-list__actions">
                  <button className="primary-button" disabled={exposurePending} onClick={handleValidateExposurePlan} type="button">
                    {exposurePending ? "Working..." : "Validate plan"}
                  </button>
                  <button className="ghost-button" disabled={exposurePending} onClick={handleSaveExposureDraft} type="button">
                    Save pending draft
                  </button>
                  <button className="ghost-button" disabled={exposurePending || !exposurePendingDraft} onClick={handleClearExposureDraft} type="button">
                    Clear pending draft
                  </button>
                </div>
              </section>
            </div>
            <div className="settings-card-stack exposure-results-stack">
              <section className={`exposure-results-card exposure-validation-summary exposure-validation-summary--${exposureValidationStatus}`}>
                <div className="settings-inline-header">
                  <div>
                    <h3>Validation summary</h3>
                    <p className="page-subnote">{exposureValidationSummary}</p>
                  </div>
                  <span className={`status-pill exposure-validation-status-pill exposure-validation-status-pill--${exposureValidationStatus}`}>
                    {exposureValidationStatusLabel}
                  </span>
                </div>
                <div className="exposure-compact-status">
                  <StatusRow label="Status" value={formatExposureValue(exposureValidationStatus)} />
                  <StatusRow label="Phase 4 verification" value={exposurePhase4VerificationStatus} />
                  <StatusRow label="Takes effect" value="No" />
                </div>
              </section>
              <section className="exposure-results-card exposure-validation-messages">
                <h3>Warnings / errors</h3>
                {exposureErrors.length > 0 ? (
                  <div className="exposure-message-group">
                    <strong>Errors</strong>
                    <ul>
                      {exposureErrors.map((entry) => <li key={`exposure-error-${entry}`}>{entry}</li>)}
                    </ul>
                  </div>
                ) : null}
                {exposureWarnings.length > 0 ? (
                  <div className="exposure-message-group">
                    <strong>Warnings</strong>
                    <ul>
                      {exposureWarnings.map((entry) => <li key={`exposure-warning-${entry}`}>{entry}</li>)}
                    </ul>
                  </div>
                ) : null}
                {exposureErrors.length === 0 && exposureWarnings.length === 0 ? (
                  <p className="page-subnote">No blocking errors.</p>
                ) : null}
              </section>
              {exposureChecks.length > 0 ? (
                <section className="exposure-results-card exposure-checks-card">
                  <h3>Checks</h3>
                  <div className="exposure-checks-list">
                    {exposureChecks.map((entry) => (
                      <div className="exposure-check-row" key={`exposure-check-${entry.name || entry.detail}`}>
                        <span className={`status-pill exposure-check-pill exposure-check-pill--${entry.status || "info"}`}>
                          {entry.status || "info"}
                        </span>
                        <span>{entry.detail}</span>
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}
              <section className="exposure-results-card exposure-prepared-switch-card">
                <div className="settings-inline-header">
                  <div>
                    <h3>Prepared switch</h3>
                    <p className="page-subnote">Prepared for manual apply only. No env files are written.</p>
                  </div>
                  <span className={`status-pill exposure-prepared-switch-pill${exposurePreparedSwitch ? " exposure-prepared-switch-pill--ready" : ""}`}>
                    {exposurePreparedSwitch ? "Prepared for manual apply" : "None"}
                  </span>
                </div>
                <div className="exposure-compact-status">
                  <StatusRow
                    label="Maintenance Mode auto-enabled"
                    value={exposurePreparedSwitch?.maintenance_mode_auto_enabled ? "Yes" : "No"}
                  />
                  <StatusRow
                    label="Verification required after restart"
                    value={exposurePreparedSwitch?.verification_required ? "Yes" : "No"}
                  />
                  <StatusRow
                    label="Current origin match"
                    value={
                      exposurePreparedSwitch?.current_origin_match_required_in_phase === "phase_4_verification"
                        ? "Checked in Phase 4"
                        : exposureCurrentOriginMatchStatus
                    }
                  />
                  <StatusRow label="URL prefix rotation" value="Manual only" />
                  <StatusRow label="Env writing" value="Manual only" />
                  <StatusRow label="Runtime effect" value="None yet" />
                  <StatusRow label="Takes effect" value="No" />
                </div>
                {exposurePreparedEnvBlock ? (
                  <div className="exposure-prepared-block">
                    <div className="settings-inline-header">
                      <h4>Env suggestions</h4>
                      <button
                        className="ghost-button ghost-button--inline"
                        onClick={() => {
                          navigator.clipboard?.writeText(exposurePreparedEnvBlock);
                          setExposurePrepareFeedback({ tone: "success", text: "Env suggestions copied." });
                        }}
                        type="button"
                      >
                        Copy env suggestions
                      </button>
                    </div>
                    <textarea
                      className="exposure-prepared-env-block"
                      readOnly
                      rows={Math.min(10, Math.max(4, exposurePreparedEnvBlock.split("\n").length))}
                      value={exposurePreparedEnvBlock}
                    />
                  </div>
                ) : (
                  <p className="page-subnote">No prepared plan yet.</p>
                )}
                {exposurePreparedManualSteps.length > 0 ? (
                  <div className="exposure-prepared-block">
                    <h4>Manual restart / reverse proxy checklist</h4>
                    <ul>
                      {exposurePreparedManualSteps.map((entry) => <li key={`prepared-step-${entry}`}>{entry}</li>)}
                    </ul>
                  </div>
                ) : null}
              </section>
              <section className="exposure-results-card exposure-plan-notes">
                <h3>Manual plan</h3>
                <details open>
                  <summary>Manual Steps</summary>
                  <ul>
                    {exposureManualSteps.map((entry) => <li key={`exposure-step-${entry}`}>{entry}</li>)}
                  </ul>
                </details>
                <details>
                  <summary>Env Suggestions</summary>
                  <ul>
                    {exposureEnvSuggestions.map((entry) => <li key={`exposure-env-${entry.name}`}>{formatExposureSuggestion(entry)}</li>)}
                  </ul>
                </details>
                <details>
                  <summary>Reverse Proxy Notes</summary>
                  <ul>
                    {exposureReverseProxyNotes.map((entry) => <li key={`exposure-proxy-${entry}`}>{entry}</li>)}
                  </ul>
                </details>
                <details>
                  <summary>Activation Notes</summary>
                  <ul>
                    {exposureActivationNotes.map((entry) => <li key={`exposure-activation-${entry}`}>{entry}</li>)}
                  </ul>
                </details>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>
  ) : null;

  const adminConfirmModalConfig = adminConfirmModal.type === "invite-delete"
    ? {
        title: "Delete invite code?",
        body: "This invite code will be revoked immediately and can no longer be used.",
        confirmLabel: "Delete",
        danger: true,
      }
    : {
        title: "Dismiss password help request?",
        body: "This request will be removed from the admin list. The user can submit another request later.",
        confirmLabel: "Dismiss",
        danger: true,
      };
  const ownTotpSecureModal = ownTotpModal.mode ? (
    <div
      aria-labelledby="control-center-own-totp-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <button
        aria-label="Close two-factor authentication dialog"
        className="browser-resume-modal__backdrop"
        disabled={ownTotpModal.pending}
        onClick={closeOwnTotpModal}
        type="button"
      />
      <form
        className="browser-resume-modal__card detail-info-modal__card control-center-secure-modal"
        onSubmit={ownTotpModal.mode === "regenerate"
          ? handleRegenerateOwnRecoveryCodes
          : handleDisableOwnTotp}
        ref={ownTotpDialogRef}
      >
        <div className="detail-info-modal__header">
          <div className="detail-info-modal__copy">
            <p className="detail-info-modal__eyebrow">Security</p>
            <h2 id="control-center-own-totp-title">
              {ownTotpModal.mode === "regenerate" ? "Regenerate recovery codes" : "Disable 2FA"}
            </h2>
          </div>
          <button
            aria-label="Close two-factor authentication dialog"
            className="detail-info-modal__close"
            disabled={ownTotpModal.pending}
            onClick={closeOwnTotpModal}
            type="button"
          >
            X
          </button>
        </div>
        <div className="detail-info-modal__body">
          {ownTotpModal.recoveryCodes.length > 0 ? (
            <>
              <p className="page-subnote">Save these replacement codes now. They are shown only once.</p>
              <pre className="control-center-recovery-codes">{ownTotpModal.recoveryCodes.join("\n")}</pre>
              {ownTotpModal.copyFeedback ? <p className="action-feedback" role="status">{ownTotpModal.copyFeedback}</p> : null}
            </>
          ) : (
            <>
              <label className="settings-field">
                <span><strong>Current password</strong></span>
                <NonLoginSecretInput
                  disabled={ownTotpModal.pending}
                  onChange={(event) => setOwnTotpModal((current) => ({ ...current, password: event.target.value, error: "" }))}
                  purpose={`own-totp-${ownTotpModal.mode}-password`}
                  value={ownTotpModal.password}
                />
              </label>
              <label className="settings-field">
                <span>
                  <strong>{ownTotpModal.mode === "regenerate" ? "Authenticator code" : "Authenticator or recovery code"}</strong>
                </span>
                <input
                  autoComplete="one-time-code"
                  disabled={ownTotpModal.pending}
                  inputMode={ownTotpModal.mode === "regenerate" ? "numeric" : "text"}
                  maxLength={ownTotpModal.mode === "regenerate" ? 6 : 64}
                  onChange={(event) => setOwnTotpModal((current) => ({ ...current, code: event.target.value, error: "" }))}
                  value={ownTotpModal.code}
                />
              </label>
            </>
          )}
          {ownTotpModal.error ? <p className="action-feedback action-feedback--error" role="alert">{ownTotpModal.error}</p> : null}
        </div>
        <div className="browser-resume-modal__actions">
          {ownTotpModal.recoveryCodes.length > 0 ? (
            <button className="primary-button" onClick={handleCopyOwnRecoveryCodes} type="button">Copy codes</button>
          ) : (
            <button
              className={ownTotpModal.mode === "disable" ? "ghost-button ghost-button--danger" : "primary-button"}
              disabled={ownTotpModal.pending}
              type="submit"
            >
              {ownTotpModal.pending
                ? "Working..."
                : ownTotpModal.mode === "regenerate" ? "Regenerate" : "Disable 2FA"}
            </button>
          )}
          <button className="ghost-button" disabled={ownTotpModal.pending} onClick={closeOwnTotpModal} type="button">
            {ownTotpModal.recoveryCodes.length > 0 ? "Done" : "Cancel"}
          </button>
        </div>
      </form>
    </div>
  ) : null;

  return (
    <section className="page-section">
      {!desktopControlCenter ? (
        <div className="admin-nav-card" aria-label="Admin sections">
        <div className="admin-nav-card__actions">
          {ADMIN_SECTIONS.map((section) => {
            const isActive = activeSection === section.key;
            const isExpanded = expandedSection === section.key;
            return (
              <button
                key={section.key}
                aria-pressed={isActive}
                className={[
                  "admin-nav-card__button",
                  isActive ? "admin-nav-card__button--active" : "",
                  isExpanded ? "admin-nav-card__button--expanded" : "",
                ].filter(Boolean).join(" ")}
                onClick={() => handleSectionClick(section.key)}
                type="button"
              >
                <span className="admin-nav-card__icon">
                  <AdminSectionIcon name={section.icon} />
                </span>
                <span className="admin-nav-card__label">{section.label}</span>
              </button>
            );
          })}
        </div>
        {activeSection === "panel" ? (
          <RefreshSweepButton
            className="ghost-button ghost-button--inline admin-nav-card__rescan"
            disabled={statusRefreshPending}
            onClick={handleRefreshStatus}
            type="button"
          >
            {statusRefreshPending ? "Refreshing..." : "Refresh status"}
          </RefreshSweepButton>
        ) : null}
          <DesktopBackToLibraryButton className="admin-nav-card__back" />
        </div>
      ) : null}

      <FeedbackBanner banner={banner} />

      {statusPayload ? (
        <div className="admin-section-stack">
		          {desktopAdminTab === "overview" ? adminOverviewSummary : null}
		          {desktopAdminTab === "security" ? adminSecurityKpis : null}
		              {activeSection === "panel" ? (
	                <>
	                  {usersCard}
	                </>
	              ) : null}

          {activeSection === "security" || (desktopControlCenter && desktopAdminTab === "users-invites")
            ? securitySection
            : null}

          {activeSection === "logs" ? logsSection : null}

          {activeSection === "recovery" ? recoverySection : null}

	          {activeSection === "panel" ? (
	              <section className="settings-card">
	                <div className="settings-inline-header admin-invite-code-header">
	                  <button
	                    aria-controls="admin-invite-code-list"
	                    aria-expanded={inviteCodesExpanded}
	                    className="admin-invite-code-header__summary"
	                    onClick={toggleInviteCodesExpanded}
	                    type="button"
	                  >
	                    <div>
	                      <h2>Generate invite code</h2>
	                      <p className="page-subnote">
	                        Codes expire after 30 minutes and can be used once. Invite codes are shown only when generated.
	                        Copy them now; they cannot be revealed again after this page is closed.
	                      </p>
	                    </div>
	                  </button>
	                  <button
	                    className="primary-button"
	                    disabled={invitePending}
	                    onClick={() => {
                        setInviteAssignedAge(18);
                        setInviteAgeModalOpen(true);
                      }}
	                    type="button"
	                  >
	                    {invitePending ? "Generating..." : "Generate invite code"}
	                  </button>
	                </div>
                {inviteCodesExpanded ? (
                  <div className="admin-list admin-list--dense admin-invite-code-list" id="admin-invite-code-list">
                    {inviteCodes.length > 0 ? (
                      inviteCodes.map((inviteCode) => {
                        const isRevealed = revealedInviteIds[inviteCode.id] === true;
                        const expiresAtMs = Date.parse(inviteCode.expires_at);
                        const statusKey = inviteCode.used_at
                          ? "used"
                          : (Number.isFinite(expiresAtMs) && expiresAtMs <= Date.now() ? "expired" : "valid");
                        const statusLabel = statusKey === "used"
                          ? "Used"
                          : (statusKey === "expired" ? "Expired" : "Valid");
                        return (
                          <div className="admin-list__row admin-list__row--card admin-invite-code-row" key={inviteCode.id}>
                            <div className="admin-invite-code-row__body">
                              <div className="admin-invite-code-row__line">
                                <div className="admin-invite-code-field">
                                  <input
                                    aria-label="Invite code"
                                    autoComplete="one-time-code"
                                    data-1p-ignore="true"
                                    data-bwignore="true"
                                    data-lpignore="true"
                                    id={`elvern-invite-code-${inviteCode.id}`}
                                    name={`elvern-invite-code-${inviteCode.id}`}
                                    readOnly
                                    type={inviteCode.code && !isRevealed ? "password" : "text"}
                                    value={inviteCode.code || "Code is only shown when generated"}
                                  />
                                  <button
                                    aria-label={isRevealed ? "Hide invite code" : "Reveal invite code"}
                                    aria-pressed={isRevealed}
                                    className="admin-invite-code-field__toggle"
                                    disabled={!inviteCode.code}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      setRevealedInviteIds((current) => ({ ...current, [inviteCode.id]: !current[inviteCode.id] }));
                                    }}
                                    type="button"
                                  >
                                    <InviteCodeEyeIcon struck={!isRevealed} />
                                  </button>
                                </div>
                                <span className={`admin-invite-status admin-invite-status--${statusKey}`}>
                                  {statusLabel}
                                </span>
                              </div>
                              <p className="page-subnote">
                                Expires {formatDate(inviteCode.expires_at)}
                                {" · "}
                                Age credential {inviteCode.assigned_age_display || formatAgeCredential(inviteCode.assigned_age)}
                                {inviteCode.used_at ? ` · used ${formatDate(inviteCode.used_at)}` : ""}
                              </p>
                            </div>
                            <div className="admin-list__actions">
                              <button
                                className="ghost-button ghost-button--inline"
                                disabled={!inviteCode.code}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  navigator.clipboard?.writeText(inviteCode.code);
                                }}
                                type="button"
                              >
                                Copy
                              </button>
                              <button
                                aria-label="Delete invite code"
                                className="ghost-button ghost-button--danger"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  openInviteCodeDeleteModal(inviteCode);
                                }}
                                type="button"
                              >
                                Delete
                              </button>
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <p className="page-subnote">No visible invite codes.</p>
                    )}
                  </div>
                ) : null}
	              </section>
	          ) : null}
        </div>
      ) : null}
      {inviteAgeModalOpen ? (
        <div
          aria-labelledby="admin-invite-age-modal-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <div
            aria-hidden="true"
            className="browser-resume-modal__backdrop"
            onClick={() => {
              if (!invitePending) {
                setInviteAgeModalOpen(false);
              }
            }}
          />
          <form
            className="browser-resume-modal__card detail-info-modal__card admin-invite-age-modal"
            onSubmit={handleGenerateInviteCode}
          >
            <div className="detail-info-modal__copy">
              <p className="eyebrow detail-info-modal__eyebrow">Invite code</p>
              <h2 className="detail-info-modal__title" id="admin-invite-age-modal-title">Assign age credential</h2>
              <p className="page-subnote">The new account created with this code receives this age credential.</p>
            </div>
            <label className="admin-inline-form">
              Age credential
              <select
                className="admin-select"
                disabled={invitePending}
                onChange={(event) => setInviteAssignedAge(Number(event.target.value))}
                value={inviteAssignedAge}
              >
                {AGE_CREDENTIAL_OPTIONS.map((age) => (
                  <option key={age} value={age}>{formatAgeCredential(age)}</option>
                ))}
              </select>
            </label>
            <div className="admin-list__actions">
              <button className="primary-button" disabled={invitePending} type="submit">
                {invitePending ? "Generating..." : "Generate invite code"}
              </button>
              <button
                className="ghost-button"
                disabled={invitePending}
                onClick={() => setInviteAgeModalOpen(false)}
                type="button"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {userActionsModal}
      {exposurePlannerModal}
      {terminateWorkerConfirmationModal}
      {dismissPlaybackStatusConfirmationModal}
      {diagnosticIdPopup}
      <AdminConfirmModal
        body={adminConfirmModalConfig.body}
        confirmLabel={adminConfirmModalConfig.confirmLabel}
        danger={adminConfirmModalConfig.danger}
        error={adminConfirmModal.error}
        onCancel={closeAdminConfirmModal}
        onConfirm={handleConfirmAdminModal}
        open={Boolean(adminConfirmModal.type)}
        pending={adminConfirmModal.pending}
        title={adminConfirmModalConfig.title}
      />
      {backupCreateModal.open ? (
        <div className="browser-resume-modal" role="presentation">
          <button
            aria-label="Close backup creation"
            className="browser-resume-modal__backdrop"
            onClick={closeCreateBackupModal}
            type="button"
          />
          <form
            className="browser-resume-modal__card detail-info-modal__card admin-diagnostic-id-modal"
            onSubmit={(event) => {
              event.preventDefault();
              handleConfirmCreateBackup();
            }}
          >
            <div className="detail-info-modal__header">
              <div className="detail-info-modal__copy">
                <p className="detail-info-modal__eyebrow">Backup</p>
                <h2>Create encrypted backup</h2>
              </div>
              <button
                aria-label="Close backup creation"
                className="detail-info-modal__close"
                disabled={createBackupPending}
                onClick={closeCreateBackupModal}
                type="button"
              >
                X
              </button>
            </div>
            <div className="detail-info-modal__body">
              <p className="page-subnote">
                Choose a passphrase to encrypt this backup. You will need this passphrase to inspect or restore later.
                Elvern cannot recover lost passphrases.
              </p>
              <p className="form-error">
                Manual backups may contain secrets such as env values, OAuth tokens, session-related secrets, and database contents. Do not commit or share them.
              </p>
              <NonLoginSecretInput
                autoComplete="new-password"
                disabled={createBackupPending}
                onChange={(event) =>
                  setBackupCreateModal((current) => ({
                    ...current,
                    passphrase: event.target.value,
                    error: "",
                  }))
                }
                placeholder="Backup passphrase"
                purpose="backup-passphrase"
                value={backupCreateModal.passphrase}
              />
              <NonLoginSecretInput
                autoComplete="new-password"
                disabled={createBackupPending}
                onChange={(event) =>
                  setBackupCreateModal((current) => ({
                    ...current,
                    confirmPassphrase: event.target.value,
                    error: "",
                  }))
                }
                placeholder="Confirm passphrase"
                purpose="backup-passphrase-confirm"
                value={backupCreateModal.confirmPassphrase}
              />
              {backupCreateModal.error ? (
                <p className="action-feedback action-feedback--error" role="alert">
                  {backupCreateModal.error}
                </p>
              ) : null}
            </div>
            <div className="browser-resume-modal__actions">
              <button className="primary-button" disabled={createBackupPending} type="submit">
                {createBackupPending ? "Creating..." : "Create encrypted backup"}
              </button>
              <button className="ghost-button" disabled={createBackupPending} onClick={closeCreateBackupModal} type="button">
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {backupPassphraseModal.mode ? (
        <div className="browser-resume-modal" role="presentation">
          <button
            aria-label="Close backup passphrase"
            className="browser-resume-modal__backdrop"
            onClick={closeBackupPassphraseModal}
            type="button"
          />
          <form
            className="browser-resume-modal__card detail-info-modal__card admin-diagnostic-id-modal"
            onSubmit={(event) => {
              event.preventDefault();
              handleSubmitBackupPassphrase();
            }}
          >
            <div className="detail-info-modal__header">
              <div className="detail-info-modal__copy">
                <p className="detail-info-modal__eyebrow">Backup</p>
                <h2>{backupPassphraseModal.mode === "inspect" ? "Inspect backup" : "Preview recovery"}</h2>
              </div>
              <button
                aria-label="Close backup passphrase"
                className="detail-info-modal__close"
                disabled={inspectPending || restorePlanPending}
                onClick={closeBackupPassphraseModal}
                type="button"
              >
                X
              </button>
            </div>
            <div className="detail-info-modal__body">
              <p className="page-subnote">
                This backup is encrypted. Enter the passphrase used when creating it.
              </p>
              <NonLoginSecretInput
                autoComplete="new-password"
                disabled={inspectPending || restorePlanPending}
                onChange={(event) =>
                  setBackupPassphraseModal((current) => ({
                    ...current,
                    passphrase: event.target.value,
                    error: "",
                  }))
                }
                placeholder="Backup passphrase"
                purpose="backup-open-passphrase"
                value={backupPassphraseModal.passphrase}
              />
              {backupPassphraseModal.error ? (
                <p className="action-feedback action-feedback--error" role="alert">
                  {backupPassphraseModal.error}
                </p>
              ) : null}
            </div>
            <div className="browser-resume-modal__actions">
              <button className="primary-button" disabled={inspectPending || restorePlanPending} type="submit">
                {backupPassphraseModal.mode === "inspect"
                  ? (inspectPending ? "Inspecting..." : "Inspect")
                  : (restorePlanPending ? "Previewing..." : "Preview recovery")}
              </button>
              <button
                className="ghost-button"
                disabled={inspectPending || restorePlanPending}
                onClick={closeBackupPassphraseModal}
                type="button"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {totpDisableUserModal.userId ? (
        <div className="browser-resume-modal" role="presentation">
          <button
            aria-label="Close 2FA disable confirmation"
            className="browser-resume-modal__backdrop"
            onClick={closeTotpDisableUserModal}
            type="button"
          />
          <form
            className="browser-resume-modal__card detail-info-modal__card admin-diagnostic-id-modal"
            onSubmit={handleConfirmDisableUserTotp}
          >
            <div className="detail-info-modal__header">
              <div className="detail-info-modal__copy">
                <p className="detail-info-modal__eyebrow">Security</p>
                <h2>Disable 2FA</h2>
              </div>
              <button
                aria-label="Close 2FA disable confirmation"
                className="detail-info-modal__close"
                disabled={totpDisableUserModal.pending}
                onClick={closeTotpDisableUserModal}
                type="button"
              >
                X
              </button>
            </div>
            <div className="detail-info-modal__body">
              <p className="page-subnote">
                This will remove the authenticator pairing for {totpDisableUserModal.username}
                and revoke that user's active sessions.
              </p>
              <p className="page-subnote">Confirm with your current admin password to proceed.</p>
              <NonLoginSecretInput
                autoComplete="new-password"
                disabled={totpDisableUserModal.pending}
                onChange={(event) =>
                  setTotpDisableUserModal((current) => ({
                    ...current,
                    currentAdminPassword: event.target.value,
                    error: "",
                  }))
                }
                placeholder="Current admin password"
                purpose="user-2fa-disable-reauth"
                value={totpDisableUserModal.currentAdminPassword}
              />
              {totpDisableUserModal.error ? (
                <p className="action-feedback action-feedback--error" role="alert">
                  {totpDisableUserModal.error}
                </p>
              ) : null}
            </div>
            <div className="browser-resume-modal__actions">
              <button className="ghost-button ghost-button--danger" disabled={totpDisableUserModal.pending} type="submit">
                {totpDisableUserModal.pending ? "Disabling..." : "Disable 2FA"}
              </button>
              <button
                className="ghost-button"
                disabled={totpDisableUserModal.pending}
                onClick={closeTotpDisableUserModal}
                type="button"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {urlPrefixRotateModal.open ? (
        <div className="browser-resume-modal" role="presentation">
          <button
            aria-label="Close URL prefix rotation confirmation"
            className="browser-resume-modal__backdrop"
            disabled={urlPrefixRotateModal.pending}
            onClick={closeUrlPrefixRotateModal}
            type="button"
          />
          <form
            className="browser-resume-modal__card detail-info-modal__card admin-diagnostic-id-modal"
            onSubmit={handleRotateUrlPrefix}
          >
            <div className="detail-info-modal__header">
              <div className="detail-info-modal__copy">
                <p className="detail-info-modal__eyebrow">Security</p>
                <h2>Rotate URL prefix</h2>
              </div>
              <button
                aria-label="Close URL prefix rotation confirmation"
                className="detail-info-modal__close"
                disabled={urlPrefixRotateModal.pending}
                onClick={closeUrlPrefixRotateModal}
                type="button"
              >
                X
              </button>
            </div>
            <div className="detail-info-modal__body">
              <p className="page-subnote">
                This will generate a new random URL prefix. All current sessions will be revoked, including yours.
                All bookmarks and share links pointing to the old prefix will stop working.
              </p>
              <p className="page-subnote">Confirm with your admin password to proceed.</p>
              <NonLoginSecretInput
                autoComplete="new-password"
                disabled={urlPrefixRotateModal.pending}
                onChange={(event) =>
                  setUrlPrefixRotateModal((current) => ({
                    ...current,
                    currentAdminPassword: event.target.value,
                    error: "",
                  }))
                }
                placeholder="Current admin password"
                purpose="url-prefix-rotate-reauth"
                value={urlPrefixRotateModal.currentAdminPassword}
              />
              {urlPrefixRotateModal.error ? (
                <p className="action-feedback action-feedback--error" role="alert">
                  {urlPrefixRotateModal.error}
                </p>
              ) : null}
            </div>
            <div className="browser-resume-modal__actions">
              <button className="primary-button" disabled={urlPrefixRotateModal.pending} type="submit">
                {urlPrefixRotateModal.pending ? "Rotating..." : "Rotate now"}
              </button>
              <button
                className="ghost-button"
                disabled={urlPrefixRotateModal.pending}
                onClick={closeUrlPrefixRotateModal}
                type="button"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {ownTotpSecureModal}
    </section>
  );
}
