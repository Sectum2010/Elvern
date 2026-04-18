import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { AssistantAttachmentGallery } from "../components/AssistantAttachmentGallery";
import { LoadingView } from "../components/LoadingView";
import { apiRequest } from "../lib/api";
import { formatDate } from "../lib/format";


const REQUEST_TYPES = [
  ["bug_report", "Bug report"],
  ["improvement_suggestion", "Improvement suggestion"],
  ["library_issue", "Library issue"],
  ["playback_issue", "Playback issue"],
  ["security_concern", "Security concern"],
  ["account_request", "Account request"],
  ["other", "Other"],
];

const URGENCY_OPTIONS = [
  ["low", "Low"],
  ["normal", "Normal"],
  ["high", "High"],
];


function summarizePlatform() {
  if (typeof navigator === "undefined") {
    return "";
  }
  const userAgent = (navigator.userAgent || "").toLowerCase();
  if (userAgent.includes("iphone")) {
    return "iPhone";
  }
  if (userAgent.includes("ipad")) {
    return "iPad";
  }
  if (userAgent.includes("android")) {
    return "Android";
  }
  if (userAgent.includes("mac os")) {
    return "macOS";
  }
  if (userAgent.includes("windows")) {
    return "Windows";
  }
  if (userAgent.includes("linux")) {
    return "Linux";
  }
  return navigator.platform || "Web";
}


function deriveAssistantContext(fromPath) {
  const path = typeof fromPath === "string" && fromPath && fromPath !== "/assistant" ? fromPath : "";
  if (!path) {
    return {
      page_context: null,
      source_context: null,
      related_entity_type: null,
      related_entity_id: null,
    };
  }
  const detailMatch = path.match(/^\/library\/(\d+)$/);
  if (detailMatch) {
    return {
      page_context: path,
      source_context: "library_detail",
      related_entity_type: "media_item",
      related_entity_id: detailMatch[1],
    };
  }
  if (path === "/library/local") {
    return { page_context: path, source_context: "library_local", related_entity_type: null, related_entity_id: null };
  }
  if (path === "/library/cloud") {
    return { page_context: path, source_context: "library_cloud", related_entity_type: null, related_entity_id: null };
  }
  if (path.startsWith("/library")) {
    return { page_context: path, source_context: "library", related_entity_type: null, related_entity_id: null };
  }
  if (path.startsWith("/settings")) {
    return { page_context: path, source_context: "settings", related_entity_type: null, related_entity_id: null };
  }
  if (path.startsWith("/install")) {
    return { page_context: path, source_context: "install", related_entity_type: null, related_entity_id: null };
  }
  return { page_context: path, source_context: "other", related_entity_type: null, related_entity_id: null };
}


function AssistantRequestCard({ entry, active, onSelect }) {
  return (
    <button
      className={active ? "assistant-request-card assistant-request-card--active" : "assistant-request-card"}
      onClick={() => onSelect(entry.id)}
      type="button"
    >
      <div className="assistant-request-card__header">
        <strong>{entry.request_number}</strong>
        <span className={`status-pill status-pill--assistant status-pill--assistant-${entry.status}`}>{entry.status.replaceAll("_", " ")}</span>
      </div>
      <h3>{entry.title}</h3>
      <p className="page-subnote">
        {entry.request_type.replaceAll("_", " ")} · {entry.urgency} · {formatDate(entry.created_at)}
      </p>
    </button>
  );
}


