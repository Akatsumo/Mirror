import asyncio
from html import escape
from pathlib import Path

from pyrogram import Client
from pyrogram.types import Message

from Mirror.config import Config
from Mirror.core.task import Task
from Mirror.core.utils import safe_split

VIDEO = {".mp4", ".mkv", ".mov", ".webm"}
AUDIO = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"}


async def upload_file(
    client: Client,
    message: Message,
    path: Path,
    *,
    split_size: int = Config.LEECH_SPLIT_SIZE,
    as_document: bool = Config.UPLOAD_AS_DOC,
    thumbnail: str | None = None,
    prefix: str = "",
    task: Task | None = None,
) -> list[Message]:
    parts = await asyncio.to_thread(safe_split, path, split_size)
    sent: list[Message] = []
    for index, part in enumerate(parts, start=1):
        caption = escape(f"{prefix}{part.name}")
        if len(parts) > 1:
            caption = escape(f"{prefix}{part.name} ({index}/{len(parts)})")
        common = {"caption": caption[:1024], "reply_to_message_id": message.id}
        if task:
            task.current = 0
            task.total = part.stat().st_size

            async def update_progress(current: int, total: int, active_task: Task) -> None:
                active_task.current = current
                active_task.total = total

            common["progress"] = update_progress
            common["progress_args"] = (task,)
        if thumbnail and Path(thumbnail).is_file():
            common["thumb"] = thumbnail
        suffix = part.suffix.lower()
        if as_document or suffix not in VIDEO | AUDIO:
            sent.append(await client.send_document(message.chat.id, str(part), **common))
        elif suffix in VIDEO:
            sent.append(await client.send_video(message.chat.id, str(part), supports_streaming=True, **common))
        else:
            sent.append(await client.send_audio(message.chat.id, str(part), **common))
    return sent
