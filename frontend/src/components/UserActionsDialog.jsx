import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export const USER_ACTION_TABS = [
  { key: "account", label: "Account" },
  { key: "assistant", label: "Assistant" },
  { key: "downloads", label: "Downloads" },
];

function UserActionsTabSection({ badge = "", children, description, legacy, title, variant }) {
  return (
    <section aria-label={title} className={legacy ? "admin-user-actions-modal__section" : `meridian-user-actions__panel meridian-user-actions__panel--${variant}`}>
      {legacy ? (
        <div className="admin-user-actions-modal__section-header"><h3>{title}</h3><p className="page-subnote">{description}</p></div>
      ) : (
        <div className="meridian-user-actions__panel-header"><p>{description}</p>{badge ? <span>{badge}</span> : null}</div>
      )}
      {children}
    </section>
  );
}

export function MeridianUserActionsAccountTab({
  actionItems,
  afterAgeContent = null,
  ageCredential,
  ageOptions,
  beforeAgeContent = null,
  disabled = false,
  formatAgeCredential,
  legacy = false,
  onAgeChange,
  onSaveAge,
  onToggleAllAges,
  quickAges = [18, 16, 13],
  showAllAges = false,
}) {
  return (
    <UserActionsTabSection
      description="Role changes and password updates still require your current admin password."
      legacy={legacy}
      title="Account actions"
      variant="account"
    >
      <div className="admin-list__actions meridian-user-actions__account-actions">
        {actionItems.map((action) => (
          <button
            className={action.danger ? "ghost-button ghost-button--danger" : "ghost-button"}
            disabled={action.disabled}
            key={action.key}
            onClick={action.onClick}
            type="button"
          >
            {action.label}
          </button>
        ))}
      </div>
      {beforeAgeContent}
      <div className="admin-inline-form admin-age-credential-editor">
        <span className="admin-age-credential-editor__label">AGE CREDENTIAL</span>
        <div className="admin-age-credential-editor__controls">
          <div className="admin-age-credential-editor__quick" role="group" aria-label="Quick age credential choices">
            {quickAges.map((age) => (
              <button
                className={Number(ageCredential) === age ? "is-active" : ""}
                disabled={disabled}
                key={age}
                onClick={() => onAgeChange(age)}
                type="button"
              >
                {formatAgeCredential(age)}
              </button>
            ))}
            <button
              aria-expanded={showAllAges}
              className={showAllAges ? "is-active" : ""}
              onClick={onToggleAllAges}
              type="button"
            >
              More ages
            </button>
          </div>
          {showAllAges ? (
            <select
              aria-label="All age credentials"
              className="admin-select admin-age-credential-editor__select"
              disabled={disabled}
              onChange={(event) => onAgeChange(Number(event.target.value))}
              value={ageCredential}
            >
              {ageOptions.map((age) => (
                <option key={age} value={age}>{formatAgeCredential(age)}</option>
              ))}
            </select>
          ) : null}
        </div>
        <div className="admin-list__actions">
          <button className="primary-button" disabled={disabled} onClick={onSaveAge} type="button">
            Save age credential
          </button>
        </div>
      </div>
      {afterAgeContent}
    </UserActionsTabSection>
  );
}

export function MeridianUserActionsAssistantTab({
  disabled = false,
  enabled,
  isStandardUser,
  legacy = false,
  onToggle,
}) {
  return (
    <UserActionsTabSection
      description="Secondary access only for the safe structured request form."
      legacy={legacy}
      title="Assistant"
      variant="assistant"
    >
      {isStandardUser ? (
        <div className="assistant-access-toggle assistant-access-toggle--modal">
          <span
            aria-hidden="true"
            className={`meridian-user-actions__assistant-dot${enabled ? " is-enabled" : ""}`}
          />
          <div>
            <strong>{enabled ? "Enabled" : "Disabled"}</strong>
            <p className="page-subnote">
              {enabled
                ? "This user can access the Assistant request flow."
                : "This user cannot access the Assistant request flow."}
            </p>
          </div>
          <button
            className={enabled ? "ghost-button" : "primary-button"}
            disabled={disabled}
            onClick={onToggle}
            type="button"
          >
            {enabled ? "Disable Assistant" : "Enable Assistant"}
          </button>
        </div>
      ) : (
        <p className="page-subnote">
          Admins always have Assistant access. The account switch is only configurable for standard users.
        </p>
      )}
    </UserActionsTabSection>
  );
}

