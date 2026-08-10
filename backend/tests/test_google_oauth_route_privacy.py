from __future__ import annotations

import backend.app.routes.cloud_libraries as cloud_libraries_routes


OPERATION_ID = "route-privacy-operation-000001"


def _login(client, admin_credentials: dict[str, str]) -> None:
    response = client.post("/api/auth/login", json=admin_credentials)
    assert response.status_code == 200


def test_google_oauth_operation_and_candidate_routes_are_never_cached(
    client,
    admin_credentials,
    monkeypatch,
) -> None:
    _login(client, admin_credentials)
    operation_payload = {
        "status": "pending",
        "expires_at": "2026-08-09T12:00:00+00:00",
        "message": None,
        "candidate_available": False,
    }
    candidate_payload = {
        "status": "account_mismatch",
        "current_account_label": "Current account",
        "candidate_account_label": "Candidate account",
        "expires_at": "2026-08-09T12:00:00+00:00",
    }
    replacement_payload = {
        "status": "connected",
        "migrated_source_count": 1,
        "failed_source_count": 0,
        "account_label": "Candidate account",
    }
    monkeypatch.setattr(
        cloud_libraries_routes,
        "get_google_oauth_operation_payload",
        lambda *_args, **_kwargs: operation_payload,
    )
    monkeypatch.setattr(
        cloud_libraries_routes,
        "cancel_google_oauth_operation",
        lambda *_args, **_kwargs: {**operation_payload, "status": "cancelled"},
    )
    monkeypatch.setattr(
        cloud_libraries_routes,
        "get_google_account_candidate_payload",
        lambda *_args, **_kwargs: candidate_payload,
    )
    monkeypatch.setattr(
        cloud_libraries_routes,
        "cancel_google_account_candidate",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cloud_libraries_routes,
        "confirm_google_account_candidate",
        lambda *_args, **_kwargs: replacement_payload,
    )

    routes = [
        "/api/cloud-libraries/google/operation/status",
        "/api/cloud-libraries/google/operation/cancel",
        "/api/cloud-libraries/google/account-candidate/status",
        "/api/cloud-libraries/google/account-candidate/cancel",
        "/api/cloud-libraries/google/account-candidate/replace",
    ]
    for route in routes:
        response = client.post(route, json={"operation_id": OPERATION_ID})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
