import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LoadingView } from "../components/LoadingView";
import { useAuth } from "../auth/AuthContext";
import { apiRequest } from "../lib/api";
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
const ADMIN_SECTIONS = [
  { key: "panel", label: "Admin Panel", icon: "panel" },
  { key: "security", label: "Security", icon: "security" },
  { key: "logs", label: "Logs", icon: "logs" },
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


function UserStatusIndicator({ color, label }) {
  return (
    <span className="user-status-pill" title={label}>
      <span aria-hidden="true" className={`user-status-indicator user-status-indicator--${color}`} />
      <span className="user-status-pill__label">{label}</span>
    </span>
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
  const [rescanPending, setRescanPending] = useState(false);
  const [createPending, setCreatePending] = useState(false);
  const [userActionPending, setUserActionPending] = useState(null);
  const [sessionActionPending, setSessionActionPending] = useState(null);
  const [showAllSessions, setShowAllSessions] = useState(false);
  const [showAllAudit, setShowAllAudit] = useState(false);
  const [userFeedback, setUserFeedback] = useState({});
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
        setBanner({
          tone: "success",
          text: status.last_scan?.message || "Library scan completed.",
        });
      }
      scanRunningRef.current = Boolean(status.scan.running);
      setStatusPayload(status);
      setUsersPayload(users.users);
      setSessionsPayload(sessions.sessions);
      setAuditPayload(audit.events);
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to load admin data",
      });
    } finally {
      if (!silent) {
        setLoading(false);
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

  async function handleRescan() {
    if (rescanPending) {
      return;
    }
    setRescanPending(true);
    setBanner(null);
    try {
      const payload = await apiRequest("/api/library/rescan", { method: "POST" });
      setBanner({ tone: "success", text: payload.message || "Library scan started." });
      scanRunningRef.current = Boolean(payload.running);
      await loadAdminData({ silent: true });
    } catch (requestError) {
      setBanner({
        tone: "error",
        text: requestError.message || "Failed to start scan",
      });
    } finally {
      setRescanPending(false);
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

  if (loading && !statusPayload) {
    return <LoadingView label="Loading admin tools..." />;
  }

  const visibleSessions = showAllSessions ? sessionsPayload : sessionsPayload.slice(0, 8);
  const visibleAuditEvents = showAllAudit ? auditPayload : auditPayload.slice(0, 10);

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
      <div className="admin-list">
        {usersPayload.map((entry) => {
          const isSelf = entry.id === user?.id;
          const roleChangeOpen = roleConfirm.userId === entry.id;
          const passwordOpen = passwordEditor.userId === entry.id;
          return (
            <div className="admin-list__row" key={entry.id}>
              <div>
                <div className="admin-user-heading">
                  <strong>{entry.username}</strong>
                  <UserStatusIndicator color={entry.status_color} label={entry.status_label} />
                </div>
                <p className="page-subnote">
                  {entry.role} · {entry.enabled ? "enabled" : "disabled"} · {entry.active_sessions} live session{entry.active_sessions === 1 ? "" : "s"} · last login {formatDate(entry.last_login_at)}
                </p>
                {entry.last_seen_at ? <p className="page-subnote">Last heartbeat {formatDate(entry.last_seen_at)}{entry.last_activity_at ? ` · last activity ${formatDate(entry.last_activity_at)}` : ""}</p> : null}
                {isSelf ? (
                  <p className="page-subnote">Your own admin account cannot be disabled. Use the self-delete flow below if needed.</p>
                ) : null}
                <InlineFeedback feedback={userFeedback[entry.id]} />
              </div>

              <div className="admin-action-stack">
                <div className="admin-list__actions">
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

                  {!isSelf ? (
                    <button
                      className="ghost-button"
                      disabled={userActionPending === entry.id}
                      onClick={() => {
                        setPasswordEditor({
                          userId: null,
                          username: "",
                          newPassword: "",
                          currentAdminPassword: "",
                        });
                        setRoleConfirm({
                          userId: entry.id,
                          username: entry.username,
                          nextRole: entry.role === "admin" ? "standard_user" : "admin",
                          currentAdminPassword: "",
                        });
                      }}
                      type="button"
                    >
                      Make {entry.role === "admin" ? "standard" : "admin"}
                    </button>
                  ) : null}

                  <button
                    className="ghost-button"
                    disabled={userActionPending === entry.id}
                    onClick={() => {
                      setRoleConfirm({
                        userId: null,
                        username: "",
                        nextRole: "standard_user",
                        currentAdminPassword: "",
                      });
                      setPasswordEditor({
                        userId: entry.id,
                        username: entry.username,
                        newPassword: "",
                        currentAdminPassword: "",
                      });
                    }}
                    type="button"
                  >
                    {isSelf ? "Update my password" : "Reset password"}
                  </button>
                </div>

                {entry.role === "standard_user" ? (
                  <div className="assistant-access-toggle">
                    <div>
                      <strong>Assistant (Beta)</strong>
                      <p className="page-subnote">Secondary access only for the safe structured request form.</p>
                    </div>
                    <button
                      className={entry.assistant_beta_enabled ? "ghost-button" : "primary-button"}
                      disabled={userActionPending === entry.id}
                      onClick={() => handleAssistantAccessToggle(entry)}
                      type="button"
                    >
                      {entry.assistant_beta_enabled ? "Disable Assistant" : "Enable Assistant"}
                    </button>
                  </div>
                ) : null}

                {roleChangeOpen ? (
                  <form
                    className="admin-inline-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      handleSubmitRoleChange(entry);
                    }}
                  >
                    <p className="page-subnote">
                      Confirm making {entry.username} {roleConfirm.nextRole === "admin" ? "an admin" : "a standard user"}.
                    </p>
                    <input
                      autoComplete="current-password"
                      onChange={(event) =>
                        setRoleConfirm((current) => ({
                          ...current,
                          currentAdminPassword: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
                      type="password"
                      value={roleConfirm.currentAdminPassword}
                    />
                    <div className="admin-list__actions">
                      <button className="primary-button" disabled={userActionPending === entry.id} type="submit">
                        Confirm role change
                      </button>
                      <button
                        className="ghost-button"
                        onClick={() => clearUserEditors(entry.id)}
                        type="button"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : null}

                {passwordOpen ? (
                  <form
                    className="admin-inline-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      handleSubmitPassword(entry);
                    }}
                  >
                    <input
                      autoComplete="new-password"
                      onChange={(event) =>
                        setPasswordEditor((current) => ({
                          ...current,
                          newPassword: event.target.value,
                        }))
                      }
                      placeholder="New password"
                      type="password"
                      value={passwordEditor.newPassword}
                    />
                    <input
                      autoComplete="current-password"
                      onChange={(event) =>
                        setPasswordEditor((current) => ({
                          ...current,
                          currentAdminPassword: event.target.value,
                        }))
                      }
                      placeholder="Current admin password"
                      type="password"
                      value={passwordEditor.currentAdminPassword}
                    />
                    <div className="admin-list__actions">
                      <button className="primary-button" disabled={userActionPending === entry.id} type="submit">
                        Save password
                      </button>
                      <button
                        className="ghost-button"
                        onClick={() => clearUserEditors(entry.id)}
                        type="button"
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );

  const createUserCard = (
    <section className="settings-card">
      <h2>Create user</h2>
      <form className="admin-form" onSubmit={handleCreateUser}>
        <label>
          Username
          <input
            onChange={(event) => setCreateUserForm((current) => ({ ...current, username: event.target.value }))}
            required
            type="text"
            value={createUserForm.username}
          />
        </label>
        <label>
          Password
          <input
            onChange={(event) => setCreateUserForm((current) => ({ ...current, password: event.target.value }))}
            required
            type="password"
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

  const selfDeleteCard = (
    <section className="settings-card admin-sidecard">
      <div>
        <h2>Delete my admin account</h2>
        <p className="page-subnote">
          This is separate from disable. Elvern requires another enabled admin before your own account can be deleted.
        </p>
      </div>
      {!hasAnotherEnabledAdmin ? (
        <p className="form-error">Create another enabled admin before deleting your own account.</p>
      ) : null}
      {!selfDeleteState.open ? (
        <button
          className="ghost-button ghost-button--danger"
          disabled={!hasAnotherEnabledAdmin}
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
          Start self-delete
        </button>
      ) : (
        <div className="admin-danger-block">
          {!selfDeleteState.armed ? (
            <form className="admin-inline-form" onSubmit={handleSelfDeletePrecheck}>
              <p className="page-subnote">
                Enter your current admin password first. You will see one final destructive confirmation before anything is deleted.
              </p>
              <input
                autoComplete="current-password"
                onChange={(event) =>
                  setSelfDeleteState((current) => ({
                    ...current,
                    password: event.target.value,
                  }))
                }
                placeholder="Current admin password"
                type="password"
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
      )}
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
        <button
          className="ghost-button ghost-button--inline admin-nav-card__rescan"
          disabled={rescanPending}
          onClick={handleRescan}
          type="button"
        >
          {rescanPending ? "Starting scan..." : "Rescan"}
        </button>
      </div>

      <FeedbackBanner banner={banner} />

      {statusPayload ? (
        <div className="admin-section-stack">
          {activeSection === "panel" ? (
            <>
              {usersCard}
              <div className="admin-section-grid">
                {createUserCard}
                {selfDeleteCard}
              </div>
            </>
          ) : null}

          {activeSection === "security" ? securitySection : null}

          {activeSection === "logs" ? logsSection : null}

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
    </section>
  );
}
