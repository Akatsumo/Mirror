import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pyrogram import Client, enums, filters
from pyrogram.filters import Filter
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.handlers.handler import Handler

from Mirror.config import Config
from Mirror.core.db import DB
from Mirror.core.task import Registry, TaskQueue

BOT_LIMIT = 2000 * 1024**2
PREMIUM_LIMIT = 4000 * 1024**2
Callback = Callable[[Client, Any], Awaitable[None]]


class App:
    def __init__(self) -> None:
        self.bot: Client
        self.user: Client | None = None
        self.premium = False
        self.db = DB()
        self.tasks = Registry()
        self.queue = TaskQueue(lambda: Config.QUEUE_DOWNLOAD, lambda: Config.QUEUE_UPLOAD, lambda: Config.QUEUE_ALL)
        self.started = time.time()
        self.stopping = False
        self.handlers: list[tuple[Handler, int]] = []
        self.background: set[asyncio.Task[Any]] = set()

    @property
    def data(self) -> Path:
        return Path(Config.DATA_DIR)

    @property
    def downloads(self) -> Path:
        return Path(Config.DOWNLOAD_DIR)

    def build_clients(self) -> None:
        common: dict[str, Any] = {"api_id": Config.API_ID, "api_hash": Config.API_HASH, "in_memory": True,
                                  "parse_mode": enums.ParseMode.HTML, "sleep_threshold": 60,
                                  "max_concurrent_transmissions": 4}
        self.bot = Client("mirror-bot", bot_token=Config.BOT_TOKEN, workers=32, **common)
        if Config.SESSION_STRING:
            self.user = Client("mirror-user", session_string=Config.SESSION_STRING, no_updates=True, **common)
        for handler, group in self.handlers:
            self.bot.add_handler(handler, group)

    @property
    def max_size(self) -> int:
        return PREMIUM_LIMIT if self.user and self.premium else BOT_LIMIT

    def uploader(self, size: int, session: str) -> Client:
        """Pick the client for a part: ``user``/``bot`` forced or ``mix`` (user only above bot limit)."""
        if not self.user or session == "bot":
            return self.bot
        if session == "user":
            return self.user
        return self.user if size > BOT_LIMIT else self.bot

    def spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        job = asyncio.ensure_future(coro)
        self.background.add(job)
        job.add_done_callback(self.background.discard)
        return job

    def command(self, *names: str, flt: Filter | None = None, group: int = 0) -> Callable[[Callback], Callback]:
        cmds = [f"{name}{Config.CMD_SUFFIX}" for name in names]

        def decorator(func: Callback) -> Callback:
            base = filters.command(cmds)
            self.handlers.append((MessageHandler(func, base & flt if flt else base), group))
            return func
        return decorator

    def message(self, flt: Filter, group: int = 0) -> Callable[[Callback], Callback]:
        def decorator(func: Callback) -> Callback:
            self.handlers.append((MessageHandler(func, flt), group))
            return func
        return decorator

    def callback(self, prefix: str, flt: Filter | None = None) -> Callable[[Callback], Callback]:
        def decorator(func: Callback) -> Callback:
            base = filters.regex(rf"^{prefix}\|")
            self.handlers.append((CallbackQueryHandler(func, base & flt if flt else base), 0))
            return func
        return decorator


app = App()
