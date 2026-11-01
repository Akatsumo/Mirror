"""Telegram leech: split (ffmpeg/bytes, equal), thumbnails, media groups, user/bot/mix sessions."""

import asyncio
from html import escape
from pathlib import Path
from typing import Any

from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InputMediaAudio, InputMediaDocument, InputMediaVideo, Message

from Mirror.core.app import BOT_LIMIT, app
from Mirror.core.files import excluded, split_file
from Mirror.core.media import kind_of, probe, split_video, thumbnail
from Mirror.core.task import Status, Task, TaskError, Uploaded
from Mirror.core.utils import files_in


def parse_dest(raw: str, fallback: int) -> tuple[int | str, int | None]:
    """``chat``, ``chat:topic`` or ``chat|topic`` (chat may be an id or @username)."""
    raw = raw.removeprefix("tg:").strip()
    if not raw:
        return fallback, None
    chat, _, topic = raw.replace("|", ":").partition(":")
    ref: int | str = int(chat) if chat.lstrip("-").isdigit() else chat
    return ref, int(topic) if topic.isdigit() else None


def plan(task: Task) -> tuple[int | str, int | None, str, int]:
    """Destination chat/topic, session mode and the effective part limit."""
    args, prefs = task.args, task.prefs
    chat, topic = parse_dest(args.up if args.up.startswith("tg") or args.up.lstrip("-").isdigit() else
                             prefs.leech_dest, task.chat_id)
    session = "user" if args.user_session else "bot" if args.bot_session else prefs.session
    if isinstance(chat, int) and chat > 0:
        session = "bot"
    ceiling = app.max_size if session != "bot" else BOT_LIMIT
    limit = min(args.split or prefs.split_size or ceiling, ceiling)
    return chat, topic, session, limit


async def _send(client: Client, kind: str, chat: int | str, path: Path, **kw: Any) -> Message:
    sender = {"video": client.send_video, "audio": client.send_audio, "photo": client.send_photo,
              "document": client.send_document}[kind]
    for _ in range(5):
        try:
            message = await sender(chat, str(path), **kw)
            if message:
                return message
        except FloodWait as wait:
            await asyncio.sleep(float(wait.value) * 1.2)
        except RPCError as exc:
            if kind != "document" and "MEDIA" in str(exc).upper():
                kind, sender = "document", client.send_document
                kw = {k: v for k, v in kw.items() if k in {"caption", "thumb", "progress", "reply_to_message_id",
                                                           "file_name", "disable_notification"}}
                continue
            raise
    raise TaskError(f"Telegram refused {path.name}")


async def _split(task: Task, path: Path, limit: int, equal: bool, as_doc: bool) -> list[Path]:
    if path.stat().st_size <= limit:
        return [path]
    task.set_status(Status.SPLIT)
    task.name = path.name
    info = await probe(path)
    if info.video and not as_doc:
        return await split_video(path, limit, info.duration, path.parent / f".{path.stem}.parts", equal)
    parts = await asyncio.to_thread(split_file, path, limit, equal)
    path.unlink(missing_ok=True)
    return parts


async def _group(client: Client, chat: int | str, topic: int | None, sent: list[Message]) -> list[Message]:
    medias = []
    for message in sent:
        media = message.video or message.document or message.audio
        maker = InputMediaVideo if message.video else InputMediaAudio if message.audio else InputMediaDocument
        medias.append(maker(media.file_id, caption=message.caption.html if message.caption else ""))
    grouped: list[Message] = []
    try:
        for start in range(0, len(medias), 10):
            grouped += await client.send_media_group(chat, medias[start:start + 10], reply_to_message_id=topic)
    except RPCError:
        return sent
    for message in sent:
        await message.delete()
    return grouped


async def upload(task: Task, path: Path, thumb: Path | None) -> Uploaded:
    args, prefs = task.args, task.prefs
    chat, topic, session, limit = plan(task)
    as_doc = True if args.doc else False if args.media else prefs.as_doc
    equal, group = args.equal or prefs.equal_split, args.group or prefs.media_group
    layout = args.layout or prefs.thumb_layout
    sources = [f for f in files_in(path) if not excluded(f, prefs.excluded_ext)]
    if not sources:
        raise TaskError("Nothing to upload (all files excluded)")
    batches = [(src.name, await _split(task, src, limit, equal, as_doc)) for src in sources]
    total = sum(p.stat().st_size for _, parts in batches for p in parts)
    task.set_status(Status.UPLOAD, total)
    items: list[tuple[str, str]] = []
    base = 0
    for original, parts in batches:
        client = app.uploader(max(p.stat().st_size for p in parts), session)
        sent: list[Message] = []
        for index, part in enumerate(parts, 1):
            task.name = part.name
            info = await probe(part)
            kind = "document" if as_doc else kind_of(part, info)
            cover = thumb
            if not cover and (info.video or layout) and kind in {"video", "document"} and info.duration:
                cover = await thumbnail(part, part.with_suffix(".thumb.jpg"), info.duration, layout)
            caption = f"<code>{escape(part.name)}</code>" + (f" ({index}/{len(parts)})" if len(parts) > 1 else "")

            async def progress(current: int, _: int, offset: int = base) -> None:
                task.progress(offset + current)
            kw: dict[str, Any] = {"caption": caption[:1024], "progress": progress, "reply_to_message_id": topic}
            if cover and kind != "photo":
                kw["thumb"] = str(cover)
            if kind == "video":
                kw |= {"duration": int(info.duration), "width": info.width, "height": info.height,
                       "supports_streaming": True}
            elif kind == "audio":
                kw |= {"duration": int(info.duration), "title": info.title or part.stem, "performer": info.artist}
            elif kind == "document":
                kw["force_document"] = True
            sent.append(await _send(client, kind, chat, part, **kw))
            base += part.stat().st_size
            if cover and cover != thumb:
                cover.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        if group and len(sent) > 1:
            sent = await _group(client, chat, topic, sent)
        items += [(original if len(sent) == 1 else m.caption or original, m.link) for m in sent]
    return Uploaded("Telegram", items[0][1] if items else "", files=len(items), items=items)
