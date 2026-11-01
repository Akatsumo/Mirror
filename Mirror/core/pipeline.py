"""Task lifecycle: queue → download → post-process → upload → report → (seed) → cleanup."""

import asyncio
import dataclasses
import logging
import shlex
import time
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path

import aiohttp
from pyrogram.types import Message

from Mirror.config import Config
from Mirror.core import auth, files, media
from Mirror.core.app import app
from Mirror.core.flags import TaskArgs
from Mirror.core.links import is_gdrive, is_magnet, is_nzb, is_rclone, is_tg_link, is_torrent
from Mirror.core.prefs import Prefs, prefs
from Mirror.core.task import Status, Task, TaskError, Uploaded
from Mirror.core.ui import kb, mention, send
from Mirror.core.utils import dir_size, files_in, human_size, human_time, new_id, run, safe_name
from Mirror.modules import (
    archive,
    aria2,
    ddl,
    debrid,
    direct,
    gallery,
    gdrive,
    jdownloader,
    leech,
    mega,
    qbit,
    rclone,
    resolvers,
    sabnzbd,
    telegram,
    ytdlp,
)

log = logging.getLogger(__name__)


def detect(link: str, hint: str, has_messages: bool = False) -> str:
    """Engine for a link/source: forced by command hint, else by link type and config defaults."""
    if has_messages:
        return "telegram"
    if hint in {"ytdl", "gallery"}:
        return hint
    if hint == "jd" or link.lower().endswith(".dlc"):
        return "jd"
    if is_tg_link(link):
        return "telegram"
    if is_magnet(link) or is_torrent(link):
        return hint if hint in {"qbit", "aria2"} else ("aria2" if Config.TORRENT_ENGINE == "aria2" else "qbit")
    if is_nzb(link) or hint == "sab":
        return "sab"
    if "mega.nz" in link or "mega.co.nz" in link:
        return "mega"
    if is_gdrive(link):
        return "gdrive"
    if is_rclone(link):
        return "rclone"
    if hint == "aria2" or (Config.DIRECT_ENGINE == "aria2" and not resolvers.resolver_for(link)):
        return "aria2"
    return "direct"


def destination(task: Task) -> tuple[str, str]:
    raw = task.args.up.strip()
    raw = str(task.prefs.upload_paths.get(raw, raw)) if raw else ""
    head, _, rest = raw.partition(":")
    if head in {"gd", "rc", "ddl", "tg"}:
        return head, rest
    if raw[:3] in {"mt:", "sa:", "tp:"} or (raw and is_gdrive(raw)):
        return "gd", raw
    if raw.startswith("mrcc:") or (raw and is_rclone(raw)):
        return "rc", raw
    if raw.lstrip("-").isdigit() or raw.startswith("@"):
        return "tg", raw
    return str(task.prefs.default_upload), ""


def final_name(task: Task) -> str:
    name = task.args.name or task.name
    if task.args.zip:
        return f"{name}.zip"
    if task.args.extract and files.is_archive(Path(name)):
        return Path(name).stem
    return name


async def _checks(task: Task) -> None:
    if error := await auth.check_limits(task, task.size):
        raise TaskError(error)
    if task.is_leech or not task.prefs.stop_duplicate or task.args.select:
        return
    kind, target = destination(task)
    name = final_name(task)
    found = None
    if kind == "gd":
        found = await gdrive.find_duplicate(name, target, task.prefs)
    elif kind == "rc":
        conf, base = rclone.config_for(target or task.prefs.rclone_path, task.prefs)
        try:
            await rclone.stat(rclone.join(base, name), conf)
            found = rclone.join(base, name)
        except RuntimeError:
            found = None
    if found:
        raise TaskError(f"Already uploaded: {found}")


def new_task(message: Message, mode: str, engine: str, args: TaskArgs, user_prefs: Prefs) -> Task:
    user = message.from_user
    task_id = new_id()
    return Task(id=task_id, user_id=user.id if user else 0, chat_id=message.chat.id, message_id=message.id,
                mode=mode, engine=engine, args=args, text=message.text or message.caption or "",
                user_name=user.first_name if user else "", dir=(app.downloads / task_id).resolve(),
                prefs=user_prefs, message=message)