export function MeridianUserActionsDownloadsTab({
  accessState,
  disabled = false,
  dirty,
  formatBytes,
  isAdmin,
  legacy = false,
  onAddMovie,
  onModeChange,
  onRemoveMovie,
  onSave,
  onSearchChange,
}) {
  const modes = [
    { key: "none", title: "No download access", description: "Hide download actions for this user." },
    { key: "all", title: "Enable access to all movies", description: "Allow downloading every visible movie." },
    { key: "selected", title: "Select available movies", description: "Grant individual movies one at a time." },
  ];
  return (
    <UserActionsTabSection
      badge={legacy ? "" : "BETA"}
      description="Download grants are separate from playback access."
      legacy={legacy}
      title="Download Access (Beta)"
      variant="downloads"
    >
      {isAdmin ? (
        <div className="download-access-card download-access-card--readonly">
          <strong>Full download access</strong>
          <p className="page-subnote">Admins inherently have access to every movie they can view.</p>
        </div>
      ) : accessState.loading ? (
        <p className="page-subnote">Loading download access...</p>
      ) : (
        <div className="download-access-card">
          {modes.map((mode) => (
            <label className="settings-toggle settings-toggle--compact" key={mode.key}>
              <span>
                <strong>{mode.title}</strong>
                <small>{mode.description}</small>
              </span>
              <input
                checked={accessState.accessMode === mode.key}
                name="download-access-mode"
                onChange={() => onModeChange(mode.key)}
                type="radio"
              />
            </label>
          ))}

          {accessState.accessMode === "selected" ? (
            <div className="download-access-picker">
              <label className="search-field">
                <span className="sr-only">Search movies for download access</span>
                <input
                  autoComplete="off"
                  onChange={(event) => onSearchChange(event.target.value)}
                  placeholder="Search movies to add"
                  type="search"
                  value={accessState.searchQuery}
                />
              </label>
              {accessState.searchQuery.trim() ? (
                <div className="download-access-results">
                  {accessState.searchPending ? <p className="page-subnote">Searching...</p> : null}
                  {!accessState.searchPending && accessState.searchResults.length === 0 ? (
                    <p className="page-subnote">No matching movies.</p>
                  ) : null}
                  {accessState.searchResults.slice(0, 8).map((item) => (
                    <button className="download-access-result" key={item.id} onClick={() => onAddMovie(item)} type="button">
                      <strong>{item.title}</strong>
                      <span>{formatBytes(item.file_size)}</span>
                    </button>
                  ))}
                </div>
              ) : null}
              {accessState.selectedItems.length > 0 ? (
                <div className="download-access-selected">
                  {accessState.selectedItems.map((item) => (
                    <span className="download-access-chip" key={item.id}>
                      {item.title}
                      <button aria-label={`Remove ${item.title}`} onClick={() => onRemoveMovie(item.id)} type="button">X</button>
                    </span>
                  ))}
                </div>
              ) : (
                <p className="page-subnote">No selected movies yet.</p>
              )}
            </div>
          ) : null}
          {accessState.error ? <p className="form-error">{accessState.error}</p> : null}
          {accessState.feedback ? <p className="action-feedback">{accessState.feedback}</p> : null}
          {dirty ? <p className="download-access-card__dirty">Unsaved changes</p> : null}
          <button className="primary-button" disabled={disabled || !dirty} onClick={onSave} type="button">
            {accessState.saving ? "Saving..." : "Save download access"}
          </button>
        </div>
      )}
    </UserActionsTabSection>
  );
}

