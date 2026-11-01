"""rclone: download/upload/server-side clone with JSON stats, remote browser, SA rotation, serve."""

import asyncio
import configparser
import json
import random
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from Mirror.config import Config
from Mirror.core.app import app
from Mirror.core.task import Status, Task, TaskError, Uploaded
from Mirror.core.ui import choose, select_files
from Mirror.core.utils import check_output, run

_serve: asyncio.subprocess.Process | None = None


def config_for(path: str, prefs: Any) -> tuple[Path, str]:
    """``mrcc:remote:path`` uses the user's rclone.conf; otherwise the owner config."""
    user_conf = prefs.file("rclone") if prefs else None
    if path.startswith("mrcc:") or (user_conf and prefs and prefs.values.get("user_creds") and path):
        if not user_conf:
            raise TaskError("Upload your rclone.conf in /usettings first")
        return user_conf, path.removeprefix("mrcc:")
    conf = app.data / "rclone.conf"
    if not conf.exists():
        raise TaskError("rclone.conf missing: add it in /bsetting → private files")
    return conf, path


def parse_flags(raw: str) -> list[str]:
    flags: list[str] = []
    for item in filter(None, (f.strip() for f in raw.split("|"))):
        key, sep, value = item.partition(":")
        flags += [key, value] if sep and key.startswith("--") else [item]
    return flags


def remote_type(conf: Path, remote: str) -> str:
    parser = configparser.ConfigParser()
    parser.read(conf)
    return parser.get(remote, "type", fallback="") if parser.has_section(remote) else ""


def extra_flags(conf: Path, remote: str, prefs: Any, task_flags: str = "") -> list[str]:
    flags = parse_flags(prefs.rclone_flags if prefs else Config.RCLONE_FLAGS) + parse_flags(task_flags)
    sa = sorted((app.data / "accounts").glob("*.json"))
    if Config.USE_SERVICE_ACCOUNTS and sa and remote_type(conf, remote) == "drive" and conf.parent == app.data:
        flags += ["--drive-service-account-file", str(random.choice(sa))]
    return flags


async def transfer(task: Task, src: str, dst: str, conf: Path, flags: list[str], verb: str = "copy") -> None:
    def on_line(line: str) -> None:
        try:
            stats = json.loads(line).get("stats")
        except (ValueError, AttributeError):
            return
        if stats:
            task.progress(int(stats.get("bytes", 0)), int(stats.get("totalBytes", 0)) or None,
                          float(stats.get("speed", 0) or 0))
            task.extra = f"{stats.get('transfers', 0)}/{stats.get('totalTransfers', 0)} files"

    cmd = ["rclone", verb, src, dst, "--config", str(conf), "--use-json-log", "--stats", "2s",
           "--log-level", "NOTICE", "--stats-log-level", "NOTICE", "--transfers", "8", "--checkers", "16",
           "--retries", "3", "--low-level-retries", "10", "--drive-acknowledge-abuse", *flags]
    code, out = await run(*cmd, on_line=on_line)
    if code:
        errors = [json.loads(x).get("msg", x) if x.startswith("{") else x for x in out.splitlines()[-5:]]
        raise TaskError("rclone: " + " | ".join(errors)[-700:])


async def size_of(path: str, conf: Path) -> tuple[int, int]:
    data = json.loads(await check_output("rclone", "size", path, "--json", "--config", str(conf)))
    return int(data.get("bytes", 0)), int(data.get("count", 0))


async def list_dir(path: str, conf: Path, dirs_only: bool = False) -> list[dict[str, Any]]:
    args = ["rclone", "lsjson", path, "--config", str(conf), "--no-mimetype", "--no-modtime"]
    if dirs_only:
        args.append("--dirs-only")
    else:
        args += ["-R", "--files-only"]
    return list(json.loads(await check_output(*args) or "[]"))


async def remotes(conf: Path) -> list[str]:
    return [r for r in (await check_output("rclone", "listremotes", "--config", str(conf))).split() if r]


async def link_for(path: str, conf: Path, is_dir: bool) -> str:
    if Config.RCLONE_SERVE_URL and conf.parent == app.data:
        remote, _, rest = path.partition(":")
        return f"{Config.RCLONE_SERVE_URL.rstrip('/')}/{quote(remote)}/{quote(rest)}{'/' if is_dir else ''}"
    code, out = await run("rclone", "link", path, "--config", str(conf))
    return out.strip().splitlines()[-1] if code == 0 and out.strip().startswith("http") else ""


def join(path: str, name: str) -> str:
    return f"{path}{'' if path.endswith((':', '/')) else '/'}{name}"


def parent(path: str) -> str:
    remote, _, rest = path.partition(":")
    rest = rest.rstrip("/")
    return f"{remote}:{rest.rsplit('/', 1)[0] if '/' in rest else ''}"