async def submit(message: Message, mode: str, hint: str, args: TaskArgs, sources: list[Message] | None = None,
                 source_file: Path | None = None) -> Task:
    user_prefs = await prefs(message.from_user.id if message.from_user else 0)
    if source_file and not args.links:
        args.links = [str(source_file)]
    engine = "clone" if mode == "clone" else detect(args.link, hint, bool(sources))
    task = new_task(message, mode, engine, args, user_prefs)
    task.meta_hook = _checks
    app.tasks.add(task)
    task.handle = app.spawn(_run(task, sources, source_file))
    return task


async def _run(task: Task, sources: list[Message] | None, source_file: Path | None) -> None:
    await app.db.add_task(task.id, chat_id=task.chat_id, message_id=task.message_id, user_id=task.user_id,
                          text=task.text, mode=task.mode)
    try:
        if task.mode == "clone":
            await _report(task, [await _clone(task)])
            return
        async with app.queue.slot("dl", task, task.args.force or task.args.force_dl):
            path = await _download(task, sources, source_file)
        if not path.exists():
            raise TaskError("Download finished but no files were found")
        task.size = downloaded = dir_size(path)
        work = files.link_tree(path, task.dir / ".upload") if task.seeder else path
        work = await _process(task, work)
        async with app.queue.slot("up", task, task.args.force or task.args.force_up):
            results = await _upload(task, work)
        await _report(task, results)
        await app.db.add_usage(task.user_id, auth.today(), downloaded)
        if task.seeder:
            files.clean(task.dir / ".upload")
            await task.seeder()
            await send(task.chat_id, f"🌱 Seeding finished: <code>{escape(task.name)}</code>", reply_to=task.message_id)
    except asyncio.CancelledError:
        if not app.stopping:
            await _notify(task, "Task cancelled")
        raise
    except TaskError as exc:
        await _notify(task, str(exc))
    except Exception as exc:
        log.exception("task %s failed", task.id)
        await _notify(task, f"{type(exc).__name__}: {exc}")
    finally:
        app.tasks.remove(task.id)
        await asyncio.to_thread(files.clean, task.dir)
        if source_file:
            source_file.unlink(missing_ok=True)
        if not app.stopping:
            await app.db.remove_task(task.id)
        await app.queue.wake()


async def _notify(task: Task, text: str) -> None:
    await send(task.chat_id, f"⚠️ {mention(task.user_id, task.user_name)}: {escape(text)[:3500]}",
               reply_to=task.message_id)


async def _each_link(task: Task, fetch: Callable[[], Awaitable[Path]]) -> Path:
    """Run ``fetch`` for every link; with ``-m`` all links land in one folder."""
    if len(task.args.links) < 2:
        return await fetch()
    original, base = task.args, task.dir
    root = base / safe_name(original.same_dir or original.name or "multi")
    try:
        for link in original.links:
            task.args, task.dir = dataclasses.replace(original, links=[link], name="", same_dir=""), root
            await fetch()
    finally:
        task.args, task.dir = original, base
    task.name = root.name
    return root


async def _direct(task: Task) -> Path:
    resolved = await resolvers.resolve(task.args.link)
    if not resolved:
        return await direct.download(task, name=task.args.name)
    if len(resolved.files) == 1:
        return await direct.download(task, resolved.files[0][0], headers=resolved.headers,
                                     name=task.args.name or resolved.files[0][1])
    folder = task.dir / safe_name(task.args.name or resolved.folder or task.id)
    await task.meta(folder.name, 0)
    for url, name in resolved.files:
        sub = folder / Path(name).parent if "/" in name else folder
        await direct.download(task, url, sub, resolved.headers, Path(name).name)
    return folder


async def _torrent(task: Task, source: Path | None) -> Path:
    if is_magnet(task.args.link) and Config.DEBRID_TOKEN and not task.args.seed and not task.args.select:
        try:
            links = await debrid.cached_links(task.args.link)
        except (RuntimeError, aiohttp.ClientError, KeyError) as exc:
            log.info("debrid lookup failed: %s", exc)
            links = []
        if links:
            task.engine = "debrid"
            folder = task.dir / "debrid"
            for url in links:
                await direct.download(task, url, folder)
            return files.single_root(folder)
    engine = qbit if task.engine == "qbit" else aria2
    return await engine.download(task, source)


