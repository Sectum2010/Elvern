import { Link } from "react-router-dom";
import { formatBytes } from "../lib/format";


export function AssistantAttachmentGallery({ attachments, returnPath = "/assistant" }) {
  if (!attachments?.length) {
    return null;
  }

  return (
    <div className="assistant-attachment-grid">
      {attachments.map((attachment) => {
        const label = attachment.original_filename || "Attachment";
        const params = new URLSearchParams({
          returnTo: returnPath,
          name: label,
        });
        if (attachment.mime_type) {
          params.set("mime", attachment.mime_type);
        }
        const viewerPath = `/attachments/${attachment.id}/view?${params.toString()}`;
        return (
          <Link
            className="assistant-attachment-card"
            key={attachment.id}
            to={viewerPath}
          >
            <div className="assistant-attachment-card__meta">
              <strong>{label}</strong>
              <p className="page-subnote">
                {attachment.mime_type || "unknown mime"} · {formatBytes(attachment.size_bytes || 0)}
              </p>
            </div>
          </Link>
        );
      })}
    </div>
  );
}
