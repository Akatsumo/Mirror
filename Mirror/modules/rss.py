"""RSS: per-user feeds with include/exclude filters, pause/resume/edit/delete and auto tasks."""

import asyncio
import logging
import shlex
from html import escape
from typing import Any

import aiohttp
import feedparser
from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from Mirror.config import Config
from Mirror.core.app import app
from Mirror.core.auth import authorized
from Mirror.core.ui import ask, edit, grid, kb, send

log = logging.getLogger(__name__)
SEEN = 60
_loop: asyncio.Task[None] | None = None


def parse_filter(text: str) -> list[list[str]]:
    """``a,b;c`` → (a OR b) AND c."""
    return [[w.strip().lower() for w in group.split(",") if w.strip()] for group in text.split(";") if group.strip()]


def passes(title: str, include: str, exclude: str) -> bool:
    title = title.lower()
    if any(word in title for group in parse_filter(exclude) for word in group):
        return False
    return all(any(word in title for word in group) for group in parse_filter(include))


def parse_feed_spec(text: str) -> dict[str, str]:
    """``name url [-c /cmd] [-inf a,b;c] [-exf x] [-f '-z -up rc']``."""
    tokens = shlex.split(text)
    if len(tokens) < 2:
        raise ValueError("need: name url")
    spec = {"name": tokens[0], "url": tokens[1], "cmd": "", "inf": "", "exf": "", "flags": ""}
    keys = {"-c": "cmd", "-inf": "inf", "-exf": "exf", "-f": "flags"}
    index = 2
    while index < len(tokens):
        key = keys.get(tokens[index])
        if not key or index + 1 >= len(tokens):
            raise ValueError(f"bad option {tokens[index]}")
        spec[key] = tokens[index + 1]
        index += 2
    return spec


def entry_link(entry: Any) -> str:
    for link in entry.get("links", []):
        if link.get("type") == "application/x-bittorrent" or str(link.get("href", "")).startswith("magnet:"):
            return str(link["href"])
    return str(entry.get("link") or "")


async def fetch(url: str) -> list[Any]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session, session.get(url) as resp:
        data = await resp.read()
    parsed = await asyncio.to_thread(feedparser.parse, data)
    return list(parsed.entries)


def entry_id(entry: Any) -> str:
    return str(entry.get("id") or entry.get("link") or entry.get("title"))


async def check_feed(feed: dict[str, Any]) -> int:
    entries = await fetch(feed["url"])
    seen = feed.get("seen")
    ids = [entry_id(e) for e in entries]
    feed["seen"] = ids[:SEEN]
    await app.db.put_feed(feed)
    if seen is None:
        return 0
    fresh = [e for e in entries if entry_id(e) not in seen][::-1]
    sent = 0
    chat = int(Config.RSS_CHAT) if Config.RSS_CHAT.lstrip("-").isdigit() else Config.RSS_CHAT or feed["chat_id"]
    for entry in fresh:
        title = str(entry.get("title", ""))
        if not passes(title, feed.get("inf", ""), feed.get("exf", "")):
            continue
        link = entry_link(entry)
        if feed.get("cmd") and link:
            text = f"{feed['cmd']} {link} {feed.get('flags', '')}".strip()
            message = await send(chat, f"<code>{escape(text)}</code>\n{escape(title)}")
            if message:
                from Mirror.modules.mirror import dispatch

                message.from_user = await app.bot.get_users(feed["user_id"])
                await dispatch(message, text)
        else:
            await send(chat, f"<b>{escape(feed['name'])}</b>\n{escape(title)}\n{escape(link)}")
        sent += 1
    return sent


async def _run_loop() -> None:
    while True:
        await asyncio.sleep(max(60, Config.RSS_INTERVAL))
        for feed in await app.db.feeds():
            if feed.get("paused"):
                continue
            try:
                await check_feed(feed)
            except Exception:
                log.warning("rss feed %s failed", feed.get("_id"), exc_info=True)

def start() -> None:
    global _loop
    if _loop is None or _loop.done():
        _loop = app.spawn(_run_loop())


def menu(uid: int) -> tuple[str, Any]:
    return "<b>RSS</b>", kb([("➕ Subscribe", f"rs|{uid}|add|-"), ("📜 My feeds", f"rs|{uid}|list|-")],
                            [("⏸ Pause all", f"rs|{uid}|pa|-"), ("▶ Resume all", f"rs|{uid}|ra|-")],
                            [("✖ Close", f"rs|{uid}|x|-")])


