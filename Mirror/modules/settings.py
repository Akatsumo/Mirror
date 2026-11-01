from pyrogram import Client, filters
from pyrogram.types import Message

from Mirror.core.database import Database


def register_settings(bot: Client, database: Database) -> None:
    @bot.on_message(filters.command("usettings") & filters.private)
    async def user_settings(_: Client, message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        text = (message.text or "").split(maxsplit=1)
        if len(text) == 2:
            command = text[1].strip().split(maxsplit=1)
            if len(command) == 2 and command[0].lower() in {"prefix", "document"}:
                key, value = command
                if key.lower() == "document" and value.lower() not in {"on", "off", "true", "false"}:
                    await message.reply_text("Usage: /usettings document on|off")
                    return
                normalized = value if key.lower() == "prefix" else value.lower() in {"on", "true"}
                await database.set_setting(user_id, key.lower(), normalized)
            else:
                await message.reply_text("Usage: /usettings · prefix TEXT · document on|off · reply to a photo to set thumbnail")
                return
        elif message.reply_to_message and message.reply_to_message.photo:
            photo = await message.reply_to_message.download()
            await database.set_setting(user_id, "thumbnail", str(photo))
        settings = await database.get_settings(user_id)
        prefix = settings.get("prefix", "") or "(none)"
        thumbnail = "set" if settings.get("thumbnail") else "not set"
        document = "on" if settings.get("document", False) else "off"
        await message.reply_text(f"<b>Your settings</b>\nPrefix: <code>{prefix}</code>\nDocument: {document}\nThumbnail: {thumbnail}")
