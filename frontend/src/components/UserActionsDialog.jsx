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
                style={{ backgroundColor: user.status_color }}
              />
              <span className="meridian-user-actions__status" style={{ color: user.status_color }}>
                {statusLabel}
              </span>
              <span className="meridian-user-actions__sessions">
                · {user.active_sessions} live session{user.active_sessions === 1 ? "" : "s"}
              </span>
            </div>
            <p className="meridian-user-actions__last-login">Last login {user.last_login_label}</p>
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
