import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest } from "../lib/api";
import { ADMIN_BACKUP_EVENT } from "../lib/adminEvents.js";
import { NonLoginSecretInput } from "./NonLoginSecretInput";

const CHECKPOINT_LIMIT = 4;
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

function safeResultLines(mode, payload) {
  if (!payload) return [];
  if (mode === "inspect") {
    return [
      { tone: payload.valid ? "ok" : "bad", text: payload.valid ? "✓ archive readable" : "× archive validation failed" },
      { tone: payload.valid ? "ok" : "bad", text: payload.valid ? "✓ manifest and file hashes verified" : "× manifest or file verification failed" },
      { tone: payload.db_integrity_check_result === "ok" ? "ok" : "warn", text: `✓ database integrity: ${payload.db_integrity_check_result || "unknown"}` },
      { tone: "ok", text: `✓ ${payload.files_verified || 0} files verified` },
    ];
  }
  const matches = Object.values(payload.settings_matches || {}).filter(Boolean).length;
  const differences = Object.values(payload.settings_matches || {}).length - matches;
  return [
    { tone: payload.checkpoint_valid ? "ok" : "bad", text: payload.checkpoint_valid ? "✓ checkpoint valid" : "× checkpoint has blocking errors" },
    { tone: payload.schema_compatible ? "ok" : "warn", text: payload.schema_compatible ? "✓ schema compatible" : "● schema differs" },
    { tone: "ok", text: `✓ users: backup ${payload.backup_counts?.users ?? 0} → live ${payload.current_counts?.users ?? 0}` },
    { tone: "ok", text: `✓ media index: backup ${payload.backup_counts?.media_items ?? 0} → live ${payload.current_counts?.media_items ?? 0}` },
    { tone: differences ? "warn" : "ok", text: `✓ settings/reference summary: ${matches} match · ${differences} differ` },
    { tone: "muted", text: "● preview only — nothing restored or changed" },
    ...(payload.blocking_errors || []).map((text) => ({ tone: "bad", text: `× ${text}` })),
    ...(payload.warnings || []).map((text) => ({ tone: "warn", text: `● ${text}` })),
  ];
}

