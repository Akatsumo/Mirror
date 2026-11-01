"""Mega.nz links through megatools' ``megadl`` (files and folders, public links)."""

import re
from pathlib import Path

from Mirror.core.files import single_root
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.utils import parse_size, run

_PROGRESS = re.compile(r"([\d.]+)%\s*-\s*[\d.]+\s*\w+\s*\((\d+)\s*bytes\)\s*of\s*([\d.]+\s*\w+)"
                       r"(?:\s*\(([\d.]+\s*\w+)/s\))?", re.I)


def parse_progress(line: str) -> tuple[int, int, float] | None:
    match = _PROGRESS.search(line)
    if not match:
        return None
    _, done, total, speed = match.groups()
    return int(done), parse_size(total), float(parse_size(speed)) if speed else 0.0


async def download(task: Task) -> Path:
    task.dir.mkdir(parents=True, exist_ok=True)
    await task.meta(task.args.name or "mega", 0)
    task.set_status(Status.DOWNLOAD)

    def on_line(line: str) -> None:
        if parsed := parse_progress(line):
            task.progress(parsed[0], parsed[1], parsed[2])

    code, out = await run("megadl", "--path", str(task.dir), "--print-names", task.args.link, on_line=on_line)
    if code:
        raise TaskError(out[-600:] or "megadl failed")
    return single_root(task.dir)
