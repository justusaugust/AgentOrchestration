"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4


class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._counter = 0

    def push(self, item: Any, priority: int = 0) -> None:
        heapq.heappush(self._queue, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Optional[Any]:
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)

    def remove_if(self, predicate: Callable[[Any], bool]) -> List[Any]:
        removed = []
        kept = []
        for entry in self._queue:
            item = entry[2]
            if predicate(item):
                removed.append(item)
            else:
                kept.append(entry)
        if removed:
            self._queue = kept
            heapq.heapify(self._queue)
        return removed


class TaskScheduler:
    def __init__(self):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict[str, Any]] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._retired_agents: Dict[str, str] = {}
        self._audit_log: List[Dict[str, Any]] = []
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        self._reject_if_retired(task, None, queue, "enqueue")
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task["retries"] = 0
        self._push_task(task, queue, priority)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        self._reject_if_retired(task, None, queue, "schedule")
        task_id = str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        self._scheduled[task_id] = {
            "run_at": time.time() + delay,
            "task": task,
            "queue": queue,
            "priority": priority,
        }
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = time.time()
        expired = [
            tid
            for tid, scheduled in self._scheduled.items()
            if scheduled["run_at"] <= now
        ]
        for tid in expired:
            scheduled = self._scheduled.pop(tid)
            task = scheduled["task"]
            target_agent = self._task_agent_id(task)
            if target_agent in self._retired_agents:
                self._record_audit(
                    "task_skipped_retired",
                    target_agent,
                    task.get("id"),
                    scheduled["queue"],
                    reason="scheduled_dispatch",
                )
                continue
            task["enqueued_at"] = time.time()
            task["retries"] = task.get("retries", 0)
            self._push_task(task, scheduled["queue"], scheduled["priority"])

        if queue in self._queues and len(self._queues[queue]) > 0:
            while len(self._queues[queue]) > 0:
                task = self._queues[queue].pop()
                if not task:
                    continue
                target_agent = self._task_agent_id(task)
                if target_agent in self._retired_agents:
                    self._record_audit(
                        "task_skipped_retired",
                        target_agent,
                        task.get("id"),
                        queue,
                        reason="dequeue",
                    )
                    continue
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        task = self._in_flight.get(task_id)
        if task and (
            task.get("cancelled")
            or task.get("retired")
            or self._task_agent_id(task) in self._retired_agents
        ):
            target_agent = self._task_agent_id(task)
            self._in_flight.pop(task_id, None)
            self._record_audit(
                "task_completion_rejected",
                target_agent,
                task_id,
                None,
                reason="retired_agent",
            )
            return False
        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            target_agent = self._task_agent_id(task)
            if (
                task.get("cancelled")
                or task.get("retired")
                or target_agent in self._retired_agents
            ):
                self._record_audit(
                    "task_retry_rejected",
                    target_agent,
                    task_id,
                    queue,
                    reason="retired_agent",
                )
                return False
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue, priority=task.get("priority", 0))
                return True
        return False

    def bind_registry(self, registry: Any) -> None:
        registry.add_listener(self._handle_registry_event)

    def retire_agent(
        self,
        agent_id: str,
        reason: str = "registry_delete",
    ) -> Dict[str, int]:
        self._retired_agents[agent_id] = reason
        queued_removed = 0
        scheduled_removed = 0
        in_flight_retired = 0

        for queue_name, queue in self._queues.items():
            removed = queue.remove_if(
                lambda task: self._task_agent_id(task) == agent_id
            )
            queued_removed += len(removed)
            for task in removed:
                self._record_audit(
                    "task_removed_queued",
                    agent_id,
                    task.get("id"),
                    queue_name,
                    reason=reason,
                )

        for task_id, scheduled in list(self._scheduled.items()):
            task = scheduled["task"]
            if self._task_agent_id(task) != agent_id:
                continue
            self._scheduled.pop(task_id)
            scheduled_removed += 1
            self._record_audit(
                "task_removed_scheduled",
                agent_id,
                task_id,
                scheduled["queue"],
                reason=reason,
            )

        for task_id, task in list(self._in_flight.items()):
            if self._task_agent_id(task) != agent_id:
                continue
            in_flight_retired += 1
            task["retired"] = True
            task["cancelled"] = True
            task["retired_at"] = time.time()
            task["cancelled_reason"] = reason
            self._record_audit(
                "task_retired_in_flight",
                agent_id,
                task_id,
                None,
                reason=reason,
            )

        summary = {
            "queued_removed": queued_removed,
            "scheduled_removed": scheduled_removed,
            "in_flight_retired": in_flight_retired,
        }
        self._record_audit(
            "agent_retired",
            agent_id,
            None,
            None,
            reason=reason,
            counts=summary,
        )
        return summary

    def is_agent_retired(self, agent_id: str) -> bool:
        return agent_id in self._retired_agents

    def audit_log(self) -> List[Dict[str, Any]]:
        return list(self._audit_log)

    def _handle_registry_event(self, event: Any) -> None:
        if event.event == "agent_deleted":
            self.retire_agent(event.agent_id, reason=event.reason)
        elif (
            event.event == "agent_status_changed"
            and event.status in {"stopped", "failed", "terminated"}
        ):
            self.retire_agent(event.agent_id, reason=event.status)

    def _reject_if_retired(
        self,
        task: Dict[str, Any],
        task_id: Optional[str],
        queue: Optional[str],
        operation: str,
    ) -> None:
        target_agent = self._task_agent_id(task)
        if target_agent in self._retired_agents:
            self._record_audit(
                "task_rejected",
                target_agent,
                task_id,
                queue,
                reason=f"retired_agent:{operation}",
            )
            raise ValueError(f"Agent {target_agent} is retired")

    def _task_agent_id(self, task: Dict[str, Any]) -> Optional[str]:
        return task.get("target_agent") or task.get("agent_id")

    def _push_task(
        self,
        task: Dict[str, Any],
        queue: str,
        priority: int,
    ) -> None:
        task["queue"] = queue
        task["priority"] = priority
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)

    def _record_audit(
        self,
        event: str,
        agent_id: Optional[str],
        task_id: Optional[str],
        queue: Optional[str],
        reason: str,
        counts: Optional[Dict[str, int]] = None,
    ) -> None:
        record = {
            "event": event,
            "agent_id": agent_id,
            "task_id": task_id,
            "queue": queue,
            "reason": reason,
            "timestamp": time.time(),
        }
        if counts is not None:
            record["counts"] = dict(counts)
        self._audit_log.append(record)

