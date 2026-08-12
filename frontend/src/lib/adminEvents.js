export const ADMIN_BACKUP_EVENT_TYPES = [
  "backup_job_updated",
  "backup_job_completed",
  "backup_job_failed",
  "backup_checkpoint_deleted",
];

export const ADMIN_BACKUP_EVENT = "elvern:admin-backup-event";
export const ADMIN_BACKUP_STREAM_STATUS_EVENT = "elvern:admin-backup-stream-status";

export function dispatchAdminBackupEvent(eventType) {
  if (typeof window === "undefined" || !ADMIN_BACKUP_EVENT_TYPES.includes(eventType)) {
    return;
  }
  window.dispatchEvent(new CustomEvent(ADMIN_BACKUP_EVENT, {
    detail: { eventType },
  }));
}

export function dispatchAdminBackupStreamStatus(connected) {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent(ADMIN_BACKUP_STREAM_STATUS_EVENT, {
    detail: { connected: Boolean(connected) },
  }));
}