async def _download(task: Task, sources: list[Message] | None, source_file: Path | None) -> Path:
    task.set_status(Status.DOWNLOAD)
    task.dir.mkdir(parents=True, exist_ok=True)
    engine = task.engine
    if engine == "telegram":
        messages = sources or [m for link in task.args.links for m in await telegram.resolve(link)]
        return await telegram.download(task, messages)
    if engine in {"qbit", "aria2"} and (source_file or is_torrent(task.args.link) or task.engine == "qbit"):
        return await _torrent(task, source_file)
    if engine == "sab":
        return await sabnzbd.download(task, source_file)
    if engine == "jd":
        return await jdownloader.download(task, source_file)
    single: dict[str, Callable[[], Awaitable[Path]]] = {
        "ytdl": lambda: ytdlp.download(task), "gallery": lambda: gallery.download(task),
        "mega": lambda: mega.download(task), "gdrive": lambda: gdrive.download(task),
        "rclone": lambda: rclone.download(task), "aria2": lambda: aria2.download(task),
        "direct": lambda: _direct(task),
    }
    return await _each_link(task, single[engine])


async def _media_files(path: Path) -> list[tuple[Path, media.MediaInfo]]:
    found = []
    for item in files_in(path):
        info = await media.probe(item)
        if info.duration and (info.video or info.audio):
            found.append((item, info))
    return found


async def _process(task: Task, path: Path) -> Path:
    args = task.args
    if args.join:
        task.set_status(Status.PROCESS)
        await asyncio.to_thread(files.join_parts, path if path.is_dir() else path.parent)
    if args.extract:
        task.set_status(Status.EXTRACT, dir_size(path))
        try:
            path = await archive.extract_all(path, args.extract_pass, task.progress)
        except (ValueError, RuntimeError) as exc:
            raise TaskError(f"Extract failed: {exc}") from exc
    if args.ffmpeg or args.convert_video or args.convert_audio or args.sample or args.shots:
        task.set_status(Status.PROCESS)
        path = await _media_tools(task, path)
    rules = args.ns or task.prefs.name_sub
    if task.is_leech:
        path = files.apply_names(path, task.prefs.prefix, task.prefs.suffix, task.prefs.remname, rules)
    elif rules:
        path = files.apply_names(path, rules=rules)
    if args.zip:
        task.set_status(Status.ARCHIVE, dir_size(path))
        split = leech.plan(task)[3] if task.is_leech and dir_size(path) > leech.plan(task)[3] else 0
        target = task.dir / ".zip" / safe_name(f"{args.name or path.name}.zip")
        try:
            await archive.create_zip([path], target, args.zip_pass, split, task.progress)
        except RuntimeError as exc:
            raise TaskError(f"Zip failed: {exc}") from exc
        files.clean(path)
        path = target if not split else target.parent
    task.name = path.name
    return path


async def _media_tools(task: Task, path: Path) -> Path:
    args = task.args
    root = path if path.is_dir() else path.parent
    for item, info in await _media_files(path):
        task.name = item.name
        if args.ffmpeg:
            out = item.with_name(f"{item.stem}.ff{item.suffix}")
            code, msg = await media_ffmpeg(item, out, args.ffmpeg)
            if code:
                raise TaskError(f"ffmpeg: {msg[-400:]}")
            out.replace(item)
        target = args.convert_video if info.video else args.convert_audio
        if target:
            item = await media.convert(item, target)
        if info.video and args.sample:
            total, _, part = args.sample.partition(":")
            await media.sample(item, item.with_name(f"{item.stem}.sample.mp4"), info.duration, int(total or 60),
                               int(part or 4))
        if info.video and args.shots:
            await media.screenshots(item, root / f"{item.stem[:40]}_screenshots", args.shots, info.duration)
    return path if path.exists() else files.single_root(root)


