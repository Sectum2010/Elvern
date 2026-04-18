import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { AssistantAttachmentGallery } from "../components/AssistantAttachmentGallery";
import { LoadingView } from "../components/LoadingView";
import { apiRequest } from "../lib/api";
import { formatDate } from "../lib/format";


const REQUEST_STATUS_OPTIONS = ["new", "triaged", "awaiting_admin", "approved", "rejected", "closed"];
const TRIAGE_CREATED_BY_OPTIONS = ["assistant", "admin_user"];
const RISK_LEVEL_OPTIONS = ["low", "medium", "high", "critical"];
const CONFIDENCE_OPTIONS = ["low", "medium", "high"];
const REVERSIBILITY_IMPACT_OPTIONS = ["none", "r0_possible", "r1_possible", "r2_or_higher", "unknown"];
const ACTION_CREATED_BY_OPTIONS = ["assistant", "admin_user"];
const ACTION_TYPE_OPTIONS = [
  "create_backup_checkpoint",
  "library_rescan",
  "service_restart",
  "prepare_patch_in_sandbox",
  "save_change_record_draft",
  "send_admin_notification",
];
const TARGET_SCOPE_OPTIONS = [
  "library_local",
  "library_cloud",
  "library_all",
  "service_backend",
  "service_frontend",
  "sandbox_repo_copy",
  "other",
];
const ACTION_STATUS_OPTIONS = ["draft", "awaiting_admin", "approved", "rejected", "cancelled"];
const APPROVAL_OPTIONS = ["approved", "rejected", "needs_more_info"];
const CHANGE_STATUS_OPTIONS = ["draft", "prepared", "executed", "reverted", "failed"];
const REVERSIBILITY_LEVEL_OPTIONS = ["r0", "r1", "r2", "r3", "unknown"];


function humanize(value) {
  return String(value || "").replaceAll("_", " ");
}


