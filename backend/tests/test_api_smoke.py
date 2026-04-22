from __future__ import annotations


def test_health_endpoint_smoke(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "Elvern"}


def test_auth_login_me_logout_smoke(client, admin_credentials) -> None:
    unauthenticated = client.get("/api/auth/me")
    assert unauthenticated.status_code == 401

    login_response = client.post("/api/auth/login", json=admin_credentials)
    assert login_response.status_code == 200
    assert login_response.cookies
    assert login_response.json()["user"]["username"] == admin_credentials["username"]
    assert login_response.json()["user"]["session_id"] is None

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["username"] == admin_credentials["username"]
    assert me_response.json()["user"]["role"] == "admin"
    assert me_response.json()["user"]["session_id"] is not None

    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {"message": "Logged out"}

    after_logout = client.get("/api/auth/me")
    assert after_logout.status_code == 401
