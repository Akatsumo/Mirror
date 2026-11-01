"""JDownloader engine via My.JDownloader (myjdapi); JD must share the download directory."""

import asyncio
import base64
import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import myjdapi

from Mirror.config import Config
from Mirror.core.files import single_root
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.ui import select_files

_device: Any = None
_lock = asyncio.Lock()


async def device() -> Any:
    global _device
    async with _lock:
        if _device is None:
            if not (Config.JD_EMAIL and Config.JD_PASS):
                raise TaskError("JDownloader is not configured (JD_EMAIL/JD_PASS)")

            def connect() -> Any:
                jd = myjdapi.Myjdapi()
                jd.set_app_key("mirror")
                jd.connect(Config.JD_EMAIL, Config.JD_PASS)
                jd.update_devices()
                return jd.get_device(Config.JD_DEVICE)
            try:
                _device = await asyncio.to_thread(connect)
            except myjdapi.MYJDException as exc:
                raise TaskError(f"JDownloader: {exc}") from exc
        return _device


async def jd(fn: Callable[[Any], Any]) -> Any:
    global _device
    dev = await device()
    try:
        return await asyncio.to_thread(fn, dev)
    except myjdapi.MYJDException as exc:
        _device = None
        raise TaskError(f"JDownloader: {exc}") from exc


async def download(task: Task, source: Path | None = None) -> Path:
    task.dir.mkdir(parents=True, exist_ok=True)
    folder = str(task.dir)
    before = {p["uuid"] for p in await jd(lambda d: d.linkgrabber.query_packages())}
    if source:
        content = f"data:application/dlc;base64,{base64.b64encode(source.read_bytes()).decode()}"
        await jd(lambda d: d.linkgrabber.add_container("DLC", content))
    else:
        await jd(lambda d: d.linkgrabber.add_links([{"autostart": False, "links": "\n".join(task.args.links),
                                                     "packageName": task.args.name or task.id,
                                                     "destinationFolder": folder, "overwritePackagizerRules": True}]))
    task.set_status(Status.WAIT)
    packages: list[dict[str, Any]] = []
    for _ in range(120):
        await asyncio.sleep(2)
        if not await jd(lambda d: d.linkgrabber.is_collecting()):
            packages = [p for p in await jd(lambda d: d.linkgrabber.query_packages()) if p["uuid"] not in before]
            if packages:
                break
    if not packages:
        raise TaskError("JDownloader found nothing to download")
    ids = [p["uuid"] for p in packages]
    moved = False
    try:
        await jd(lambda d: d.action("/linkgrabberv2/setDownloadDirectory", [folder, ids]))
        links = await jd(lambda d: d.linkgrabber.query_links([{"packageUUIDs": ids, "bytesTotal": True,
                                                                "availability": True}]))
        online = [link for link in links if link.get("availability") != "OFFLINE"]
        if not online:
            raise TaskError("All links are offline")
        if task.args.select:
            entries = [(str(i), x["name"], int(x.get("bytesTotal", 0))) for i, x in enumerate(online)]
            chosen = await select_files(task.message, task.user_id, entries)
            if chosen is None:
                raise TaskError("File selection cancelled")
            online = [x for i, x in enumerate(online) if str(i) in chosen]
        await task.meta(packages[0].get("name", task.id), sum(int(x.get("bytesTotal", 0)) for x in online))
        link_ids = [x["uuid"] for x in online]
        await jd(lambda d: d.linkgrabber.move_to_downloadlist(link_ids, ids))
        moved = True
        task.set_status(Status.DOWNLOAD)
        await _follow(task, ids)
        await jd(lambda d: d.downloads.remove_links([], ids))
        return single_root(task.dir)
    finally:
        with contextlib.suppress(Exception):
            if moved:
                await jd(lambda d: d.downloads.remove_links([], ids))
            await jd(lambda d: d.linkgrabber.remove_links([], ids))


async def _follow(task: Task, ids: list[str]) -> None:
    query = [{"packageUUIDs": ids, "bytesLoaded": True, "bytesTotal": True, "speed": True, "finished": True,
              "status": True}]
    while True:
        await asyncio.sleep(3)
        packages = await jd(lambda d: d.downloads.query_packages(query))
        if not packages:
            raise TaskError("Package removed from JDownloader")
        loaded = sum(int(p.get("bytesLoaded", 0)) for p in packages)
        total = sum(int(p.get("bytesTotal", 0)) for p in packages)
        task.progress(loaded, total or None, float(sum(int(p.get("speed", 0)) for p in packages)))
        task.extra = str(packages[0].get("status", ""))
        if all(p.get("finished") for p in packages):
            return