export function AdminAssistantRequestDetailPage() {
  const { requestId } = useParams();
  const [searchParams] = useSearchParams();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [statusForm, setStatusForm] = useState({ status: "new", admin_note: "" });
  const [triageForm, setTriageForm] = useState({
    created_by: "assistant",
    model_provider: "local_stub",
    model_name: "",
    summary: "",
    classification: "",
    risk_level: "medium",
    confidence_level: "low",
    suggested_next_step: "",
    suggested_owner: "",
    notes_for_admin: "",
    needs_admin_approval: false,
    needs_external_access_approval: false,
    reversibility_impact_if_action_taken: "unknown",
  });
  const [actionForm, setActionForm] = useState({
    triage_draft_id: "",
    created_by_type: "assistant",
    action_type: "send_admin_notification",
    target_scope: "other",
    reason: "",
    proposed_plan: "",
    risk_level: "medium",
    requires_admin_approval: true,
    requires_external_access_approval: false,
    reversibility_level: "unknown",
    warning_if_not_fully_reversible: "",
    status: "awaiting_admin",
  });
  const [approvalForm, setApprovalForm] = useState({
    action_request_id: "",
    decision: "approved",
    decision_note: "",
    backup_required: false,
    rollback_plan_required: false,
    external_access_approved: false,
  });
  const [changeForm, setChangeForm] = useState({
    linked_action_request_id: "",
    created_by_type: "admin_user",
    change_summary: "",
    reversibility_level: "unknown",
    backup_reference: "",
    revert_recipe_draft: "",
    verification_plan_draft: "",
    status: "draft",
  });
  const attachmentsSectionRef = useRef(null);
  const returnSection = searchParams.get("section");

  async function loadDetail() {
    const payload = await apiRequest(`/api/admin/assistant/requests/${requestId}`);
    setDetail(payload.request);
    setStatusForm({
      status: payload.request.status || "new",
      admin_note: payload.request.admin_note || "",
    });
    setApprovalForm((current) => ({
      ...current,
      action_request_id: current.action_request_id || String(payload.request.action_requests?.[0]?.id || ""),
    }));
  }

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const payload = await apiRequest(`/api/admin/assistant/requests/${requestId}`);
        if (!active) {
          return;
        }
        setDetail(payload.request);
        setStatusForm({
          status: payload.request.status || "new",
          admin_note: payload.request.admin_note || "",
        });
        setApprovalForm((current) => ({
          ...current,
          action_request_id: String(payload.request.action_requests?.[0]?.id || ""),
        }));
      } catch (requestError) {
        if (active) {
          setError(requestError.message || "Failed to load Assistant request");
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
  }, [requestId]);

  useEffect(() => {
    if (returnSection !== "attachments" || !detail?.attachments?.length) {
      return;
    }
    const node = attachmentsSectionRef.current;
    if (!node) {
      return;
    }
    window.requestAnimationFrame(() => {
      node.scrollIntoView({ block: "start", behavior: "auto" });
    });
  }, [detail?.id, returnSection]);

  async function runAdminUpdate(label, action) {
    setSaving(label);
    setError("");
    setMessage("");
    try {
      await action();
      await loadDetail();
      setMessage("Assistant workflow draft saved.");
    } catch (requestError) {
      setError(requestError.message || "Failed to update Assistant request");
    } finally {
      setSaving("");
    }
  }

  if (loading) {
    return <LoadingView label="Loading Assistant request..." />;
  }

  if (!detail) {
    return (
      <section className="page-section page-section--assistant-admin">
        <section className="settings-card settings-card--wide">
          <p className="form-error">{error || "Assistant request not found."}</p>
          <Link aria-label="Back to Assistant requests" className="assistant-back-button" to="/admin/assistant">&lt;</Link>
        </section>
      </section>
    );
  }

  return (
    <section className="page-section page-section--assistant-admin">
      <div className="assistant-page-stack">
        <section className="settings-card settings-card--wide">
          <div className="settings-inline-header">
            <div>
              <p className="eyebrow">Admin · Assistant (Beta)</p>
              <div className="assistant-request-detail__title-row">
                <h1>{detail.request_number}</h1>
                <span className={`status-pill status-pill--assistant status-pill--assistant-${detail.status}`}>{humanize(detail.status)}</span>
              </div>
              <p className="page-subnote">
                Submitted by {detail.submitted_by_display_name_snapshot} · {humanize(detail.request_type)} · {formatDate(detail.created_at)}
              </p>
            </div>
            <div className="assistant-page-actions">
              <Link aria-label="Back to Assistant requests" className="assistant-back-button" to="/admin/assistant">&lt;</Link>
            </div>
          </div>
          {message ? <p className="page-note">{message}</p> : null}
          {error ? <p className="form-error">{error}</p> : null}
        </section>

        <div className="assistant-admin-detail-grid">
          <section className="settings-card assistant-request-detail-card">
            <h2>Original request</h2>
            <h3>{detail.title}</h3>
            <p>{detail.description}</p>
            <div className="assistant-meta-grid">
              <div><span>Urgency</span><strong>{detail.urgency}</strong></div>
              <div><span>Status</span><strong>{humanize(detail.status)}</strong></div>
              <div><span>Page</span><strong>{detail.page_context || "Not captured"}</strong></div>
              <div><span>Platform</span><strong>{detail.platform || "Not captured"}</strong></div>
            </div>
            {detail.repro_steps ? (
              <div className="assistant-detail-block">
                <h4>Repro steps</h4>
                <p>{detail.repro_steps}</p>
              </div>
            ) : null}
            {detail.expected_result ? (
              <div className="assistant-detail-block">
                <h4>Expected result</h4>
                <p>{detail.expected_result}</p>
              </div>
            ) : null}
            {detail.actual_result ? (
              <div className="assistant-detail-block">
                <h4>Actual result</h4>
                <p>{detail.actual_result}</p>
              </div>
            ) : null}
            {detail.attachments.length > 0 ? (
              <div className="assistant-detail-block" ref={attachmentsSectionRef}>
                <h4>Attachments</h4>
                <AssistantAttachmentGallery
                  attachments={detail.attachments}
                  returnPath={`/admin/assistant/${detail.id}?section=attachments`}
                />
              </div>
            ) : null}
          </section>

          <section className="settings-card">
            <h2>Request status</h2>
            <form className="assistant-form" onSubmit={(event) => {
              event.preventDefault();
              runAdminUpdate("status", async () => {
                await apiRequest(`/api/admin/assistant/requests/${detail.id}/status`, {
                  method: "PATCH",
                  data: statusForm,
                });
              });
            }}>
              <label>
                Status
                <select
                  className="admin-select"
                  onChange={(event) => setStatusForm((current) => ({ ...current, status: event.target.value }))}
                  value={statusForm.status}
                >
                  {REQUEST_STATUS_OPTIONS.map((value) => (
                    <option key={value} value={value}>{humanize(value)}</option>
                  ))}
                </select>
              </label>
              <label>
                Admin note
                <textarea
                  onChange={(event) => setStatusForm((current) => ({ ...current, admin_note: event.target.value }))}
                  rows={5}
                  value={statusForm.admin_note}
                />
              </label>
              <button className="primary-button" disabled={saving === "status"} type="submit">
                {saving === "status" ? "Saving..." : "Save status"}
              </button>
            </form>
          </section>
        </div>

        <div className="assistant-admin-detail-grid">
          <section className="settings-card">
            <h2>Triage drafts</h2>
            <div className="assistant-sublist">
              {detail.triage_drafts.length > 0 ? detail.triage_drafts.map((entry) => (
                <div className="assistant-sublist__row" key={entry.id}>
                  <strong>{entry.summary}</strong>
                  <p className="page-subnote">{entry.created_by} · {entry.risk_level} risk · {entry.confidence_level} confidence</p>
                </div>
              )) : <p className="page-subnote">No triage drafts stored yet.</p>}
            </div>
            <form className="assistant-form" onSubmit={(event) => {
              event.preventDefault();
              runAdminUpdate("triage", async () => {
                await apiRequest(`/api/admin/assistant/requests/${detail.id}/triage-drafts`, {
                  method: "POST",
                  data: {
                    ...triageForm,
                    possible_duplicate_request_ids: [],
                  },
                });
              });
            }}>
              <div className="assistant-form__grid">
                <label>
                  Created by
                  <select className="admin-select" onChange={(event) => setTriageForm((current) => ({ ...current, created_by: event.target.value }))} value={triageForm.created_by}>
                    {TRIAGE_CREATED_BY_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
                <label>
                  Risk
                  <select className="admin-select" onChange={(event) => setTriageForm((current) => ({ ...current, risk_level: event.target.value }))} value={triageForm.risk_level}>
                    {RISK_LEVEL_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
              </div>
              <label>
                Summary
                <textarea onChange={(event) => setTriageForm((current) => ({ ...current, summary: event.target.value }))} required rows={3} value={triageForm.summary} />
              </label>
              <label>
                Classification
                <input onChange={(event) => setTriageForm((current) => ({ ...current, classification: event.target.value }))} required type="text" value={triageForm.classification} />
              </label>
              <div className="assistant-form__grid">
                <label>
                  Confidence
                  <select className="admin-select" onChange={(event) => setTriageForm((current) => ({ ...current, confidence_level: event.target.value }))} value={triageForm.confidence_level}>
                    {CONFIDENCE_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
                <label>
                  Reversibility impact
                  <select className="admin-select" onChange={(event) => setTriageForm((current) => ({ ...current, reversibility_impact_if_action_taken: event.target.value }))} value={triageForm.reversibility_impact_if_action_taken}>
                    {REVERSIBILITY_IMPACT_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
              </div>
              <label>
                Suggested next step
                <input onChange={(event) => setTriageForm((current) => ({ ...current, suggested_next_step: event.target.value }))} type="text" value={triageForm.suggested_next_step} />
              </label>
              <button className="primary-button" disabled={saving === "triage"} type="submit">
                {saving === "triage" ? "Saving..." : "Create triage draft"}
              </button>
            </form>
          </section>

          <section className="settings-card">
            <h2>Action requests</h2>
            <div className="assistant-sublist">
              {detail.action_requests.length > 0 ? detail.action_requests.map((entry) => (
                <div className="assistant-sublist__row" key={entry.id}>
                  <strong>{humanize(entry.action_type)}</strong>
                  <p className="page-subnote">{humanize(entry.target_scope)} · {entry.risk_level} risk · {humanize(entry.status)}</p>
                </div>
              )) : <p className="page-subnote">No action requests stored yet.</p>}
            </div>
            <form className="assistant-form" onSubmit={(event) => {
              event.preventDefault();
              runAdminUpdate("action", async () => {
                await apiRequest(`/api/admin/assistant/requests/${detail.id}/action-requests`, {
                  method: "POST",
                  data: {
                    ...actionForm,
                    triage_draft_id: actionForm.triage_draft_id ? Number(actionForm.triage_draft_id) : null,
                  },
                });
              });
            }}>
              <div className="assistant-form__grid">
                <label>
                  Origin
                  <select className="admin-select" onChange={(event) => setActionForm((current) => ({ ...current, created_by_type: event.target.value }))} value={actionForm.created_by_type}>
                    {ACTION_CREATED_BY_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
                <label>
                  Triage draft
                  <select className="admin-select" onChange={(event) => setActionForm((current) => ({ ...current, triage_draft_id: event.target.value }))} value={actionForm.triage_draft_id}>
                    <option value="">None</option>
                    {detail.triage_drafts.map((entry) => <option key={entry.id} value={entry.id}>{entry.id}</option>)}
                  </select>
                </label>
              </div>
              <div className="assistant-form__grid">
                <label>
                  Action type
                  <select className="admin-select" onChange={(event) => setActionForm((current) => ({ ...current, action_type: event.target.value }))} value={actionForm.action_type}>
                    {ACTION_TYPE_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
                <label>
                  Target scope
                  <select className="admin-select" onChange={(event) => setActionForm((current) => ({ ...current, target_scope: event.target.value }))} value={actionForm.target_scope}>
                    {TARGET_SCOPE_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
              </div>
              <label>
                Reason
                <textarea onChange={(event) => setActionForm((current) => ({ ...current, reason: event.target.value }))} required rows={3} value={actionForm.reason} />
              </label>
              <label>
                Proposed plan
                <textarea onChange={(event) => setActionForm((current) => ({ ...current, proposed_plan: event.target.value }))} rows={3} value={actionForm.proposed_plan} />
              </label>
              <div className="assistant-form__grid">
                <label>
                  Risk
                  <select className="admin-select" onChange={(event) => setActionForm((current) => ({ ...current, risk_level: event.target.value }))} value={actionForm.risk_level}>
                    {RISK_LEVEL_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
                <label>
                  Status
                  <select className="admin-select" onChange={(event) => setActionForm((current) => ({ ...current, status: event.target.value }))} value={actionForm.status}>
                    {ACTION_STATUS_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
              </div>
              <button className="primary-button" disabled={saving === "action"} type="submit">
                {saving === "action" ? "Saving..." : "Create action request"}
              </button>
            </form>
          </section>
        </div>

        <div className="assistant-admin-detail-grid">
          <section className="settings-card">
            <h2>Approval records</h2>
            <div className="assistant-sublist">
              {detail.approval_records.length > 0 ? detail.approval_records.map((entry) => (
                <div className="assistant-sublist__row" key={entry.id}>
                  <strong>{humanize(entry.decision)}</strong>
                  <p className="page-subnote">Action #{entry.action_request_id} · by user #{entry.decided_by_user_id} · {formatDate(entry.decided_at)}</p>
                </div>
              )) : <p className="page-subnote">No approval records stored yet.</p>}
            </div>
            <form className="assistant-form" onSubmit={(event) => {
              event.preventDefault();
              runAdminUpdate("approval", async () => {
                await apiRequest(`/api/admin/assistant/action-requests/${approvalForm.action_request_id}/approval-records`, {
                  method: "POST",
                  data: {
                    decision: approvalForm.decision,
                    decision_note: approvalForm.decision_note,
                    backup_required: approvalForm.backup_required,
                    rollback_plan_required: approvalForm.rollback_plan_required,
                    external_access_approved: approvalForm.external_access_approved,
                  },
                });
              });
            }}>
              <label>
                Action request
                <select className="admin-select" onChange={(event) => setApprovalForm((current) => ({ ...current, action_request_id: event.target.value }))} value={approvalForm.action_request_id}>
                  <option value="">Select action request</option>
                  {detail.action_requests.map((entry) => <option key={entry.id} value={entry.id}>#{entry.id} · {humanize(entry.action_type)}</option>)}
                </select>
              </label>
              <label>
                Decision
                <select className="admin-select" onChange={(event) => setApprovalForm((current) => ({ ...current, decision: event.target.value }))} value={approvalForm.decision}>
                  {APPROVAL_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                </select>
              </label>
              <label>
                Decision note
                <textarea onChange={(event) => setApprovalForm((current) => ({ ...current, decision_note: event.target.value }))} rows={3} value={approvalForm.decision_note} />
              </label>
              <button className="primary-button" disabled={saving === "approval" || !approvalForm.action_request_id} type="submit">
                {saving === "approval" ? "Saving..." : "Store approval record"}
              </button>
            </form>
          </section>

          <section className="settings-card">
            <h2>Change records</h2>
            <div className="assistant-sublist">
              {detail.change_records.length > 0 ? detail.change_records.map((entry) => (
                <div className="assistant-sublist__row" key={entry.id}>
                  <strong>{entry.change_summary || "Untitled change draft"}</strong>
                  <p className="page-subnote">{entry.created_by_type} · {entry.reversibility_level} · {humanize(entry.status)}</p>
                </div>
              )) : <p className="page-subnote">No change records stored yet.</p>}
            </div>
            <form className="assistant-form" onSubmit={(event) => {
              event.preventDefault();
              runAdminUpdate("change", async () => {
                await apiRequest(`/api/admin/assistant/requests/${detail.id}/change-records`, {
                  method: "POST",
                  data: {
                    ...changeForm,
                    linked_action_request_id: changeForm.linked_action_request_id ? Number(changeForm.linked_action_request_id) : null,
                  },
                });
              });
            }}>
              <label>
                Linked action request
                <select className="admin-select" onChange={(event) => setChangeForm((current) => ({ ...current, linked_action_request_id: event.target.value }))} value={changeForm.linked_action_request_id}>
                  <option value="">None</option>
                  {detail.action_requests.map((entry) => <option key={entry.id} value={entry.id}>#{entry.id} · {humanize(entry.action_type)}</option>)}
                </select>
              </label>
              <label>
                Change summary
                <textarea onChange={(event) => setChangeForm((current) => ({ ...current, change_summary: event.target.value }))} rows={3} value={changeForm.change_summary} />
              </label>
              <div className="assistant-form__grid">
                <label>
                  Reversibility
                  <select className="admin-select" onChange={(event) => setChangeForm((current) => ({ ...current, reversibility_level: event.target.value }))} value={changeForm.reversibility_level}>
                    {REVERSIBILITY_LEVEL_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
                <label>
                  Status
                  <select className="admin-select" onChange={(event) => setChangeForm((current) => ({ ...current, status: event.target.value }))} value={changeForm.status}>
                    {CHANGE_STATUS_OPTIONS.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
              </div>
              <button className="primary-button" disabled={saving === "change"} type="submit">
                {saving === "change" ? "Saving..." : "Create change record"}
              </button>
            </form>
          </section>
        </div>
      </div>
    </section>
  );
}
