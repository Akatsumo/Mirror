import asyncio
from pathlib import Path

try:
    import uvloop
except ImportError:
    uvloop = None

from pyrogram import Client

from Mirror.config import Config


def install_event_loop() -> None:
    if uvloop is not None:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


def create_clients() -> tuple[Client, Client | None]:
    workdir = Path(Config.DOWNLOAD_DIR)
    workdir.mkdir(parents=True, exist_ok=True)
    bot = Client(
        "mirror_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        workdir=str(workdir),
        sleep_threshold=30,
    )
    user = None
    if Config.SESSION_STRING:
        user = Client(
            "mirror_user",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=Config.SESSION_STRING,
            workdir=str(workdir),
            sleep_threshold=30,
        )
    return bot, user
