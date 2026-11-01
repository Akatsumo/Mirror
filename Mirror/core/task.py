import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from Mirror.core.flags import TaskArgs


class Status(StrEnum):
    QUEUE_DL = "QueueDl"
    QUEUE_UP = "QueueUp"
    WAIT = "Waiting"
    DOWNLOAD = "Download"
    UPLOAD = "Upload"
    CLONE = "Clone"
    ARCHIVE = "Archive"
    EXTRACT = "Extract"
    SPLIT = "Split"
    PROCESS = "Process"
    SEED = "Seed"


GROUPS: dict[str, tuple[Status, ...]] = {
    "all": tuple(Status),
    "dl": (Status.DOWNLOAD,),
    "up": (Status.UPLOAD, Status.CLONE),
    "queue": (Status.QUEUE_DL, Status.QUEUE_UP, Status.WAIT),
    "proc": (Status.ARCHIVE, Status.EXTRACT, Status.SPLIT, Status.PROCESS),
    "seed": (Status.SEED,),
}


@dataclass
class Uploaded:
    target: str
    link: str = ""
    index: str = ""
    view: str = ""
    files: int = 0
    folders: int = 0
    items: list[tuple[str, str]] = field(default_factory=list)


class TaskError(Exception):
    """A user-facing task failure (message is shown as is)."""


@dataclass(eq=False)
class Task:
    id: str
    user_id: int
    chat_id: int
    message_id: int
    mode: str
    engine: str
    args: TaskArgs
    text: str = ""
    user_name: str = ""
    name: str = ""
    size: int = 0
    done: int = 0
    speed: float = 0.0
    status: Status = Status.QUEUE_DL
    extra: str = ""
    started: float = field(default_factory=time.time)
    dir: Path = Path()
    prefs: Any = None
    message: Any = None
    handle: asyncio.Task[Any] | None = None
    cancel_hooks: list[Callable[[], Any]] = field(default_factory=list)
    meta_hook: Callable[["Task"], Awaitable[None]] | None = None
    seeder: Callable[[], Awaitable[None]] | None = None
    _mark: tuple[float, int] = (0.0, 0)

    @property
    def percent(self) -> float:
        return min(100.0, self.done * 100 / self.size) if self.size else 0.0

    @property
    def eta(self) -> float:
        return (self.size - self.done) / self.speed if self.speed > 0 and self.size > self.done else 0

    @property
    def is_leech(self) -> bool:
        return self.mode == "leech"

    async def meta(self, name: str, size: int) -> None:
        """Engines call this once name/size are known (runs duplicate and limit checks)."""
        self.name, self.size = name or self.name, size or self.size
        if self.meta_hook:
            hook, self.meta_hook = self.meta_hook, None
            await hook(self)

    def set_status(self, status: Status, size: int | None = None) -> None:
        self.status, self.done, self.speed, self.extra = status, 0, 0.0, ""
        self._mark = (time.monotonic(), 0)
        if size is not None:
            self.size = size

    def progress(self, done: int, size: int | None = None, speed: float | None = None) -> None:
        if size:
            self.size = size
        now = time.monotonic()
        if speed is not None:
            self.speed = speed
        elif now - self._mark[0] >= 1:
            current = (done - self._mark[1]) / (now - self._mark[0])
            self.speed = max(0.0, current if not self.speed else self.speed * 0.6 + current * 0.4)
            self._mark = (now, done)
        self.done = done

    def cancel(self) -> bool:
        for hook in self.cancel_hooks:
            hook()
        if self.handle and not self.handle.done():
            self.handle.cancel()
            return True
        return False


class Registry:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def __iter__(self) -> Iterator[Task]:
        return iter(list(self._tasks.values()))

    def __len__(self) -> int:
        return len(self._tasks)

    def add(self, task: Task) -> None:
        self._tasks[task.id] = task

    def remove(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def select(self, group: str = "all", user_id: int | None = None) -> list[Task]:
        wanted = GROUPS.get(group, GROUPS["all"])
        return [t for t in self if t.status in wanted and (user_id is None or t.user_id == user_id)]

    def count_user(self, user_id: int) -> int:
        return sum(1 for t in self if t.user_id == user_id and t.status != Status.SEED)

    def cancel_where(self, group: str = "all", user_id: int | None = None) -> int:
        return sum(task.cancel() for task in self.select(group, user_id))

    def speeds(self) -> tuple[float, float]:
        down = sum(t.speed for t in self if t.status == Status.DOWNLOAD)
        up = sum(t.speed for t in self if t.status in (Status.UPLOAD, Status.CLONE, Status.SEED))
        return down, up


class Limiter:
    """Counting gate whose limit is read live (0 = unlimited); forced entries bypass it."""

    def __init__(self, limit: Callable[[], int]) -> None:
        self._limit = limit
        self._active = 0
        self._cond = asyncio.Condition()

    @property
    def active(self) -> int:
        return self._active

    async def acquire(self, force: bool = False) -> None:
        async with self._cond:
            if not force:
                await self._cond.wait_for(lambda: self._limit() <= 0 or self._active < self._limit())
            self._active += 1

    async def release(self) -> None:
        async with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()

    async def wake(self) -> None:
        async with self._cond:
            self._cond.notify_all()


class TaskQueue:
    def __init__(self, download: Callable[[], int], upload: Callable[[], int], total: Callable[[], int]) -> None:
        self.gates = {"dl": Limiter(download), "up": Limiter(upload)}
        self.total = Limiter(total)

    @asynccontextmanager
    async def slot(self, kind: str, task: Task, force: bool = False) -> Any:
        task.set_status(Status.QUEUE_DL if kind == "dl" else Status.QUEUE_UP)
        gate = self.gates[kind]
        await self.total.acquire(force)
        try:
            await gate.acquire(force)
        except BaseException:
            await self.total.release()
            raise
        try:
            yield
        finally:
            await gate.release()
            await self.total.release()

    async def wake(self) -> None:
        for gate in (*self.gates.values(), self.total):
            await gate.wake()
