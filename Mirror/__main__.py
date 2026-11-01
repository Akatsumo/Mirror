import asyncio
import logging
from pathlib import Path

from Mirror.config import Config
from Mirror.core.client import create_clients, install_event_loop
from Mirror.core.database import Database
from Mirror.core.task import Task, TaskQueue
from Mirror.modules.commands import register_commands
from Mirror.modules.download import Downloader
from Mirror.modules.settings import register_settings


async def main() -> None:
    if not Config.API_ID or not Config.API_HASH or not Config.BOT_TOKEN:
        raise RuntimeError("Set API_ID, API_HASH, and BOT_TOKEN before starting the bot")
    bot, user = create_clients()
    database = Database()

    async def persist(task: Task) -> None:
        await database.save_task(task.task_id, {
            "user_id": task.user_id,
            "name": task.name,
            "status": task.status,
            "current": task.current,
            "total": task.total,
            "error": task.error,
        })

    queue = TaskQueue(Config.CONCURRENT_TASKS, on_change=persist)
    downloader = Downloader(Path(Config.DOWNLOAD_DIR))
    register_commands(bot, user, database, queue, downloader)
    register_settings(bot, database)
    clients = [bot, *([user] if user else [])]
    try:
        for client in clients:
            await client.start()
        await asyncio.Event().wait()
    finally:
        await queue.close()
        for client in reversed(clients):
            await client.stop()
        await database.close()


if __name__ == "__main__":
    install_event_loop()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
