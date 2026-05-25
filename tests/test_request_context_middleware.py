import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.middleware import (
    REQUEST_CONTEXT_VALIDATION_HEADER,
    RequestContextMiddleware,
    get_request_context,
)
from src.api.server import create_app


class DownstreamProbeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.app.state.downstream_calls += 1
        return await call_next(request)


def build_client(raise_server_exceptions=True):
    app = FastAPI()
    app.state.downstream_calls = 0
    app.add_middleware(DownstreamProbeMiddleware)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/context")
    async def context():
        current = get_request_context()
        return {
            "workspace_id": current.workspace_id,
            "role": current.role,
            "scope_digest": current.scope_digest,
        }

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    return TestClient(
        app,
        raise_server_exceptions=raise_server_exceptions,
    ), app


def scoped_headers(
    workspace_id="workspace-a",
    role="admin",
    correlation_id="trace-123",
    correlation_scope=None,
):
    headers = {
        "X-Workspace-ID": workspace_id,
        "X-Workspace-Role": role,
        "X-Correlation-ID": correlation_id,
    }
    if correlation_scope is not None:
        headers["X-Correlation-Scope"] = correlation_scope
    return headers


def test_scopes_correlation_id_to_workspace_and_role():
    client, app = build_client()

    response = client.get("/context", headers=scoped_headers())

    assert response.status_code == 200
    assert response.headers[REQUEST_CONTEXT_VALIDATION_HEADER] == "accepted"
    assert response.headers["X-Correlation-Scope"]
    assert response.json() == {
        "workspace_id": "workspace-a",
        "role": "admin",
        "scope_digest": response.headers["X-Correlation-Scope"],
    }
    assert app.state.downstream_calls == 1
    assert get_request_context() is None


def test_rejects_replayed_correlation_scope_for_different_tenant(caplog):
    client, app = build_client()
    caplog.set_level(logging.WARNING, logger="src.api.middleware")

    first_response = client.get("/context", headers=scoped_headers())
    tenant_a_scope = first_response.headers["X-Correlation-Scope"]

    response = client.get(
        "/context",
        headers=scoped_headers(
            workspace_id="workspace-b",
            role="admin",
            correlation_id="trace-123",
            correlation_scope=tenant_a_scope,
        ),
    )

    assert response.status_code == 400
    assert response.text == "Correlation scope does not match caller"
    assert response.headers[REQUEST_CONTEXT_VALIDATION_HEADER] == "rejected"
    assert app.state.downstream_calls == 1
    assert get_request_context() is None
    assert "Rejected cross-tenant correlation scope" in caplog.text

    for sensitive_value in ("workspace-a", "workspace-b", "trace-123"):
        assert sensitive_value not in caplog.text
        assert sensitive_value not in "\n".join(response.headers.values())


def test_rejects_correlation_id_without_workspace_or_role():
    client, app = build_client()

    response = client.get(
        "/context",
        headers={"X-Correlation-ID": "trace-without-scope"},
    )

    assert response.status_code == 400
    assert response.text == "Correlation ID requires workspace and role scope"
    assert response.headers[REQUEST_CONTEXT_VALIDATION_HEADER] == "rejected"
    assert app.state.downstream_calls == 0
    assert get_request_context() is None


def test_exception_path_clears_request_context():
    client, app = build_client(raise_server_exceptions=False)

    response = client.get("/boom", headers=scoped_headers())

    assert response.status_code == 500
    assert app.state.downstream_calls == 1
    assert get_request_context() is None


def test_create_app_rejects_correlation_scope_before_auth():
    client = TestClient(create_app())

    response = client.get(
        "/api/v2/agents",
        headers={"X-Correlation-ID": "trace-without-scope"},
    )

    assert response.status_code == 400
    assert response.text == "Correlation ID requires workspace and role scope"
    assert response.headers[REQUEST_CONTEXT_VALIDATION_HEADER] == "rejected"
    assert "Unauthorized" not in response.text
