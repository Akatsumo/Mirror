"""Transfer commands: mirror/leech per engine, clone, count, delete, Drive search; multi/bulk input."""

import dataclasses
from html import escape
from pathlib import Path

from pyrogram import Client
from pyrogram.types import Message

from Mirror.config import Config
from Mirror.core import pipeline
from Mirror.core.app import app
from Mirror.core.auth import authorized, force_sub, sudo_only, task_slots_left
from Mirror.core.flags import FlagError, TaskArgs, flags_help, parse_args
from Mirror.core.links import is_url
from Mirror.core.prefs import prefs
from Mirror.core.telegraph import publish
from Mirror.core.ui import kb, send
from Mirror.core.utils import human_size, new_id
from Mirror.modules import gdrive
from Mirror.modules.status import show
from Mirror.modules.telegram import media_of

COMMANDS: dict[str, tuple[str, str]] = {
    "mirror": ("mirror", ""), "m": ("mirror", ""), "leech": ("leech", ""), "l": ("leech", ""),
    "qbmirror": ("mirror", "qbit"), "qm": ("mirror", "qbit"), "qbleech": ("leech", "qbit"), "ql": ("leech", "qbit"),
    "aria2mirror": ("mirror", "aria2"), "am": ("mirror", "aria2"), "aria2leech": ("leech", "aria2"),
    "al": ("leech", "aria2"), "ytdl": ("mirror", "ytdl"), "y": ("mirror", "ytdl"), "ytdlleech": ("leech", "ytdl"),
    "yl": ("leech", "ytdl"), "gdl": ("mirror", "gallery"), "gdlleech": ("leech", "gallery"),
    "nzbmirror": ("mirror", "sab"), "nm": ("mirror", "sab"), "nzbleech": ("leech", "sab"), "nl": ("leech", "sab"),
    "jdmirror": ("mirror", "jd"), "jm": ("mirror", "jd"), "jdleech": ("leech", "jd"), "jl": ("leech", "jd"),
    "clone": ("clone", ""),
}
DEFAULTS = TaskArgs()


def merge(base: TaskArgs, extra: TaskArgs) -> TaskArgs:
    """Per-line bulk flags override the command flags."""
    changed = {f.name: getattr(extra, f.name) for f in dataclasses.fields(TaskArgs)
               if getattr(extra, f.name) != getattr(DEFAULTS, f.name)}
    return dataclasses.replace(base, **changed)


async def _text_of(message: Message) -> str:
    if message.document and (message.document.mime_type or "").startswith("text"):
        data = await message.download(in_memory=True)
        return bytes(data.getbuffer()).decode(errors="ignore")
    return message.text or message.caption or ""


async def _source_file(message: Message) -> Path | None:
    name = (message.document.file_name or "").lower() if message.document else ""
    if name.endswith((".torrent", ".nzb", ".dlc")):
        folder = app.downloads / ".sources"
        folder.mkdir(parents=True, exist_ok=True)
        return Path(await message.download(file_name=str(folder / f"{new_id()}_{Path(name).name}")))
    return None


async def _series(message: Message, count: int) -> list[Message]:
    ids = list(range(message.id, message.id + count))
    found = await message._client.get_messages(message.chat.id, ids)
    return [m for m in (found if isinstance(found, list) else [found]) if m and not m.empty and media_of(m)]


async def dispatch(message: Message, text: str | None = None) -> None:
    """Parse a transfer command (also used by RSS/restore) and submit one or many tasks."""
    text = text if text is not None else message.text or message.caption or ""
    command = text.split(maxsplit=1)[0].lstrip("/").split("@")[0].lower() if text else "mirror"
    mode, hint = COMMANDS.get(command.removesuffix(Config.CMD_SUFFIX), ("mirror", ""))
    uid = message.from_user.id if message.from_user else 0
    try:
        args = parse_args(text)
    except FlagError as exc:
        await send(message.chat.id, f"⚠️ {escape(str(exc))}", reply_to=message.id)
        return
    if error := await force_sub(message):
        await send(message.chat.id, error, reply_to=message.id)
        return
    if not task_slots_left(uid):
        await send(message.chat.id, "You reached your task limit; wait for running tasks.", reply_to=message.id)
        return
    reply = message.reply_to_message
    jobs: list[tuple[TaskArgs, list[Message] | None, Path | None]] = []
    if args.bulk and reply:
        lines = [x.strip() for x in (await _text_of(reply)).splitlines() if x.strip()]
        jobs = [(merge(args, parse_args(line, has_command=False)), None, None) for line in lines]
    elif not args.links and reply and media_of(reply):
        source = await _source_file(reply)
        if source:
            jobs = [(args, None, source)]
        else:
            series = await _series(reply, args.multi) if args.multi > 1 else [reply]
            jobs = [(args, series, None)] if args.same_dir or len(series) == 1 else [(args, [m], None) for m in series]
    elif not args.links and reply:
        links = [w for w in (await _text_of(reply)).split() if is_url(w) or w.startswith("magnet:")]
        args.links = links[:1] if not args.same_dir else links
        jobs = [(args, None, None)] if args.links else []
    elif len(args.links) > 1 and not args.same_dir and mode != "clone":
        jobs = [(dataclasses.replace(args, links=[link]), None, None) for link in args.links]
    elif args.links:
        jobs = [(args, None, None)]
    if not jobs:
        await send(message.chat.id, usage(command), reply_to=message.id)
        return
    for job_args, sources, source in jobs:
        await pipeline.submit(message, mode, hint, job_args, sources, source)
    await show(message)


