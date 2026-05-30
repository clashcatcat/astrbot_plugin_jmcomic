from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Generic, TypeVar

InputT = TypeVar("InputT")
ResultT = TypeVar("ResultT")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class QueueTask(Generic[InputT, ResultT]):
    id: int
    input: InputT
    status: str = "pending"
    created_at: str = field(default_factory=_now)
    processed_at: str | None = None
    result: ResultT | None = None
    error: str | None = None


@dataclass
class AddTaskResult:
    task_id: int
    pending_ahead: int
    queue_position: int


class TaskQueue(Generic[InputT, ResultT]):
    def __init__(self, processor: Callable[[InputT], Awaitable[ResultT]], concurrency: int):
        if concurrency < 1:
            raise ValueError("concurrency must be a positive integer")
        self._processor = processor
        self._concurrency = concurrency
        self._tasks: list[QueueTask[InputT, ResultT]] = []
        self._next_id = 1
        self._active = 0
        self._lock = asyncio.Lock()
        self._task_events: dict[int, asyncio.Event] = {}

    async def add(self, input_value: InputT) -> AddTaskResult:
        async with self._lock:
            task = QueueTask(id=self._next_id, input=input_value)
            self._next_id += 1
            self._tasks.append(task)
            pending_ahead, queue_position = self._position(task.id)
            self._task_events[task.id] = asyncio.Event()
        asyncio.create_task(self._process())
        return AddTaskResult(task.id, pending_ahead, queue_position)

    async def get(self, task_id: int) -> QueueTask[InputT, ResultT] | None:
        async with self._lock:
            return next((task for task in self._tasks if task.id == task_id), None)

    async def list(self) -> list[QueueTask[InputT, ResultT]]:
        async with self._lock:
            return list(self._tasks)

    async def wait_for_task(self, task_id: int, timeout_ms: int) -> QueueTask[InputT, ResultT] | None:
        task = await self.get(task_id)
        if task is None or self._is_terminal(task):
            return task
        event = self._task_events[task_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            pass
        return await self.get(task_id)

    async def prune_terminal_tasks(self, retention_ms: int) -> int:
        if retention_ms <= 0:
            return 0
        async with self._lock:
            before = len(self._tasks)
            now = datetime.now(timezone.utc).timestamp() * 1000
            kept: list[QueueTask[InputT, ResultT]] = []
            for task in self._tasks:
                if not self._is_terminal(task):
                    kept.append(task)
                    continue
                raw = task.processed_at or task.created_at
                try:
                    timestamp = datetime.fromisoformat(raw).timestamp() * 1000
                except Exception:
                    kept.append(task)
                    continue
                if timestamp > now - retention_ms:
                    kept.append(task)
                else:
                    self._task_events.pop(task.id, None)
            self._tasks = kept
            return before - len(self._tasks)

    def _position(self, task_id: int) -> tuple[int, int]:
        pending_ahead = 0
        queue_position = -1
        for task in self._tasks:
            if task.id == task_id:
                queue_position = pending_ahead + 1
                break
            if task.status in {"pending", "processing"}:
                pending_ahead += 1
        return pending_ahead, queue_position

    async def _process(self) -> None:
        while True:
            async with self._lock:
                if self._active >= self._concurrency:
                    return
                task = next((item for item in self._tasks if item.status == "pending"), None)
                if task is None:
                    return
                task.status = "processing"
                self._active += 1
            asyncio.create_task(self._run(task))

    async def _run(self, task: QueueTask[InputT, ResultT]) -> None:
        try:
            task.result = await self._processor(task.input)
            task.status = "completed"
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
        finally:
            task.processed_at = _now()
            async with self._lock:
                self._active -= 1
                event = self._task_events.get(task.id)
                if event is not None:
                    event.set()
            await self._process()

    @staticmethod
    def _is_terminal(task: QueueTask[Any, Any]) -> bool:
        return task.status in {"completed", "failed"}
