import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.api.table_visibility import (
    column_toggle_payload,
    project_rows,
    render_table_html,
)


def test_hidden_task_sensitive_columns_are_absent_from_dom_markup():
    rows = [
        {
            "id": "task-1",
            "title": "Sync customer records",
            "status": "running",
            "assignee": "ops",
            "customer_email": "vip@example.com",
            "internal_notes": "contains acquisition target",
            "secret_token": "tok_live_hidden",
        }
    ]

    html = render_table_html(
        "tasks",
        rows,
        requested_columns=[
            "id",
            "title",
            "customer_email",
            "internal_notes",
            "secret_token",
        ],
        permissions=[],
    )

    assert "task-1" in html
    assert "Sync customer records" in html
    assert "vip@example.com" not in html
    assert "contains acquisition target" not in html
    assert "tok_live_hidden" not in html
    assert "display:none" not in html
    assert "hidden" not in html


def test_member_sensitive_columns_render_only_after_authorization():
    rows = [
        {
            "id": "member-1",
            "name": "Riley",
            "role": "admin",
            "email": "riley@example.com",
            "salary": "$185000",
            "api_key": "sk_member_secret",
        }
    ]

    unauthorized = project_rows(
        "members",
        rows,
        requested_columns=["id", "name", "email", "salary", "api_key"],
        permissions=["members:sensitive"],
    )
    authorized = project_rows(
        "members",
        rows,
        requested_columns=["id", "name", "email", "salary", "api_key"],
        permissions=[
            "members:sensitive",
            "members:compensation",
            "members:secrets",
        ],
    )

    assert unauthorized == [
        {
            "id": "member-1",
            "name": "Riley",
            "email": "riley@example.com",
        }
    ]
    assert authorized == [
        {
            "id": "member-1",
            "name": "Riley",
            "email": "riley@example.com",
            "salary": "$185000",
            "api_key": "sk_member_secret",
        }
    ]


def test_column_toggles_are_filtered_by_viewer_permissions():
    anonymous_keys = {
        column["key"]
        for column in column_toggle_payload("members", permissions=[])
    }
    privileged_keys = {
        column["key"]
        for column in column_toggle_payload(
            "members",
            permissions=[
                "members:sensitive",
                "members:compensation",
                "members:secrets",
            ],
        )
    }

    assert anonymous_keys == {"id", "name", "role"}
    assert {"email", "salary", "api_key"}.isdisjoint(anonymous_keys)
    assert {"email", "salary", "api_key"}.issubset(privileged_keys)


def test_rendered_authorized_values_are_escaped():
    html = render_table_html(
        "tasks",
        [
            {
                "id": "task-1",
                "title": "<script>alert(1)</script>",
                "status": "queued",
            }
        ],
        requested_columns=["id", "title", "status"],
    )

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_unknown_requested_columns_fail_closed():
    with pytest.raises(ValueError, match="unknown columns"):
        render_table_html(
            "tasks",
            [{"id": "task-1"}],
            requested_columns=["id", "raw_secret"],
        )


def test_table_render_endpoint_projects_before_returning_html():
    client = TestClient(create_app())
    response = client.post(
        "/api/v2/tables/tasks/render",
        headers={"Authorization": "Bearer test-token"},
        json={
            "columns": ["id", "title", "secret_token"],
            "permissions": [],
            "rows": [
                {
                    "id": "task-1",
                    "title": "Deploy",
                    "secret_token": "tok_live_hidden",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == [{"id": "task-1", "title": "Deploy"}]
    assert "Deploy" in body["html"]
    assert "tok_live_hidden" not in body["html"]
    assert "secret_token" not in {column["key"] for column in body["columns"]}


def test_agent_column_metadata_excludes_sensitive_columns_for_default_viewer():
    client = TestClient(create_app())
    response = client.get(
        "/api/v2/agents/table/columns",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    keys = {column["key"] for column in response.json()["columns"]}
    assert {"id", "name", "type", "status"}.issubset(keys)
    assert {"config", "metrics"}.isdisjoint(keys)


def test_agent_column_metadata_includes_sensitive_columns_for_operator():
    client = TestClient(create_app())
    response = client.get(
        "/api/v2/agents/table/columns",
        headers={
            "Authorization": "Bearer test-token",
            "X-Viewer-Role": "operator",
        },
    )

    assert response.status_code == 200
    keys = {column["key"] for column in response.json()["columns"]}
    assert {"config", "metrics"}.issubset(keys)


def test_agent_table_excludes_unrequested_sensitive_values_even_for_admin():
    client = TestClient(create_app())
    response = client.post(
        "/api/v2/agents/table?role=admin",
        headers={"Authorization": "Bearer test-token"},
        json={
            "columns": ["id", "name", "status"],
            "rows": [
                {
                    "id": "agent-1",
                    "name": "payments-worker",
                    "type": "worker.processor",
                    "status": "running",
                    "config": {"token": "sk_hidden_agent"},
                    "metrics": {"private_notes": "do not leak"},
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == [
        {
            "id": "agent-1",
            "name": "payments-worker",
            "status": "running",
        }
    ]
    assert "payments-worker" in body["html"]
    assert "sk_hidden_agent" not in body["html"]
    assert "do not leak" not in body["html"]
    assert "config" not in body["html"]
    assert "metrics" not in body["html"]
    assert "display:none" not in body["html"]


def test_agent_table_renders_sensitive_values_when_authorized():
    client = TestClient(create_app())
    response = client.post(
        "/api/v2/agents/table",
        headers={
            "Authorization": "Bearer test-token",
            "X-Viewer-Role": "operator",
        },
        json={
            "columns": ["id", "name", "config", "metrics"],
            "rows": [
                {
                    "id": "agent-1",
                    "name": "payments-worker",
                    "config": {"token": "sk_allowed_operator"},
                    "metrics": {"errors": 0},
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "sk_allowed_operator" in body["html"]
    assert "&#x27;errors&#x27;: 0" in body["html"]
    assert body["rows"] == [
        {
            "id": "agent-1",
            "name": "payments-worker",
            "config": {"token": "sk_allowed_operator"},
            "metrics": {"errors": 0},
        }
    ]


def test_agent_table_routes_are_not_captured_by_dynamic_agent_id_route():
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer test-token"}

    columns_response = client.get(
        "/api/v2/agents/table/columns",
        headers=headers,
    )
    table_response = client.post(
        "/api/v2/agents/table",
        headers=headers,
        json={"rows": [{"id": "agent-1", "name": "worker"}]},
    )
    count_response = client.get("/api/v2/agents/count", headers=headers)

    assert columns_response.status_code == 200
    assert table_response.status_code == 200
    assert count_response.status_code == 200
    assert "Agent not found" not in columns_response.text
    assert "Agent not found" not in table_response.text
    assert count_response.json() == {"count": 0}