export function RecoveryPanel({ onToast }) {
  const [phase, setPhase] = useState("create");
  const [keySource, setKeySource] = useState("auto");
  const [passphrase, setPassphrase] = useState("");
  const [passphraseConfirmation, setPassphraseConfirmation] = useState("");
  const [catalog, setCatalog] = useState([]);
  const [backupsDirectory, setBackupsDirectory] = useState("backups");
  const [catalogState, setCatalogState] = useState({ loading: true, error: "" });
  const [selectedId, setSelectedId] = useState("");
  const [newCheckpointId, setNewCheckpointId] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState({ mode: "", payload: null, error: "" });
  const [actionPending, setActionPending] = useState("");
  const [stepUp, setStepUp] = useState({ open: false, password: "", pending: false, error: "" });
  const [checkpointPassphrase, setCheckpointPassphrase] = useState({ open: false, mode: "", value: "", error: "" });
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [auditState, setAuditState] = useState({ loading: true, error: "", warnings: [] });
  const panelRef = useRef(null);
  const pendingSensitiveActionRef = useRef(null);
  const pendingSensitiveActionKindRef = useRef("");
  const createIntentRef = useRef({ idempotencyKey: "", pending: false });
  const completionHandledRef = useRef("");
  const dialogReturnFocusRef = useRef(null);

  const rememberDialogReturnFocus = useCallback(() => {
    if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
      dialogReturnFocusRef.current = document.activeElement;
    }
  }, []);

  const closeRecoveryDialog = useCallback(() => {
    if (stepUp.pending || actionPending === "delete") {
      return;
    }
    if (pendingSensitiveActionKindRef.current === "create") {
      createIntentRef.current = { idempotencyKey: "", pending: false };
    }
    pendingSensitiveActionRef.current = null;
    pendingSensitiveActionKindRef.current = "";
    setStepUp({ open: false, password: "", pending: false, error: "" });
    setCheckpointPassphrase({ open: false, mode: "", value: "", error: "" });
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
      const payload = await apiRequest("/api/admin/backups");
      const checkpoints = payload.checkpoints || [];
      setCatalog(checkpoints);
      setBackupsDirectory(payload.backups_dir || "backups");
      setSelectedId((current) => {
        const requested = preferredId || current;
        return checkpoints.some((entry) => entry.checkpoint_id === requested)
          ? requested
          : (checkpoints[0]?.checkpoint_id || "");
      });
      setCatalogState({ loading: false, error: "" });
    } catch (error) {
      setCatalogState((current) => ({ ...current, loading: false, error: error.message || "Checkpoint catalog is unavailable." }));
    }
  }, []);

  const loadAuditWarnings = useCallback(async () => {
    setAuditState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const payload = await apiRequest("/api/admin/audit?limit=100");
      const warnings = (payload.events || []).filter((event) =>
        /backup/i.test(event.action || "") && event.outcome !== "success").slice(0, 6);
      setAuditState({ loading: false, error: "", warnings });
    } catch (error) {
      setAuditState((current) => ({ ...current, loading: false, error: error.message || "Backup audit status is unavailable." }));
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    Promise.allSettled([
      loadCatalog(),
      loadAuditWarnings(),
      apiRequest("/api/admin/backup-jobs/active").then((payload) => {
        if (!disposed) setJob(payload.job || null);
      }),
    ]);
    return () => { disposed = true; };
  }, [loadAuditWarnings, loadCatalog]);

  useEffect(() => {
    if (!job?.id || !ACTIVE_JOB_STATES.has(job.state)) return undefined;
    let disposed = false;
    const poll = async () => {
      try {
        const payload = await apiRequest(`/api/admin/backup-jobs/${encodeURIComponent(job.id)}`);
        if (!disposed) setJob(payload);
      } catch {
        // Keep the last real milestone. Polling will retry while the job is active.
      }
    };
    const interval = window.setInterval(poll, 1000);
    poll();
    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, [job?.id, job?.state]);

  useEffect(() => {
    let disposed = false;
    async function handleBackupEvent(event) {
      try {
        if (job?.id) {
          const payload = await apiRequest(`/api/admin/backup-jobs/${encodeURIComponent(job.id)}`);
          if (!disposed) setJob(payload);
        } else {
          const payload = await apiRequest("/api/admin/backup-jobs/active");
          if (!disposed && payload.job) setJob(payload.job);
        }
      } catch {
        // The persisted polling path remains the recovery fallback.
      }
      if (["backup_job_completed", "backup_job_failed", "backup_checkpoint_deleted"].includes(event.detail?.eventType)) {
        await Promise.allSettled([loadCatalog(), loadAuditWarnings()]);
      }
    }
    window.addEventListener(ADMIN_BACKUP_EVENT, handleBackupEvent);
    return () => {
      disposed = true;
      window.removeEventListener(ADMIN_BACKUP_EVENT, handleBackupEvent);
    };
  }, [job?.id, loadAuditWarnings, loadCatalog]);

  useEffect(() => {
    if (job?.state !== "completed" || !job.checkpoint_id || completionHandledRef.current === job.id) return;
    completionHandledRef.current = job.id;
    setNewCheckpointId(job.checkpoint_id);
    setPassphrase("");
    setPassphraseConfirmation("");
    loadCatalog({ preferredId: job.checkpoint_id }).then(() => setPhase("checkpoints"));
    onToast?.({ tone: "success", text: "Encrypted backup checkpoint created." });
  }, [job, loadCatalog, onToast]);

  const selected = useMemo(
    () => catalog.find((entry) => entry.checkpoint_id === selectedId) || null,
    [catalog, selectedId],
  );
  const visibleCatalog = showAll ? catalog : catalog.slice(0, CHECKPOINT_LIMIT);
  const sameHostPaths = backupsDirectory.startsWith("/");

  const runWithRecentAuth = useCallback(async (action, actionKind = "") => {
    let status;
    try {
      status = await apiRequest("/api/admin/backups/recent-auth/status");
    } catch {
      // The password step-up is the safe fallback when status cannot be confirmed.
    }
    if (status?.verified) {
      await action();
      return;
    }
    rememberDialogReturnFocus();
    pendingSensitiveActionRef.current = action;
    pendingSensitiveActionKindRef.current = actionKind;
    setStepUp({ open: true, password: "", pending: false, error: "" });
  }, [rememberDialogReturnFocus]);

  async function submitStepUp(event) {
    event.preventDefault();
    if (!stepUp.password || stepUp.pending) return;
    setStepUp((current) => ({ ...current, pending: true, error: "" }));
    try {
      await apiRequest("/api/admin/backups/recent-auth", {
        method: "POST",
        data: { current_admin_password: stepUp.password },
      });
      const action = pendingSensitiveActionRef.current;
      pendingSensitiveActionRef.current = null;
      pendingSensitiveActionKindRef.current = "";
      setStepUp({ open: false, password: "", pending: false, error: "" });
      await action?.();
    } catch (error) {
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
      if (passphrase !== passphraseConfirmation) {
        setResult({ mode: "", payload: null, error: "Passphrases do not match." });
        return;
      }
    }
    if (!createIntentRef.current.idempotencyKey) {
      createIntentRef.current.idempotencyKey = crypto.randomUUID();
    }
    createIntentRef.current.pending = true;
    const idempotencyKey = createIntentRef.current.idempotencyKey;
    const requestedPassphrase = keySource === "passphrase" ? passphrase : null;
    runWithRecentAuth(async () => {
      setActionPending("create");
      setResult({ mode: "", payload: null, error: "" });
      try {
        const payload = await apiRequest("/api/admin/backup-jobs", {
          method: "POST",
          data: {
            key_source: keySource,
            passphrase: requestedPassphrase,
            passphrase_confirmation: requestedPassphrase,
            idempotency_key: idempotencyKey,
          },
        });
        setJob(payload);
        createIntentRef.current.idempotencyKey = "";
        setPassphrase("");
        setPassphraseConfirmation("");
      } catch (error) {
        if (Number(error?.status) >= 400 && Number(error?.status) < 500) {
          createIntentRef.current.idempotencyKey = "";
        }
        setResult({ mode: "", payload: null, error: error.message || "Backup creation could not start." });
        setPassphrase("");
        setPassphraseConfirmation("");
      } finally {
        createIntentRef.current.pending = false;
        setActionPending("");
      }
    }, "create");
  }

  function selectCheckpoint(checkpointId) {
    setSelectedId(checkpointId);
    setResult({ mode: "", payload: null, error: "" });
    setCheckpointPassphrase({ open: false, mode: "", value: "", error: "" });
  }

  function requestCheckpointAction(mode) {
    if (!selected) return;
    if (selected.backup_key_source === "passphrase") {
      rememberDialogReturnFocus();
      setCheckpointPassphrase({ open: true, mode, value: "", error: "" });
      return;
    }
    runCheckpointAction(mode, null);
  }

  function runCheckpointAction(mode, suppliedPassphrase) {
    if (suppliedPassphrase) {
      setCheckpointPassphrase({ open: false, mode: "", value: "", error: "" });
    }
    runWithRecentAuth(async () => {
      setActionPending(mode);
      setResult({ mode: "", payload: null, error: "" });
      try {
        const endpoint = mode === "inspect"
          ? `/api/admin/backups/${encodeURIComponent(selectedId)}/inspect`
          : `/api/admin/backups/${encodeURIComponent(selectedId)}/preview`;
        const payload = mode === "inspect" && !suppliedPassphrase
          ? await apiRequest(endpoint)
          : await apiRequest(endpoint, { method: "POST", data: { passphrase: suppliedPassphrase } });
        setResult({ mode, payload, error: "" });
        setCheckpointPassphrase({ open: false, mode: "", value: "", error: "" });
        await loadCatalog({ preferredId: selectedId });
      } catch (error) {
        const message = error.message || "Checkpoint verification failed.";
        if (suppliedPassphrase && ["backup_passphrase_invalid", "backup_wrong_passphrase"].includes(errorCode(error))) {
          setCheckpointPassphrase({ open: true, mode, value: "", error: message });
        } else {
          setResult({ mode, payload: null, error: message });
        }
      } finally {
        setActionPending("");
      }
    });
  }

  function confirmDelete() {
    if (!selected) return;
    runWithRecentAuth(async () => {
      setActionPending("delete");
      try {
        await apiRequest(`/api/admin/backups/${encodeURIComponent(selected.checkpoint_id)}`, {
          method: "DELETE",
          data: { checkpoint_id: selected.checkpoint_id, confirm: true },
        });
        const oldIndex = catalog.findIndex((entry) => entry.checkpoint_id === selected.checkpoint_id);
        const remaining = catalog.filter((entry) => entry.checkpoint_id !== selected.checkpoint_id);
        const neighbor = remaining[Math.min(Math.max(oldIndex, 0), Math.max(remaining.length - 1, 0))];
        setCatalog(remaining);
        setSelectedId(neighbor?.checkpoint_id || "");
        setResult({ mode: "", payload: null, error: "" });
        setDeleteConfirm(false);
        onToast?.({ tone: "success", text: "Backup checkpoint deleted." });
        loadAuditWarnings();
      } catch (error) {
        setResult({ mode: "delete", payload: null, error: error.message || "Checkpoint deletion failed." });
      } finally {
        setActionPending("");
      }
    });
  }

  function openDeleteConfirmation() {
    rememberDialogReturnFocus();
    setDeleteConfirm(true);
  }

  const running = job && ACTIVE_JOB_STATES.has(job.state);
  const resultLines = safeResultLines(result.mode, result.payload);
  const warningText = auditState.loading
    ? "Loading backup warning status…"
    : auditState.error
      ? "Backup warning status unavailable."
      : auditState.warnings.length
        ? `${auditState.warnings.length} recent backup warning${auditState.warnings.length === 1 ? "" : "s"}.`
        : "No recent backup warnings are visible in the loaded audit log.";

  return (
    <div className="meridian-recovery" data-visual-landmark="recovery-panel" ref={panelRef}>
      <section className="meridian-recovery__summary meridian-card">
        <div className="meridian-recovery__chips">
          <span>ADMIN-ONLY</span><span>EXCLUDES MEDIA FILES</span><span className="is-danger">CONTAINS SECRETS — NEVER SHARE</span>
        </div>
        <div className="meridian-recovery__summary-row">
          <div><h2>Backup &amp; Recovery</h2><p>Encrypted checkpoints protect Elvern runtime state, not movie or poster files.</p></div>
          <div className="meridian-recovery__metrics">
            <strong>{catalogState.error ? "Unavailable" : catalog.length}</strong>
            <span>{catalogState.error ? "catalog status" : "checkpoints"}</span>
            <small>{catalog[0]?.created_at_utc ? `Latest ${dateLabel(catalog[0].created_at_utc)}` : "No checkpoint yet"}</small>
          </div>
        </div>
        {catalogState.error ? <p className="meridian-recovery__error" role="alert">{catalogState.error}</p> : null}
        <nav aria-label="Recovery stages" className="meridian-recovery__stages">
          {STAGES.map((stage, index) => (
            <button className={phase === stage.key ? "is-active" : ""} key={stage.key} onClick={() => setPhase(stage.key)} type="button">
              <i style={{ background: index <= STAGES.findIndex((entry) => entry.key === phase) ? "#2e4fe0" : undefined }} />{stage.label}
            </button>
          ))}
        </nav>
      </section>

      <section className="meridian-recovery__stage meridian-card">
        {phase === "create" ? (
          <div className="meridian-recovery__phase" key="create">
            <p className="meridian-recovery__label">ENCRYPTION KEY</p>
            <div className="meridian-recovery__segments" role="group" aria-label="Backup encryption key">
              <button className={keySource === "auto" ? "is-active" : ""} onClick={() => setKeySource("auto")} type="button">Auto key</button>
              <button className={keySource === "passphrase" ? "is-active" : ""} onClick={() => setKeySource("passphrase")} type="button">Manual passphrase</button>
            </div>
            <p className="meridian-recovery__note">
              {keySource === "auto"
                ? "Convenient server-local rollback protection using Elvern’s independent backup keyring."
                : "Recommended for long-term or off-host recovery. Elvern never stores the passphrase."}
            </p>
            {keySource === "passphrase" ? (
              <div className="meridian-recovery__passphrases">
                <NonLoginSecretInput autoComplete="new-password" onChange={(event) => setPassphrase(event.target.value)} placeholder="Passphrase" purpose="recovery-create-passphrase" value={passphrase} />
                <NonLoginSecretInput autoComplete="new-password" onChange={(event) => setPassphraseConfirmation(event.target.value)} placeholder="Confirm passphrase" purpose="recovery-create-passphrase-confirm" value={passphraseConfirmation} />
              </div>
            ) : null}
            <div className="meridian-recovery__actions">
              <button className="primary-button" disabled={running || actionPending === "create"} onClick={createBackup} type="button">Create encrypted backup</button>
              <button className="ghost-button" disabled={catalogState.loading} onClick={() => { loadCatalog(); loadAuditWarnings(); }} type="button">{catalogState.loading ? "Refreshing…" : "Refresh"}</button>
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
                <div className="meridian-recovery__paths"><div><code>{sameHostPaths ? backupsDirectory : "Server-local backup directory"}</code>{sameHostPaths ? <button onClick={() => navigator.clipboard?.writeText(backupsDirectory)} type="button">Copy</button> : null}</div><div><code>{sameHostPaths ? selected.path : `backups/${selected.checkpoint_id}`}</code>{sameHostPaths ? <button onClick={() => navigator.clipboard?.writeText(selected.path)} type="button">Copy</button> : null}</div></div>
                <div className={`meridian-recovery__warning${auditState.error ? " is-unavailable" : ""}`}><i />{warningText}</div>
              </>
            ) : <p className="meridian-recovery__note">Select a checkpoint in stage 2 first.</p>}
          </div>
        ) : null}

        <div className="meridian-recovery__nav"><button disabled={phase === "create"} onClick={() => setPhase(phase === "verify" ? "checkpoints" : "create")} type="button">Back</button>{phase !== "verify" ? <button onClick={() => setPhase(phase === "create" ? "checkpoints" : "verify")} type="button">{phase === "create" ? "Continue to checkpoints" : "Continue to verify & protect"}</button> : null}</div>
      </section>

      {stepUp.open ? <div className="meridian-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="recovery-step-up-title"><div aria-hidden="true" className="meridian-recovery-dialog__backdrop" onClick={closeRecoveryDialog} /><form className="meridian-recovery-dialog__card" onSubmit={submitStepUp}><p className="meridian-recovery__label">RECENT ADMIN AUTHENTICATION</p><h2 id="recovery-step-up-title">Confirm your password</h2><p>Recovery actions require a recent password confirmation for this browser session.</p><NonLoginSecretInput autoFocus autoComplete="new-password" onChange={(event) => setStepUp((current) => ({ ...current, password: event.target.value, error: "" }))} placeholder="Current admin password" purpose="recovery-recent-auth" value={stepUp.password} />{stepUp.error ? <p className="meridian-recovery__error" role="alert">{stepUp.error}</p> : null}<div><button className="ghost-button" disabled={stepUp.pending} onClick={closeRecoveryDialog} type="button">Cancel</button><button className="primary-button" disabled={stepUp.pending || !stepUp.password} type="submit">{stepUp.pending ? "Confirming…" : "Confirm"}</button></div></form></div> : null}

      {checkpointPassphrase.open ? <div className="meridian-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="checkpoint-passphrase-title"><div aria-hidden="true" className="meridian-recovery-dialog__backdrop" onClick={closeRecoveryDialog} /><form className="meridian-recovery-dialog__card" onSubmit={(event) => { event.preventDefault(); runCheckpointAction(checkpointPassphrase.mode, checkpointPassphrase.value); }}><p className="meridian-recovery__label">MANUAL CHECKPOINT</p><h2 id="checkpoint-passphrase-title">Enter checkpoint passphrase</h2><NonLoginSecretInput autoFocus autoComplete="new-password" onChange={(event) => setCheckpointPassphrase((current) => ({ ...current, value: event.target.value, error: "" }))} placeholder="Checkpoint passphrase" purpose="checkpoint-passphrase" value={checkpointPassphrase.value} />{checkpointPassphrase.error ? <p className="meridian-recovery__error" role="alert">{checkpointPassphrase.error}</p> : null}<div><button className="ghost-button" onClick={closeRecoveryDialog} type="button">Cancel</button><button className="primary-button" disabled={!checkpointPassphrase.value || Boolean(actionPending)} type="submit">Continue</button></div></form></div> : null}

      {deleteConfirm && selected ? <div className="meridian-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="checkpoint-delete-title"><div aria-hidden="true" className="meridian-recovery-dialog__backdrop" onClick={closeRecoveryDialog} /><div className="meridian-recovery-dialog__card"><p className="meridian-recovery__label">DELETE CHECKPOINT</p><h2 id="checkpoint-delete-title">Delete this checkpoint?</h2><p><strong>{selected.checkpoint_id}</strong><br />{dateLabel(selected.created_at_utc)} · {bytesLabel(selected.total_size_bytes)}. This cannot be undone.</p><div><button className="ghost-button" disabled={actionPending === "delete"} onClick={closeRecoveryDialog} type="button">Cancel</button><button className="ghost-button ghost-button--danger" disabled={actionPending === "delete"} onClick={confirmDelete} type="button">{actionPending === "delete" ? "Deleting…" : "Delete checkpoint"}</button></div></div></div> : null}
    </div>
  );
}
