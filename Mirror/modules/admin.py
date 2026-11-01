"""Access control, quotas, stats/ping/log/restart, owner shell/exec and broadcast."""

import asyncio
import contextlib
import io
import os
import platform
import shutil
import sys
import textwrap
import time
import traceback
from html import escape
from pathlib import Path

import psutil
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

from Mirror.config import Config
from Mirror.core.app import app
from Mirror.core.auth import Roles, authorized, is_authorized, is_owner, owner_only, sudo_only, today
from Mirror.core.flags import flags_help
from Mirror.core.ui import send
from Mirror.core.utils import human_size, human_time, parse_size

LOG_FILE = Path("log.txt")
RESTART_FILE = Path(".restart")
ROLE_COMMANDS = {
    "authorize": ("auth", True), "unauthorize": ("auth", False), "addsudo": ("sudo", True),
    "rmsudo": ("sudo", False), "ban": ("banned", True), "unban": ("banned", False),
}
HELP = """<b>Transfers</b>
/mirror /leech · /qbmirror /qbleech · /aria2mirror /aria2leech · /ytdl /ytdlleech
/gdl /gdlleech (gallery-dl) · /nzbmirror /nzbleech · /jdmirror /jdleech · /clone
<b>Drive</b> /count /del /list · <b>Control</b> /status [me] /cancel ID /cancelall
<b>Settings</b> /usettings · /bsetting (owner) · /rss · /search
<b>Admin</b> /authorize /unauthorize /addsudo /rmsudo /ban /unban /users /setquota
/stats /ping /log /restart /shell /exec /broadcast"""


def target_of(message: Message) -> int | None:
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].lstrip("-").isdigit():
        return int(parts[1])
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    return message.chat.id if message.chat.type.value != "private" else None


@app.command("start", "help")
async def start_cmd(_: Client, message: Message) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not is_authorized(uid, message.chat.id):
        await send(message.chat.id, "This bot is private. Ask the owner for access.", reply_to=message.id)
        return
    extra = "\n\n<b>Flags</b>\n" + flags_help() if "help" in (message.text or "") else ""
    await send(message.chat.id, HELP + extra, reply_to=message.id)


@app.command(*ROLE_COMMANDS, flt=sudo_only)
async def role_cmd(_: Client, message: Message) -> None:
    role, on = ROLE_COMMANDS[message.command[0].removesuffix(Config.CMD_SUFFIX)]
    if role == "sudo" and not is_owner(message.from_user.id if message.from_user else 0):
        await send(message.chat.id, "Owner only.", reply_to=message.id)
        return
    target = target_of(message)
    if target is None:
        await send(message.chat.id, "Give an id or reply to a user.", reply_to=message.id)
        return
    await Roles.set(role, target, on)
    await send(message.chat.id, f"<code>{target}</code>: {role} → {'on' if on else 'off'}", reply_to=message.id)


@app.command("users", flt=sudo_only)
async def users_cmd(_: Client, message: Message) -> None:
    lines = [f"<b>Owner</b> <code>{Config.OWNER_ID}</code>"]
    for label, ids in (("Sudo", {*Config.SUDO_USERS, *Roles.sudo}), ("Authorized", {*Config.AUTHORIZED_CHATS,
                                                                                    *Roles.authorized}),
                       ("Banned", Roles.banned)):
        lines.append(f"<b>{label}</b> " + (" ".join(f"<code>{i}</code>" for i in sorted(ids)) or "—"))
    await send(message.chat.id, "\n".join(lines), reply_to=message.id)


