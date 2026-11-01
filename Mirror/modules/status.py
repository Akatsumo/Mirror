"""Status boards (paged, filtered, auto-refreshing) plus cancel and cancel-all controls."""

import asyncio
import contextlib
import shutil
import time
from dataclasses import dataclass
from html import escape

import psutil
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from Mirror.config import Config
from Mirror.core.app import app
from Mirror.core.auth import authorized, is_sudo
from Mirror.core.task import GROUPS, Status, Task
from Mirror.core.ui import Row, delete, edit, kb, send
from Mirror.core.utils import human_size, human_time, progress_bar

STEPS = (1, 2, 4, 8)
LABELS = {"all": "All", "dl": "DL", "up": "UP", "queue": "Queue", "proc": "Proc", "seed": "Seed"}


@dataclass
class Board:
    message: Message
    user_id: int | None
    page: int = 0
    step: int = 1
    group: str = "all"


boards: dict[int, Board] = {}
_updater: asyncio.Task[None] | None = None


def task_block(index: int, task: Task) -> str:
    lines = [f"<b>{index}. {escape(task.name or task.args.link[:60] or task.id)}</b>"]
    if task.status not in (Status.QUEUE_DL, Status.QUEUE_UP, Status.WAIT):
        lines.append(f"├ {progress_bar(task.done, task.size)} {task.percent:.1f}%")
        lines.append(f"├ {human_size(task.done)} / {human_size(task.size) if task.size else '?'}"
                     f" · {human_size(task.speed)}/s" + (f" · ETA {human_time(task.eta)}" if task.eta else ""))
    lines.append(f"├ {task.status} · {task.engine}" + (f" · {escape(task.extra)}" if task.extra else ""))
    lines.append(f"├ {human_time(time.time() - task.started)} · {escape(task.user_name or str(task.user_id))}")
    lines.append(f"└ /cancel_{task.id}")
    return "\n".join(lines)


def footer() -> str:
    down, up = app.tasks.speeds()
    disk = shutil.disk_usage(app.downloads if app.downloads.exists() else ".")
    return (f"<b>CPU</b> {psutil.cpu_percent()}% · <b>RAM</b> {psutil.virtual_memory().percent}% · "
            f"<b>Free</b> {human_size(disk.free)}\n<b>Uptime</b> {human_time(time.time() - app.started)} · "
            f"▼ {human_size(down)}/s · ▲ {human_size(up)}/s")


def render(board: Board) -> tuple[str, list[Row]]:
    tasks = app.tasks.select(board.group, board.user_id)
    limit = max(1, Config.STATUS_LIMIT)
    pages = max(1, -(-len(tasks) // limit))
    board.page %= pages
    chunk = tasks[board.page * limit:(board.page + 1) * limit]
    body = "\n\n".join(task_block(board.page * limit + i + 1, t) for i, t in enumerate(chunk)) or "No tasks here."
    title = f"<b>Tasks</b> {len(tasks)} · {LABELS[board.group]}" + (f" · page {board.page + 1}/{pages}"
                                                                       if pages > 1 else "")
    chat = board.message.chat.id
    rows: list[Row] = []
    if pages > 1:
        rows.append([("◀", f"st|{chat}|p|-1"), (f"×{board.step}", f"st|{chat}|s|0"), ("▶", f"st|{chat}|p|1")])
    if len(app.tasks) > limit or board.group != "all":
        rows.append([(("• " if g == board.group else "") + label, f"st|{chat}|g|{g}") for g, label in LABELS.items()])
    rows.append([("♻ Refresh", f"st|{chat}|r|0"), ("✖ Close", f"st|{chat}|x|0")])
    return f"{title}\n\n{body}\n\n{footer()}", rows


async def show(message: Message, user_id: int | None = None) -> None:
    chat = message.chat.id
    if old := boards.pop(chat, None):
        await delete(old.message)
    tmp = Board(message, user_id)
    text, rows = render(tmp)
    sent = await send(chat, text, kb(*rows))
    if not sent:
        return
    if user_id is None:
        boards[chat] = Board(sent, None)
        _ensure_updater()

def _ensure_updater() -> None:
    global _updater
    if _updater is None or _updater.done():
        _updater = asyncio.create_task(_update_loop())


async def _update_loop() -> None:
    while boards:
        await asyncio.sleep(max(3, Config.STATUS_UPDATE_INTERVAL))
        for chat, board in list(boards.items()):
            if not len(app.tasks):
                boards.pop(chat, None)
                await delete(board.message)
                continue
            text, rows = render(board)
            if not await edit(board.message, text, kb(*rows)):
                boards.pop(chat, None)


@app.command("status", "s", flt=authorized)
async def status_cmd(_: Client, message: Message) -> None:
    mine = len(message.command) > 1 and message.command[1].lower() == "me"
    if not len(app.tasks):
        await send(message.chat.id, f"No active tasks.\n\n{footer()}", reply_to=message.id)
        return
    await show(message, message.from_user.id if mine and message.from_user else None)


@app.callback("st")
async def status_cb(_: Client, query: CallbackQuery) -> None:
    _, chat, action, arg = str(query.data).split("|", 3)
    board = boards.get(int(chat))
    if not board or board.message.id != query.message.id:
        await query.answer("Old status message", show_alert=False)
        await delete(query.message)
        return
    await query.answer()
    if action == "p":
        board.page += int(arg) * board.step
    elif action == "s":
        board.step = STEPS[(STEPS.index(board.step) + 1) % len(STEPS)]
    elif action == "g" and arg in GROUPS:
        board.group, board.page = arg, 0
    elif action == "x":
        boards.pop(int(chat), None)
        await delete(board.message)
        return
    text, rows = render(board)
    await edit(board.message, text, kb(*rows))


async def _cancel(message: Message, task_id: str) -> None:
    task = app.tasks.get(task_id)
    uid = message.from_user.id if message.from_user else 0
    if not task:
        await send(message.chat.id, "Task not found.", reply_to=message.id)
    elif task.user_id != uid and not is_sudo(uid):
        await send(message.chat.id, "Not your task.", reply_to=message.id)
    else:
        task.cancel()


@app.command("cancel", "c", flt=authorized)
async def cancel_cmd(_: Client, message: Message) -> None:
    if len(message.command) > 1:
        await _cancel(message, message.command[1])
    else:
        await send(message.chat.id, "Usage: /cancel ID (or tap /cancel_ID in status)", reply_to=message.id)


@app.message(authorized & filters.regex(r"^/cancel_([0-9a-f]+)"))
async def cancel_link(_: Client, message: Message) -> None:
    await _cancel(message, message.matches[0].group(1))


@app.command("cancelall", "ca", flt=authorized)
async def cancel_all(_: Client, message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    rows = [[(f"{label} ({len(app.tasks.select(g, None if is_sudo(uid) else uid))})", f"cx|{uid}|{g}")]
            for g, label in LABELS.items()]
    await send(message.chat.id, "Cancel which tasks?", kb(*rows, [("Close", f"cx|{uid}|close")]), reply_to=message.id)


@app.callback("cx")
async def cancel_all_cb(_: Client, query: CallbackQuery) -> None:
    _, uid, group = str(query.data).split("|", 2)
    if query.from_user.id != int(uid):
        await query.answer("Not yours", show_alert=True)
        return
    if group == "close":
        await delete(query.message)
        return
    count = app.tasks.cancel_where(group, None if is_sudo(int(uid)) else int(uid))
    await query.answer(f"Cancelled {count} task(s)", show_alert=True)
    with contextlib.suppress(Exception):
        await query.message.delete()
