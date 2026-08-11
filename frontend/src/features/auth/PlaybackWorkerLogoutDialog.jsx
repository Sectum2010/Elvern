import { buildLogoutPlaybackWorkerPrompt } from "../../lib/playbackWorkerOwnership.js";


export function PlaybackWorkerLogoutDialog({ coordinator }) {
  const {
    closeWorkerChoice,
    error,
    keepPreparing,
    pendingChoice,
    terminateAndLogout,
    workerChoice,
  } = coordinator;
  if (!workerChoice) return null;

  return (
    <div
      aria-labelledby="logout-playback-worker-modal-title"
      aria-modal="true"
      className="browser-resume-modal"
      role="dialog"
    >
      <div aria-hidden="true" className="browser-resume-modal__backdrop" onClick={closeWorkerChoice} />
      <div className="browser-resume-modal__card detail-info-modal__card playback-worker-choice-modal">
        <div className="detail-info-modal__copy">
          <p className="eyebrow detail-info-modal__eyebrow">PLAYBACK WORKER</p>
          <p className="detail-info-modal__title" id="logout-playback-worker-modal-title">
            {buildLogoutPlaybackWorkerPrompt(workerChoice.movieTitle)}
          </p>
          {error ? <p className="page-subnote playback-worker-choice-modal__error" role="alert">{error}</p> : null}
        </div>
        <div className="browser-resume-modal__actions playback-worker-choice-modal__actions">
          <button className="primary-button" disabled={Boolean(pendingChoice)} onClick={keepPreparing} type="button">
            Keep Preparing
          </button>
          <button
            className="ghost-button ghost-button--danger"
            disabled={Boolean(pendingChoice)}
            onClick={terminateAndLogout}
            type="button"
          >
            Terminate Process
          </button>
        </div>
      </div>
    </div>
  );
}
