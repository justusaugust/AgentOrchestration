import asyncio
import hashlib
from pathlib import Path

from src.agent.executor import AgentExecutor
from src.common.artifact_cache import ArtifactDownloadCache


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def test_agent_executor_resolves_artifacts_through_cache(tmp_path):
    payload = b"artifact-for-handler"
    fetches = []
    seen_tasks = []
    cache = ArtifactDownloadCache(tmp_path / "cache")

    def fetch_artifact(url):
        fetches.append(url)
        return payload

    async def handler(agent_id, task):
        seen_tasks.append(task)
        artifact = task["artifacts"][0]
        return {
            "agent_id": agent_id,
            "artifact_bytes": Path(artifact["local_path"]).read_bytes(),
            "cache_sha256": artifact["cache"]["sha256"],
        }

    executor = AgentExecutor(
        artifact_cache=cache,
        artifact_fetcher=fetch_artifact,
    )
    task = {
        "id": "task-1",
        "artifacts": [
            {
                "name": "model",
                "url": "https://example.test/model.bin",
                "checksum": f"sha256:{sha256(payload)}",
            }
        ],
    }

    first_execution_id = asyncio.run(
        executor.execute("agent-1", task, handler)
    )
    second_execution_id = asyncio.run(
        executor.execute("agent-1", task, handler)
    )

    first_result = executor.get_result(first_execution_id)["result"]
    second_result = executor.get_result(second_execution_id)["result"]

    assert fetches == ["https://example.test/model.bin"]
    assert first_result["artifact_bytes"] == payload
    assert second_result["artifact_bytes"] == payload
    assert first_result["cache_sha256"] == sha256(payload)
    assert seen_tasks[0]["artifacts"][0]["local_path"]
    assert seen_tasks[0]["artifacts"][0]["cache"]["hit"] is False
    assert seen_tasks[1]["artifacts"][0]["cache"]["hit"] is True
    assert "local_path" not in task["artifacts"][0]


def test_agent_executor_keeps_different_digests_immutable(tmp_path):
    old_payload = b"artifact-v1"
    new_payload = b"artifact-v2"
    payloads = {
        "https://example.test/model.bin": [old_payload, new_payload],
    }
    seen_paths = []

    def fetch_artifact(url):
        return payloads[url].pop(0)

    async def handler(agent_id, task):
        artifact = task["artifacts"][0]
        seen_paths.append(Path(artifact["local_path"]))
        return Path(artifact["local_path"]).read_bytes()

    executor = AgentExecutor(
        artifact_cache=ArtifactDownloadCache(tmp_path / "cache"),
        artifact_fetcher=fetch_artifact,
    )
    first_task = {
        "id": "task-old",
        "artifacts": [
            {
                "url": "https://example.test/model.bin",
                "sha256": sha256(old_payload),
            }
        ],
    }
    second_task = {
        "id": "task-new",
        "artifacts": [
            {
                "url": "https://example.test/model.bin",
                "sha256": sha256(new_payload),
            }
        ],
    }

    first_execution_id = asyncio.run(
        executor.execute("agent-1", first_task, handler)
    )
    second_execution_id = asyncio.run(
        executor.execute("agent-1", second_task, handler)
    )

    assert executor.get_result(first_execution_id)["result"] == old_payload
    assert executor.get_result(second_execution_id)["result"] == new_payload
    assert seen_paths[0] != seen_paths[1]
    assert seen_paths[0].read_bytes() == old_payload
    assert seen_paths[1].read_bytes() == new_payload