export function AssistantPage() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [requests, setRequests] = useState([]);
  const [selectedRequestId, setSelectedRequestId] = useState(null);
  const [selectedRequest, setSelectedRequest] = useState(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const safeContext = useMemo(
    () => deriveAssistantContext(location.state?.fromPath),
    [location.state],
  );
  const attachmentsSectionRef = useRef(null);
  const selectedRequestParam = searchParams.get("requestId");
  const returnSection = searchParams.get("section");
  const [submitPanelExpanded, setSubmitPanelExpanded] = useState(false);
  const [formState, setFormState] = useState({
    request_type: "bug_report",
    title: "",
    description: "",
    repro_steps: "",
    expected_result: "",
    actual_result: "",
    urgency: "normal",
    attachments: [],
  });

  async function loadRequests() {
    const payload = await apiRequest("/api/assistant/requests");
    setRequests(payload.requests || []);
  }

  function updateRequestContext(nextRequestId, nextSection = null) {
    const nextParams = new URLSearchParams(searchParams);
    if (nextRequestId) {
      nextParams.set("requestId", String(nextRequestId));
    } else {
      nextParams.delete("requestId");
    }
    if (nextRequestId && nextSection) {
      nextParams.set("section", nextSection);
    } else {
      nextParams.delete("section");
    }
    setSearchParams(nextParams, { replace: true });
  }

  function handleRequestSelect(requestId) {
    if (String(selectedRequestId || "") === String(requestId)) {
      updateRequestContext(null);
      return;
    }
    updateRequestContext(requestId);
  }

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const payload = await apiRequest("/api/assistant/requests");
        if (!active) {
          return;
        }
        const nextRequests = payload.requests || [];
        setRequests(nextRequests);
      } catch (requestError) {
        if (active) {
          setError(requestError.message || "Failed to load Assistant requests");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setSelectedRequestId(selectedRequestParam || null);
  }, [selectedRequestParam]);

  useEffect(() => {
    if (!selectedRequestParam || !requests.length) {
      return;
    }
    const requestExists = requests.some((entry) => String(entry.id) === String(selectedRequestParam));
    if (!requestExists) {
      updateRequestContext(null);
      setSelectedRequest(null);
    }
  }, [requests, selectedRequestParam]);

  useEffect(() => {
    let active = true;
    if (!selectedRequestId) {
      setSelectedRequest(null);
      return undefined;
    }
    (async () => {
      try {
        const payload = await apiRequest(`/api/assistant/requests/${selectedRequestId}`);
        if (active) {
          setSelectedRequest(payload.request);
        }
      } catch (requestError) {
        if (active) {
          setError(requestError.message || "Failed to load request detail");
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [selectedRequestId]);

  useEffect(() => {
    if (returnSection !== "attachments" || !selectedRequest?.attachments?.length) {
      return;
    }
    const node = attachmentsSectionRef.current;
    if (!node) {
      return;
    }
    window.requestAnimationFrame(() => {
      node.scrollIntoView({ block: "start", behavior: "auto" });
    });
  }, [returnSection, selectedRequest?.id]);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitPanelExpanded(true);
    setSubmitting(true);
    setError("");
    setMessage("");
    try {
      const formData = new FormData();
      formData.set("request_type", formState.request_type);
      formData.set("title", formState.title);
      formData.set("description", formState.description);
      formData.set("urgency", formState.urgency);
      if (formState.repro_steps.trim()) {
        formData.set("repro_steps", formState.repro_steps);
      }
      if (formState.expected_result.trim()) {
        formData.set("expected_result", formState.expected_result);
      }
      if (formState.actual_result.trim()) {
        formData.set("actual_result", formState.actual_result);
      }
      if (safeContext.page_context) {
        formData.set("page_context", safeContext.page_context);
      }
      formData.set("platform", summarizePlatform());
      if (safeContext.source_context) {
        formData.set("source_context", safeContext.source_context);
      }
      if (safeContext.related_entity_type) {
        formData.set("related_entity_type", safeContext.related_entity_type);
      }
      if (safeContext.related_entity_id) {
        formData.set("related_entity_id", safeContext.related_entity_id);
      }
      for (const attachment of formState.attachments) {
        formData.append("attachments", attachment);
      }
      const payload = await apiRequest("/api/assistant/requests", {
        method: "POST",
        data: formData,
      });
      setMessage(`${payload.request.request_number} submitted.`);
      setFormState({
        request_type: formState.request_type,
        title: "",
        description: "",
        repro_steps: "",
        expected_result: "",
        actual_result: "",
        urgency: formState.urgency,
        attachments: [],
      });
      await loadRequests();
      updateRequestContext(null);
      setSelectedRequest(null);
    } catch (requestError) {
      setSubmitPanelExpanded(true);
      setError(requestError.message || "Failed to submit Assistant request");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return <LoadingView label="Loading Assistant..." />;
  }

  return (
    <section className="page-section page-section--assistant">
      <div className="assistant-page-stack">
        <details
          className="settings-card settings-card--wide assistant-request-form-card settings-disclosure"
          onToggle={(event) => setSubmitPanelExpanded(event.currentTarget.open)}
          open={submitPanelExpanded}
        >
          <summary className="settings-disclosure__summary">
            <div className="settings-disclosure__header">
              <span className="settings-disclosure__title">Submit a request</span>
              <span className="settings-disclosure__copy">
                Open this when you want to send a new Assistant request.
              </span>
            </div>
            <div className="settings-disclosure__summary-meta">
              <span className={submitPanelExpanded ? "settings-disclosure__chevron settings-disclosure__chevron--open" : "settings-disclosure__chevron"}>⌄</span>
            </div>
          </summary>

          <div className="settings-disclosure__body">
            <p className="eyebrow">Assistant (Beta)</p>
            <p className="page-subnote">
              This beta only stores safe request records and placeholder workflow data. It does not run models or system actions yet.
            </p>
            {message ? <p className="page-note">{message}</p> : null}
            {error ? <p className="form-error">{error}</p> : null}
            <form className="assistant-form" onSubmit={handleSubmit}>
              <div className="assistant-form__grid">
                <label>
                  Type
                  <select
                    className="admin-select"
                    onChange={(event) => setFormState((current) => ({ ...current, request_type: event.target.value }))}
                    value={formState.request_type}
                  >
                    {REQUEST_TYPES.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Urgency
                  <select
                    className="admin-select"
                    onChange={(event) => setFormState((current) => ({ ...current, urgency: event.target.value }))}
                    value={formState.urgency}
                  >
                    {URGENCY_OPTIONS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                Title
                <input
                  onChange={(event) => setFormState((current) => ({ ...current, title: event.target.value }))}
                  required
                  type="text"
                  value={formState.title}
                />
              </label>
              <label>
                Description
                <textarea
                  onChange={(event) => setFormState((current) => ({ ...current, description: event.target.value }))}
                  required
                  rows={5}
                  value={formState.description}
                />
              </label>
              <label>
                Repro steps
                <textarea
                  onChange={(event) => setFormState((current) => ({ ...current, repro_steps: event.target.value }))}
                  rows={3}
                  value={formState.repro_steps}
                />
              </label>
              <div className="assistant-form__grid">
                <label>
                  Expected result
                  <textarea
                    onChange={(event) => setFormState((current) => ({ ...current, expected_result: event.target.value }))}
                    rows={3}
                    value={formState.expected_result}
                  />
                </label>
                <label>
                  Actual result
                  <textarea
                    onChange={(event) => setFormState((current) => ({ ...current, actual_result: event.target.value }))}
                    rows={3}
                    value={formState.actual_result}
                  />
                </label>
              </div>
              <label>
                Optional screenshots
                <input
                  accept="image/*"
                  multiple
                  onChange={(event) => setFormState((current) => ({
                    ...current,
                    attachments: Array.from(event.target.files || []),
                  }))}
                  type="file"
                />
                {formState.attachments.length > 0 ? (
                  <small className="page-subnote">{formState.attachments.length} attachment(s) selected.</small>
                ) : null}
              </label>
              <div className="assistant-context-row">
                {safeContext.page_context ? <span className="status-pill">Page {safeContext.page_context}</span> : null}
                <span className="status-pill">{summarizePlatform()}</span>
              </div>
              <button className="primary-button" disabled={submitting} type="submit">
                {submitting ? "Submitting..." : "Submit request"}
              </button>
            </form>
          </div>
        </details>

        <div className={selectedRequest ? "assistant-detail-grid" : "assistant-detail-grid assistant-detail-grid--single"}>
          <section className="settings-card">
            <div className="settings-inline-header">
              <div>
                <h2>My requests</h2>
                <p className="page-subnote">Only your own Assistant requests are shown here.</p>
              </div>
            </div>
            <div className="assistant-request-list">
              {requests.length > 0 ? requests.map((entry) => (
                <AssistantRequestCard
                  active={String(entry.id) === String(selectedRequestId || "")}
                  entry={entry}
                  key={entry.id}
                  onSelect={handleRequestSelect}
                />
              )) : <p className="page-subnote">No Assistant requests submitted yet.</p>}
            </div>
          </section>

          {selectedRequest ? (
            <section className="settings-card assistant-request-detail-card">
            <div className="settings-inline-header">
              <div>
                <h2>Request detail</h2>
                <p className="page-subnote">Safe request content only. No logs, env, or diagnostic surfaces are exposed here.</p>
              </div>
            </div>
              <div className="assistant-request-detail">
                <div className="assistant-request-detail__header">
                  <strong>{selectedRequest.request_number}</strong>
                  <span className={`status-pill status-pill--assistant status-pill--assistant-${selectedRequest.status}`}>{selectedRequest.status.replaceAll("_", " ")}</span>
                </div>
                <h3>{selectedRequest.title}</h3>
                <p>{selectedRequest.description}</p>
                <div className="assistant-meta-grid">
                  <div><span>Type</span><strong>{selectedRequest.request_type.replaceAll("_", " ")}</strong></div>
                  <div><span>Urgency</span><strong>{selectedRequest.urgency}</strong></div>
                  <div><span>Created</span><strong>{formatDate(selectedRequest.created_at)}</strong></div>
                  <div><span>Status</span><strong>{selectedRequest.status.replaceAll("_", " ")}</strong></div>
                </div>
                {selectedRequest.repro_steps ? (
                  <div className="assistant-detail-block">
                    <h4>Repro steps</h4>
                    <p>{selectedRequest.repro_steps}</p>
                  </div>
                ) : null}
                {selectedRequest.expected_result ? (
                  <div className="assistant-detail-block">
                    <h4>Expected result</h4>
                    <p>{selectedRequest.expected_result}</p>
                  </div>
                ) : null}
                {selectedRequest.actual_result ? (
                  <div className="assistant-detail-block">
                    <h4>Actual result</h4>
                    <p>{selectedRequest.actual_result}</p>
                  </div>
                ) : null}
                {selectedRequest.attachments.length > 0 ? (
                  <div className="assistant-detail-block" ref={attachmentsSectionRef}>
                    <h4>Attachments</h4>
                    <AssistantAttachmentGallery
                      attachments={selectedRequest.attachments}
                      returnPath={`/assistant?requestId=${selectedRequest.id}&section=attachments`}
                    />
                  </div>
                ) : null}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </section>
  );
}
