"""gallery-dl engine (subprocess) with cookies, config file and per-user/task options."""

import asyncio
import json
import sys
from pathlib import Path

from Mirror.config import Config
from Mirror.core.files import single_root
from Mirror.core.prefs import private
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.utils import dir_size, parse_opts, run


def command(task: Task) -> list[str]:
    folder = task.dir / (task.args.name or "gallery")
    cmd = [sys.executable, "-m", "gallery_dl", "-D", str(folder), "--no-part"]
    if cookies := (task.prefs.file("cookies") if task.prefs else None) or private("cookies.txt"):
        cmd += ["--cookies", str(cookies)]
    if conf := private("gallery-dl.conf"):
        cmd += ["-c", str(conf)]
    options = {**(task.prefs.gallery_opts if task.prefs else Config.GALLERY_DL_OPTIONS), **parse_opts(task.args.opts)}
    for key, value in options.items():
        cmd += ["-o", f"{key}={json.dumps(value) if not isinstance(value, str) else value}"]
    return [*cmd, task.args.link]


async def download(task: Task) -> Path:
    task.dir.mkdir(parents=True, exist_ok=True)
    await task.meta(task.args.name or "gallery", 0)
    task.set_status(Status.DOWNLOAD)
    count = 0

    def on_line(line: str) -> None:
        nonlocal count
        if line.startswith(str(task.dir)):
            count += 1
            task.extra = f"{count} files"

    async def meter() -> None:
        while True:
            await asyncio.sleep(2)
            task.progress(await asyncio.to_thread(dir_size, task.dir))

    job = asyncio.create_task(meter())
    try:
        code, out = await run(*command(task), on_line=on_line)
    finally:
        job.cancel()
    if code and not count:
        raise TaskError(out[-800:] or "gallery-dl failed")
    task.progress(dir_size(task.dir))
    return single_root(task.dir)