async def stat(path: str, conf: Path) -> dict[str, Any]:
    return dict(json.loads(await check_output("rclone", "lsjson", "--stat", path, "--config", str(conf))))


async def browse(task: Task, conf: Path) -> str:
    """Interactive remote/path picker built from buttons."""
    names = await remotes(conf)
    if not names:
        raise TaskError("No remotes in rclone.conf")
    path = names[0] if len(names) == 1 or not task.message else await choose(
        task.message, task.user_id, "Choose rclone remote", [(r, r) for r in names])
    while path and task.message:
        dirs = await list_dir(path, conf, dirs_only=True)
        picks = [("✔ Here", "."), ("⬆ Up", "..")] + [(f"📁 {d['Name']}", d["Name"]) for d in dirs[:40]]
        picked = await choose(task.message, task.user_id, f"<code>{path}</code>", picks)
        if picked is None or picked == ".":
            break
        path = parent(path) if picked == ".." else join(path, picked)
    if not path:
        raise TaskError("Path selection cancelled")
    return path


async def upload(task: Task, path: Path, dest: str) -> Uploaded:
    conf, base = config_for(dest or task.prefs.rclone_path or "", task.prefs)
    if not base or base == "rc":
        base = await browse(task, conf)
    dst = join(base, path.name)
    flags = extra_flags(conf, base.split(":", 1)[0], task.prefs, task.args.rcf)
    await transfer(task, str(path), dst if path.is_dir() else base, conf, flags)
    files = 1 if path.is_file() else sum(1 for p in path.rglob("*") if p.is_file())
    return Uploaded("Rclone", await link_for(dst, conf, path.is_dir()), dst, "", files)


async def download(task: Task) -> Path:
    conf, src = config_for(task.args.link, task.prefs)
    info = await stat(src, conf)
    is_dir = bool(info.get("IsDir"))
    size = (await size_of(src, conf))[0] if is_dir else int(info.get("Size", 0))
    name = task.args.name or str(info.get("Name") or src.split(":", 1)[0])
    await task.meta(name, size)
    flags = extra_flags(conf, src.split(":", 1)[0], task.prefs, task.args.rcf)
    if task.args.select and is_dir:
        items = [i for i in await list_dir(src, conf) if not i.get("IsDir")]
        chosen = await select_files(task.message, task.user_id,
                                    [(str(n), i["Path"], int(i.get("Size", 0))) for n, i in enumerate(items)])
        if chosen is None:
            raise TaskError("File selection cancelled")
        for n, item in enumerate(items):
            if str(n) in chosen:
                flags += ["--include", "/" + re.sub(r"([\\[\]*?{}])", r"\\1", item["Path"])]
    task.set_status(Status.DOWNLOAD, size)
    target = task.dir / name
    await transfer(task, src, str(target), conf, flags, "copy" if is_dir else "copyto")
    return target


async def clone(task: Task, dest: str) -> Uploaded:
    conf, src = config_for(task.args.link, task.prefs)
    _, base = config_for(dest or task.prefs.rclone_path, task.prefs)
    base = base or await browse(task, conf)
    size, count = await size_of(src, conf)
    name = str((await stat(src, conf)).get("Name") or "clone")
    await task.meta(name, size)
    task.set_status(Status.CLONE, size)
    dst = join(base, name)
    flags = ["--server-side-across-configs", *extra_flags(conf, base.split(":", 1)[0], task.prefs, task.args.rcf)]
    await transfer(task, src, dst, conf, flags)
    return Uploaded("Rclone", await link_for(dst, conf, True), dst, "", count)


async def serve() -> None:
    """Expose every owner remote through one ``combine`` remote over HTTP (RCLONE_SERVE_PORT)."""
    global _serve
    conf = app.data / "rclone.conf"
    if not Config.RCLONE_SERVE_PORT or not conf.exists():
        return
    names = [r.rstrip(":") for r in await remotes(conf)]
    combined = app.data / "rclone-serve.conf"
    combined.write_text(conf.read_text() + "\n[combine]\ntype = combine\nupstreams = "
                        + " ".join(f"{n}={n}:" for n in names) + "\n")
    args = ["rclone", "serve", "http", "combine:", "--config", str(combined), "--addr",
            f":{Config.RCLONE_SERVE_PORT}", "--read-only", "--no-modtime"]
    if Config.RCLONE_SERVE_USER and Config.RCLONE_SERVE_PASS:
        args += ["--user", Config.RCLONE_SERVE_USER, "--pass", Config.RCLONE_SERVE_PASS]
    if _serve and _serve.returncode is None:
        _serve.kill()
    _serve = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                  stderr=asyncio.subprocess.DEVNULL)
