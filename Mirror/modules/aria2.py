import asyncio
import time
from pathlib import Path
from typing import Any

import aria2p

from Mirror.config import Config
from Mirror.core.files import single_root
from Mirror.core.links import is_torrent
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.ui import select_files
from Mirror.modules.direct import headers_of

_api: aria2p.API | None = None


def api() -> aria2p.API:
    global _api
    if _api is None:
        _api = aria2p.API(aria2p.Client(host=Config.ARIA2_HOST, port=Config.ARIA2_PORT, secret=Config.ARIA2_SECRET,
                                        timeout=20))
    return _api


async def call(name: str, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(getattr(api(), name), *args, **kwargs)


async def global_options() -> dict[str, str]:
    options = await call("get_global_options")
    return {k: str(v) for k, v in options.get_struct().items()}


async def set_global_option(key: str, value: str) -> None:
    await asyncio.to_thread(api().client.change_global_option, {key: value})

def _options(task: Task) -> dict[str, str]:
    args = task.args
    opts = {"dir": str(task.dir), "seed-time": "0", "follow-torrent": "mem", "bt-remove-unselected-file": "true"}
    if args.name and not is_torrent(args.link):
        opts["out"] = args.name
    if args.auth_user:
        opts["http-user"] = args.auth_user
    if args.auth_pass:
        opts["http-passwd"] = args.auth_pass
    if args.headers:
        opts["header"] = "\n".join(f"{k}: {v}" for k, v in headers_of(args.headers).items())
    if args.seed:
        del opts["seed-time"]
        if args.seed_time:
            opts["seed-time"] = str(max(1, args.seed_time // 60))
        if args.ratio:
            opts["seed-ratio"] = str(args.ratio)
    if args.select:
        opts["pause-metadata"] = "true"
    return opts


async def _remove(gid: str, files: bool = True) -> None:
    try:
        dl = await call("get_download", gid)
        await call("remove", [dl], force=True, files=files, clean=True)
    except Exception:  # noqa: BLE001 - already gone
        return


async def download(task: Task, source: Path | None = None) -> Path:
    task.dir.mkdir(parents=True, exist_ok=True)
    opts = _options(task)
    link = task.args.link
    if source:
        dl = await asyncio.to_thread(api().add_torrent, str(source), options=opts)
    elif link.startswith("magnet:"):
        dl = await asyncio.to_thread(api().add_magnet, link, options=opts)
    else:
        dl = await asyncio.to_thread(api().add_uris, [link], options=opts)
    gid, finished, informed, selected = dl.gid, False, False, not task.args.select
    stalled = time.monotonic()
    task.set_status(Status.DOWNLOAD)
    try:
        while True:
            await asyncio.sleep(2)
            dl = await call("get_download", gid)
            if dl.followed_by_ids:
                gid = dl.followed_by_ids[0]
                continue
            if dl.has_failed:
                raise TaskError(dl.error_message or "aria2 download failed")
            if dl.is_removed:
                raise TaskError("Removed from aria2")
            meta = dl.is_metadata
            task.extra = f"S:{dl.num_seeders} C:{dl.connections}" if dl.bittorrent else f"C:{dl.connections}"
            if not meta and dl.total_length and not informed:
                informed = True
                await task.meta(dl.name, dl.total_length)
            if not meta and not selected and dl.files:
                selected = True
                await _select(task, dl)
                await call("resume", [dl])
            task.progress(dl.completed_length, dl.total_length, float(dl.download_speed))
            if dl.download_speed:
                stalled = time.monotonic()
            if Config.TORRENT_TIMEOUT and dl.bittorrent and time.monotonic() - stalled > Config.TORRENT_TIMEOUT:
                raise TaskError("Dead torrent: no progress within TORRENT_TIMEOUT")
            if dl.is_complete or (dl.bittorrent and dl.seeder):
                break
        finished = True
        task.name = dl.name
        if task.args.seed and dl.bittorrent and dl.is_active:
            task.seeder = lambda: _seed(task, gid)
        else:
            await _remove(gid, files=False)
        return single_root(task.dir)
    finally:
        if not finished:
            await _remove(gid)


async def _select(task: Task, dl: aria2p.Download) -> None:
    await call("pause", [dl])
    files = [(str(f.index), str(f.path).removeprefix(str(task.dir)).lstrip("/"), f.length) for f in dl.files]
    chosen = await select_files(task.message, task.user_id, files)
    if chosen is None:
        raise TaskError("File selection cancelled")
    await asyncio.to_thread(api().client.change_option, dl.gid, {"select-file": ",".join(sorted(chosen, key=int))})


async def _seed(task: Task, gid: str) -> None:
    task.set_status(Status.SEED)
    try:
        while True:
            dl = await call("get_download", gid)
            if not dl.is_active:
                break
            task.progress(dl.upload_length, dl.total_length, float(dl.upload_speed))
            task.extra = f"Ratio {dl.upload_length / max(1, dl.total_length):.2f}"
            await asyncio.sleep(5)
    finally:
        await _remove(gid, files=False)
