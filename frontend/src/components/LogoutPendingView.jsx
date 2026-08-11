export function LogoutPendingView({ error = "", onRetry, retrying = false }) {
  return (
    <main className="centered-state logout-pending-view" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      <h1>Signing out</h1>
      <p>
        {error || "Elvern is confirming that this session has ended."}
      </p>
      {error ? (
        <button className="ghost-button" disabled={retrying} onClick={onRetry} type="button">
          {retrying ? "Retrying..." : "Retry"}
        </button>
      ) : null}
    </main>
  );
}
