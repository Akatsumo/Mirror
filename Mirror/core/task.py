import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Task:
    task_id: str
    user_id: int
    name: str
    runner: Callable[["Task"], Awaitable[Any]]
    status: str = "queued"
    current: int = 0
    total: int = 0
    started_at: float = field(default_factory=time.monotonic)
    error: str = ""

    async def run(self) -> Any:
        self.status = "running"
        try:
            result = await self.runner(self)
            self.status = "completed"
            return result
        except asyncio.CancelledError:
            self.status = "cancelled"
            raise
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            raise


class TaskQueue:
    def __init__(self, limit: int = 2, on_change: Callable[[Task], Awaitable[None]] | None = None) -> None:
        self.semaphore = asyncio.Semaphore(max(1, limit))
        self.on_change = on_change
        self.tasks: dict[str, tuple[Task, asyncio.Task[Any]]] = {}

    async def _notify(self, task: Task) -> None:
        if self.on_change:
            try:
                await self.on_change(task)
            except Exception:
                logger.exception("Could not persist task %s", task.task_id)

    async def submit(self, task: Task) -> None:
        async def execute() -> None:
            try:
                async with self.semaphore:
                    task.status = "running"
                    await self._notify(task)
                    await task.run()
            except asyncio.CancelledError:
                if task.status == "queued":
                    task.status = "cancelled"
                raise
            except Exception:
                logger.exception("Task %s failed", task.task_id)
            finally:
                await self._notify(task)

        handle = asyncio.create_task(execute(), name=task.task_id)
        self.tasks[task.task_id] = (task, handle)
        await self._notify(task)

    async def cancel(self, task_id: str, user_id: int | None = None) -> bool:
        entry = self.tasks.get(task_id)
        if not entry or (user_id is not None and entry[0].user_id != user_id):
            return False
        entry[1].cancel()
        return True

    def active(self) -> list[Task]:
        return [task for task, handle in self.tasks.values() if not handle.done()]

    async def close(self) -> None:
        handles = [handle for _, handle in self.tasks.values() if not handle.done()]
        for handle in handles:
            handle.cancel()
        if handles:
            await asyncio.gather(*handles, return_exceptions=True)
