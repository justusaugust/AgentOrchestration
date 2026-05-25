import asyncio

import pytest
from src.orchestrator.engine import OrchestrationEngine
from src.orchestrator.scheduler import TaskScheduler


class TestTaskScheduler:
    def setup_method(self):
        self.scheduler = TaskScheduler()

    def test_enqueue_task(self):
        task_id = self.scheduler.enqueue({"type": "test", "payload": {}})
        assert task_id is not None

    def test_dequeue_task(self):
        self.scheduler.enqueue({"type": "test", "payload": {"data": 1}})
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert task["type"] == "test"

    def test_enqueue_multiple_priorities(self):
        self.scheduler.enqueue({"type": "low"}, priority=1)
        self.scheduler.enqueue({"type": "high"}, priority=10)
        task = asyncio.run(self.scheduler.dequeue())
        assert task["type"] == "high"

    def test_complete_task(self):
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.complete(task["id"])

    def test_fail_task_with_retry(self):
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.fail(task["id"])


class TestTaskSchedulerRetirement:
    """Regression tests for issue #4389: scheduler retirement semantics."""

    def setup_method(self):
        self.scheduler = TaskScheduler()

    def test_schedule_preserves_full_task_through_dequeue(self):
        task = {
            "type": "work",
            "target_agent": "agent-1",
            "payload": {"x": 42},
        }
        task_id = self.scheduler.schedule(task, delay=0)
        result = asyncio.run(self.scheduler.dequeue())
        assert result is not None
        assert result["id"] == task_id
        assert result.get("target_agent") == "agent-1"
        assert result.get("payload") == {"x": 42}

    def test_retire_agent_removes_queued_tasks(self):
        self.scheduler.enqueue({"type": "work", "target_agent": "agent-1"})
        summary = self.scheduler.retire_agent("agent-1")

        assert summary["queued_removed"] == 1
        assert asyncio.run(self.scheduler.dequeue()) is None

    def test_retire_agent_removes_scheduled_future_tasks(self):
        task = {"type": "work", "target_agent": "agent-1"}
        self.scheduler.schedule(task, delay=60)
        summary = self.scheduler.retire_agent("agent-1")
        assert summary["scheduled_removed"] == 1

        result = asyncio.run(self.scheduler.dequeue())
        assert result is None

    def test_retire_agent_dequeue_after_due_returns_none(self):
        task = {"type": "work", "target_agent": "agent-1"}
        self.scheduler.schedule(task, delay=0)
        self.scheduler.retire_agent("agent-1")
        result = asyncio.run(self.scheduler.dequeue())
        assert result is None

    def test_schedule_rejects_retired_agent_before_commit(self):
        self.scheduler.retire_agent("agent-retired")
        task = {"type": "work", "target_agent": "agent-retired"}
        with pytest.raises(ValueError):
            self.scheduler.schedule(task, delay=0)
        assert asyncio.run(self.scheduler.dequeue()) is None

    def test_enqueue_rejects_retired_agent_before_commit(self):
        self.scheduler.retire_agent("agent-retired")
        task = {"type": "work", "target_agent": "agent-retired"}

        with pytest.raises(ValueError):
            self.scheduler.enqueue(task)

        assert asyncio.run(self.scheduler.dequeue()) is None

    def test_complete_returns_false_for_retired_in_flight_task(self):
        task = {
            "type": "work",
            "target_agent": "agent-1",
            "payload": {"data": "secret"},
        }
        self.scheduler.enqueue(task)
        dequeued = asyncio.run(self.scheduler.dequeue())
        assert dequeued is not None
        self.scheduler.retire_agent("agent-1")
        assert dequeued["retired"] is True
        assert dequeued["cancelled_reason"] == "registry_delete"
        assert self.scheduler.complete(dequeued["id"]) is False

    def test_fail_returns_false_for_retired_in_flight_task(self):
        task = {"type": "work", "target_agent": "agent-1"}
        self.scheduler.enqueue(task)
        dequeued = asyncio.run(self.scheduler.dequeue())
        assert dequeued is not None
        self.scheduler.retire_agent("agent-1")

        assert self.scheduler.fail(dequeued["id"]) is False
        assert asyncio.run(self.scheduler.dequeue()) is None

    def test_retire_agent_audit_event_omits_payload(self):
        task = {
            "type": "work",
            "target_agent": "agent-1",
            "payload": {"secret": "value"},
        }
        self.scheduler.enqueue(task)
        dequeued = asyncio.run(self.scheduler.dequeue())
        assert dequeued is not None
        self.scheduler.retire_agent("agent-1")
        audit_log = self.scheduler.audit_log()
        retirement_events = [
            e for e in audit_log if e.get("event") == "task_retired_in_flight"
        ]
        assert len(retirement_events) >= 1
        for event in retirement_events:
            assert "payload" not in event
            assert event["agent_id"] == "agent-1"
            assert event["task_id"] == dequeued["id"]
            assert event["queue"] is None


