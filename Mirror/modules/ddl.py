"""Direct-download hosts: Gofile and Streamtape, uploaded concurrently to every chosen site."""

import asyncio
import io
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp

from Mirror.core.task import Status, Task, TaskError, Uploaded
from Mirror.core.utils import files_in

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".3gp"}
TIMEOUT = aiohttp.ClientTimeout(total=None, connect=30, sock_read=600)


class Meter(io.BufferedReader):
    """File reader that reports every chunk aiohttp pulls (keeps Content-Length known)."""

    def __init__(self, path: Path, tick: Callable[[int], None]) -> None:
        super().__init__(io.FileIO(path, "rb"), buffer_size=1 << 20)
        self._tick = tick

    def read(self, size: int | None = -1) -> bytes:
        data = super().read(size)
        self._tick(len(data))
        return data


async def _json(response: aiohttp.ClientResponse) -> Any:
    data = await response.json(content_type=None)
    if response.status >= 400:
        raise TaskError(f"HTTP {response.status}: {data}")
    return data


async def gofile(session: aiohttp.ClientSession, path: Path, token: str, tick: Callable[[int], None]) -> Uploaded:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    root: dict[str, str] = {}
    folders: dict[Path, str] = {}
    first_link = ""

    async def folder_for(file: Path) -> str:
        parent = file.parent
        if path.is_file() or parent == path or not root:
            return root.get("id", "")
        if parent not in folders:
            base = await folder_for(parent)
            async with session.post("https://api.gofile.io/contents/createFolder", headers=headers,
                                    json={"parentFolderId": base, "folderName": parent.name}) as response:
                folders[parent] = (await _json(response))["data"]["id"]
        return folders[parent]

    for file in files_in(path):
        form = aiohttp.FormData()
        if folder := await folder_for(file):
            form.add_field("folderId", folder)
        with Meter(file, tick) as reader:
            form.add_field("file", reader, filename=file.name)
            async with session.post("https://upload.gofile.io/uploadfile", data=form, headers=headers) as response:
                data = (await _json(response)).get("data", {})
        if not root:
            root = {"id": data.get("parentFolder", ""), "page": data.get("downloadPage", "")}
            token = token or data.get("guestToken", "")
            headers = {"Authorization": f"Bearer {token}"} if token else {}
        first_link = first_link or data.get("downloadPage", "")
    link = root.get("page") if path.is_file() else f"https://gofile.io/d/{root.get('id', '')}"
    return Uploaded("Gofile", link or first_link, files=len(files_in(path)))


async def streamtape(session: aiohttp.ClientSession, path: Path, login: str, key: str,
                     tick: Callable[[int], None]) -> Uploaded:
    if not (login and key):
        raise TaskError("Streamtape needs login and key (usettings or config)")
    items: list[tuple[str, str]] = []
    for file in (f for f in files_in(path) if f.suffix.lower() in VIDEO_EXT):
        async with session.get("https://api.streamtape.com/file/ul", params={"login": login, "key": key}) as response:
            target = (await _json(response))["result"]["url"]
        form = aiohttp.FormData()
        with Meter(file, tick) as reader:
            form.add_field("file1", reader, filename=file.name)
            async with session.post(target, data=form) as response:
                items.append((file.name, (await _json(response))["result"]["url"]))
    if not items:
        raise TaskError("Streamtape accepts video files only")
    return Uploaded("Streamtape", items[0][1], files=len(items), items=items)


async def upload(task: Task, path: Path, sites: list[str]) -> list[Uploaded]:
    prefs = task.prefs
    total = sum(f.stat().st_size for f in files_in(path)) * max(1, len(sites))
    task.set_status(Status.UPLOAD, total)

    def tick(amount: int) -> None:
        task.progress(task.done + amount)

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        jobs: dict[str, Callable[[], Awaitable[Uploaded]]] = {
            "gofile": lambda: gofile(session, path, prefs.gofile_token, tick),
            "streamtape": lambda: streamtape(session, path, prefs.streamtape_login, prefs.streamtape_key, tick),
        }
        unknown = [s for s in sites if s not in jobs]
        if unknown:
            raise TaskError(f"Unknown DDL site(s): {', '.join(unknown)} (supported: {', '.join(jobs)})")
        results = await asyncio.gather(*(jobs[s]() for s in sites), return_exceptions=True)
    done = [r for r in results if isinstance(r, Uploaded)]
    if not done:
        raise TaskError("; ".join(str(r) for r in results))
    return done
