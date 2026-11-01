"""/bsetting (owner): live config editing saved to Mongo, private files, engine options, SAB servers."""

import json
from collections.abc import Awaitable, Callable
from html import escape
from types import ModuleType
from typing import Any

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from Mirror.config import BOOT_ONLY, Config
from Mirror.core.app import app
from Mirror.core.auth import owner_only
from Mirror.core.prefs import PRIVATE_FILES, private, save_private_file
from Mirror.core.ui import ask, edit, grid, kb, send
from Mirror.modules import aria2, qbit, rclone, sabnzbd

ENGINES: dict[str, ModuleType] = {"aria2": aria2, "qbit": qbit, "sab": sabnzbd}
PAGE = 20
_engine_keys: dict[str, list[str]] = {}
SIDE_EFFECTS: dict[str, Callable[[], Awaitable[None]]] = {
    "RCLONE_SERVE_PORT": rclone.serve, "RCLONE_SERVE_USER": rclone.serve, "RCLONE_SERVE_PASS": rclone.serve,
    "QUEUE_ALL": app.queue.wake, "QUEUE_DOWNLOAD": app.queue.wake, "QUEUE_UPLOAD": app.queue.wake,
}


def home() -> tuple[str, Any]:
    sections = [(name, f"bs|s|{name}|0") for name in Config.sections()]
    extra = [("📁 Private files", "bs|pf|-|0"), ("aria2 options", "bs|eg|aria2|0"), ("qBit options", "bs|eg|qbit|0"),
             ("SABnzbd options", "bs|eg|sab|0"), ("SABnzbd servers", "bs|sv|-|0")]
    store = "Mongo" if app.db.persistent else "memory (no DATABASE_URL)"
    return f"<b>Bot settings</b>\nOverrides are stored in {store}.", kb(*grid(sections, 3), *grid(extra, 2),
                                                                           [("✖ Close", "bs|x|-|0")])


def section(name: str) -> tuple[str, Any]:
    keys = Config.sections()[name]
    rows = [[(f"{k}: {Config.show(k)[:28] or '—'}", f"bs|k|{k}|0")] for k in keys]
    return f"<b>{name}</b>", kb(*rows, [("⬅ Back", "bs|h|-|0")])


def key_page(key: str) -> tuple[str, Any]:
    kind = Config.types()[key]
    note = "\n<i>Boot-only: set it in the environment and restart.</i>" if key in BOOT_ONLY else ""
    text = (f"<b>{key}</b> <code>{getattr(kind, '__name__', str(kind))}</code>\nValue: <code>"
            f"{escape(Config.show(key))}</code>{note}")
    back = next(s for s, keys in Config.sections().items() if key in keys)
    actions = [] if key in BOOT_ONLY else [("✏ Edit", f"bs|ke|{key}|0"), ("↺ Reset", f"bs|kr|{key}|0")]
    return text, kb(actions, [("⬅ Back", f"bs|s|{back}|0")])


def files_page() -> tuple[str, Any]:
    rows = [[(f"{name} {'✅' if private(name) else '—'}", f"bs|fu|{i}|0"), ("🗑", f"bs|fd|{i}|0")]
            for i, name in enumerate(PRIVATE_FILES)]
    return "<b>Private files</b>\nTap a name to upload/replace it.", kb(*rows, [("⬅ Back", "bs|h|-|0")])


