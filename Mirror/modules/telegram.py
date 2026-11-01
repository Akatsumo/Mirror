"""Telegram sources: replied media, public/private/topic message links and ranges (user session)."""

from pathlib import Path
from typing import Any

from pyrogram import Client
from pyrogram.errors import RPCError
from pyrogram.types import Message

from Mirror.core.app import app
from Mirror.core.files import single_root
from Mirror.core.links import parse_tg_link
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.utils import safe_name

MEDIA = ("document", "video", "audio", "photo", "animation", "voice", "video_note", "sticker")


def media_of(message: Message) -> Any:
    return next((getattr(message, kind) for kind in MEDIA if getattr(message, kind, None)), None)


def file_name(message: Message) -> str:
    media = media_of(message)
    name = getattr(media, "file_name", None)
    if name:
        return safe_name(name)
    ext = {"photo": "jpg", "voice": "ogg", "video_note": "mp4", "animation": "mp4", "sticker": "webp",
           "video": "mp4", "audio": "mp3"}.get(next(k for k in MEDIA if getattr(message, k, None)), "bin")
    return f"{message.chat.id}_{message.id}.{ext}"


async def resolve(link: str) -> list[Message]:
    """Fetch messages for a t.me link; private chats require SESSION_STRING with membership."""
    ref = parse_tg_link(link)
    order = (app.bot, app.user) if isinstance(ref.chat, str) else (app.user, app.bot)
    clients: list[Client] = [c for c in order if c]
    error: Exception | None = None
    for client in clients:
        try:
            found = await client.get_messages(ref.chat, ref.ids)
        except RPCError as exc:
            error = exc
            continue
        messages = [m for m in (found if isinstance(found, list) else [found]) if m and not m.empty]
        if messages:
            return messages
    hint = "" if app.user else " (add SESSION_STRING for private chats)"
    raise TaskError(f"Cannot access {link}: {error or 'message not found'}{hint}")


async def download(task: Task, messages: list[Message]) -> Path:
    items = [m for m in messages if media_of(m)]
    if not items:
        raise TaskError("No media in the given message(s)")
    total = sum(int(getattr(media_of(m), "file_size", 0) or 0) for m in items)
    name = task.args.name if task.args.name and len(items) == 1 else file_name(items[0])
    await task.meta(task.args.same_dir or name, total)
    task.set_status(Status.DOWNLOAD, total)
    folder = task.dir / task.args.same_dir if task.args.same_dir else task.dir
    folder.mkdir(parents=True, exist_ok=True)
    offset = 0
    for message in items:
        target = folder / (safe_name(task.args.name) if task.args.name and len(items) == 1 else file_name(message))

        async def progress(current: int, _: int, base: int = offset) -> None:
            task.progress(base + current)
        result = await message._client.download_media(message, file_name=str(target), progress=progress)
        if not result:
            raise TaskError(f"Download failed for message {message.id}")
        offset += Path(result).stat().st_size
    return single_root(task.dir)
