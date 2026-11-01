"""Direct HTTP(S) downloads: per-link auth/headers, .netrc, and parallel ranged segments."""

import asyncio
import base64
import netrc
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp

from Mirror.core.prefs import private
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.utils import safe_name

CHUNK = 1 << 20
SEGMENTS = 8
PARALLEL_MIN = 64 * 1024**2
TIMEOUT = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)


def headers_of(raw: str) -> dict[str, str]:
    pairs = (h.partition(":") for h in raw.split("|") if ":" in h)
    return {k.strip(): v.strip() for k, _, v in pairs if k.strip()}


def basic(user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def auth_for(url: str, user: str, password: str) -> dict[str, str]:
    """Authorization header from ``-au/-ap`` or the matching ``.netrc`` entry."""
    if user or password:
        return basic(user, password)
    path = private(".netrc")
    host = urlparse(url).hostname or ""
    if path and host:
        try:
            entry = netrc.netrc(str(path)).authenticators(host)
        except (OSError, netrc.NetrcParseError):
            entry = None
        if entry:
            return basic(entry[0], entry[2] or "")
    return {}


def filename_of(response: aiohttp.ClientResponse, fallback: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)|filename=\"?([^\";]+)", disposition, re.I)
    if match:
        return safe_name(unquote((match.group(1) or match.group(2)).strip()))
    return safe_name(unquote(Path(urlparse(str(response.url)).path).name) or fallback)


def segments(size: int, count: int = SEGMENTS) -> list[tuple[int, int]]:
    step = -(-size // count)
    return [(start, min(size, start + step) - 1) for start in range(0, size, step)]


async def _segment(session: aiohttp.ClientSession, url: str, fd: int, span: tuple[int, int], tick: list[int],
                   task: Task) -> None:
    start, end = span
    for attempt in range(5):
        try:
            async with session.get(url, headers={"Range": f"bytes={start}-{end}"}) as response:
                if response.status != 206:
                    raise TaskError(f"Range request refused ({response.status})")
                async for chunk in response.content.iter_chunked(CHUNK):
                    os.pwrite(fd, chunk, start)
                    start += len(chunk)
                    tick[0] += len(chunk)
                    task.progress(tick[0])
            if start > end:
                return
        except (TimeoutError, aiohttp.ClientError):
            await asyncio.sleep(2 ** attempt)
    raise TaskError("Segment failed after retries")


async def download(task: Task, url: str | None = None, folder: Path | None = None,
                   headers: dict[str, str] | None = None, name: str = "") -> Path:
    url = url or task.args.link
    folder = folder or task.dir
    folder.mkdir(parents=True, exist_ok=True)
    merged = {**auth_for(url, task.args.auth_user, task.args.auth_pass), **headers_of(task.args.headers),
              **(headers or {})}
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=merged) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status >= 400:
                raise TaskError(f"HTTP {response.status} {response.reason}")
            if "text/html" in response.headers.get("Content-Type", "") and not name:
                raise TaskError("Link returned a web page, not a file (unsupported site or auth needed)")
            name = safe_name(name) if name else filename_of(response, "download.bin")
            size = int(response.headers.get("Content-Length") or 0)
            final = str(response.url)
            if url == task.args.link:
                await task.meta(name, size)
            task.set_status(Status.DOWNLOAD, size or None)
            target = folder / name
            ranged = response.headers.get("Accept-Ranges", "").lower() == "bytes" and size >= PARALLEL_MIN
            if not ranged:
                done = 0
                with target.open("wb") as output:
                    async for chunk in response.content.iter_chunked(CHUNK):
                        output.write(chunk)
                        done += len(chunk)
                        task.progress(done)
                if size and done < size:
                    raise TaskError(f"Connection closed early ({done}/{size} bytes)")
                return target
        fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.ftruncate(fd, size)
            tick = [0]
            await asyncio.gather(*(_segment(session, final, fd, span, tick, task) for span in segments(size)))
        finally:
            os.close(fd)
    return target
