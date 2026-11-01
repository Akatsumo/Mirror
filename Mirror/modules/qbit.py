"""qBittorrent engine: tag-tracked torrents, one shared poller, metadata/stall/dead handling, seeding."""

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any

import qbittorrentapi as qba

from Mirror.config import Config
from Mirror.core.files import clean, single_root
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.ui import select_files

log = logging.getLogger(__name__)
CATEGORY = "mirror"
TICK = 3.0
META = {"metaDL", "forcedMetaDL", "checkingResumeData"}
CHECKING = {"checkingDL", "checkingUP", "checkingResumeData", "allocating", "moving"}
STOPPED_UP = {"pausedUP", "stoppedUP"}
COMPLETE = STOPPED_UP | {"uploading", "stalledUP", "queuedUP", "forcedUP"}
_client: qba.Client | None = None
_lock = asyncio.Lock()


async def call(name: str, *args: Any, **kwargs: Any) -> Any:
    """Run a client method in a thread; reconnect and retry on transient/auth failures."""
    global _client
    for attempt in range(4):
        async with _lock:
            if _client is None:
                _client = await asyncio.to_thread(_connect)
            client = _client
        try:
            return await asyncio.to_thread(getattr(client, name), *args, **kwargs)
        except (qba.Forbidden403Error, qba.LoginFailed, qba.APIConnectionError) as exc:
            if attempt == 3:
                raise TaskError(f"qBittorrent unreachable: {exc}") from exc
            _client = None
            await asyncio.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def _connect() -> qba.Client:
    client = qba.Client(host=Config.QBIT_HOST, username=Config.QBIT_USER, password=Config.QBIT_PASS,
                        REQUESTS_ARGS={"timeout": (10, 60)}, VERIFY_WEBUI_CERTIFICATE=False)
    client.auth_log_in()
    with contextlib.suppress(qba.Conflict409Error):
        client.torrents_create_category(name=CATEGORY)
    client.app_set_preferences({"queueing_enabled": False})
    return client


class _Watcher:
    """Fetch every bot torrent once per tick and fan the snapshot out to waiting tasks."""

    def __init__(self) -> None:
        self.snapshot: dict[str, Any] = {}
        self.users = 0
        self._tick = asyncio.Event()
        self._job: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while self.users:
            try:
                self.snapshot = {t.hash: t for t in await call("torrents_info", category=CATEGORY)}
            except Exception:
                log.warning("qBittorrent poll failed", exc_info=True)
            self._tick.set()
            self._tick = asyncio.Event()
            await asyncio.sleep(TICK)
        self._job = None

    async def get(self, torrent_hash: str) -> Any:
        if self._job is None:
            self._job = asyncio.create_task(self._loop())
        await self._tick.wait()
        return self.snapshot.get(torrent_hash)


watcher = _Watcher()


async def global_options() -> dict[str, str]:
    return {k: str(v) for k, v in (await call("app_preferences")).items() if not isinstance(v, (dict, list))}


async def set_global_option(key: str, value: str) -> None:
    from Mirror.core.utils import parse_value

    await call("app_set_preferences", {key: parse_value(value)})


async def _add(task: Task, source: Path | None, tag: str) -> str:
    args = task.args
    params: dict[str, Any] = {
        "save_path": str(task.dir), "category": CATEGORY, "tags": tag, "use_auto_torrent_management": False,
        "is_paused": args.select, "is_stopped": args.select,
    }
    if args.select:
        params["stop_condition"] = "MetadataReceived"
    if args.seed:
        params["ratio_limit"] = args.ratio if args.ratio is not None else -2
        params["seeding_time_limit"] = args.seed_time // 60 if args.seed_time else -2
    if source:
        params["torrent_files"] = source.read_bytes()
    else:
        params["urls"] = args.link
    result = await call("torrents_add", **params)
    if str(result).strip().lower().startswith("fail"):
        raise TaskError("qBittorrent rejected the torrent (already added or invalid)")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if found := await call("torrents_info", tag=tag):
            return str(found[0].hash)
        await asyncio.sleep(1)
    raise TaskError("Torrent was not registered by qBittorrent within 60s")


async def _remove(torrent_hash: str, tag: str, files: bool = True) -> None:
    with contextlib.suppress(Exception):
        await call("torrents_delete", delete_files=files, torrent_hashes=torrent_hash)
    with contextlib.suppress(Exception):
        await call("torrents_delete_tags", tags=tag)