@app.command("setquota", flt=sudo_only)
async def quota_cmd(_: Client, message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
        await send(message.chat.id, "Usage: /setquota user_id 50GB (0 = default)", reply_to=message.id)
        return
    size = parse_size(parts[2])
    await app.db.update_user(int(parts[1]), quota=size)
    await send(message.chat.id, f"Daily quota of <code>{parts[1]}</code>: {human_size(size) if size else 'default'}",
               reply_to=message.id)


@app.command("stats", flt=authorized)
async def stats_cmd(_: Client, message: Message) -> None:
    disk = shutil.disk_usage(app.downloads if app.downloads.exists() else ".")
    net = psutil.net_io_counters()
    me = await app.db.user(message.from_user.id) if message.from_user else {}
    used = me.get("used", 0) if me.get("used_day") == today() else 0
    text = (f"<b>Bot</b> uptime {human_time(time.time() - app.started)} · tasks {len(app.tasks)}\n"
            f"<b>System</b> {platform.system()} {platform.release()} · Python {platform.python_version()}\n"
            f"<b>CPU</b> {psutil.cpu_percent(interval=0.3)}% of {psutil.cpu_count()} · <b>RAM</b> "
            f"{psutil.virtual_memory().percent}% of {human_size(psutil.virtual_memory().total)}\n"
            f"<b>Disk</b> {human_size(disk.used)} / {human_size(disk.total)} (free {human_size(disk.free)})\n"
            f"<b>Network</b> ▲ {human_size(net.bytes_sent)} ▼ {human_size(net.bytes_recv)}\n"
            f"<b>User session</b> {'on' + (' (premium 4GB)' if app.premium else '') if app.user else 'off'}\n"
            f"<b>Your usage today</b> {human_size(used)}")
    await send(message.chat.id, text, reply_to=message.id)


@app.command("ping", flt=authorized)
async def ping_cmd(_: Client, message: Message) -> None:
    start = time.monotonic()
    reply = await send(message.chat.id, "Pong…", reply_to=message.id)
    if reply:
        await reply.edit_text(f"Pong {int((time.monotonic() - start) * 1000)} ms")


@app.command("log", flt=sudo_only)
async def log_cmd(_: Client, message: Message) -> None:
    if LOG_FILE.exists():
        await message.reply_document(str(LOG_FILE), quote=True)


@app.command("restart", flt=sudo_only)
async def restart_cmd(_: Client, message: Message) -> None:
    note = await send(message.chat.id, "♻️ Restarting…", reply_to=message.id)
    if note:
        RESTART_FILE.write_text(f"{note.chat.id} {note.id}")
    app.stopping = True
    for task in app.tasks:
        task.cancel()
    await asyncio.sleep(2)
    script = "start.sh" if Path("start.sh").exists() else None
    if script:
        os.execvp("bash", ["bash", script])
    os.execv(sys.executable, [sys.executable, "-m", "Mirror"])


async def announce_restart() -> None:
    if RESTART_FILE.exists():
        chat, msg = RESTART_FILE.read_text().split()
        RESTART_FILE.unlink()
        with contextlib.suppress(RPCError):
            await app.bot.edit_message_text(int(chat), int(msg), "✅ Restarted.")


async def _reply_output(message: Message, output: str, name: str) -> None:
    output = output.strip() or "(no output)"
    if len(output) > 3800:
        file = io.BytesIO(output.encode())
        file.name = name
        await message.reply_document(file, quote=True)
    else:
        await send(message.chat.id, f"<pre>{escape(output)}</pre>", reply_to=message.id)


@app.command("shell", "sh", flt=owner_only)
async def shell_cmd(_: Client, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return
    proc = await asyncio.create_subprocess_shell(parts[1], stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    await _reply_output(message, out.decode(errors="replace") + f"\n[exit {proc.returncode}]", "shell.txt")


@app.command("exec", "eval", flt=owner_only)
async def exec_cmd(client: Client, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return
    namespace = {"app": app, "client": client, "message": message, "Config": Config, "asyncio": asyncio}
    buffer = io.StringIO()
    try:
        exec(f"async def __run():\n{textwrap.indent(parts[1], '    ')}", namespace)
        with contextlib.redirect_stdout(buffer):
            result = await namespace["__run"]()
        if result is not None:
            print(repr(result), file=buffer)
    except Exception:  # noqa: BLE001 - show traceback to owner
        buffer.write(traceback.format_exc())
    await _reply_output(message, buffer.getvalue(), "exec.txt")


@app.command("broadcast", flt=owner_only)
async def broadcast_cmd(_: Client, message: Message) -> None:
    source = message.reply_to_message
    if not source:
        await send(message.chat.id, "Reply to the message to broadcast.", reply_to=message.id)
        return
    users = [d["_id"] for d in await app.db.users() if isinstance(d["_id"], int) and d["_id"] > 0
             and not d.get("banned")]
    ok = failed = 0
    for uid in users:
        for _ in range(2):
            try:
                await source.copy(uid)
                ok += 1
                break
            except FloodWait as wait:
                await asyncio.sleep(float(wait.value) + 1)
            except RPCError:
                failed += 1
                break
    await send(message.chat.id, f"Broadcast done: {ok} sent, {failed} failed.", reply_to=message.id)


@app.message(filters.incoming & filters.regex(r"^/") & filters.private, group=5)
async def remember_user(_: Client, message: Message) -> None:
    if message.from_user:
        await app.db.update_user(message.from_user.id, seen=today())
