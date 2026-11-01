"""SABnzbd usenet engine over the HTTP API (shared filesystem with the bot)."""

import asyncio
import contextlib
import shutil
from pathlib import Path
from typing import Any

import aiohttp

from Mirror.config import Config
from Mirror.core.files import single_root
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.ui import select_files

MB = 1024**2


async def api(mode: str, upload: Path | None = None, **params: Any) -> Any:
    if not Config.SABNZBD_API_KEY:
        raise TaskError("SABnzbd is not configured (SABNZBD_API_KEY)")
    query = {"mode": mode, "apikey": Config.SABNZBD_API_KEY, "output": "json", **params}
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if upload:
            form = aiohttp.FormData()
            form.add_field("name", upload.read_bytes(), filename=upload.name)
            request = session.post(f"{Config.SABNZBD_HOST}/api", params=query, data=form)
        else:
            request = session.get(f"{Config.SABNZBD_HOST}/api", params=query)
        async with request as response:
            data = await response.json(content_type=None)
    if isinstance(data, dict) and data.get("status") is False:
        raise TaskError(f"SABnzbd: {data.get('error', 'request failed')}")
    return data


async def global_options() -> dict[str, str]:
    misc = (await api("get_config", section="misc"))["config"]["misc"]
    return {k: str(v) for k, v in misc.items() if not isinstance(v, (list, dict))}


async def set_global_option(key: str, value: str) -> None:
    await api("set_config", section="misc", keyword=key, value=value)


async def servers() -> list[dict[str, Any]]:
    return list((await api("get_config", section="servers"))["config"].get("servers", []))


async def save_server(fields: dict[str, str]) -> None:
    if "name" not in fields and "host" in fields:
        fields["name"] = fields["host"]
    await api("set_config", section="servers", keyword=fields["name"], **fields)


async def delete_server(name: str) -> None:
    await api("del_config", section="servers", keyword=name)


async def _status(nzo: str) -> tuple[str, dict[str, Any]]:
    queue = (await api("queue", nzo_ids=nzo))["queue"]
    if queue.get("slots"):
        return "queue", {**queue["slots"][0], "kbpersec": queue.get("kbpersec", "0")}
    history = (await api("history", nzo_ids=nzo))["history"].get("slots", [])
    return ("history", history[0]) if history else ("missing", {})


async def download(task: Task, source: Path | None = None) -> Path:
    task.dir.mkdir(parents=True, exist_ok=True)
    params = {"nzbname": task.id, "priority": "-2" if task.args.select else "0"}
    added = await (api("addfile", upload=source, **params) if source else api("addurl", name=task.args.link, **params))
    if not added.get("nzo_ids"):
        raise TaskError("SABnzbd did not accept the NZB")
    nzo, finished, informed = added["nzo_ids"][0], False, False
    task.set_status(Status.DOWNLOAD)
    try:
        while True:
            await asyncio.sleep(3)
            where, slot = await _status(nzo)
            if where == "missing":
                raise TaskError("NZB removed from SABnzbd")
            if where == "history":
                if slot.get("status") == "Failed":
                    raise TaskError(slot.get("fail_message") or "SABnzbd job failed")
                if slot.get("status") == "Completed":
                    storage = Path(slot["storage"])
                    target = task.dir / storage.name
                    await asyncio.to_thread(shutil.move, str(storage), str(target))
                    finished = True
                    await api("history", name="delete", value=nzo)
                    return single_root(task.dir)
                task.extra = str(slot.get("status", ""))
                continue
            total, left = float(slot.get("mb", 0)) * MB, float(slot.get("mbleft", 0)) * MB
            task.progress(int(total - left), int(total) or None, float(slot.get("kbpersec") or 0) * 1024)
            task.extra = str(slot.get("status", ""))
            if not informed and total:
                informed = True
                await task.meta(str(slot.get("filename", task.id)), int(total))
                if task.args.select:
                    await _select(task, nzo)
                    await api("queue", name="resume", value=nzo)
    finally:
        if not finished:
            with contextlib.suppress(Exception):
                await api("queue", name="delete", value=nzo, del_files=1)
            with contextlib.suppress(Exception):
                await api("history", name="delete", value=nzo, del_files=1)


async def _select(task: Task, nzo: str) -> None:
    files = (await api("get_files", value=nzo)).get("files", [])
    entries = [(str(i), f["filename"], int(float(f.get("mb", 0)) * MB)) for i, f in enumerate(files)]
    chosen = await select_files(task.message, task.user_id, entries)
    if chosen is None:
        raise TaskError("File selection cancelled")
    for index, item in enumerate(files):
        if str(index) not in chosen:
            await api("queue", name="delete_nzf", value=nzo, value2=item["nzf_id"])
