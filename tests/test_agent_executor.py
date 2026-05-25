import asyncio

import pytest

from src.agent.executor import AgentExecutor


def test_default_max_concurrent_is_valid():
    executor = AgentExecutor()

    assert executor.max_concurrent == 5


@pytest.mark.parametrize("max_concurrent", [1, 2])
def test_positive_integer_max_concurrent_is_valid(max_concurrent):
    executor = AgentExecutor(max_concurrent=max_concurrent)

    assert executor.max_concurrent == max_concurrent


@pytest.mark.parametrize(
    "max_concurrent", [0, -1, True, False, 1.5, "2", None]
)
def test_invalid_max_concurrent_raises_value_error(max_concurrent):
    with pytest.raises(
        ValueError, match="max_concurrent must be a positive integer"
    ):
        AgentExecutor(max_concurrent=max_concurrent)


def test_execute_succeeds_with_single_concurrency_limit():
    async def handler(agent_id, task):
        await asyncio.sleep(0)
        return {"agent_id": agent_id, "task_id": task["id"]}

    async def run_execution():
        executor = AgentExecutor(max_concurrent=1)
        execution_id = await executor.execute(
            "agent-1", {"id": "task-1"}, handler
        )
        return executor.get_result(execution_id)

    result = asyncio.run(run_execution())

    assert result["agent_id"] == "agent-1"
    assert result["task_id"] == "task-1"
    assert result["result"] == {"agent_id": "agent-1", "task_id": "task-1"}
