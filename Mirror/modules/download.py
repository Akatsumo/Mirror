import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp
from pyrogram import Client, types

from Mirror.config import Config
from Mirror.core.task import Task


class Downloader:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    async def direct(self, url: str, task: Task, filename: str | None = None) -> Path:
        timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=120)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
            url, allow_redirects=True
        ) as response:
            response.raise_for_status()
            name = filename or unquote(Path(urlparse(str(response.url)).path).name) or "download.bin"
            path = self.directory / Path(name).name
            task.total = int(response.headers.get("Content-Length", "0"))
            task.current = 0
            with path.open("wb") as output:
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    output.write(chunk)
                    task.current += len(chunk)
            if not task.total:
                task.total = task.current
            return path

    async def ytdlp(self, url: str, task: Task) -> Path:
        before = set(self.directory.iterdir())
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "yt_dlp",
            "--newline",
            "--restrict-filenames",
            "-o",
            str(self.directory / "%(title).180B.%(ext)s"),
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output_lines: list[bytes] = []
        assert process.stdout is not None
        async for line in process.stdout:
            output_lines.append(line)
            output_lines = output_lines[-30:]
            match = re.search(rb"(\d+(?:\.\d+)?)% of (?:~)?([\d.]+)(KiB|MiB|GiB|KB|MB|GB)", line)
            if match:
                units = {b"KiB": 1024, b"MiB": 1024**2, b"GiB": 1024**3, b"KB": 1000, b"MB": 1000**2, b"GB": 1000**3}
                task.total = int(float(match.group(2)) * units[match.group(3)])
                task.current = int(task.total * float(match.group(1)) / 100)
        await process.wait()
        if process.returncode:
            raise RuntimeError(b"".join(output_lines).decode(errors="replace")[-1500:] or "yt-dlp failed")
        created = [path for path in self.directory.iterdir() if path not in before and path.is_file()]
        candidates = [path for path in created if not path.suffix.endswith((".part", ".ytdl"))]
        if not candidates:
            raise RuntimeError("yt-dlp completed without a downloaded file")
        path = max(candidates, key=lambda item: item.stat().st_mtime)
        task.current = task.total = path.stat().st_size
        return path

    async def aria2(self, url: str, task: Task, filename: str | None = None) -> Path:
        try:
            import aria2p
        except ImportError as exc:
            raise RuntimeError("aria2p is not installed") from exc
        rpc = urlparse(Config.ARIA2_RPC_URL)
        if not rpc.hostname:
            raise ValueError("ARIA2_RPC_URL must contain a hostname")
        api = aria2p.API(
            aria2p.Client(
                host=f"{rpc.scheme}://{rpc.hostname}",
                port=rpc.port or (443 if rpc.scheme == "https" else 6800),
                secret=Config.ARIA2_SECRET,
            )
        )
        options = {"dir": str(self.directory)}
        if filename:
            options["out"] = Path(filename).name
        download = await asyncio.to_thread(api.add_uris, [url], options=options)
        while not download.is_complete and not download.is_removed:
            await asyncio.sleep(1)
            download = await asyncio.to_thread(api.get_download, download.gid)
            task.current = download.completed_length
            task.total = download.total_length
            if download.has_failed:
                raise RuntimeError(download.error_message or "aria2 download failed")
        if download.is_removed:
            raise RuntimeError("aria2 download was stopped")
        return Path(download.files[0].path)

    async def telegram(self, client: Client, message: types.Message, task: Task) -> Path:
        media = message.document or message.video or message.audio
        if media is None:
            raise ValueError("Reply to a document, video, or audio file")
        task.total = int(media.file_size or 0)
        target = self.directory / Path(media.file_name or f"telegram-{media.file_id}").name

        async def update_progress(current: int, total: int, active_task: Task) -> None:
            active_task.current = current
            active_task.total = total

        result = await client.download_media(
            message,
            file_name=str(target),
            progress=update_progress,
            progress_args=(task,),
        )
        if not result:
            raise RuntimeError("Telegram download failed")
        path = Path(result)
        task.current = path.stat().st_size
        return path
