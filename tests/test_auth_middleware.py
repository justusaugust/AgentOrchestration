import base64
import json

from fastapi.testclient import TestClient

from src.api.server import create_app


def make_token(**claims):
    data = json.dumps(claims, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def auth_headers(token, workspace_id="workspace-a"):
    return {
        "Authorization": f"Bearer {token}",
        "X-Workspace-Id": workspace_id,
    }


def workspace_headers(workspace_id="workspace-a"):
    return {"X-Workspace-Id": workspace_id}


def create_test_client():
    app = create_app(
        {
            "auth_sessions": {
                "active-session": {
                    "rotation": 2,
                    "roles": {"workspace-a": "operator"},
                },
                "revoked-session": {
                    "rotation": 1,
                    "revoked": True,
                    "roles": {"workspace-a": "operator"},
                },
                "viewer-session": {
                    "rotation": 2,
                    "roles": {"workspace-a": "viewer"},
                },
            }
        }
    )
    return TestClient(app)


def active_operator_token():
    return make_token(
        sub="user-1",
        session_id="active-session",
        session_rotation=2,
        scopes=["orchestrator:read", "orchestrator:write"],
        roles={"workspace-a": "operator"},
    )


def test_anonymous_request_is_denied():
    client = create_test_client()

    response = client.get("/api/v2/agents")

    assert response.status_code == 401
    assert "unauthorized" in response.text.lower()


def test_stale_session_rotation_is_denied():
    client = create_test_client()
    stale_token = make_token(
        sub="user-1",
        session_id="active-session",
        session_rotation=1,
        scopes=["orchestrator:read"],
        roles={"workspace-a": "operator"},
    )

    response = client.get("/api/v2/agents", headers=auth_headers(stale_token))

    assert response.status_code == 401
    assert "stale" in response.text.lower()


def test_revoked_session_is_denied():
    client = create_test_client()
    revoked_token = make_token(
        sub="user-1",
        session_id="revoked-session",
        session_rotation=1,
        scopes=["orchestrator:read"],
        roles={"workspace-a": "operator"},
    )

    response = client.get(
        "/api/v2/agents",
        headers=auth_headers(revoked_token),
    )

    assert response.status_code == 401
    assert "revoked" in response.text.lower()


def test_malformed_bearer_token_is_denied():
    client = create_test_client()

    response = client.get("/api/v2/agents", headers=auth_headers("not-json"))

    assert response.status_code == 401
    assert "malformed" in response.text.lower()


def test_write_with_insufficient_scope_is_denied():
    client = create_test_client()
    read_only_token = make_token(
        sub="user-1",
        session_id="active-session",
        session_rotation=2,
        scopes=["orchestrator:read"],
        roles={"workspace-a": "viewer"},
    )

    response = client.post(
        "/api/v2/agents?name=limited&agent_type=worker",
        headers=auth_headers(read_only_token),
    )

    assert response.status_code == 403
    assert "scope" in response.text.lower()


def test_write_with_insufficient_workspace_role_is_denied():
    client = create_test_client()
    viewer_token = make_token(
        sub="user-1",
        session_id="viewer-session",
        session_rotation=2,
        scopes=["orchestrator:read", "orchestrator:write"],
        roles={"workspace-a": "viewer"},
    )

    response = client.post(
        "/api/v2/agents?name=viewer&agent_type=worker",
        headers=auth_headers(viewer_token),
    )

    assert response.status_code == 403
    assert "role" in response.text.lower()


def test_browser_cookie_token_uses_session_rotation_guard():
    client = create_test_client()
    stale_token = make_token(
        sub="user-1",
        session_id="active-session",
        session_rotation=1,
        scopes=["orchestrator:read"],
        roles={"workspace-a": "operator"},
    )
    client.cookies.set("ao_refresh_token", stale_token)

    response = client.get(
        "/api/v2/agents",
        headers=workspace_headers(),
    )

    assert response.status_code == 401
    assert "stale" in response.text.lower()


def test_browser_cookie_operator_can_complete_workflow():
    client = create_test_client()
    client.cookies.set("ao_access_token", active_operator_token())

    response = client.post(
        "/api/v2/agents?name=browser&agent_type=worker",
        headers=workspace_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "registered"


def test_authorized_workspace_operator_can_register_agent():
    client = create_test_client()

    response = client.post(
        "/api/v2/agents?name=allowed&agent_type=worker",
        headers=auth_headers(active_operator_token()),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "registered"
