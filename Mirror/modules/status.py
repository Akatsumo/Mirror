import asyncio
import logging
import time
from html import escape

from pyrogram.errors import RPCError
from pyrogram.types import Message

from Mirror.config import Config
from Mirror.core.task import Task, TaskQueue
from Mirror.core.utils import human_size, human_time, progress_bar

logger = logging.getLogger(__name__)


def render_task(task: Task) -> str:
    elapsed = max(0, int(time.monotonic() - task.started_at))
    pct = int(task.current * 100 / task.total) if task.total else 0
    total = human_size(task.total) if task.total else "unknown"
    line = f"<code>{escape(task.name)}</code> · {escape(task.status)} · <code>{escape(task.task_id)}</code>"
    if task.status == "running":
        line += f"\n{progress_bar(task.current, task.total)} {pct}% · {human_size(task.current)}/{total} · {human_time(elapsed)}"
    if task.error:
        line += f"\n<code>{escape(task.error[:300])}</code>"
    return line


async def status_text(queue: TaskQueue, user_id: int) -> str:
    current = [task for task in queue.active() if task.user_id == user_id]
    if not current:
        return "No active tasks."
    return "<b>Active tasks</b>\n\n" + "\n\n".join(render_task(task) for task in current)


async def watch_progress(message: Message, task: Task) -> None:
    while task.status in {"queued", "running"}:
        try:
            await message.edit_text(render_task(task))
        except RPCError:
            logger.debug("Could not update task progress message", exc_info=True)
        await asyncio.sleep(Config.PROGRESS_INTERVAL)
    try:
        await message.edit_text(render_task(task))
    except RPCError:
        logger.debug("Could not finalize task progress message", exc_info=True)
