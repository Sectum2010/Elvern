import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { apiRequest } from "../lib/api";


function normalizeReturnPath(value) {
  const candidate = String(value || "").trim();
  if (!candidate.startsWith("/") || candidate.startsWith("//")) {
    return "/assistant";
  }
  return candidate;
}

function detectAttachmentOpenPlatform() {
  if (typeof navigator === "undefined") {
    return "desktop";
  }
  const agent = String(navigator.userAgent || "").toLowerCase();
  if (agent.includes("iphone") || agent.includes("ipad") || agent.includes("ipod")) {
    return "ios";
  }
  if (agent.includes("android")) {
    return "android";
  }
  return "desktop";
}

function imageExternalOpenIsConstrained() {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return false;
  }
  const agent = String(navigator.userAgent || "").toLowerCase();
  const standaloneMedia = typeof window.matchMedia === "function"
    ? window.matchMedia("(display-mode: standalone)").matches
    : false;
  const navigatorStandalone = Boolean(navigator.standalone);
  const androidWebView = agent.includes("; wv)") || agent.includes(" version/") && agent.includes(" chrome/");
  const iosDevice = /iphone|ipad|ipod/.test(agent);
  const iosSafari = iosDevice && agent.includes("safari") && !agent.includes("crios") && !agent.includes("fxios");
  const iosWebView = iosDevice && !iosSafari;
  return standaloneMedia || navigatorStandalone || androidWebView || iosWebView;
}

function attachmentExtension(name) {
  const match = String(name || "").toLowerCase().match(/\.([a-z0-9]+)$/);
  return match ? match[1] : "";
}

function resolveOpenOriginalKind({ mime, name, externalUrl }) {
  const normalizedMime = String(mime || "").toLowerCase();
  const ext = attachmentExtension(name);
  const normalizedUrl = String(externalUrl || "").toLowerCase();

  if (normalizedMime === "application/vnd.google-apps.document" || normalizedUrl.includes("docs.google.com/document/")) {
    return "google_doc";
  }
  if (normalizedMime === "application/vnd.google-apps.presentation" || normalizedUrl.includes("docs.google.com/presentation/")) {
    return "google_slide";
  }
  if (normalizedMime.startsWith("video/") || normalizedMime.startsWith("audio/") || ["mp4", "mov", "m4v", "mp3", "wav"].includes(ext)) {
    return "media";
  }
  if (normalizedMime.startsWith("image/") || ["png", "jpg", "jpeg", "webp", "gif", "heic"].includes(ext)) {
    return "browser_image";
  }
  if (
    normalizedMime === "application/pdf"
    || normalizedMime.startsWith("text/")
    || normalizedMime === "text/markdown"
    || normalizedMime === "application/json"
    || normalizedMime === "application/xml"
    || normalizedMime === "text/csv"
    || normalizedMime === "text/html"
    || ["pdf", "txt", "md", "csv", "json", "xml", "html", "htm"].includes(ext)
  ) {
    return "browser_document";
  }
  return "generic_external";
}

function humanAttachmentTypeLabel({ mime, name, openKind }) {
  const normalizedMime = String(mime || "").toLowerCase();
  const ext = attachmentExtension(name);
  if (openKind === "google_doc") {
    return "Google Doc";
  }
  if (openKind === "google_slide") {
    return "Google Slides";
  }
  if (openKind === "media") {
    return normalizedMime.startsWith("audio/") || ["mp3", "wav"].includes(ext) ? "Audio" : "Video";
  }
  if (openKind === "browser_image") {
    return "Image";
  }
  if (normalizedMime === "application/pdf" || ext === "pdf") {
    return "PDF";
  }
  if (normalizedMime === "text/markdown" || ext === "md") {
    return "Markdown";
  }
  if (normalizedMime === "text/plain" || ext === "txt") {
    return "Text";
  }
  if (
    normalizedMime === "application/json"
    || normalizedMime === "application/xml"
    || normalizedMime === "text/csv"
    || normalizedMime === "text/html"
    || ["csv", "json", "xml", "html", "htm"].includes(ext)
  ) {
    return "Document";
  }
  return "File";
}

function openInNewBrowsingContext(url) {
  if (typeof window === "undefined") {
    return false;
  }
  const popup = window.open(url, "_blank", "noopener,noreferrer");
  if (!popup) {
    return false;
  }
  return true;
}


