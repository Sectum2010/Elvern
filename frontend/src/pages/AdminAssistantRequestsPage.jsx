import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { LoadingView } from "../components/LoadingView";
import { apiRequest } from "../lib/api";
import { formatDate } from "../lib/format";


export function AdminAssistantRequestsPage() {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const payload = await apiRequest("/api/admin/assistant/requests");
        if (active) {
          setRequests(payload.requests || []);
        }
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

  if (loading) {
    return <LoadingView label="Loading Assistant requests..." />;
  }

  return (
    <section className="page-section page-section--assistant-admin">
      <div className="assistant-page-stack">
        <section className="settings-card settings-card--wide">
          <div className="settings-inline-header">
            <div>
              <p className="eyebrow">Admin · Assistant (Beta)</p>
              <h1>Request queue</h1>
              <p className="page-subnote">
                Safe placeholder workflow only. No model execution or privileged actions run from this screen.
              </p>
            </div>
            <Link aria-label="Back to Assistant page" className="assistant-back-button" to="/admin?section=assistant">&lt;</Link>
          </div>
          {error ? <p className="form-error">{error}</p> : null}
        </section>

        <section className="settings-card settings-card--wide">
          <div className="settings-inline-header">
            <div>
              <h2>Requests</h2>
              <p className="page-subnote">{requests.length} open request{requests.length === 1 ? "" : "s"} shown.</p>
            </div>
          </div>
          <div className="admin-list admin-list--dense">
            {requests.length > 0 ? requests.map((entry) => (
              <Link className="admin-list__row admin-list__row--card assistant-admin-link-card" key={entry.id} to={`/admin/assistant/${entry.id}`}>
                <div className="assistant-request-card__header">
                  <strong>{entry.request_number}</strong>
                  <span className={`status-pill status-pill--assistant status-pill--assistant-${entry.status}`}>{entry.status.replaceAll("_", " ")}</span>
                </div>
                <h3>{entry.title}</h3>
                <p className="page-subnote">
                  {entry.request_type.replaceAll("_", " ")} · {entry.submitted_by_display_name_snapshot} · {entry.urgency} · {formatDate(entry.created_at)}
                </p>
              </Link>
            )) : <p className="page-subnote">No Assistant requests have been submitted yet.</p>}
          </div>
        </section>
      </div>
    </section>
  );
}
