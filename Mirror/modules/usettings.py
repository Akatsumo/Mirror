"""/usettings: per-user options and files (thumbnail, rclone.conf, token.pickle, cookies) in Mongo."""

import json
from html import escape
from typing import Any

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from Mirror.config import Config, Size
from Mirror.core.app import app
from Mirror.core.auth import authorized
from Mirror.core.prefs import USER_FILES, USER_OPTIONS, parse_option, prefs, save_user_file
from Mirror.core.ui import Row, ask, edit, grid, kb, send
from Mirror.core.utils import human_size

PAGE = 12
FILE_LABELS = {"thumb": "🖼 Thumbnail", "rclone": "⚙ rclone.conf", "token": "🔑 token.pickle",
               "cookies": "🍪 cookies.txt"}


def show_value(key: str, value: Any) -> str:
    kind = USER_OPTIONS[key][1]
    if kind is bool:
        return "✅" if value else "❌"
    if kind is Size:
        return human_size(int(value)) if value else "auto"
    if key in {"gofile_token", "streamtape_key"}:
        return "•••" if value else "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value)[:40] or "—"
    return str(value)[:40] or "—"


async def main_menu(uid: int, page: int = 0) -> tuple[str, Any]:
    user = await prefs(uid)
    keys = list(USER_OPTIONS)
    pages = -(-len(keys) // PAGE)
    page %= pages
    buttons = [(f"{USER_OPTIONS[k][0]}: {show_value(k, user.values[k])}", f"us|{uid}|o|{k}")
               for k in keys[page * PAGE:(page + 1) * PAGE]]
    files = [(f"{label} {'✅' if user.file(kind) else '—'}", f"us|{uid}|f|{kind}")
             for kind, label in FILE_LABELS.items()]
    nav: Row = [("◀", f"us|{uid}|p|{page - 1}"), (f"{page + 1}/{pages}", f"us|{uid}|p|{page}"),
                ("▶", f"us|{uid}|p|{page + 1}")]
    text = (f"<b>User settings</b> · <code>{uid}</code>\nSession: {user.session} · Default upload: "
            f"{user.default_upload} · Saved in {'Mongo' if app.db.persistent else 'memory'}")
    return text, kb(*grid(buttons, 1), *grid(files, 2), nav, [("♻ Reset all", f"us|{uid}|ra|-"),
                                                             ("✖ Close", f"us|{uid}|x|-")])


async def option_menu(uid: int, key: str) -> tuple[str, Any]:
    label, kind, cfg = USER_OPTIONS[key]
    user = await prefs(uid)
    value = user.values[key]
    action = ("🔁 Toggle", f"us|{uid}|t|{key}") if kind is bool else ("✏ Edit", f"us|{uid}|e|{key}")
    text = (f"<b>{label}</b>\nCurrent: <code>{escape(show_value(key, value))}</code>\n"
            f"Default (<code>{cfg}</code>): <code>{escape(show_value(key, getattr(Config, cfg)))}</code>")
    return text, kb([action, ("↺ Default", f"us|{uid}|r|{key}")], [("⬅ Back", f"us|{uid}|p|0")])


async def file_menu(uid: int, kind: str) -> tuple[str, Any]:
    user = await prefs(uid)
    state = "saved" if user.file(kind) else "not set"
    return (f"<b>{FILE_LABELS[kind]}</b>: {state}",
            kb([("⬆ Upload", f"us|{uid}|fu|{kind}"), ("🗑 Delete", f"us|{uid}|fd|{kind}")],
               [("⬅ Back", f"us|{uid}|p|0")]))


@app.command("usettings", "us", flt=authorized)
async def usettings(_: Client, message: Message) -> None:
    if message.from_user:
        await send(message.chat.id, *await main_menu(message.from_user.id), reply_to=message.id)


@app.callback("us")
async def usettings_cb(_: Client, query: CallbackQuery) -> None:
    _, owner, action, key = str(query.data).split("|", 3)
    uid = int(owner)
    if query.from_user.id != uid:
        await query.answer("Open your own /usettings", show_alert=True)
        return
    await query.answer()
    message = query.message
    if action == "x":
        await message.delete()
        return
    if action == "p":
        await edit(message, *await main_menu(uid, int(key)))
        return
    if action == "o":
        await edit(message, *await option_menu(uid, key))
        return
    if action == "t":
        await app.db.update_user(uid, **{key: not (await prefs(uid)).values[key]})
    elif action == "r":
        await app.db.unset_user(uid, key)
    elif action == "ra":
        await app.db.unset_user(uid, *USER_OPTIONS)
        for kind in USER_FILES:
            await save_user_file(uid, kind, None)
        await edit(message, *await main_menu(uid))
        return
    elif action == "e":
        reply = await ask(message, uid, f"Send new value for <b>{USER_OPTIONS[key][0]}</b>")
        if reply and reply.text:
            try:
                await app.db.update_user(uid, **{key: parse_option(key, reply.text)})
            except (ValueError, json.JSONDecodeError) as exc:
                await send(message.chat.id, f"⚠️ Invalid value: {escape(str(exc))}")
            await reply.delete()
    elif action in {"f", "fu", "fd"}:
        if action == "fu":
            prompt = "Send a photo" if key == "thumb" else f"Send the {USER_FILES[key]} file"
            reply = await ask(message, uid, prompt)
            media = (reply.photo or reply.document) if reply else None
            if reply and media:
                data = await reply.download(in_memory=True)
                await save_user_file(uid, key, bytes(data.getbuffer()))
                await reply.delete()
        elif action == "fd":
            await save_user_file(uid, key, None)
        await edit(message, *await file_menu(uid, key))
        return
    await edit(message, *await option_menu(uid, key))
