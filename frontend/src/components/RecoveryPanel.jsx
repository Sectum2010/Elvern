import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "../lib/api";
import {
  ADMIN_BACKUP_EVENT,
  ADMIN_BACKUP_STREAM_STATUS_EVENT,
} from "../lib/adminEvents.js";
import { MeridianScanIcon } from "./meridian/MeridianScanIcon.jsx";
import { NonLoginSecretInput } from "./NonLoginSecretInput";

const CHECKPOINT_LIMIT = 4;
export const RECOVERY_JOB_SSE_POLL_MS = 5_000;
export const RECOVERY_JOB_FALLBACK_POLL_MS = 2_000;
export const RECOVERY_SSE_COALESCE_MS = 150;
export const RECOVERY_JUST_CREATED_MS = 2_600;
const ACTIVE_JOB_STATES = new Set([
  "queued",
  "snapshotting_database",
  "collecting_components",
  "sealing_manifest",
  "archiving",
  "encrypting",
  "writing_checkpoint",
  "verifying_checkpoint",
]);

const STAGES = [
  { key: "create", label: "1 · Create" },
  { key: "checkpoints", label: "2 · Checkpoints" },
  { key: "verify", label: "3 · Verify & protect" },
];

const RECOVERY_DIALOG_FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const STATUS_LABELS = {
  valid: "VALID",
  needs_passphrase: "NEEDS PASSPHRASE",
  unverified: "UNVERIFIED",
  verification_stale: "STALE",
  corrupt: "CORRUPT",
  key_unavailable: "KEY UNAVAILABLE",
  unsupported: "UNSUPPORTED",
  creating: "CREATING",
  failed: "FAILED",
  missing: "MISSING",
  legacy_unverified: "LEGACY UNVERIFIED",
};

function bytesLabel(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function dateLabel(value) {
  if (!value) return "Unknown time";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function compactDateLabel(value) {
  if (!value) return "Unknown time";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    month: "short",
  }).format(parsed);
}

function triggerLabel(value) {
  const labels = {
    auto_before_shared_local_path_update: "Auto · Shared local path update",
    auto_before_admin_rescan: "Auto · Admin rescan",
    manual_admin_ui: "Manual · Admin UI",
    manual_cli: "Manual · CLI",
  };
  return labels[value] || String(value || "Checkpoint").replaceAll("_", " ");
}

function errorCode(error) {
  return error?.detail?.code || error?.payload?.detail?.code || "";
}

function isRequestAbort(error) {
  return error?.name === "AbortError";
}

function safeResultLines(mode, payload) {
  if (!payload) return [];
  if (mode === "inspect") {
    const valid = payload.valid === true;
    const knownInvalid = payload.valid === false;
    const integrity = payload.db_integrity_check_result;
    const filesVerified = Number(payload.files_verified || 0);
    return [
      { tone: valid ? "ok" : knownInvalid ? "bad" : "muted", text: valid ? "✓ archive readable" : knownInvalid ? "× archive validation failed" : "● archive readability unavailable" },
      { tone: valid ? "ok" : knownInvalid ? "bad" : "muted", text: valid ? "✓ manifest and file hashes verified" : knownInvalid ? "× manifest or file verification failed" : "● manifest verification unavailable" },
      { tone: integrity === "ok" ? "ok" : integrity ? "bad" : "muted", text: `${integrity === "ok" ? "✓" : integrity ? "×" : "●"} database integrity: ${integrity || "unknown"}` },
      { tone: filesVerified > 0 ? "ok" : "muted", text: `${filesVerified > 0 ? "✓" : "●"} ${filesVerified} files verified` },
    ];
  }
  const settingsValues = Object.values(payload.settings_matches || {});
  const matches = settingsValues.filter((value) => value === true).length;
  const differences = settingsValues.filter((value) => value === false).length;
  const backupUsers = payload.backup_counts?.users;
  const currentUsers = payload.current_counts?.users;
  const backupMedia = payload.backup_counts?.media_items;
  const currentMedia = payload.current_counts?.media_items;
  const usersKnown = Number.isFinite(Number(backupUsers)) && Number.isFinite(Number(currentUsers));
  const mediaKnown = Number.isFinite(Number(backupMedia)) && Number.isFinite(Number(currentMedia));
  return [
    { tone: payload.checkpoint_valid === true ? "ok" : payload.checkpoint_valid === false ? "bad" : "muted", text: payload.checkpoint_valid === true ? "✓ checkpoint valid" : payload.checkpoint_valid === false ? "× checkpoint has blocking errors" : "● checkpoint validity unavailable" },
    { tone: payload.schema_compatible === true ? "ok" : payload.schema_compatible === false ? "warn" : "muted", text: payload.schema_compatible === true ? "✓ schema compatible" : payload.schema_compatible === false ? "● schema differs" : "● schema compatibility unavailable" },
    { tone: !usersKnown ? "muted" : Number(backupUsers) === Number(currentUsers) ? "ok" : "warn", text: `${usersKnown && Number(backupUsers) === Number(currentUsers) ? "✓" : "●"} users: backup ${backupUsers ?? "unknown"} → live ${currentUsers ?? "unknown"}` },
    { tone: !mediaKnown ? "muted" : Number(backupMedia) === Number(currentMedia) ? "ok" : "warn", text: `${mediaKnown && Number(backupMedia) === Number(currentMedia) ? "✓" : "●"} media index: backup ${backupMedia ?? "unknown"} → live ${currentMedia ?? "unknown"}` },
    { tone: differences ? "warn" : settingsValues.length ? "ok" : "muted", text: `${differences ? "●" : settingsValues.length ? "✓" : "●"} settings/reference summary: ${matches} match · ${differences} differ` },
    { tone: "muted", text: "● preview only — nothing restored or changed" },
    ...(payload.blocking_errors || []).map((text) => ({ tone: "bad", text: `× ${text}` })),
    ...(payload.warnings || []).map((text) => ({ tone: "warn", text: `● ${text}` })),
  ];
}

