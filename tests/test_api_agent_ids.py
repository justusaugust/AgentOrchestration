from fastapi.testclient import TestClient

from src.agent.registry import AgentRegistry
from src.api import routes
from src.api.server import create_app


AUTH_HEADERS = {"Authorization": "Bearer test-token"}


def mixed_case(agent_id: str) -> str:
    return "".join(
        char.upper() if index % 2 == 0 else char
        for index, char in enumerate(agent_id)
    )


def make_client():
    routes.registry = AgentRegistry()
    return TestClient(create_app()), routes.registry


def test_missing_authorization_denies_agent_route_before_handling():
    client, registry = make_client()
    agent_id = registry.register("test-agent", "worker.processor")

    response = client.get(f"/api/v2/agents/{mixed_case(agent_id)}")

    assert response.status_code == 401
    assert registry.get(agent_id) is not None


def test_malformed_agent_id_returns_400_without_mutation():
    client, registry = make_client()
    agent_id = registry.register("test-agent", "worker.processor")

    response = client.post(
        "/api/v2/agents/not-a-uuid/start",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert registry.get(agent_id)["status"] == "pending"
    assert registry.count() == 1


def test_malformed_agent_id_get_fails_before_lookup():
    client, registry = make_client()
    registry._agents["not-a-uuid"] = {"id": "not-a-uuid", "name": "poison"}

    response = client.get(
        "/api/v2/agents/not-a-uuid",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Malformed agent id"


def test_malformed_agent_id_delete_returns_400_without_mutation():
    client, registry = make_client()
    agent_id = registry.register("test-agent", "worker.processor")

    response = client.delete(
        "/api/v2/agents/not-a-uuid",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert registry.get(agent_id) is not None
    assert registry.count() == 1


def test_mixed_case_agent_id_get_resolves_canonical_record():
    client, registry = make_client()
    agent_id = registry.register("test-agent", "worker.processor")

    response = client.get(
        f"/api/v2/agents/{mixed_case(agent_id)}",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["id"] == agent_id


def test_mixed_case_agent_id_start_stop_delete_mutate_canonical_record():
    client, registry = make_client()
    agent_id = registry.register("test-agent", "worker.processor")
    route_id = mixed_case(agent_id)

    start_response = client.post(
        f"/api/v2/agents/{route_id}/start",
        headers=AUTH_HEADERS,
    )
    assert start_response.status_code == 200
    assert registry.get(agent_id)["status"] == "running"

    stop_response = client.post(
        f"/api/v2/agents/{route_id}/stop",
        headers=AUTH_HEADERS,
    )
    assert stop_response.status_code == 200
    assert registry.get(agent_id)["status"] == "paused"

    delete_response = client.delete(
        f"/api/v2/agents/{route_id}",
        headers=AUTH_HEADERS,
    )
    assert delete_response.status_code == 200
    assert registry.get(agent_id) is None
    assert registry.count() == 0


def test_unknown_well_formed_agent_id_returns_404():
    client, registry = make_client()
    registry.register("test-agent", "worker.processor")

    response = client.get(
        "/api/v2/agents/00000000-0000-4000-8000-000000000000",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 404
    assert registry.count() == 1


def test_agent_count_route_is_not_treated_as_agent_id():
    client, registry = make_client()
    registry.register("test-agent", "worker.processor")

    response = client.get("/api/v2/agents/count", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"count": 1}
