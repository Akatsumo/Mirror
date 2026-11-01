import asyncio
import shutil
import uuid
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from Mirror.config import Config
from Mirror.core.database import Database
from Mirror.core.task import Task, TaskQueue
from Mirror.modules.download import Downloader
from Mirror.modules.status import status_text, watch_progress
from Mirror.modules.upload import upload_file


def register_commands(
    bot: Client,
    user_client: Client | None,
    database: Database,
    queue: TaskQueue,
    downloader: Downloader,
) -> None:
    async def authorized(message: Message) -> bool:
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else 0
        if Config.AUTHORIZED_CHATS and chat_id not in Config.AUTHORIZED_CHATS:
            await message.reply_text("This chat is not authorized.")
            return False
        privileged = {Config.OWNER_ID, *Config.SUDO_USERS} - {0}
        if privileged and user_id not in privileged and chat_id not in Config.AUTHORIZED_CHATS:
            await message.reply_text("You are not authorized to run transfers.")
            return False
        return True

    async def start_task(message: Message, mode: str, argument: str, *, telegram_source: Message | None = None) -> None:
        if not await authorized(message):
            return
        user_id = message.from_user.id if message.from_user else 0
        if not argument and telegram_source is None:
            await message.reply_text("Reply to a Telegram file or provide a URL.")
            return
        settings = await database.get_settings(user_id)
        status_message = await message.reply_text(f"Queued <code>{mode}</code> task…")
        task_id = uuid.uuid4().hex[:10]
        task_directory = Path(downloader.directory) / task_id
        upload_client = user_client or bot

        async def runner(task: Task) -> None:
            task_downloader = Downloader(task_directory)
            try:
                if telegram_source:
                    source = await task_downloader.telegram(bot, telegram_source, task)
                elif mode == "ytdl":
                    source = await task_downloader.ytdlp(argument, task)
                elif mode == "aria2":
                    source = await task_downloader.aria2(argument, task)
                else:
                    source = await task_downloader.direct(argument, task)
                task.name = source.name
                await upload_file(
                    upload_client,
                    message,
                    source,
                    split_size=Config.LEECH_SPLIT_SIZE,
                    as_document=bool(settings.get("document", Config.UPLOAD_AS_DOC)),
                    thumbnail=settings.get("thumbnail"),
                    prefix=str(settings.get("prefix", "")),
                    task=task,
                )
            finally:
                await asyncio.to_thread(shutil.rmtree, task_directory, True)

        task = Task(task_id=task_id, user_id=user_id, name=mode, runner=runner)
        await queue.submit(task)
        asyncio.create_task(watch_progress(status_message, task))

    @bot.on_message(filters.command("start"))
    async def start(_: Client, message: Message) -> None:
        await message.reply_text("Send /mirror URL or reply to a Telegram file with /leech.")

    @bot.on_message(filters.command("help"))
    async def help_command(_: Client, message: Message) -> None:
        await message.reply_text(
            "<b>Mirror bot</b>\n/mirror URL — direct download\n/mirror --aria2 URL — aria2 download\n/ytdl URL — yt-dlp\n"
            "/leech URL or reply to a file — upload to Telegram\n/status — show active tasks\n/cancel ID — cancel your task"
        )

    @bot.on_message(filters.command("mirror"))
    async def mirror(_: Client, message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        source = message.reply_to_message if len(parts) == 1 else None
        argument = parts[1] if len(parts) > 1 else ""
        mode = "direct"
        if argument.startswith("--aria2 "):
            mode = "aria2"
            argument = argument.removeprefix("--aria2 ").strip()
        await start_task(message, mode, argument, telegram_source=source)

    @bot.on_message(filters.command("leech"))
    async def leech(_: Client, message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        source = message.reply_to_message if len(parts) == 1 else None
        await start_task(message, "leech", parts[1] if len(parts) > 1 else "", telegram_source=source)

    @bot.on_message(filters.command("ytdl"))
    async def ytdl(_: Client, message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        await start_task(message, "ytdl", parts[1] if len(parts) > 1 else "")

    @bot.on_message(filters.command("cancel"))
    async def cancel(_: Client, message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        user_id = message.from_user.id if message.from_user else 0
        if len(parts) != 2 or not await queue.cancel(parts[1], user_id):
            await message.reply_text("Task not found or not yours. Use /status.")
        else:
            await message.reply_text("Cancellation requested.")

    @bot.on_message(filters.command("status"))
    async def status(_: Client, message: Message) -> None:
        await message.reply_text(await status_text(queue, message.from_user.id if message.from_user else 0))