def usage(command: str) -> str:
    return (f"<b>/{command}</b> link(s) or reply to a file/link.\n"
            "Flags:\n" + flags_help() + "\n\nExample: <code>/leech https://x/y.mkv -n My File.mkv -z secret</code>")


@app.command(*COMMANDS, flt=authorized)
async def transfer(_: Client, message: Message) -> None:
    await dispatch(message)


def _arg(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()
    reply = message.reply_to_message
    return next((w for w in (reply.text or reply.caption or "").split() if "drive" in w or len(w) > 25), "") \
        if reply else ""


@app.command("count", flt=authorized)
async def count_cmd(_: Client, message: Message) -> None:
    link = _arg(message)
    if not link:
        await send(message.chat.id, "Usage: /count drive link", reply_to=message.id)
        return
    note = await send(message.chat.id, "Counting…", reply_to=message.id)
    try:
        name, folder, size, files, folders = await gdrive.count(link, await prefs(message.from_user.id))
        text = (f"<b>{escape(name)}</b>\n├ Size: {human_size(size)}\n├ Type: {'Folder' if folder else 'File'}\n"
                f"└ Files: {files}" + (f" · Folders: {folders}" if folder else ""))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        text = f"⚠️ {escape(str(exc))}"
    if note:
        await note.edit_text(text)


@app.command("del", "delete", flt=sudo_only)
async def delete_cmd(_: Client, message: Message) -> None:
    link = _arg(message)
    if not link:
        await send(message.chat.id, "Usage: /del drive link", reply_to=message.id)
        return
    try:
        await gdrive.delete(link, await prefs(message.from_user.id))
        text = "🗑 Deleted."
    except Exception as exc:  # noqa: BLE001
        text = f"⚠️ {escape(str(exc))}"
    await send(message.chat.id, text, reply_to=message.id)


@app.command("list", "search_drive", flt=authorized)
async def list_cmd(_: Client, message: Message) -> None:
    query = _arg(message)
    kind = ""
    if query.split(maxsplit=1)[0] in {"-f", "-d"} if query else False:
        flag, _, query = query.partition(" ")
        kind = "files" if flag == "-f" else "folders"
    if not query:
        await send(message.chat.id, "Usage: /list [-f|-d] name", reply_to=message.id)
        return
    note = await send(message.chat.id, f"Searching <code>{escape(query)}</code>…", reply_to=message.id)
    results = await gdrive.search(query, await prefs(message.from_user.id), kind)
    if not results:
        text, markup = f"No results for <code>{escape(query)}</code>.", None
    else:
        blocks, current = [], ""
        for drive, item, index in results:
            folder = item["mimeType"] == gdrive.FOLDER
            url, index_url, view = gdrive.links(item["id"], item["name"], folder, index)
            if drive != current:
                blocks.append(f"<h4>{escape(drive)}</h4>")
                current = drive
            size = "" if folder else f" ({human_size(int(item.get('size', 0)))})"
            extra = (f'<a href="{index_url}">Index</a>' if index_url else "") + (
                f' | <a href="{view}">View</a>' if view else "")
            blocks.append(f"<p>{'📁' if folder else '📄'} <code>{escape(item['name'])}</code>{size}<br>"
                          f'<a href="{url}">Drive</a>{extra}</p>')
        page = await publish(f"Search: {query}", blocks)
        text, markup = f"Found {len(results)} result(s) for <code>{escape(query)}</code>.", kb([("🔎 Results", page)])
    if note:
        await note.edit_text(text, reply_markup=markup)