class TestOrchestrationEngineRetirementWiring:
    """Regression tests for issue #4389."""

    def setup_method(self):
        self.engine = OrchestrationEngine()

    def test_registry_delete_retires_agent_in_scheduler(self):
        agent_id = self.engine.registry.register(
            "test-agent",
            "worker.processor",
        )
        task = {"type": "work", "target_agent": agent_id}
        self.engine.scheduler.schedule(task, delay=60)
        self.engine.registry.delete(agent_id)
        assert self.engine.scheduler.is_agent_retired(agent_id)
        result = asyncio.run(self.engine.scheduler.dequeue())
        assert result is None


# 2019-01-09T19:07:03 update

# 2019-02-18T12:30:02 update

# 2019-04-11T16:04:51 update

# 2019-04-17T16:25:46 update

# 2019-05-24T19:32:13 update

# 2019-07-02T12:54:25 update

# 2019-07-03T20:37:00 update

# 2019-08-21T19:37:17 update

# 2019-10-18T10:30:31 update

# 2019-10-25T09:01:38 update

# 2019-10-29T12:59:34 update

# 2019-11-05T10:07:06 update

# 2019-11-11T10:43:52 update

# 2020-01-17T13:40:02 update

# 2020-02-07T14:06:34 update

# 2020-04-03T08:53:40 update

# 2020-04-06T19:36:29 update

# 2020-05-12T11:51:05 update

# 2020-08-17T08:37:15 update

# 2020-09-15T10:39:38 update

# 2020-10-06T11:26:19 update

# 2020-10-21T13:32:43 update

# 2020-12-14T18:18:36 update

# 2020-12-23T17:15:03 update

# 2021-01-25T16:29:00 update

# 2021-02-23T11:23:50 update

# 2021-03-19T12:21:19 update

# 2021-07-29T18:48:25 update

# 2021-08-25T12:46:58 update

# 2021-09-09T16:27:13 update

# 2021-12-16T12:05:30 update

# 2022-05-07T14:05:12 update

# 2022-07-18T20:52:29 update

# 2022-07-31T18:42:26 update

# 2022-09-09T13:10:08 update

# 2023-01-04T15:16:57 update

# 2023-01-17T14:49:04 update

# 2023-02-15T13:51:30 update

# 2023-03-08T09:15:53 update

# 2023-03-23T16:32:20 update

# 2023-03-28T09:32:01 update

# 2023-05-05T17:28:22 update

# 2023-06-01T08:13:52 update

# 2023-06-20T09:58:10 update

# 2023-07-04T16:14:34 update

# 2023-07-17T20:49:40 update

# 2023-12-26T11:49:18 update

# 2024-05-27T11:00:06 update

# 2024-07-04T08:53:03 update

# 2024-07-18T16:19:02 update

# 2024-08-07T09:35:35 update

# 2024-08-22T14:32:14 update

# 2025-05-20T14:19:23 update

# 2025-07-17T17:54:48 update

# 2025-07-28T13:06:30 update

# 2025-12-22T19:05:25 update

# 2026-01-08T18:43:02 update

# 2026-01-12T16:53:28 update

# 2026-04-16T16:58:23 update