export function AssistantAttachmentViewerPage() {
  const { attachmentId } = useParams();
  const [searchParams] = useSearchParams();
  const [imageFallbackModalOpen, setImageFallbackModalOpen] = useState(false);
  const [imageFallbackMode, setImageFallbackMode] = useState("open");
  const [imageFallbackUrl, setImageFallbackUrl] = useState("");
  const [imageFallbackCopyState, setImageFallbackCopyState] = useState("");
  const [imageFallbackError, setImageFallbackError] = useState("");
  const [imageActionPending, setImageActionPending] = useState(false);
  const imageFallbackFieldRef = useRef(null);
  const mode = searchParams.get("mode") || "";
  const viewerModeOriginal = mode === "original";
  const rawReturnTo = searchParams.get("returnTo");
  const returnTo = normalizeReturnPath(rawReturnTo);
  const name = searchParams.get("name") || `Attachment ${attachmentId}`;
  const mime = searchParams.get("mime") || "";
  const externalUrl = searchParams.get("external") || "";
  const rawUrl = `/api/assistant/attachments/${attachmentId}`;
  const imageLike = mime.startsWith("image/");
  const mobilePlatform = detectAttachmentOpenPlatform();
  const constrainedImageExternalOpen = imageExternalOpenIsConstrained();
  const openOriginalUrl = externalUrl || rawUrl;
  const openOriginalKind = resolveOpenOriginalKind({
    mime,
    name,
    externalUrl: openOriginalUrl,
  });
  const typeLabel = humanAttachmentTypeLabel({
    mime,
    name,
    openKind: openOriginalKind,
  });
  const viewerSearchParams = new URLSearchParams(searchParams);
  viewerSearchParams.delete("mode");
  const viewerPath = `/attachments/${attachmentId}/view?${viewerSearchParams.toString()}`;
  const browserOriginalSearchParams = new URLSearchParams(searchParams);
  browserOriginalSearchParams.set("mode", "original");
  browserOriginalSearchParams.set("returnTo", viewerPath);
  const browserOriginalPath = `/attachments/${attachmentId}/view?${browserOriginalSearchParams.toString()}`;
  const effectiveReturnPath = viewerModeOriginal ? normalizeReturnPath(rawReturnTo || viewerPath) : returnTo;

  useEffect(() => {
    if (!imageFallbackModalOpen || imageFallbackMode !== "copy" || !imageFallbackUrl) {
      return;
    }
    const field = imageFallbackFieldRef.current;
    if (!field) {
      return;
    }
    field.focus();
    if (typeof field.select === "function") {
      field.select();
    }
  }, [imageFallbackModalOpen, imageFallbackMode, imageFallbackUrl]);

  async function mintImageExternalOpen() {
    const payload = await apiRequest(`/api/assistant/attachments/${attachmentId}/external-open`, {
      method: "POST",
    });
    return String(payload.external_open_url || "").trim();
  }

  function openImageFallbackModal({ mode = "open", url = "", error = "", note = "" } = {}) {
    setImageFallbackMode(mode);
    setImageFallbackUrl(url);
    setImageFallbackError(error);
    setImageFallbackCopyState(note);
    setImageFallbackModalOpen(true);
  }

  async function copyTextToClipboard(value) {
    if (!navigator?.clipboard?.writeText) {
      throw new Error("clipboard_unavailable");
    }
    await navigator.clipboard.writeText(value);
  }

  async function handleOpenOriginal(event) {
    event.preventDefault();
    if (openOriginalKind === "browser_image") {
      setImageActionPending(true);
      setImageFallbackCopyState("");
      setImageFallbackError("");
      try {
        const mintedUrl = await mintImageExternalOpen();
        if (constrainedImageExternalOpen) {
          openImageFallbackModal({ mode: "open", url: mintedUrl });
          return;
        }
        const opened = openInNewBrowsingContext(mintedUrl);
        if (!opened) {
          openImageFallbackModal({ mode: "open", url: mintedUrl });
        }
      } catch (requestError) {
        openImageFallbackModal({
          mode: "open",
          url: "",
          error: requestError.message || "Failed to create external image link.",
          note: "",
        });
      } finally {
        setImageActionPending(false);
      }
      return;
    }
    if (mobilePlatform === "desktop") {
      const opened = openInNewBrowsingContext(browserOriginalPath);
      if (!opened) {
        window.location.assign(browserOriginalPath);
      }
      return;
    }
    if (openOriginalKind === "browser_document") {
      const opened = openInNewBrowsingContext(browserOriginalPath);
      if (!opened) {
        window.location.assign(browserOriginalPath);
      }
      return;
    }
    window.location.assign(openOriginalUrl);
  }

  async function handleCopyImageLink() {
    let mintedUrl = "";
    try {
      mintedUrl = await mintImageExternalOpen();
      setImageFallbackUrl(mintedUrl);
      await copyTextToClipboard(mintedUrl);
      setImageFallbackUrl(mintedUrl);
      setImageFallbackError("");
      setImageFallbackCopyState("Link copied.");
      if (imageFallbackModalOpen && imageFallbackMode === "copy") {
        setImageFallbackModalOpen(false);
      }
    } catch (requestError) {
      const clipboardFailure = Boolean(mintedUrl);
      const message = requestError?.message === "clipboard_unavailable"
        ? "This browser does not allow clipboard access here."
        : requestError?.message || (clipboardFailure ? "Copy failed in this environment." : "Failed to create external image link.");
      if (!clipboardFailure) {
        setImageFallbackError(message);
        setImageFallbackCopyState("");
        return;
      }
      openImageFallbackModal({
        mode: "copy",
        url: mintedUrl,
        error: message,
        note: "",
      });
    }
  }

  async function handleCopyImageLinkAgain() {
    if (!imageFallbackUrl) {
      return;
    }
    try {
      await copyTextToClipboard(imageFallbackUrl);
      setImageFallbackError("");
      setImageFallbackCopyState("Link copied.");
      setImageFallbackModalOpen(false);
    } catch (requestError) {
      const message = requestError?.message === "clipboard_unavailable"
        ? "This browser does not allow clipboard access here."
        : requestError?.message || "Copy failed in this environment.";
      setImageFallbackError(message);
      setImageFallbackCopyState("");
    }
  }

  function handleOpenImageInCurrentContext() {
    setImageFallbackModalOpen(false);
    if (imageFallbackUrl) {
      window.location.assign(imageFallbackUrl);
    }
  }

  return (
    <section className="page-section page-section--assistant">
      <div className="assistant-page-stack">
        <section className="settings-card settings-card--wide assistant-attachment-viewer-card">
          <div className="assistant-attachment-viewer__header">
            <div className="assistant-attachment-viewer__copy">
              <p className="eyebrow">{viewerModeOriginal ? "Original" : "Attachment"}</p>
              <p className="assistant-attachment-viewer__type">{typeLabel}</p>
              <h1 className="assistant-attachment-viewer__name">{name}</h1>
            </div>
            <div className="assistant-attachment-viewer__actions">
              {!viewerModeOriginal ? (
                <button
                  className="ghost-button ghost-button--inline"
                  disabled={imageActionPending}
                  onClick={handleOpenOriginal}
                  type="button"
                >
                  {imageActionPending && openOriginalKind === "browser_image" ? "Preparing..." : "Open original"}
                </button>
              ) : null}
              <Link
                aria-label={viewerModeOriginal ? "Back to attachment viewer" : "Back to Assistant request"}
                className="assistant-back-button"
                to={effectiveReturnPath}
              >
                &lt;
              </Link>
            </div>
          </div>

          <div className="assistant-attachment-viewer__surface">
            {imageLike ? (
              <img
                alt={name}
                className="assistant-attachment-viewer__image"
                src={rawUrl}
              />
            ) : (
              <iframe
                className="assistant-attachment-viewer__frame"
                src={rawUrl}
                title={name}
              />
            )}
          </div>
        </section>
      </div>
      {imageFallbackModalOpen ? (
        <div
          aria-labelledby="attachment-image-fallback-title"
          aria-modal="true"
          className="browser-resume-modal"
          role="dialog"
        >
          <button
            aria-label="Close image open options"
            className="browser-resume-modal__backdrop"
            onClick={() => setImageFallbackModalOpen(false)}
            type="button"
          />
          <div className="browser-resume-modal__card">
            <p className="eyebrow">Image</p>
            <h2 id="attachment-image-fallback-title">Open image outside Elvern</h2>
            <p className="page-subnote">
              {imageFallbackMode === "copy"
                ? "This environment does not allow Elvern to copy the image link automatically. You can copy the raw image link manually, or open it directly."
                : "This environment cannot reliably open a separate browser tab from inside Elvern. You can open the image here anyway, or copy the raw link and open it elsewhere yourself."}
            </p>
            {imageFallbackError ? <p className="form-error">{imageFallbackError}</p> : null}
            {imageFallbackCopyState ? <p className="page-note">{imageFallbackCopyState}</p> : null}
            {imageFallbackMode === "copy" && imageFallbackUrl ? (
              <textarea
                aria-label="External image link"
                className="assistant-attachment-fallback-link"
                onFocus={(event) => event.currentTarget.select()}
                readOnly
                ref={imageFallbackFieldRef}
                rows={4}
                value={imageFallbackUrl}
              />
            ) : null}
            <div className="browser-resume-modal__actions">
              <button
                className="primary-button"
                disabled={!imageFallbackUrl}
                onClick={handleOpenImageInCurrentContext}
                type="button"
              >
                {imageFallbackMode === "copy" ? "Open image" : "Open image here anyway"}
              </button>
              <button
                className="ghost-button ghost-button--inline"
                onClick={imageFallbackMode === "copy" ? handleCopyImageLinkAgain : handleCopyImageLink}
                type="button"
              >
                {imageFallbackMode === "copy" ? "Copy again" : "Copy link"}
              </button>
              <button
                className="ghost-button ghost-button--inline"
                onClick={() => setImageFallbackModalOpen(false)}
                type="button"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