async def feed_page(uid: int, feed_id: str) -> tuple[str, Any]:
    feed = next((f for f in await app.db.feeds() if f["_id"] == feed_id), None)
    if not feed:
        return "Feed not found.", kb([("⬅ Back", f"rs|{uid}|list|-")])
    fields = "\n".join(f"{label}: <code>{escape(feed.get(key) or '—')}</code>" for key, label in
                       (("url", "url"), ("cmd", "cmd"), ("flags", "flags"), ("inf", "include"), ("exf", "exclude")))
    text = f"<b>{escape(feed['name'])}</b> {'⏸' if feed.get('paused') else '▶'}\n{fields}"
    short = feed_id.split(":", 1)[1]
    return text, kb([("⏯ Pause/Resume", f"rs|{uid}|tg|{short}"), ("✏ Edit", f"rs|{uid}|ed|{short}")],
                    [("🔎 Latest 5", f"rs|{uid}|lt|{short}"), ("🗑 Delete", f"rs|{uid}|del|{short}")],
                    [("⬅ Back", f"rs|{uid}|list|-")])


@app.command("rss", flt=authorized)
async def rss_cmd(_: Client, message: Message) -> None:
    if message.from_user:
        await send(message.chat.id, *menu(message.from_user.id), reply_to=message.id)


@app.callback("rs")
async def rss_cb(_: Client, query: CallbackQuery) -> None:
    _, owner, action, arg = str(query.data).split("|", 3)
    uid = int(owner)
    if query.from_user.id != uid:
        await query.answer("Open your own /rss", show_alert=True)
        return
    await query.answer()
    msg = query.message
    feed_id = f"{uid}:{arg}"
    page: tuple[str, Any] = menu(uid)
    if action == "x":
        await msg.delete()
        return
    if action == "add":
        reply = await ask(msg, uid, "Send: <code>name url [-c /mirror] [-inf 1080p,720p;x265] [-exf cam] "
                                    "[-f \"-up rc\"]</code>")
        if reply and reply.text:
            try:
                spec = parse_feed_spec(reply.text)
                feed = {"_id": f"{uid}:{spec['name']}", "user_id": uid, "chat_id": msg.chat.id, "paused": False,
                        **spec}
                await check_feed(feed)
                page = await feed_page(uid, feed["_id"])
            except Exception as exc:  # noqa: BLE001 - invalid spec or unreachable feed
                await send(msg.chat.id, f"⚠️ {escape(str(exc))}")
            await reply.delete()
    elif action == "list":
        feeds = await app.db.feeds(uid)
        rows = grid([(f"{'⏸' if f.get('paused') else '▶'} {f['name']}", f"rs|{uid}|open|{f['_id'].split(':', 1)[1]}")
                     for f in feeds], 2)
        page = (f"<b>Your feeds</b> ({len(feeds)})", kb(*rows, [("⬅ Back", f"rs|{uid}|menu|-")]))
    elif action in {"pa", "ra"}:
        for feed in await app.db.feeds(uid):
            feed["paused"] = action == "pa"
            await app.db.put_feed(feed)
    elif action == "open":
        page = await feed_page(uid, feed_id)
    elif action in {"tg", "ed", "del", "lt"}:
        feed = next((f for f in await app.db.feeds(uid) if f["_id"] == feed_id), None)
        if feed and action == "tg":
            feed["paused"] = not feed.get("paused")
            await app.db.put_feed(feed)
        elif feed and action == "ed":
            reply = await ask(msg, uid, "Send fields to change, one per line: <code>cmd=/leech\ninf=1080p\nexf=cam\n"
                                        "flags=-z\nurl=https://…</code>")
            if reply and reply.text:
                for line in reply.text.splitlines():
                    key, _, value = line.partition("=")
                    if key.strip() in {"cmd", "inf", "exf", "flags", "url"}:
                        feed[key.strip()] = value.strip()
                await app.db.put_feed(feed)
                await reply.delete()
        elif feed and action == "del":
            await app.db.del_feed(feed_id)
            page = menu(uid)
        elif feed and action == "lt":
            entries = (await fetch(feed["url"]))[:5]
            await send(msg.chat.id, "\n\n".join(f"<b>{escape(str(e.get('title', '')))}</b>\n{escape(entry_link(e))}"
                                                for e in entries) or "Empty feed.")
        if action != "del":
            page = await feed_page(uid, feed_id)
    await edit(msg, *page)
