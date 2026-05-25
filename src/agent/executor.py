"""Agent Executor — Handles task execution within agent sandboxes."""

import asyncio
import copy
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.request import Request, urlopen
from uuid import uuid4

from src.common.artifact_cache import ArtifactDownloadCache


class AgentExecutor:
    def __init__(
        self,
        max_concurrent: int = 5,
        artifact_cache: Optional[ArtifactDownloadCache] = None,
        artifact_cache_dir: Optional[Path] = None,
        artifact_fetcher: Optional[Callable[[str], bytes]] = None,
    ):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Any] = {}
        cache_dir = artifact_cache_dir or os.getenv("AO_ARTIFACT_CACHE_DIR")
        if artifact_cache is None and cache_dir is None:
            cache_dir = (
                Path(tempfile.gettempdir())
                / "agent-orchestrator"
                / "artifact-cache"
            )
        self._artifact_cache = (
            artifact_cache or ArtifactDownloadCache(cache_dir)
        )
        self._artifact_fetcher = artifact_fetcher or _download_artifact

    async def execute(
        self,
        agent_id: str,
        task: Dict[str, Any],
        handler: Callable,
    ) -> str:
        execution_id = str(uuid4())
        async with self._semaphore:
            task_obj = asyncio.create_task(
                self._run_execution(execution_id, agent_id, task, handler)
            )
            self._active_tasks[execution_id] = task_obj
            try:
                result = await task_obj
                self._results[execution_id] = result
            except Exception as e:
                self._results[execution_id] = {"error": str(e)}
            finally:
                self._active_tasks.pop(execution_id, None)
        return execution_id

    async def _run_execution(
        self,
        exec_id: str,
        agent_id: str,
        task: Dict,
        handler: Callable,
    ) -> Any:
        start = time.time()
        resolved_task = await self._resolve_task_artifacts(task)
        result = await handler(agent_id, resolved_task)
        duration = time.time() - start
        return {
            "execution_id": exec_id,
            "agent_id": agent_id,
            "task_id": task.get("id"),
            "result": result,
            "duration": duration,
            "timestamp": time.time(),
        }

    def get_result(self, execution_id: str) -> Optional[Any]:
        return self._results.get(execution_id)

    def cancel(self, execution_id: str) -> bool:
        task = self._active_tasks.get(execution_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        for task in self._active_tasks.values():
            task.cancel()
        if self._active_tasks:
            await asyncio.gather(
                *self._active_tasks.values(),
                return_exceptions=True,
            )

    async def _resolve_task_artifacts(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        artifacts = task.get("artifacts")
        if not isinstance(artifacts, list):
            return task

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._resolve_task_artifacts_sync,
            task,
        )

    def _resolve_task_artifacts_sync(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        resolved_task = copy.deepcopy(task)
        resolved_artifacts = []

        for artifact in resolved_task.get("artifacts", []):
            if not isinstance(artifact, dict):
                resolved_artifacts.append(artifact)
                continue

            url = artifact.get("url")
            expected_digest = _artifact_expected_sha256(artifact)
            if not url or not expected_digest:
                resolved_artifacts.append(artifact)
                continue

            resolution = self._artifact_cache.get_with_info(
                str(url),
                expected_sha256=expected_digest,
                fetcher=lambda artifact_url=str(url): self._artifact_fetcher(
                    artifact_url
                ),
            )
            artifact["local_path"] = str(resolution.path)
            artifact["cache"] = {
                "hit": resolution.hit,
                "key": resolution.key,
                "sha256": resolution.sha256,
                "size": resolution.size,
            }
            resolved_artifacts.append(artifact)

        resolved_task["artifacts"] = resolved_artifacts
        return resolved_task


def _artifact_expected_sha256(artifact: Dict[str, Any]) -> Optional[str]:
    for field in ("sha256", "digest", "checksum"):
        value = artifact.get(field)
        if value:
            return str(value)
    return None


def _download_artifact(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "agent-orchestrator/2.4"})
    with urlopen(request, timeout=60) as response:
        return response.read()

# 2019-01-31T14:19:34 update

# 2019-02-14T17:08:55 update

# 2019-03-28T08:28:18 update

# 2019-10-22T14:08:13 update

# 2020-01-02T11:41:47 update

# 2020-05-27T15:54:00 update

# 2020-06-03T11:28:30 update

# 2020-06-30T11:26:45 update

# 2020-07-22T16:27:48 update

# 2020-10-26T10:21:42 update

# 2020-12-11T08:18:01 update

# 2021-01-19T19:48:39 update

# 2021-02-11T20:31:28 update

# 2021-03-03T17:36:43 update

# 2021-04-13T12:11:10 update

# 2021-10-01T12:48:15 update

# 2021-10-11T13:15:06 update

# 2022-03-04T17:29:10 update

# 2022-09-21T15:04:04 update

# 2022-09-26T11:39:01 update

# 2022-10-07T14:50:58 update

# 2022-10-31T11:14:41 update

# 2022-12-20T14:55:07 update

# 2023-03-06T20:32:35 update

# 2023-05-23T12:22:34 update

# 2023-06-02T12:50:51 update

# 2023-06-27T14:46:58 update

# 2023-09-21T08:31:49 update

# 2023-10-05T20:34:46 update

# 2023-10-10T19:50:50 update

# 2023-12-14T16:01:46 update

# 2024-01-08T09:32:16 update

# 2024-01-25T19:20:00 update

# 2024-04-15T17:45:27 update

# 2024-04-30T12:53:42 update

# 2024-08-20T11:11:27 update

# 2024-09-13T13:17:03 update

# 2024-11-01T12:39:45 update

# 2024-12-17T19:48:45 update

# 2025-01-10T20:46:40 update

# 2025-01-12T17:49:15 update

# 2025-01-24T17:40:56 update

# 2025-01-31T10:14:08 update

# 2025-03-07T08:51:21 update

# 2025-05-19T17:28:46 update

# 2025-07-04T17:58:43 update

# 2025-09-02T09:44:39 update

# 2025-09-22T20:20:24 update

# 2026-01-02T20:01:17 update

# 2026-03-17T08:56:59 update