export function RecoveryPanel({ identityKey = "anonymous", onToast }) {
  const [phase, setPhase] = useState("create");
  const [keySource, setKeySource] = useState("auto");
  const [passphrase, setPassphrase] = useState("");
  const [catalog, setCatalog] = useState([]);
  const [backupsDirectory, setBackupsDirectory] = useState("backups");
  const [catalogState, setCatalogState] = useState({ loading: true, loaded: false, error: "" });
  const [selectedId, setSelectedId] = useState("");
  const [newCheckpointId, setNewCheckpointId] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState({ mode: "", payload: null, error: "" });
  const [actionPending, setActionPending] = useState("");
  const [stepUp, setStepUp] = useState({ open: false, password: "", pending: false, error: "" });
  const [checkpointPassphrase, setCheckpointPassphrase] = useState({ open: false, mode: "", checkpointId: "", value: "", error: "" });
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [auditState, setAuditState] = useState({ loading: true, error: "", warnings: [] });
  const [recentAuthState, setRecentAuthState] = useState({ checking: false, unknown: false, error: "" });
  const [sseConnected, setSseConnected] = useState(false);
  const panelRef = useRef(null);
  const pendingSensitiveIntentRef = useRef(null);
  const createIntentRef = useRef({ idempotencyKey: "", pending: false });
  const completionHandledRef = useRef("");
  const dialogReturnFocusRef = useRef(null);
  const requestOwnerRef = useRef({ generation: 0, controllers: new Map() });
  const sseRefreshTimerRef = useRef(null);
  const pollFailureCountRef = useRef(0);

  useEffect(() => {
    const owner = requestOwnerRef.current;
    owner.generation += 1;
    owner.controllers.forEach((controller) => controller.abort());
    owner.controllers.clear();
    return () => {
      owner.generation += 1;
      owner.controllers.forEach((controller) => controller.abort());
      owner.controllers.clear();
      if (sseRefreshTimerRef.current) {
        window.clearTimeout(sseRefreshTimerRef.current);
        sseRefreshTimerRef.current = null;
      }
    };
  }, [identityKey]);

  const runOwnedRequest = useCallback(async (resource, request, { replace = true } = {}) => {
    const owner = requestOwnerRef.current;
    const existing = owner.controllers.get(resource);
    if (existing && !replace) {
      return { current: false, skipped: true };
    }
    existing?.abort();
    const controller = new AbortController();
    const generation = owner.generation;
    owner.controllers.set(resource, controller);
    try {
      const payload = await request(controller.signal);
      return {
        current: owner.generation === generation
          && owner.controllers.get(resource) === controller
          && !controller.signal.aborted,
        payload,
      };
    } finally {
      if (owner.controllers.get(resource) === controller) {
        owner.controllers.delete(resource);
      }
    }
  }, [identityKey]);

  const rememberDialogReturnFocus = useCallback(() => {
    if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
      dialogReturnFocusRef.current = document.activeElement;
    }
  }, []);

  const closeRecoveryDialog = useCallback(() => {
    if (stepUp.pending || actionPending === "delete") {
      return;
    }
    if (pendingSensitiveIntentRef.current?.kind === "create") {
      createIntentRef.current = { idempotencyKey: "", pending: false };
    }
    pendingSensitiveIntentRef.current = null;
    setStepUp({ open: false, password: "", pending: false, error: "" });
    setCheckpointPassphrase({ open: false, mode: "", checkpointId: "", value: "", error: "" });
    setRecentAuthState({ checking: false, unknown: false, error: "" });
    setDeleteConfirm(false);
  }, [actionPending, stepUp.pending]);

  useEffect(() => {
    const dialogOpen = stepUp.open || checkpointPassphrase.open || deleteConfirm;
    if (!dialogOpen || typeof document === "undefined") {
      return undefined;
    }
    const dialog = panelRef.current?.querySelector(".meridian-recovery-dialog__card");
    if (!dialog) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.querySelector(RECOVERY_DIALOG_FOCUSABLE_SELECTOR)?.focus();

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRecoveryDialog();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const focusable = [...dialog.querySelectorAll(RECOVERY_DIALOG_FOCUSABLE_SELECTOR)];
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

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
      dialogReturnFocusRef.current?.focus?.();
    };
  }, [checkpointPassphrase.open, closeRecoveryDialog, deleteConfirm, stepUp.open]);

  const loadCatalog = useCallback(async ({ preferredId = "" } = {}) => {
    setCatalogState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const requestResult = await runOwnedRequest(
        "catalog",
        (signal) => apiRequest("/api/admin/backups", { signal }),
      );
      if (!requestResult.current) return;
      const payload = requestResult.payload;
      const checkpoints = payload.checkpoints || [];
      setCatalog(checkpoints);
      setBackupsDirectory(payload.backups_dir || "backups");
      setSelectedId((current) => {
        const requested = preferredId || current;
        return checkpoints.some((entry) => entry.checkpoint_id === requested)
          ? requested
          : (checkpoints[0]?.checkpoint_id || "");
      });
      setCatalogState({ loading: false, loaded: true, error: "" });
    } catch (error) {
      if (isRequestAbort(error)) return;
      setCatalogState((current) => ({ ...current, loading: false, error: error.message || "Checkpoint catalog is unavailable." }));
    }
  }, [runOwnedRequest]);

  const loadAuditWarnings = useCallback(async () => {
    setAuditState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const requestResult = await runOwnedRequest(
        "audit",
        (signal) => apiRequest("/api/admin/audit?limit=100", { signal }),
      );
      if (!requestResult.current) return;
      const payload = requestResult.payload;
      const warnings = (payload.events || []).filter((event) =>
        /backup/i.test(event.action || "") && event.outcome !== "success").slice(0, 6);
      setAuditState({ loading: false, error: "", warnings });
    } catch (error) {
      if (isRequestAbort(error)) return;
      setAuditState((current) => ({ ...current, loading: false, error: error.message || "Backup audit status is unavailable." }));
    }
  }, [runOwnedRequest]);

  const loadActiveJob = useCallback(async ({ replace = true } = {}) => {
    try {
      const requestResult = await runOwnedRequest(
        "job-status",
        (signal) => apiRequest("/api/admin/backup-jobs/active", { signal }),
        { replace },
      );
      if (requestResult.current) {
        setJob(requestResult.payload.job || null);
        pollFailureCountRef.current = 0;
      }
      return requestResult.current;
    } catch (error) {
      if (!isRequestAbort(error)) pollFailureCountRef.current += 1;
      return false;
    }
  }, [runOwnedRequest]);

  const loadJobStatus = useCallback(async (jobId, { replace = false } = {}) => {
    if (!jobId) return loadActiveJob({ replace });
    try {
      const requestResult = await runOwnedRequest(
        "job-status",
        (signal) => apiRequest(`/api/admin/backup-jobs/${encodeURIComponent(jobId)}`, { signal }),
        { replace },
      );
      if (requestResult.current) {
        setJob(requestResult.payload);
        pollFailureCountRef.current = 0;
      }
      return requestResult.current;
    } catch (error) {
      if (!isRequestAbort(error)) pollFailureCountRef.current += 1;
      return false;
    }
  }, [loadActiveJob, runOwnedRequest]);

  useEffect(() => {
    Promise.allSettled([
      loadCatalog(),
      loadAuditWarnings(),
      loadActiveJob(),
    ]);
  }, [loadActiveJob, loadAuditWarnings, loadCatalog]);

  useEffect(() => {
    if (!job?.id || !ACTIVE_JOB_STATES.has(job.state)) return undefined;
    let disposed = false;
    let timerId = null;
    const schedule = () => {
      if (disposed || document.visibilityState === "hidden") return;
      const baseDelay = sseConnected ? RECOVERY_JOB_SSE_POLL_MS : RECOVERY_JOB_FALLBACK_POLL_MS;
      const delay = sseConnected
        ? baseDelay
        : Math.min(baseDelay * (2 ** Math.min(pollFailureCountRef.current, 2)), 8_000);
      timerId = window.setTimeout(poll, delay);
    };
    const poll = async () => {
      if (disposed || document.visibilityState === "hidden") return;
      await loadJobStatus(job.id, { replace: false });
      schedule();
    };
    const handleVisibility = () => {
      if (document.visibilityState !== "visible") {
        if (timerId) window.clearTimeout(timerId);
        timerId = null;
        return;
      }
      if (timerId) window.clearTimeout(timerId);
      timerId = null;
      poll();
    };
    document.addEventListener("visibilitychange", handleVisibility);
    schedule();
    return () => {
      disposed = true;
      if (timerId) window.clearTimeout(timerId);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [job?.id, job?.state, loadJobStatus, sseConnected]);

  useEffect(() => {
    function handleStreamStatus(event) {
      setSseConnected(Boolean(event.detail?.connected));
    }
    function handleBackupEvent(event) {
      if (sseRefreshTimerRef.current) return;
      sseRefreshTimerRef.current = window.setTimeout(async () => {
        sseRefreshTimerRef.current = null;
        await loadJobStatus(job?.id || "", { replace: true });
        if (["backup_job_completed", "backup_job_failed", "backup_checkpoint_deleted"].includes(event.detail?.eventType)) {
          await Promise.allSettled([loadCatalog(), loadAuditWarnings()]);
        }
      }, RECOVERY_SSE_COALESCE_MS);
    }
    window.addEventListener(ADMIN_BACKUP_EVENT, handleBackupEvent);
    window.addEventListener(ADMIN_BACKUP_STREAM_STATUS_EVENT, handleStreamStatus);
    return () => {
      window.removeEventListener(ADMIN_BACKUP_EVENT, handleBackupEvent);
      window.removeEventListener(ADMIN_BACKUP_STREAM_STATUS_EVENT, handleStreamStatus);
      if (sseRefreshTimerRef.current) {
        window.clearTimeout(sseRefreshTimerRef.current);
        sseRefreshTimerRef.current = null;
      }
    };
  }, [job?.id, loadAuditWarnings, loadCatalog, loadJobStatus]);

  useEffect(() => {
    if (job?.state !== "completed" || !job.checkpoint_id || completionHandledRef.current === job.id) return;
    completionHandledRef.current = job.id;
    setNewCheckpointId(job.checkpoint_id);
    setPassphrase("");
    loadCatalog({ preferredId: job.checkpoint_id }).then(() => setPhase("checkpoints"));
    onToast?.({ tone: "success", text: "Encrypted backup checkpoint created." });
  }, [job, loadCatalog, onToast]);

  useEffect(() => {
    if (!newCheckpointId) return undefined;
    const timerId = window.setTimeout(() => setNewCheckpointId(""), RECOVERY_JUST_CREATED_MS);
    return () => window.clearTimeout(timerId);
  }, [newCheckpointId]);

  const selected = useMemo(
    () => catalog.find((entry) => entry.checkpoint_id === selectedId) || null,
    [catalog, selectedId],
  );
  const visibleCatalog = showAll ? catalog : catalog.slice(0, CHECKPOINT_LIMIT);
  const sameHostPaths = backupsDirectory.startsWith("/");

  async function runWithRecentAuth(intent) {
    pendingSensitiveIntentRef.current = intent;
    setRecentAuthState({ checking: true, unknown: false, error: "" });
    try {
      const requestResult = await runOwnedRequest(
        "recent-auth-status",
        (signal) => apiRequest("/api/admin/backups/recent-auth/status", { signal }),
      );
      if (!requestResult.current) return;
      setRecentAuthState({ checking: false, unknown: false, error: "" });
      if (requestResult.payload?.verified) {
        pendingSensitiveIntentRef.current = null;
        await continueSensitiveIntent(intent);
        return;
      }
      rememberDialogReturnFocus();
      setStepUp({ open: true, password: "", pending: false, error: "" });
    } catch (error) {
      if (isRequestAbort(error)) return;
      rememberDialogReturnFocus();
      setRecentAuthState({
        checking: false,
        unknown: true,
        error: "Could not verify recent authentication.",
      });
    }
  }

  async function submitStepUp(event) {
    event.preventDefault();
    if (!stepUp.password || stepUp.pending) return;
    setStepUp((current) => ({ ...current, pending: true, error: "" }));
    try {
      const password = stepUp.password;
      const requestResult = await runOwnedRequest(
        "recent-auth-confirm",
        (signal) => apiRequest("/api/admin/backups/recent-auth", {
          method: "POST",
          data: { current_admin_password: password },
          signal,
        }),
      );
      if (!requestResult.current) return;
      const intent = pendingSensitiveIntentRef.current;
      pendingSensitiveIntentRef.current = null;
      setStepUp({ open: false, password: "", pending: false, error: "" });
      setRecentAuthState({ checking: false, unknown: false, error: "" });
      await continueSensitiveIntent(intent);
    } catch (error) {
      if (isRequestAbort(error)) return;
      setStepUp({ open: true, password: "", pending: false, error: error.message || "Password confirmation failed." });
    }
  }

  function createBackup() {
    if (createIntentRef.current.pending) {
      return;
    }
    if (keySource === "passphrase") {
      if (passphrase.length < 12 || passphrase.length > 1024) {
        setResult({ mode: "", payload: null, error: "Use a passphrase between 12 and 1024 characters." });
        return;
      }
    }
    if (!createIntentRef.current.idempotencyKey) {
      createIntentRef.current.idempotencyKey = crypto.randomUUID();
    }
    createIntentRef.current.pending = true;
    runWithRecentAuth({ kind: "create", keySource });
  }

  async function issueCreateBackup(intent) {
    if (intent?.reenterPassphrase && intent.keySource === "passphrase") {
      createIntentRef.current.pending = false;
      setResult({ mode: "", payload: null, error: "Recent authentication was renewed. Re-enter the manual passphrase to continue." });
      return;
    }
    const requestedPassphrase = intent.keySource === "passphrase" ? passphrase : null;
    const idempotencyKey = createIntentRef.current.idempotencyKey;
    setActionPending("create");
    setResult({ mode: "", payload: null, error: "" });
    try {
      const requestResult = await runOwnedRequest(
        "create-action",
        (signal) => apiRequest("/api/admin/backup-jobs", {
          method: "POST",
          data: {
            key_source: intent.keySource,
            passphrase: requestedPassphrase,
            passphrase_confirmation: requestedPassphrase,
            idempotency_key: idempotencyKey,
          },
          signal,
        }),
        { replace: false },
      );
      if (requestResult.current) {
        setJob(requestResult.payload);
        createIntentRef.current.idempotencyKey = "";
      }
    } catch (error) {
      if (isRequestAbort(error)) return;
      if (errorCode(error) === "recent_auth_required") {
        setPassphrase("");
        createIntentRef.current.pending = false;
        await runWithRecentAuth({ ...intent, reenterPassphrase: true });
        return;
      }
      if (Number(error?.status) >= 400 && Number(error?.status) < 500) {
        createIntentRef.current.idempotencyKey = "";
      }
      setResult({ mode: "", payload: null, error: error.message || "Backup creation could not start." });
    } finally {
      setPassphrase("");
      createIntentRef.current.pending = false;
      setActionPending("");
    }
  }

  function selectCheckpoint(checkpointId) {
    setSelectedId(checkpointId);
    setResult({ mode: "", payload: null, error: "" });
    setCheckpointPassphrase({ open: false, mode: "", checkpointId: "", value: "", error: "" });
  }

  function requestCheckpointAction(mode) {
    if (!selected) return;
    runWithRecentAuth({ kind: "checkpoint", mode, checkpointId: selected.checkpoint_id });
  }

  async function runCheckpointAction(mode, checkpointId, suppliedPassphrase) {
    if (suppliedPassphrase) {
      setCheckpointPassphrase({ open: false, mode: "", checkpointId: "", value: "", error: "" });
    }
    setActionPending(mode);
    setResult({ mode: "", payload: null, error: "" });
    try {
      const endpoint = mode === "inspect"
        ? `/api/admin/backups/${encodeURIComponent(checkpointId)}/inspect`
        : `/api/admin/backups/${encodeURIComponent(checkpointId)}/preview`;
      const requestResult = await runOwnedRequest(
        `${mode}-action`,
        (signal) => mode === "inspect" && !suppliedPassphrase
          ? apiRequest(endpoint, { signal })
          : apiRequest(endpoint, { method: "POST", data: { passphrase: suppliedPassphrase }, signal }),
      );
      if (!requestResult.current) return;
      setResult({ mode, payload: requestResult.payload, error: "" });
      setCheckpointPassphrase({ open: false, mode: "", checkpointId: "", value: "", error: "" });
      await loadCatalog({ preferredId: checkpointId });
    } catch (error) {
      if (isRequestAbort(error)) return;
      const message = error.message || "Checkpoint verification failed.";
      if (errorCode(error) === "recent_auth_required") {
        setCheckpointPassphrase({ open: false, mode: "", checkpointId: "", value: "", error: "" });
        await runWithRecentAuth({ kind: "checkpoint", mode, checkpointId });
      } else if (suppliedPassphrase && ["backup_passphrase_invalid", "backup_wrong_passphrase"].includes(errorCode(error))) {
        setCheckpointPassphrase({ open: true, mode, checkpointId, value: "", error: message });
      } else {
        setResult({ mode, payload: null, error: message });
      }
    } finally {
      setActionPending("");
    }
  }

  async function issueDelete(checkpointId) {
    const checkpoint = catalog.find((entry) => entry.checkpoint_id === checkpointId);
    if (!checkpoint) return;
    setActionPending("delete");
    try {
      const requestResult = await runOwnedRequest(
        "delete-action",
        (signal) => apiRequest(`/api/admin/backups/${encodeURIComponent(checkpointId)}`, {
          method: "DELETE",
          data: { checkpoint_id: checkpointId, confirm: true },
          signal,
        }),
      );
      if (!requestResult.current) return;
      const oldIndex = catalog.findIndex((entry) => entry.checkpoint_id === checkpointId);
      const remaining = catalog.filter((entry) => entry.checkpoint_id !== checkpointId);
      const neighbor = remaining[Math.min(Math.max(oldIndex, 0), Math.max(remaining.length - 1, 0))];
      setCatalog(remaining);
      setSelectedId(neighbor?.checkpoint_id || "");
      setResult({ mode: "", payload: null, error: "" });
      setDeleteConfirm(false);
      onToast?.({ tone: "success", text: "Backup checkpoint deleted." });
      loadAuditWarnings();
    } catch (error) {
      if (isRequestAbort(error)) return;
      if (errorCode(error) === "recent_auth_required") {
        await runWithRecentAuth({ kind: "delete", checkpointId });
      } else {
        setResult({ mode: "delete", payload: null, error: error.message || "Checkpoint deletion failed." });
      }
    } finally {
      setActionPending("");
    }
  }

  async function continueSensitiveIntent(intent) {
    if (!intent) return;
    if (intent.kind === "create") {
      await issueCreateBackup(intent);
      return;
    }
    if (intent.kind === "checkpoint") {
      const checkpoint = catalog.find((entry) => entry.checkpoint_id === intent.checkpointId);
      if (!checkpoint) return;
      if (checkpoint.backup_key_source === "passphrase") {
        rememberDialogReturnFocus();
        setCheckpointPassphrase({ open: true, mode: intent.mode, checkpointId: intent.checkpointId, value: "", error: "" });
      } else {
        await runCheckpointAction(intent.mode, intent.checkpointId, null);
      }
      return;
    }
    if (intent.kind === "delete") {
      await issueDelete(intent.checkpointId);
    }
  }

  function confirmDelete() {
    if (!selected) return;
    runWithRecentAuth({ kind: "delete", checkpointId: selected.checkpoint_id });
  }

  function openDeleteConfirmation() {
    rememberDialogReturnFocus();
    setDeleteConfirm(true);
  }

  const running = job && ACTIVE_JOB_STATES.has(job.state);
  const resultLines = safeResultLines(result.mode, result.payload);
  const latestKnownCheckpoint = catalog.reduce((latest, checkpoint) => {
    const timestamp = Date.parse(checkpoint.created_at_utc || "");
    if (!Number.isFinite(timestamp)) return latest;
    return !latest || timestamp > latest.timestamp ? { timestamp, checkpoint } : latest;
  }, null);
  const catalogMetric = catalogState.error
    ? { count: "Unavailable", label: "catalog status", latest: "latest time unavailable" }
    : !catalogState.loaded
      ? { count: "Loading", label: "catalog", latest: "checking checkpoints" }
      : catalog.length === 0
        ? { count: "0", label: "checkpoints", latest: "No checkpoint yet" }
        : {
            count: String(catalog.length),
            label: "checkpoints",
            latest: latestKnownCheckpoint
              ? `latest ${compactDateLabel(latestKnownCheckpoint.checkpoint.created_at_utc)}`
              : "latest time unavailable",
          };
  const warningText = auditState.loading
    ? "Loading backup warning status…"
    : auditState.error
      ? "Backup warning status unavailable."
      : auditState.warnings.length
        ? `${auditState.warnings.length} recent backup warning${auditState.warnings.length === 1 ? "" : "s"}.`
        : "No recent backup warnings are visible in the loaded audit log.";

  return (
    <div className="meridian-recovery" data-visual-landmark="recovery-panel" ref={panelRef}>
      <section className="meridian-recovery__card meridian-card">
        <div className="meridian-recovery__top-row">
          <div className="meridian-recovery__chips">
            <span>ADMIN-ONLY</span><span>EXCLUDES MEDIA FILES</span><span className="is-danger">CONTAINS SECRETS — NEVER SHARE</span>
          </div>
          <div className={`meridian-recovery__metrics${catalogState.error ? " is-unavailable" : ""}`}>
            <i aria-hidden="true" />
            <strong>{catalogMetric.count}</strong>
            <span>{catalogMetric.label}</span>
            <small>· {catalogMetric.latest}</small>
          </div>
        </div>
        <p className="meridian-recovery__scope">Backups protect Elvern runtime state — never movie files, poster libraries, or playback cache.</p>
        {catalogState.loading && catalogState.loaded ? <p className="meridian-recovery__refreshing" role="status">Refreshing checkpoint catalog…</p> : null}
        {catalogState.error ? <p className="meridian-recovery__error" role="alert">{catalogState.error}</p> : null}
        <nav aria-label="Recovery stages" className="meridian-recovery__stages">
          {STAGES.map((stage, index) => (
            <button className={phase === stage.key ? "is-active" : ""} key={stage.key} onClick={() => setPhase(stage.key)} type="button">
              <i style={{ background: index <= STAGES.findIndex((entry) => entry.key === phase) ? "#2e4fe0" : undefined }} />
              <span>{stage.label}</span>
            </button>
          ))}
        </nav>
        <div className="meridian-recovery__stage">
        {phase === "create" ? (
          <div className="meridian-recovery__phase" key="create">
            <div className="meridian-recovery__key-controls">
              <div>
                <p className="meridian-recovery__label">ENCRYPTION KEY</p>
                <div className="meridian-recovery__segments" role="group" aria-label="Backup encryption key">
                  <button className={keySource === "auto" ? "is-active" : ""} onClick={() => setKeySource("auto")} type="button">Auto key</button>
                  <button className={keySource === "passphrase" ? "is-active" : ""} onClick={() => setKeySource("passphrase")} type="button">Manual passphrase</button>
                </div>
              </div>
              {keySource === "passphrase" ? (
                <div className="meridian-recovery__manual-passphrase">
                  <p className="meridian-recovery__label">PASSPHRASE</p>
                  <NonLoginSecretInput autoComplete="new-password" onChange={(event) => setPassphrase(event.target.value)} placeholder="Enter a strong passphrase" purpose="recovery-create-passphrase" value={passphrase} />
                </div>
              ) : null}
            </div>
            <p className="meridian-recovery__note">
              {keySource === "auto"
                ? "Convenient server-local rollback protection using Elvern’s independent backup keyring."
                : "Recommended for long-term or off-host recovery. Elvern never stores the passphrase."}
            </p>
            <div className="meridian-recovery__actions">
              <button className="primary-button meridian-recovery__create-button" disabled={running || createIntentRef.current.pending || actionPending === "create" || recentAuthState.checking} onClick={createBackup} type="button"><MeridianScanIcon />Create encrypted backup</button>
              <button className="ghost-button meridian-recovery__refresh-button" disabled={catalogState.loading} onClick={() => { loadCatalog(); loadAuditWarnings(); }} type="button"><MeridianScanIcon spinning={catalogState.loading} />Refresh</button>
            </div>
            {job ? (
              <div className={`meridian-recovery__terminal${job.state === "failed" || job.state === "interrupted" ? " is-error" : ""}`}>
                <div><span>{job.message || job.state.replaceAll("_", " ")}</span><strong>{job.progress_percent}%</strong></div>
                <div className="meridian-recovery__progress"><i style={{ width: `${job.progress_percent}%` }}><b /></i></div>
                <code>→ {job.checkpoint_id ? `backups/${job.checkpoint_id}` : "server-local encrypted checkpoint"}</code>
                {job.error_message ? <p>{job.error_message}</p> : null}
              </div>
            ) : null}
            {result.error ? <p className="meridian-recovery__error" role="alert">{result.error}</p> : null}
          </div>
        ) : null}

        {phase === "checkpoints" ? (
          <div className="meridian-recovery__phase" key="checkpoints">
            <div className="meridian-recovery__list-head"><p>{catalog.length ? `Showing ${visibleCatalog.length} of ${catalog.length}.` : "No checkpoints found yet."} Automatic checkpoints stay server-local.</p>{catalog.length > CHECKPOINT_LIMIT ? <button onClick={() => setShowAll((current) => !current)} type="button">{showAll ? "Show fewer" : `Show all ${catalog.length}`}</button> : null}</div>
            <div className="meridian-recovery__rows">
              {visibleCatalog.map((checkpoint, index) => {
                const isSelected = checkpoint.checkpoint_id === selectedId;
                const status = STATUS_LABELS[checkpoint.catalog_status] || "UNVERIFIED";
                return (
                  <button className={`${isSelected ? "is-selected" : ""}${checkpoint.checkpoint_id === newCheckpointId ? " is-new" : ""}`} key={checkpoint.checkpoint_id} onClick={() => selectCheckpoint(checkpoint.checkpoint_id)} type="button">
                    <span className="meridian-recovery__row-number">{String(index + 1).padStart(2, "0")}</span><i className="meridian-recovery__radio"><b /></i>
                    <span className="meridian-recovery__row-copy"><strong>{triggerLabel(checkpoint.backup_trigger)}</strong><small>{dateLabel(checkpoint.created_at_utc)} · {checkpoint.auto_checkpoint ? "Automatic checkpoint" : "Manual checkpoint"}</small></span>
                    {checkpoint.checkpoint_id === newCheckpointId ? <em>JUST CREATED</em> : null}
                    <span className="meridian-recovery__row-size">{bytesLabel(checkpoint.total_size_bytes)}</span>
                    <span className={`meridian-recovery__status is-${checkpoint.catalog_status}`}><i />{status}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {phase === "verify" ? (
          <div className="meridian-recovery__phase" key="verify">
            {selected ? (
              <>
                <div className="meridian-recovery__verify-head"><div><h3>{triggerLabel(selected.backup_trigger)}</h3><p>{dateLabel(selected.created_at_utc)} · {selected.auto_checkpoint ? "Automatic" : "Manual"} checkpoint</p></div><div className="meridian-recovery__meta"><span>{selected.backup_format_version ? `V${selected.backup_format_version}` : "LEGACY"}</span><span>{selected.backup_key_source || "UNKNOWN KEY"}</span><span>{bytesLabel(selected.total_size_bytes)}</span></div></div>
                <div className="meridian-recovery__id-row"><code>ID {selected.checkpoint_id}</code><button onClick={() => navigator.clipboard?.writeText(selected.checkpoint_id)} type="button">Copy ID</button></div>
                <div className="meridian-recovery__actions"><button className="primary-button" disabled={Boolean(actionPending)} onClick={() => requestCheckpointAction("preview")} type="button">Preview recovery</button><button className="ghost-button" disabled={Boolean(actionPending)} onClick={() => requestCheckpointAction("inspect")} type="button">{actionPending === "inspect" ? "Inspecting…" : "Inspect"}</button><button className="ghost-button ghost-button--danger" disabled={Boolean(actionPending)} onClick={openDeleteConfirmation} type="button">Delete checkpoint</button></div>
                <p className="meridian-recovery__note">Preview compares against the live environment without restoring anything. Inspect validates the checkpoint without exposing raw contents.</p>
                {result.error ? <p className="meridian-recovery__error" role="alert">{result.error}</p> : null}
                {resultLines.length ? <div className="meridian-recovery__result"><strong>{result.mode.toUpperCase()} · {selected.checkpoint_id.slice(0, 22)}</strong>{resultLines.map((line, index) => <p className={`is-${line.tone}`} key={`${line.text}-${index}`}>{line.text}</p>)}</div> : null}
                <div className="meridian-recovery__divider" />
                <p className="meridian-recovery__label">OFF-HOST PROTECTION</p>
                <p className="meridian-recovery__note">Server-local checkpoints protect against bad scans and app mistakes, not drive failure. Copy them to secure off-host storage using host access.</p>
                {selected.backup_key_source === "auto" ? <p className="meridian-recovery__off-host-warning">The encrypted checkpoint file alone is not portable without this server’s backup keyring. Use a manual-passphrase backup for independent off-host recovery.</p> : null}
                <div className="meridian-recovery__paths"><div><code>{sameHostPaths ? backupsDirectory : "Server-local backup directory"}</code>{sameHostPaths ? <button onClick={() => navigator.clipboard?.writeText(backupsDirectory)} type="button">Copy</button> : null}</div><div><code>{sameHostPaths ? selected.path : `backups/${selected.checkpoint_id}`}</code>{sameHostPaths ? <button onClick={() => navigator.clipboard?.writeText(selected.path)} type="button">Copy</button> : null}</div></div>
                <div className={`meridian-recovery__warning${auditState.error ? " is-unavailable" : ""}`}><i />{warningText}</div>
              </>
            ) : <p className="meridian-recovery__note">Select a checkpoint in stage 2 first.</p>}
          </div>
        ) : null}

        </div>
        {recentAuthState.unknown ? (
          <div className="meridian-recovery__recent-auth-unknown" role="alert">
            <span>{recentAuthState.error}</span>
            <button disabled={recentAuthState.checking} onClick={() => runWithRecentAuth(pendingSensitiveIntentRef.current)} type="button">
              {recentAuthState.checking ? "Retrying…" : "Retry"}
            </button>
          </div>
        ) : null}
        <div className="meridian-recovery__nav"><button disabled={phase === "create"} onClick={() => setPhase(phase === "verify" ? "checkpoints" : "create")} type="button">Back</button>{phase !== "verify" ? <button onClick={() => setPhase(phase === "create" ? "checkpoints" : "verify")} type="button">{phase === "create" ? "Continue to checkpoints" : "Continue to verify & protect"}</button> : null}</div>
      </section>

      {stepUp.open ? <div className="meridian-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="recovery-step-up-title"><div aria-hidden="true" className="meridian-recovery-dialog__backdrop" onClick={closeRecoveryDialog} /><form className="meridian-recovery-dialog__card" onSubmit={submitStepUp}><p className="meridian-recovery__label">RECENT ADMIN AUTHENTICATION</p><h2 id="recovery-step-up-title">Confirm your password</h2><p>Recovery actions require a recent password confirmation for this browser session.</p><NonLoginSecretInput autoFocus autoComplete="new-password" onChange={(event) => setStepUp((current) => ({ ...current, password: event.target.value, error: "" }))} placeholder="Current admin password" purpose="recovery-recent-auth" value={stepUp.password} />{stepUp.error ? <p className="meridian-recovery__error" role="alert">{stepUp.error}</p> : null}<div><button className="ghost-button" disabled={stepUp.pending} onClick={closeRecoveryDialog} type="button">Cancel</button><button className="primary-button" disabled={stepUp.pending || !stepUp.password} type="submit">{stepUp.pending ? "Confirming…" : "Confirm"}</button></div></form></div> : null}

      {checkpointPassphrase.open ? <div className="meridian-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="checkpoint-passphrase-title"><div aria-hidden="true" className="meridian-recovery-dialog__backdrop" onClick={closeRecoveryDialog} /><form className="meridian-recovery-dialog__card" onSubmit={(event) => { event.preventDefault(); runCheckpointAction(checkpointPassphrase.mode, checkpointPassphrase.checkpointId, checkpointPassphrase.value); }}><p className="meridian-recovery__label">MANUAL CHECKPOINT</p><h2 id="checkpoint-passphrase-title">Enter checkpoint passphrase</h2><NonLoginSecretInput autoFocus autoComplete="new-password" onChange={(event) => setCheckpointPassphrase((current) => ({ ...current, value: event.target.value, error: "" }))} placeholder="Checkpoint passphrase" purpose="checkpoint-passphrase" value={checkpointPassphrase.value} />{checkpointPassphrase.error ? <p className="meridian-recovery__error" role="alert">{checkpointPassphrase.error}</p> : null}<div><button className="ghost-button" onClick={closeRecoveryDialog} type="button">Cancel</button><button className="primary-button" disabled={!checkpointPassphrase.value || Boolean(actionPending)} type="submit">Continue</button></div></form></div> : null}

      {deleteConfirm && selected ? <div className="meridian-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="checkpoint-delete-title"><div aria-hidden="true" className="meridian-recovery-dialog__backdrop" onClick={closeRecoveryDialog} /><div className="meridian-recovery-dialog__card"><p className="meridian-recovery__label">DELETE CHECKPOINT</p><h2 id="checkpoint-delete-title">Delete this checkpoint?</h2><p><strong>{selected.checkpoint_id}</strong><br />{dateLabel(selected.created_at_utc)} · {bytesLabel(selected.total_size_bytes)}. This cannot be undone.</p><div><button className="ghost-button" disabled={actionPending === "delete"} onClick={closeRecoveryDialog} type="button">Cancel</button><button className="ghost-button ghost-button--danger" disabled={actionPending === "delete"} onClick={confirmDelete} type="button">{actionPending === "delete" ? "Deleting…" : "Delete checkpoint"}</button></div></div></div> : null}
    </div>
  );
}
