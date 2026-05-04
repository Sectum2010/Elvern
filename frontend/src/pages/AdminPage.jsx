import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LoadingView } from "../components/LoadingView";
import { PasswordInput } from "../components/PasswordInput";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";
import {
  buildPlaybackWorkerSummaryBubbles,
  buildPlaybackWorkerTerminatePrompt,
  buildPlaybackWorkersByUserId,
  buildWorkerPlaybackMetadataLabel,
  buildWorkerDisplayStatus,
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


const SELF_DELETE_CONFIRM_DETAIL = "Confirm deletion before removing your own account";
const ADMIN_STREAM_RELEVANT_EVENTS = [
  "stream_connected",
  "session_created",
  "session_ended",
  "session_revoked",
  "session_cleanup_confirmed",
  "session_status_changed",
  "user_disabled",
  "user_enabled",
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
  { key: "assistant", label: "Assistant (Beta)", icon: "assistant" },
];


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
  if (name === "assistant") {
    return (
      <svg aria-hidden="true" className="admin-nav-card__icon-svg" viewBox="0 0 24 24">
        <path d="M12 4l1.8 4.2L18 10l-4.2 1.8L12 16l-1.8-4.2L6 10l4.2-1.8zM18.5 4.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6zM18.5 14.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
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
  const [showAllRecoveryCheckpoints, setShowAllRecoveryCheckpoints] = useState(false);
  const [showAllRecoveryWarnings, setShowAllRecoveryWarnings] = useState(false);
  const [userActionPending, setUserActionPending] = useState(null);
  const [sessionActionPending, setSessionActionPending] = useState(null);
  const [showAllSessions, setShowAllSessions] = useState(false);
  const [showAllAudit, setShowAllAudit] = useState(false);
  const [userFeedback, setUserFeedback] = useState({});
  const [userActionsModalUserId, setUserActionsModalUserId] = useState(null);
  const [playbackWorkersPayload, setPlaybackWorkersPayload] = useState(null);
  const [playbackWorkersWarning, setPlaybackWorkersWarning] = useState("");
  const [playbackWorkersFeedback, setPlaybackWorkersFeedback] = useState(null);
  const [terminateWorkerPending, setTerminateWorkerPending] = useState("");
  const [terminateWorkerModal, setTerminateWorkerModal] = useState(null);
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
  });
  const cloudSyncWarningRef = useRef("");
  const scanRunningRef = useRef(false);
  const adminStreamRef = useRef(null);
  const adminStreamReconnectTimerRef = useRef(null);
  const adminStreamReconnectDelayRef = useRef(3000);
  const realtimeRefreshInFlightRef = useRef(false);
  const realtimeRefreshQueuedRef = useRef(false);
  const sectionCollapseTimerRef = useRef(0);
  const [activeSection, setActiveSection] = useState("panel");
  const [expandedSection, setExpandedSection] = useState(null);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const requestedSection = params.get("section");
    if (!requestedSection) {
      return;
    }
    const isKnownSection = ADMIN_SECTIONS.some((section) => section.key === requestedSection);
    if (!isKnownSection) {
      return;
    }
    setActiveSection(requestedSection);
    setExpandedSection(requestedSection);
  }, [location.search]);

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

  async function loadAdminData({ silent = false } = {}) {
    if (!silent) {
      setLoading(true);
    }
    try {
      const [status, users, sessions, audit] = await Promise.all([
        apiRequest("/api/system/status"),
        apiRequest("/api/admin/users"),
        apiRequest("/api/admin/sessions"),
        apiRequest("/api/admin/audit?limit=100"),
      ]);
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
      setUsersPayload(users.users);
      setSessionsPayload(sessions.sessions);
      setAuditPayload(audit.events);
      if (user?.role === "admin" && activeSection === "panel") {
        await loadPlaybackWorkers({ silent: true });
      }
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
      const [users, sessions] = await Promise.all([
        apiRequest("/api/admin/users"),
        apiRequest("/api/admin/sessions"),
      ]);
      setUsersPayload(users.users);
      setSessionsPayload(sessions.sessions);
      if (activeSection === "panel") {
        await loadPlaybackWorkers({ silent: true });
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
    loadAdminData();
  }, []);

  useEffect(() => {
    if (activeSection !== "recovery" || recoveryLoaded || recoveryLoading) {
      return;
    }
    loadRecoveryData();
  }, [activeSection, recoveryLoaded, recoveryLoading]);

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
      loadAdminData({ silent: true });
    }, 2500);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [statusPayload?.scan?.running]);

  useEffect(() => {
    if (user?.role !== "admin" || activeSection !== "panel") {
      return undefined;
    }
    loadPlaybackWorkers({ silent: true });
    const intervalId = window.setInterval(() => {
      loadPlaybackWorkers({ silent: true });
    }, PLAYBACK_WORKERS_POLL_MS);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [activeSection, user?.role]);

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
  }

  function openUserActionsModal(entry) {
    clearUserEditors(userActionsModalUserId);
    setUserActionsModalUserId(entry.id);
  }

  function closeUserActionsModal() {
    if (userActionsModalUserId != null) {
      clearUserEditors(userActionsModalUserId);
    }
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
      await loadAdminRealtimeState();
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
      const refreshed = await loadAdminData({ silent: true });
      await loadPlaybackWorkers({ silent: true });
      if (refreshed) {
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
  }

  async function handleCreateBackupNow() {
    if (createBackupPending) {
      return;
    }
    setCreateBackupPending(true);
    setRecoveryFeedback(null);
    try {
      const payload = await apiRequest("/api/admin/backups", { method: "POST" });
      const checkpoint = payload.checkpoint || {};
      const checkpointId = checkpoint.checkpoint_id || "";
      setInspectPayload(null);
      setRestorePlanPayload(null);
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

  async function handleInspectCheckpoint() {
    if (!selectedCheckpointId || inspectPending) {
      return;
    }
    setInspectPending(true);
    setRecoveryFeedback(null);
    try {
      const payload = await apiRequest(`/api/admin/backups/${encodeURIComponent(selectedCheckpointId)}/inspect`);
      setInspectPayload(payload);
    } catch (requestError) {
      setRecoveryFeedback({
        tone: "error",
        text: requestError.message || "Failed to inspect checkpoint.",
      });
    } finally {
      setInspectPending(false);
    }
  }

  async function handleGenerateRestorePlan() {
    if (!selectedCheckpointId || restorePlanPending) {
      return;
    }
    setRestorePlanPending(true);
    setRecoveryFeedback(null);
    setShowAllRecoveryWarnings(false);
    try {
      const payload = await apiRequest(`/api/admin/backups/${encodeURIComponent(selectedCheckpointId)}/restore-plan`);
      setRestorePlanPayload(payload);
    } catch (requestError) {
      setRecoveryFeedback({
        tone: "error",
        text: requestError.message || "Failed to build recovery preview.",
      });
    } finally {
      setRestorePlanPending(false);
    }
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
        },
      });
      setCreateUserForm({ username: "", password: "", role: "standard_user" });
      setBanner({ tone: "success", text: "User created." });
      await loadAdminData({ silent: true });
    } catch (requestError) {
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
      await loadAdminData({ silent: true });
    } catch (requestError) {
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
        `${entry.username} ${entry.assistant_beta_enabled ? "lost" : "gained"} Assistant (Beta) access.`,
      );
      await loadAdminData({ silent: true });
    } catch (requestError) {
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

  async function handleRevokeSession(session) {
    setSessionActionPending(session.id);
    setBanner(null);
    try {
      await apiRequest(`/api/admin/sessions/${session.id}/revoke`, { method: "POST" });
      setBanner({ tone: "success", text: `Session ${session.id} revoked.` });
      await loadAdminData({ silent: true });
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
        pending: false,
        error: requestError.message || "Failed to delete your admin account.",
      }));
    }
  }

  const visibleSessions = showAllSessions ? sessionsPayload : sessionsPayload.slice(0, 8);
  const visibleAuditEvents = showAllAudit ? auditPayload : auditPayload.slice(0, 10);
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
    if ((!selectedUserActionsEntry && !terminateWorkerModal && !diagnosticIdModal) || typeof document === "undefined") {
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
        closeUserActionsModal();
      }
    }
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [selectedUserActionsEntry, terminateWorkerModal, terminateWorkerPending, diagnosticIdModal]);

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
        {usersPayload.map((entry) => {
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
                        {workerGroup.items.map((worker) => {
                          const preparedRanges = formatPreparedRanges(worker.prepared_ranges);
                          const sessionDiagnosticId = shortenDiagnosticId(worker.session_id);
                          const workerDiagnosticId = shortenDiagnosticId(worker.worker_id);
                          const epochDiagnosticId = shortenDiagnosticId(worker.epoch_id);
                          const hasTargetPosition = Number.isFinite(worker.target_position_seconds);
                          const displayStatus = buildWorkerDisplayStatus(worker);
                          const canTerminateWorker = canTerminatePlaybackWorker(worker.state);
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
                        {workerGroup.nativeItems.map((nativePlayback) => {
                          const displayStatus = buildWorkerDisplayStatus(nativePlayback);
                          const sessionDiagnosticId = shortenDiagnosticId(nativePlayback.session_id);
                          const positionSeconds = Number(nativePlayback.last_position_seconds);
                          const durationSeconds = Number(nativePlayback.last_duration_seconds);
                          const hasPosition = Number.isFinite(positionSeconds) && positionSeconds >= 0;
                          const hasDuration = Number.isFinite(durationSeconds) && durationSeconds >= 0;
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
                </h2>
                <div className="admin-user-actions-modal__subtitle">
                  <UserStatusIndicator
                    color={selectedUserActionsEntry.status_color}
                    label={selectedUserActionsEntry.status_label}
                  />
                  <span>
                    {selectedUserActionsEntry.role} · {selectedUserActionsEntry.enabled ? "enabled" : "disabled"} · {selectedUserActionsEntry.active_sessions} live session{selectedUserActionsEntry.active_sessions === 1 ? "" : "s"}
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
            <div className="admin-user-actions-modal__meta">
              <span className="admin-user-actions-modal__meta-pill">Role: {selectedUserActionsEntry.role}</span>
              <span className="admin-user-actions-modal__meta-pill">
                Account: {selectedUserActionsEntry.enabled ? "Enabled" : "Disabled"}
              </span>
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
                {selectedUserActionsEntry.id === user?.id ? "Update my password" : "Reset password"}
              </button>
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
                    <PasswordInput
                      autoComplete="current-password"
                      onChange={(event) =>
                        setSelfDeleteState((current) => ({
                          ...current,
                          password: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
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
                <PasswordInput
                  autoComplete="current-password"
                  onChange={(event) =>
                    setRoleConfirm((current) => ({
                      ...current,
                      currentAdminPassword: event.target.value,
                    }))
                  }
                  placeholder="Current admin password"
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
                <PasswordInput
                  autoComplete="new-password"
                  onChange={(event) =>
                    setPasswordEditor((current) => ({
                      ...current,
                      newPassword: event.target.value,
                    }))
                  }
                  placeholder="New password"
                  value={passwordEditor.newPassword}
                />
                <PasswordInput
                  autoComplete="current-password"
                  onChange={(event) =>
                    setPasswordEditor((current) => ({
                      ...current,
                      currentAdminPassword: event.target.value,
                    }))
                  }
                  placeholder="Current admin password"
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
              <h3>Assistant Beta</h3>
              <p className="page-subnote">Secondary access only for the safe structured request form.</p>
            </div>
            {selectedUserActionsEntry.role === "standard_user" ? (
              <div className="assistant-access-toggle assistant-access-toggle--modal">
                <div>
                  <strong>{selectedUserActionsEntry.assistant_beta_enabled ? "Enabled" : "Disabled"}</strong>
                  <p className="page-subnote">
                    {selectedUserActionsEntry.assistant_beta_enabled
                      ? "This user can access the Assistant (Beta) request flow."
                      : "This user cannot access the Assistant (Beta) request flow."}
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
                Assistant (Beta) access is only configurable for standard users in this phase.
              </p>
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

  const createUserCard = (
    <section className="settings-card">
      <h2>Create user</h2>
      <form
        autoComplete="off"
        className="admin-form"
        id="elvern-admin-create-account-form"
        name="elvern-admin-create-account-form"
        onSubmit={handleCreateUser}
      >
        <div aria-hidden="true" className="admin-autofill-decoys">
          <input autoComplete="username" name="username" tabIndex={-1} type="text" />
          <input autoComplete="current-password" name="password" tabIndex={-1} type="password" />
        </div>
        <label>
          Username
          <input
            autoComplete="off"
            data-1p-ignore="true"
            data-lpignore="true"
            id="elvern-new-account-username"
            name="elvern-new-account-username"
            onChange={(event) => setCreateUserForm((current) => ({ ...current, username: event.target.value }))}
            required
            spellCheck="false"
            type="text"
            value={createUserForm.username}
          />
        </label>
        <label>
          Password
          <PasswordInput
            autoComplete="new-password"
            data-1p-ignore="true"
            data-lpignore="true"
            id="elvern-new-account-password"
            name="elvern-new-account-password"
            onChange={(event) => setCreateUserForm((current) => ({ ...current, password: event.target.value }))}
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
        <button className="primary-button" disabled={createPending} type="submit">
          {createPending ? "Creating..." : "Create user"}
        </button>
      </form>
    </section>
  );

  const securitySection = statusPayload ? (
    <div className="admin-section-grid">
      <section className="settings-card">
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

      <section className="settings-card">
        <h2>Security</h2>
        <StatusRow label="Private-only mode" value={statusPayload.security.private_network_only ? "Enabled" : "Disabled"} />
        <StatusRow label="Multi-user" value={statusPayload.security.multiuser_enabled ? "Enabled" : "Disabled"} />
        <StatusRow label="Users" value={String(statusPayload.total_users)} />
        <StatusRow label="Active auth sessions" value={String(sessionsPayload.length)} />
        <StatusRow label="Session TTL" value={`${statusPayload.security.session_ttl_hours} hour(s)`} />
      </section>
    </div>
  ) : null;

  const logsSection = (
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
          Backups may contain secrets. Treat checkpoint folders as private.
        </p>
        <div className="admin-list__actions">
          <button
            className="primary-button"
            disabled={createBackupPending}
            onClick={handleCreateBackupNow}
            type="button"
          >
            {createBackupPending ? "Creating backup..." : "Create backup now"}
          </button>
          <button
            className="ghost-button"
            disabled={recoveryLoading}
            onClick={() => loadRecoveryData()}
            type="button"
          >
            {recoveryLoading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
        <FeedbackBanner banner={recoveryFeedback} />
      </section>

      <div className="admin-activity-grid admin-recovery-grid">
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
                        {checkpoint.contains_secrets ? "Contains secrets" : "No secrets flagged"} · DB integrity {checkpoint.db_integrity_check_result || "unknown"} · {formatBytes(checkpoint.total_size_bytes)} · {checkpoint.file_count} files
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
              <h2>Inspect checkpoint</h2>
              <p className="page-subnote">
                Compact checkpoint validation only. No manifest secrets or raw database contents are shown here.
              </p>
            </div>
            <button
              className="ghost-button ghost-button--inline"
              disabled={!selectedCheckpointId || inspectPending}
              onClick={handleInspectCheckpoint}
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
      </div>

      <div className="admin-activity-grid admin-recovery-grid">
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
              onClick={handleGenerateRestorePlan}
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
            <p className="page-subnote">Preview recovery to compare the checkpoint against the current live environment.</p>
          )}
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
  );

  return (
    <section className="page-section">
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
          <button
            className="ghost-button ghost-button--inline admin-nav-card__rescan"
            disabled={statusRefreshPending}
            onClick={handleRefreshStatus}
            type="button"
          >
            {statusRefreshPending ? "Refreshing..." : "Refresh status"}
          </button>
        ) : null}
      </div>

      <FeedbackBanner banner={banner} />

      {statusPayload ? (
        <div className="admin-section-stack">
              {activeSection === "panel" ? (
                <>
                  {usersCard}
                  {createUserCard}
                </>
              ) : null}

          {activeSection === "security" ? securitySection : null}

          {activeSection === "logs" ? logsSection : null}

          {activeSection === "recovery" ? recoverySection : null}

          {activeSection === "assistant" ? (
            <section className="settings-card admin-assistant-placeholder">
              <p className="eyebrow">Assistant (Beta)</p>
              <h2>Request workflow</h2>
              <p className="page-subnote">
                Review structured user requests, placeholder triage drafts, proposed action requests, approval records, and change drafts.
              </p>
              <div className="admin-list__actions">
                <Link className="primary-button" to="/admin/assistant">
                  Open request queue
                </Link>
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
      {userActionsModal}
      {terminateWorkerConfirmationModal}
      {diagnosticIdPopup}
    </section>
  );
}