export function UserActionsDialog({
  activeTab,
  children,
  crown,
  legacy = false,
  legacyAvatarClass = "",
  onRequestClose,
  onTabChange,
  returnFocusElement,
  user,
}) {
  const dialogRef = useRef(null);
  const onRequestCloseRef = useRef(onRequestClose);
  onRequestCloseRef.current = onRequestClose;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.querySelector(FOCUSABLE_SELECTOR)?.focus();

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        onRequestCloseRef.current();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const focusable = [...dialog.querySelectorAll(FOCUSABLE_SELECTOR)];
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
      returnFocusElement?.focus?.();
    };
  }, [returnFocusElement]);

  const statusLabel = user.status_label || (user.enabled ? "Offline" : "Disabled");
  const isActive = /active/i.test(statusLabel);
  const statusColor = {
    green: "var(--mer-status-active)",
    grey: "var(--mer-status-offline)",
    red: "var(--mer-danger)",
    yellow: "var(--mer-status-background)",
    orange: "var(--mer-status-pending)",
  }[user.status_color] || "var(--mer-status-offline)";
  const activityMetadata = [
    user.last_login_at ? `Last login ${user.last_login_label}` : "",
    user.last_activity_at ? `Last activity ${user.last_activity_label}` : "",
    user.last_seen_at ? `Last heartbeat ${user.last_heartbeat_label}` : "",
  ].filter(Boolean).join(" · ");

  if (legacy) {
    return (
      <div aria-labelledby="admin-user-actions-modal-title" aria-modal="true" className="browser-resume-modal" role="dialog">
        <div aria-hidden="true" className="browser-resume-modal__backdrop" onClick={onRequestClose} />
        <div className="browser-resume-modal__card detail-info-modal__card admin-user-actions-modal" ref={dialogRef}>
          <div className="detail-info-modal__header admin-user-actions-modal__header">
            <div className="detail-info-modal__copy">
              <p className="eyebrow detail-info-modal__eyebrow">User actions</p>
              <div className="admin-user-actions-modal__title-row">
                <div aria-hidden="true" className={`user-avatar-button user-avatar-button--static ${legacyAvatarClass}`}>
                  <span className="user-avatar-button__initials">{(user.username || "??").slice(0, 2).toUpperCase()}</span>
                </div>
                <div className="admin-user-actions-modal__title-copy">
                  <h2 className="detail-info-modal__title" id="admin-user-actions-modal-title">{user.username}{crown}</h2>
                  <div className="admin-user-actions-modal__subtitle"><span>{statusLabel}</span><span>{user.active_sessions} live session{user.active_sessions === 1 ? "" : "s"}</span></div>
                </div>
              </div>
              <p className="page-subnote">Last login {user.last_login_label}</p>
            </div>
            <button className="ghost-button detail-info-modal__close" onClick={onRequestClose} type="button">Close</button>
          </div>
          <div className="detail-info-modal__body admin-user-actions-modal__body">{children}</div>
        </div>
      </div>
    );
  }

  return (
    <div
      aria-labelledby="admin-user-actions-modal-title"
      aria-modal="true"
      className="meridian-user-actions"
      role="dialog"
    >
      <div aria-hidden="true" className="meridian-user-actions__backdrop" onClick={onRequestClose} />
      <div className="meridian-user-actions__card" ref={dialogRef}>
        <div className="meridian-user-actions__top">
          <header className="meridian-user-actions__header">
            <div aria-hidden="true" className="meridian-user-actions__avatar">
              {(user.username || "??").slice(0, 2).toUpperCase()}
            </div>
            <div className="meridian-user-actions__identity">
              <p className="meridian-user-actions__eyebrow">USER ACTIONS</p>
              <div className="meridian-user-actions__name-line">
                <h2 id="admin-user-actions-modal-title">{user.username}</h2>
                {crown}
                <span
                  aria-hidden="true"
                  className={`meridian-user-actions__status-dot${isActive ? " meridian-user-actions__status-dot--active" : ""}`}
                  style={{ backgroundColor: statusColor }}
                />
                <span className="meridian-user-actions__status" style={{ color: statusColor }}>
                  {statusLabel}
                </span>
                <span className="meridian-user-actions__sessions">
                  · {user.active_sessions} live session{user.active_sessions === 1 ? "" : "s"}
                </span>
              </div>
              {activityMetadata ? <p className="meridian-user-actions__last-login" title={activityMetadata}>{activityMetadata}</p> : null}
            </div>
            <button className="meridian-user-actions__close" onClick={onRequestClose} type="button">
              Close
            </button>
          </header>
          <div aria-label="User action sections" className="meridian-user-actions__tabs" role="tablist">
            {USER_ACTION_TABS.map((tab) => (
              <button
                aria-controls={`user-actions-panel-${tab.key}`}
                aria-selected={activeTab === tab.key}
                className={activeTab === tab.key ? "is-active" : ""}
                id={`user-actions-tab-${tab.key}`}
                key={tab.key}
                onClick={() => onTabChange(tab.key)}
                role="tab"
                type="button"
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
        <div
          aria-labelledby={`user-actions-tab-${activeTab}`}
          className="meridian-user-actions__body"
          id={`user-actions-panel-${activeTab}`}
          role="tabpanel"
        >
          {children}
        </div>
      </div>
    </div>
  );
}
