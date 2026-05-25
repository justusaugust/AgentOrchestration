import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.middleware import (
    ForwardedHeaderMiddleware,
    get_forwarded_header_source,
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
    app.add_middleware(ForwardedHeaderMiddleware)

    @app.get("/source")
    async def source():
        return {"source": get_forwarded_header_source()}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    return TestClient(
        app,
        raise_server_exceptions=raise_server_exceptions,
    ), app


def test_forwarded_header_middleware_accepts_single_header_family():
    client, app = build_client()

    response = client.get(
        "/source",
        headers={"X-Forwarded-For": "203.0.113.7"},
    )

    assert response.status_code == 200
    assert response.json() == {"source": "x-forwarded"}
    assert response.headers["X-Forwarded-Header-Validation"] == "accepted"
    assert app.state.downstream_calls == 1
    assert get_forwarded_header_source() is None


def test_forwarded_header_middleware_rejects_conflicts_before_downstream(
    caplog,
):
    client, app = build_client()
    caplog.set_level(logging.WARNING, logger="src.api.middleware")

    response = client.get(
        "/source",
        headers={
            "Forwarded": "for=sensitive-client;proto=https",
            "X-Forwarded-Host": "private.example.test",
            "X-Forwarded-For": "198.51.100.4",
        },
    )

    assert response.status_code == 400
    assert response.text == "Conflicting forwarded headers"
    assert response.headers["X-Forwarded-Header-Validation"] == "rejected"
    assert app.state.downstream_calls == 0
    assert get_forwarded_header_source() is None

    assert "Rejected conflicting forwarded headers" in caplog.text
    for sensitive_value in (
        "sensitive-client",
        "private.example.test",
        "198.51.100.4",
    ):
        assert sensitive_value not in caplog.text
        assert sensitive_value not in "\n".join(response.headers.values())


def test_forwarded_header_middleware_clears_state_on_exceptions():
    client, app = build_client(raise_server_exceptions=False)

    response = client.get(
        "/boom",
        headers={"Forwarded": "for=203.0.113.9"},
    )

    assert response.status_code == 500
    assert app.state.downstream_calls == 1
    assert get_forwarded_header_source() is None


def test_create_app_rejects_conflicts_before_auth_or_route_work(caplog):
    client = TestClient(create_app())
    caplog.set_level(logging.WARNING, logger="src.api.middleware")

    response = client.get(
        "/api/v2/agents",
        headers={
            "Forwarded": "for=sensitive-client",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 400
    assert response.text == "Conflicting forwarded headers"
    assert response.headers["X-Forwarded-Header-Validation"] == "rejected"
    assert "Unauthorized" not in response.text
    assert "sensitive-client" not in caplog.text