async def _select(task: Task, torrent_hash: str) -> None:
    files = await call("torrents_files", torrent_hash=torrent_hash)
    entries = [(str(f.index), f.name, int(f.size)) for f in files]
    task.status = Status.WAIT
    chosen = await select_files(task.message, task.user_id, entries)
    task.status = Status.DOWNLOAD
    if chosen is None:
        raise TaskError("File selection cancelled")
    if skip := [int(i) for i, _, _ in entries if i not in chosen]:
        await call("torrents_file_priority", torrent_hash=torrent_hash, file_ids=skip, priority=0)
        task.size = sum(s for i, _, s in entries if i in chosen)


async def _drop_unwanted(task: Task, torrent_hash: str) -> None:
    for item in await call("torrents_files", torrent_hash=torrent_hash):
        if item.priority == 0:
            clean(task.dir / item.name)
    for part in task.dir.rglob("*.!qB"):
        part.unlink(missing_ok=True)
    for folder in sorted((p for p in task.dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            folder.rmdir()


async def download(task: Task, source: Path | None = None) -> Path:
    task.dir.mkdir(parents=True, exist_ok=True)
    tag = f"mirror_{task.id}"
    torrent_hash = await _add(task, source, tag)
    task.set_status(Status.DOWNLOAD)
    watcher.users += 1
    finished = False
    try:
        info = await _follow(task, torrent_hash)
        if not task.args.seed:
            await call("torrents_stop", torrent_hashes=torrent_hash)
        await _drop_unwanted(task, torrent_hash)
        finished = True
        task.name = info.name
        if task.args.seed:
            task.seeder = lambda: _seed(task, torrent_hash, tag)
        else:
            await _remove(torrent_hash, tag, files=False)
        content = Path(info.content_path)
        return content if content.exists() and task.dir in content.parents else single_root(task.dir)
    finally:
        watcher.users -= 1
        if not finished:
            await _remove(torrent_hash, tag)


async def _follow(task: Task, torrent_hash: str) -> Any:
    started = last_move = last_announce = time.monotonic()
    best, missing, rechecked, ready = -1, 0, False, False
    while True:
        info = await watcher.get(torrent_hash)
        now = time.monotonic()
        if info is None:
            missing += 1
            if missing > 5:
                raise TaskError("Torrent disappeared from qBittorrent")
            continue
        missing, state = 0, info.state
        task.extra = f"S:{info.num_seeds} L:{info.num_leechs}"
        task.progress(int(info.completed), int(info.size) or None, float(info.dlspeed))
        if info.completed > best:
            best, last_move = info.completed, now
        if state == "error":
            raise TaskError("qBittorrent error (check free space and permissions)")
        if state == "missingFiles":
            await call("torrents_recheck", torrent_hashes=torrent_hash)
        if state in META:
            task.name = f"[METADATA] {info.name}"
            if Config.TORRENT_TIMEOUT and now - started > Config.TORRENT_TIMEOUT:
                raise TaskError("Dead torrent: metadata not received")
        elif not ready and info.size:
            ready = True
            await task.meta(info.name, int(info.size))
            if task.args.select:
                await _select(task, torrent_hash)
                await call("torrents_start", torrent_hashes=torrent_hash)
                last_move = time.monotonic()
                continue
        if state == "stalledDL" and info.progress >= 0.9999 and not rechecked:
            rechecked = True
            await call("torrents_recheck", torrent_hashes=torrent_hash)
        elif Config.TORRENT_TIMEOUT and state not in CHECKING and now - last_move > Config.TORRENT_TIMEOUT:
            raise TaskError("Dead torrent: no progress within TORRENT_TIMEOUT")
        if state in META | {"stalledDL"} and now - last_announce > 60:
            last_announce = now
            await call("torrents_reannounce", torrent_hashes=torrent_hash)
        if ready and info.progress >= 1 and state in COMPLETE:
            return info


async def _seed(task: Task, torrent_hash: str, tag: str) -> None:
    task.set_status(Status.SEED)
    watcher.users += 1
    try:
        while (info := await watcher.get(torrent_hash)) is not None and info.state not in STOPPED_UP:
            task.progress(int(info.uploaded), int(info.size), float(info.upspeed))
            task.extra = f"Ratio {info.ratio:.2f} · {int(info.seeding_time) // 60}m"
    finally:
        watcher.users -= 1
        await _remove(torrent_hash, tag)
