import datetime as dt
import shutil
from typing import Any

from pyrogram import Client, filters
from pyrogram.errors import RPCError, UserNotParticipant
from pyrogram.types import CallbackQuery, Message

from Mirror.config import Config
from Mirror.core.app import app
from Mirror.core.task import Task
from Mirror.core.utils import human_size

ENGINE_LIMITS = {
    "direct": "DIRECT_LIMIT", "aria2": "DIRECT_LIMIT", "qbit": "TORRENT_LIMIT", "debrid": "TORRENT_LIMIT",
    "aria2-torrent": "TORRENT_LIMIT", "sab": "NZB_LIMIT", "ytdl": "YTDL_LIMIT", "gallery": "YTDL_LIMIT",
    "gdrive": "GDRIVE_LIMIT", "clone": "CLONE_LIMIT", "rclone": "DIRECT_LIMIT", "jd": "DIRECT_LIMIT",
}


class Roles:
    sudo: set[int] = set()
    authorized: set[int] = set()
    banned: set[int] = set()

    @classmethod
    async def load(cls) -> None:
        docs = await app.db.users()
        cls.sudo = {d["_id"] for d in docs if d.get("sudo")}
        cls.authorized = {d["_id"] for d in docs if d.get("auth")}
        cls.banned = {d["_id"] for d in docs if d.get("banned")}

    @classmethod
    async def set(cls, role: str, chat_id: int, on: bool) -> None:
        bucket: set[int] = getattr(cls, {"sudo": "sudo", "auth": "authorized", "banned": "banned"}[role])
        (bucket.add if on else bucket.discard)(chat_id)
        await app.db.update_user(chat_id, **{role: on})


def is_owner(user_id: int) -> bool:
    return bool(user_id) and user_id == Config.OWNER_ID


def is_sudo(user_id: int) -> bool:
    return is_owner(user_id) or user_id in Config.SUDO_USERS or user_id in Roles.sudo


def is_authorized(user_id: int, chat_id: int) -> bool:
    if user_id in Roles.banned:
        return False
    allowed = {*Config.AUTHORIZED_CHATS, *Roles.authorized}
    return is_sudo(user_id) or user_id in allowed or chat_id in allowed


def _uid(update: Message | CallbackQuery) -> int:
    return update.from_user.id if update.from_user else 0


def _chat(update: Message | CallbackQuery) -> int:
    msg = update.message if isinstance(update, CallbackQuery) else update
    return msg.chat.id if msg and msg.chat else 0


async def _owner(_: Any, __: Client, update: Message | CallbackQuery) -> bool:
    return is_owner(_uid(update))


async def _sudo(_: Any, __: Client, update: Message | CallbackQuery) -> bool:
    return is_sudo(_uid(update))


async def _auth(_: Any, __: Client, update: Message | CallbackQuery) -> bool:
    return is_authorized(_uid(update), _chat(update))


owner_only = filters.create(_owner)
sudo_only = filters.create(_sudo)
authorized = filters.create(_auth)


async def force_sub(message: Message) -> str | None:
    """Return an error text with join links when the user misses a required chat."""
    uid = _uid(message)
    if not Config.FSUB_CHATS or is_sudo(uid):
        return None
    missing = []
    for chat_id in Config.FSUB_CHATS:
        try:
            await app.bot.get_chat_member(chat_id, uid)
        except UserNotParticipant:
            try:
                chat = await app.bot.get_chat(chat_id)
                missing.append(chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else str(chat_id)))
            except RPCError:
                missing.append(str(chat_id))
        except RPCError:
            continue
    return "Join first:\n" + "\n".join(missing) if missing else None


def today() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


async def check_limits(task: Task, size: int) -> str | None:
    """Validate a known size against engine, leech, storage and daily quota limits."""
    if is_sudo(task.user_id):
        return None
    key = "CLONE_LIMIT" if task.mode == "clone" else ENGINE_LIMITS.get(task.engine)
    checks = [(key, getattr(Config, key) if key else 0)]
    if task.is_leech:
        checks.append(("LEECH_LIMIT", Config.LEECH_LIMIT))
    for name, limit in checks:
        if limit and size > limit:
            return f"{name.replace('_', ' ').title()} is {human_size(limit)}; task is {human_size(size)}"
    if Config.STORAGE_THRESHOLD and task.mode != "clone":
        free = shutil.disk_usage(app.downloads).free
        need = size * (2 if task.args.zip or task.args.extract else 1)
        if free - need < Config.STORAGE_THRESHOLD:
            return f"Not enough free space: keeping {human_size(Config.STORAGE_THRESHOLD)} free"
    user = await app.db.user(task.user_id)
    quota = int(user.get("quota") or Config.DAILY_QUOTA)
    used = int(user.get("used", 0)) if user.get("used_day") == today() else 0
    if quota and used + size > quota:
        return f"Daily quota {human_size(quota)} exceeded (used {human_size(used)})"
    return None


def task_slots_left(user_id: int) -> bool:
    return is_sudo(user_id) or not Config.MAX_USER_TASKS or app.tasks.count_user(user_id) < Config.MAX_USER_TASKS