# 2019-04-25T08:37:12 update

# 2019-06-04T16:40:00 update

# 2019-07-11T12:01:28 update

# 2019-08-02T12:20:21 update

# 2019-08-23T10:38:50 update

# 2019-10-31T13:55:52 update

# 2019-11-04T20:12:32 update

# 2019-12-13T12:22:36 update

# 2020-02-01T10:32:37 update

# 2020-02-26T09:44:38 update

# 2020-03-09T19:00:55 update

# 2020-05-01T18:40:34 update

# 2020-05-12T15:10:31 update

# 2020-06-30T13:24:19 update

# 2020-09-22T16:00:45 update

# 2020-10-20T10:52:48 update

# 2020-10-21T12:18:08 update

# 2020-11-06T12:35:01 update

# 2020-12-09T08:09:33 update

# 2021-01-07T08:20:36 update

# 2021-10-02T15:23:16 update

# 2021-10-06T16:14:57 update

# 2021-10-06T09:27:41 update

# 2021-11-19T08:37:40 update

# 2022-03-01T16:39:54 update

# 2022-05-26T13:43:07 update

# 2022-06-02T10:50:58 update

# 2022-06-14T10:46:48 update

# 2022-07-31T16:44:34 update

# 2022-08-30T18:20:12 update

# 2022-11-04T14:47:03 update

# 2022-12-06T10:36:49 update

# 2022-12-22T13:21:12 update

# 2022-12-26T12:24:50 update

# 2023-03-09T08:09:55 update

# 2023-05-01T10:07:37 update

# 2023-06-08T14:32:15 update

# 2023-07-14T17:24:18 update

# 2023-12-14T08:38:31 update

# 2024-02-20T13:43:58 update

# 2024-03-24T08:52:42 update

# 2024-03-28T15:27:17 update

# 2024-03-29T18:10:33 update

# 2024-04-15T20:18:31 update

# 2024-05-27T13:11:52 update

# 2024-05-27T16:42:56 update

# 2024-06-20T13:03:45 update

# 2024-06-28T12:32:58 update

# 2024-07-10T14:10:16 update

# 2024-07-26T14:18:59 update

# 2024-08-12T08:21:05 update

# 2024-08-21T16:58:40 update

# 2024-09-27T19:54:30 update

# 2024-10-21T13:47:42 update

# 2024-11-11T09:19:27 update

# 2024-12-24T08:23:41 update

# 2025-02-14T10:35:15 update

# 2025-03-31T18:09:40 update

# 2025-06-21T17:32:49 update

# 2025-07-21T16:52:28 update

# 2025-08-20T19:45:16 update

# 2025-11-04T18:54:24 update

# 2025-12-09T20:17:36 update

# 2026-01-12T15:42:32 update

# 2026-01-23T14:41:20 update

# 2026-03-18T14:43:07 update

# 2026-04-13T11:43:19 update
