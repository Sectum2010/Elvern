export const ADMIN_BACKUP_EVENT_TYPES = [
  "backup_job_updated",
  "backup_job_completed",
  "backup_job_failed",
  "backup_checkpoint_deleted",
];

export const ADMIN_BACKUP_EVENT = "elvern:admin-backup-event";

export function dispatchAdminBackupEvent(eventType) {
  if (typeof window === "undefined" || !ADMIN_BACKUP_EVENT_TYPES.includes(eventType)) {
    return;
  }
  window.dispatchEvent(new CustomEvent(ADMIN_BACKUP_EVENT, {
    detail: { eventType },
  }));
}
