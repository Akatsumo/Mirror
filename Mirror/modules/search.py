"""Torrent search through a Torrent-API compatible service or qBittorrent search plugins."""

import asyncio
import time
from html import escape
from typing import Any

import aiohttp
from pyrogram import Client
from pyrogram.types import Message

from Mirror.config import Config
from Mirror.core.app import app
from Mirror.core.auth import authorized
from Mirror.core.telegraph import publish
from Mirror.core.ui import choose, kb, send
from Mirror.core.utils import human_size
from Mirror.modules import qbit

Result = dict[str, Any]
_plugins_ready = False


async def api_sites() -> list[str]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session, session.get(
            f"{Config.SEARCH_API_URL.rstrip('/')}/api/v1/sites") as response:
        data = await response.json(content_type=None)
    return list(data.get("supported_sites") or data.get("sites") or [])


async def api_search(query: str, site: str) -> list[Result]:
    base = Config.SEARCH_API_URL.rstrip("/")
    url = f"{base}/api/v1/all/search" if site == "all" else f"{base}/api/v1/search"
    params = {"query": query, "limit": str(Config.SEARCH_LIMIT), **({"site": site} if site != "all" else {})}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as session, session.get(
            url, params=params) as response:
        data = await response.json(content_type=None)
    return [{"name": r.get("name", ""), "size": r.get("size", ""), "seeders": r.get("seeders", "?"),
             "leechers": r.get("leechers", "?"), "magnet": r.get("magnet", ""), "url": r.get("url", ""),
             "torrent": r.get("torrent", "")} for r in data.get("data", [])][:Config.SEARCH_LIMIT]


async def plugin_search(query: str) -> list[Result]:
    global _plugins_ready
    if not _plugins_ready and Config.SEARCH_PLUGINS:
        await qbit.call("search_install_plugin", sources=Config.SEARCH_PLUGINS)
        _plugins_ready = True
    job = await qbit.call("search_start", pattern=query, plugins="enabled", category="all")
    deadline = time.monotonic() + 40
    try:
        while time.monotonic() < deadline:
            status = (await qbit.call("search_status", search_id=job.id))[0]
            if status.status == "Stopped" or status.total >= Config.SEARCH_LIMIT:
                break
            await asyncio.sleep(2)
        results = (await qbit.call("search_results", search_id=job.id, limit=Config.SEARCH_LIMIT)).results
    finally:
        await qbit.call("search_delete", search_id=job.id)
    return [{"name": r.fileName, "size": human_size(int(r.fileSize or 0)) if int(r.fileSize or 0) > 0 else "?",
             "seeders": r.nbSeeders, "leechers": r.nbLeechers, "magnet": r.fileUrl if
             str(r.fileUrl).startswith("magnet:") else "", "torrent": "" if str(r.fileUrl).startswith("magnet:")
             else r.fileUrl, "url": r.descrLink} for r in results]

def to_blocks(results: list[Result]) -> list[str]:
    blocks = []
    for index, r in enumerate(results, 1):
        links = " | ".join(f'<a href="{escape(str(r[k]))}">{label}</a>' for k, label in
                           (("url", "Page"), ("torrent", "Torrent")) if r.get(k))
        magnet = f"<br><code>{escape(str(r['magnet']))}</code>" if r.get("magnet") else ""
        blocks.append(f"<p><b>{index}. {escape(str(r['name']))}</b><br>{r['size']} · S {r['seeders']} · "
                      f"L {r['leechers']}<br>{links}{magnet}</p>")
    return blocks


@app.command("search", "torsearch", flt=authorized)
async def search_cmd(_: Client, message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    uid = message.from_user.id if message.from_user else 0
    if len(parts) < 2:
        await send(message.chat.id, "Usage: /search query", reply_to=message.id)
        return
    query = parts[1]
    sources = ([("Search API", "api")] if Config.SEARCH_API_URL else []) + [("qBit plugins", "plugins")]
    source = sources[0][1] if len(sources) == 1 else await choose(message, uid, "Search with:", sources)
    if not source:
        return
    note = await send(message.chat.id, f"🔎 Searching <code>{escape(query)}</code>…", reply_to=message.id)
    try:
        if source == "api":
            sites = await api_sites()
            site = await choose(message, uid, "Choose site", [("All", "all"), *[(s, s) for s in sites]], 3)
            if not site:
                return
            results = await api_search(query, site)
        else:
            results = await plugin_search(query)
        if not results:
            text, markup = "No results.", None
        else:
            text = f"Found {len(results)} result(s) for <code>{escape(query)}</code>."
            markup = kb([("🔗 Open results", await publish(f"Torrents: {query}", to_blocks(results)))])
    except Exception as exc:  # noqa: BLE001 - reported to the user
        text, markup = f"⚠️ Search failed: {escape(str(exc))}", None
    if note:
        await note.edit_text(text, reply_markup=markup)