async def media_ffmpeg(source: Path, out: Path, raw: str) -> tuple[int, str]:
    return await run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), *shlex.split(raw),
                     str(out))


async def _thumb(task: Task) -> Path | None:
    raw = task.args.thumb
    if raw.lower() == "none":
        return None
    if not raw:
        return task.prefs.file("thumb")
    target = task.dir / ".thumb.jpg"
    if is_tg_link(raw):
        message = (await telegram.resolve(raw))[0]
        return Path(await message._client.download_media(message, file_name=str(target)))
    async with aiohttp.ClientSession() as session, session.get(raw) as response:
        if response.status == 200:
            target.write_bytes(await response.read())
            return target
    return None


async def _upload(task: Task, path: Path) -> list[Uploaded]:
    kind, target = destination(task)
    results = []
    if task.is_leech or kind == "tg" or task.args.hybrid:
        if not task.is_leech and kind == "tg":
            task.args.up = f"tg:{target}"
        results.append(await leech.upload(task, path if not task.args.hybrid else files.link_tree(
            path, task.dir / ".hybrid"), await _thumb(task)))
        if not task.args.hybrid:
            return results
        kind, target = (kind, target) if kind != "tg" else (str(task.prefs.default_upload), "")
    task.set_status(Status.UPLOAD, dir_size(path))
    if kind == "rc":
        results.append(await rclone.upload(task, path, target))
    elif kind == "ddl":
        results += await ddl.upload(task, path, target.split(",") if target else list(task.prefs.ddl_sites))
    else:
        results.append(await gdrive.upload(task, path, target))
    return results


async def _clone(task: Task) -> Uploaded:
    kind, target = destination(task)
    if is_gdrive(task.args.link):
        return await gdrive.clone(task, target if kind == "gd" else "")
    if is_rclone(task.args.link):
        return await rclone.clone(task, target if kind == "rc" else "")
    raise TaskError("Clone supports Google Drive links and rclone paths")


def _buttons(results: list[Uploaded]) -> list[list[tuple[str, str]]]:
    rows = []
    for result in results:
        row = [(f"☁️ {result.target}", result.link)] if result.link.startswith("http") and result.target != "Telegram" \
            else []
        if result.index.startswith("http"):
            row.append(("⚡ Index", result.index))
        if result.view:
            row.append(("🌐 View", result.view))
        rows.append(row)
    return rows


async def _report(task: Task, results: list[Uploaded]) -> None:
    head = (f"<b>{escape(task.name)}</b>\n├ Size: {human_size(task.size)}\n"
            f"├ Elapsed: {human_time(time.time() - task.started)}\n├ Mode: {task.mode} · {task.engine}\n")
    lines = []
    for result in results:
        head += f"├ {result.target}: {result.files} file(s)" + (f", {result.folders} folder(s)" if result.folders
                                                                 else "") + "\n"
        if result.target == "Rclone" and result.index:
            head += f"├ Path: <code>{escape(result.index)}</code>\n"
        lines += [f"• <a href=\"{link}\">{escape(name)}</a>" for name, link in result.items]
    head += f"└ By: {mention(task.user_id, task.user_name)}"
    await send(task.chat_id, head, kb(*_buttons(results)), reply_to=task.message_id)
    for start in range(0, len(lines), 40):
        await send(task.chat_id, "\n".join(lines[start:start + 40]), reply_to=task.message_id)


async def restore() -> int:
    """Re-run commands of tasks that were active before a restart (or report them)."""
    restored = 0
    stored = await app.db.tasks()
    await app.db.clear_tasks()
    from Mirror.modules.mirror import dispatch

    for doc in stored:
        try:
            message = await app.bot.get_messages(doc["chat_id"], doc["message_id"])
        except Exception:  # noqa: BLE001 - message may be gone
            continue
        if not message or message.empty:
            continue
        if Config.RESTORE_TASKS:
            await dispatch(message, doc.get("text") or message.text or "")
            restored += 1
        else:
            await send(doc["chat_id"], "♻️ Bot restarted; this task was interrupted.", reply_to=doc["message_id"])
    return restored