async def engine_page(engine: str, page: int) -> tuple[str, Any]:
    try:
        options = await ENGINES[engine].global_options()
    except Exception as exc:  # noqa: BLE001 - engine may be offline
        return f"⚠️ {engine} unavailable: {escape(str(exc))}", kb([("⬅ Back", "bs|h|-|0")])
    keys = _engine_keys[engine] = sorted(options)
    pages = max(1, -(-len(keys) // PAGE))
    page %= pages
    rows = [[(f"{k}: {options[k][:20]}", f"bs|eo|{engine}|{page * PAGE + i}")]
            for i, k in enumerate(keys[page * PAGE:(page + 1) * PAGE])]
    nav = [("◀", f"bs|eg|{engine}|{page - 1}"), (f"{page + 1}/{pages}", f"bs|eg|{engine}|{page}"),
           ("▶", f"bs|eg|{engine}|{page + 1}")]
    return f"<b>{engine} global options</b>", kb(*rows, nav, [("⬅ Back", "bs|h|-|0")])


async def servers_page() -> tuple[str, Any]:
    try:
        servers = await sabnzbd.servers()
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ SABnzbd unavailable: {escape(str(exc))}", kb([("⬅ Back", "bs|h|-|0")])
    rows = [[(f"{s.get('name')} · {s.get('host')}:{s.get('port')}", "bs|sv|-|0"), ("🗑", f"bs|sd|{i}|0")]
            for i, s in enumerate(servers)]
    return "<b>Usenet servers</b>", kb(*rows, [("➕ Add / edit", "bs|sa|-|0"), ("⬅ Back", "bs|h|-|0")])


@app.command("bsetting", "bs", flt=owner_only)
async def bsetting(_: Client, message: Message) -> None:
    await send(message.chat.id, *home(), reply_to=message.id)


@app.callback("bs", owner_only)
async def bsetting_cb(_: Client, query: CallbackQuery) -> None:
    _, action, arg, num = str(query.data).split("|", 3)
    await query.answer()
    msg, uid = query.message, query.from_user.id
    page: tuple[str, Any]
    if action == "x":
        await msg.delete()
        return
    if action == "s":
        page = section(arg)
    elif action == "k":
        page = key_page(arg)
    elif action in {"ke", "kr"}:
        if action == "ke":
            reply = await ask(msg, uid, f"Send value for <b>{arg}</b> (lists: space separated, dict: JSON)")
            if reply and reply.text:
                try:
                    value = Config.set(arg, reply.text)
                    await app.db.set_override(arg, value)
                except (ValueError, json.JSONDecodeError) as exc:
                    await send(msg.chat.id, f"⚠️ {escape(str(exc))}")
                await reply.delete()
        else:
            Config.reset(arg)
            await app.db.unset_override(arg)
        if hook := SIDE_EFFECTS.get(arg):
            await hook()
        page = key_page(arg)
    elif action == "pf":
        page = files_page()
    elif action in {"fu", "fd"}:
        name = PRIVATE_FILES[int(arg)]
        if action == "fu":
            reply = await ask(msg, uid, f"Send <code>{name}</code> as a document")
            if reply and reply.document:
                await save_private_file(name, bytes((await reply.download(in_memory=True)).getbuffer()))
                await reply.delete()
        else:
            await save_private_file(name, None)
        if name == "rclone.conf":
            await rclone.serve()
        page = files_page()
    elif action == "eg":
        page = await engine_page(arg, int(num))
    elif action == "eo":
        key = _engine_keys.get(arg, [])[int(num)]
        reply = await ask(msg, uid, f"Send value for {arg} option <code>{key}</code>")
        if reply and reply.text:
            try:
                await ENGINES[arg].set_global_option(key, reply.text.strip())
                await app.db.set_engine_opt(arg, key, reply.text.strip())
            except Exception as exc:  # noqa: BLE001
                await send(msg.chat.id, f"⚠️ {escape(str(exc))}")
            await reply.delete()
        page = await engine_page(arg, int(num) // PAGE)
    elif action == "sv":
        page = await servers_page()
    elif action == "sa":
        reply = await ask(msg, uid, "Send server fields, one per line:\n<code>name=x\nhost=news.example.com\nport=563"
                                    "\nusername=u\npassword=p\nconnections=20\nssl=1</code>")
        if reply and reply.text:
            fields = dict(line.split("=", 1) for line in reply.text.splitlines() if "=" in line)
            await sabnzbd.save_server({k.strip(): v.strip() for k, v in fields.items()})
            await reply.delete()
        page = await servers_page()
    elif action == "sd":
        servers = await sabnzbd.servers()
        await sabnzbd.delete_server(str(servers[int(arg)].get("name")))
        page = await servers_page()
    else:
        page = home()
    await edit(msg, *page)


async def restore_engine_options() -> None:
    """Re-apply engine options saved from /bsetting (called at boot)."""
    for name, module in ENGINES.items():
        for key, value in (await app.db.engine_opts(name)).items():
            try:
                await module.set_global_option(key, str(value))
            except Exception:  # noqa: BLE001 - engine may be offline/disabled
                break
