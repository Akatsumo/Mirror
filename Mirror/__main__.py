import asyncio
import contextlib
import logging
import shutil
from logging.handlers import RotatingFileHandler

import uvloop
from pyrogram import idle
from pyrogram.types import BotCommand

from Mirror.config import Config
from Mirror.core.app import app

COMMANDS = {
    "mirror": "Mirror to cloud", "leech": "Upload to Telegram", "qbmirror": "Mirror torrent (qBittorrent)",
    "qbleech": "Leech torrent (qBittorrent)", "ytdl": "Mirror with yt-dlp", "ytdlleech": "Leech with yt-dlp",
    "clone": "Clone Drive/rclone", "count": "Count Drive link", "list": "Search Drive", "search": "Search torrents",
    "status": "Task status", "cancelall": "Cancel tasks", "usettings": "User settings", "rss": "RSS feeds",
    "stats": "Bot stats", "help": "Help",
}


def setup_logging() -> None:
    logging.basicConfig(
        level=Config.LOG_LEVEL.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), RotatingFileHandler("log.txt", maxBytes=5 << 20, backupCount=1)],
    )
    for noisy in ("pyrogram", "aiohttp", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def main() -> None:
    log = logging.getLogger("Mirror")
    await app.db.connect(Config.DATABASE_URL, Config.DATABASE_NAME)
    if applied := Config.apply(await app.db.overrides()):
        log.info("config overrides from database: %s", ", ".join(applied))
    from Mirror import modules
    from Mirror.core import pipeline
    from Mirror.core.auth import Roles
    from Mirror.core.prefs import restore_files
    from Mirror.modules import admin, bsettings, rclone, rss

    modules.load()
    shutil.rmtree(app.downloads, ignore_errors=True)
    app.downloads.mkdir(parents=True, exist_ok=True)
    app.data.mkdir(parents=True, exist_ok=True)
    await Roles.load()
    await restore_files()
    app.build_clients()
    await app.bot.start()
    if app.user:
        await app.user.start()
        app.premium = bool(app.user.me.is_premium)
    with contextlib.suppress(Exception):
        await app.bot.set_bot_commands([BotCommand(f"{k}{Config.CMD_SUFFIX}", v) for k, v in COMMANDS.items()])
    await bsettings.restore_engine_options()
    await rclone.serve()
    rss.start()
    await admin.announce_restart()
    restored = await pipeline.restore()
    log.info("bot @%s started (user session: %s, restored tasks: %d)", app.bot.me.username,
             "premium" if app.premium else bool(app.user), restored)
    try:
        await idle()
    finally:
        app.stopping = True
        for task in app.tasks:
            task.cancel()
        await asyncio.sleep(1)
        for client in (app.user, app.bot):
            if client and client.is_connected:
                await client.stop()
        app.db.close()


if __name__ == "__main__":
    setup_logging()
    uvloop.run(main())
