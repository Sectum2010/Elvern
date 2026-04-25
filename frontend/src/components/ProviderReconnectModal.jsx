export function ProviderReconnectModal({
  open,
  eyebrow = "Provider connection",
  title,
  message,
  errorMessage = "",
  reconnectLabel = "Reconnect Google Drive",
  secondaryLabel = "Later",
  reconnectPending = false,
  allowReconnect = true,
  onReconnect,
  onSecondary,
  onClose,
}) {
  if (!open) {
    return null;
  }

  const handleClose = reconnectPending ? undefined : onClose || onSecondary;

  return (
    <div
      aria-labelledby="provider-reconnect-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div
        aria-hidden="true"
        className="browser-resume-modal__backdrop"
        onClick={handleClose}
      />
      <div className="browser-resume-modal__card">
        <p className="eyebrow">{eyebrow}</p>
        <h2 id="provider-reconnect-modal-title">{title}</h2>
        <p className="page-subnote">{message}</p>
        {errorMessage ? <p className="form-error">{errorMessage}</p> : null}
        <div className="browser-resume-modal__actions">
          {allowReconnect ? (
            <button
              className="primary-button"
              disabled={reconnectPending}
              onClick={onReconnect}
              type="button"
            >
              {reconnectPending ? "Connecting..." : reconnectLabel}
            </button>
          ) : null}
          {secondaryLabel ? (
            <button
              className="ghost-button"
              disabled={reconnectPending && allowReconnect}
              onClick={onSecondary}
              type="button"
            >
              {secondaryLabel}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
