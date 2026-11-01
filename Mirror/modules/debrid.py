"""Real-Debrid cached magnets: instant HTTP links when the torrent is already cached."""

import asyncio
from typing import Any

import aiohttp

from Mirror.config import Config

API = "https://api.real-debrid.com/rest/1.0"


async def _req(session: aiohttp.ClientSession, method: str, path: str, **data: Any) -> Any:
    async with session.request(method, f"{API}{path}", data=data or None) as response:
        if response.status == 204:
            return {}
        payload = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(payload.get("error", response.reason) if isinstance(payload, dict) else response.reason)
        return payload


async def cached_links(magnet: str, wait: float = 12) -> list[str]:
    """Return direct links for a cached magnet, or ``[]`` when it is not cached/disabled."""
    if not Config.DEBRID_TOKEN:
        return []
    headers = {"Authorization": f"Bearer {Config.DEBRID_TOKEN}"}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as session:
        torrent_id = (await _req(session, "POST", "/torrents/addMagnet", magnet=magnet))["id"]
        try:
            await _req(session, "POST", f"/torrents/selectFiles/{torrent_id}", files="all")
            for _ in range(int(wait / 2)):
                info = await _req(session, "GET", f"/torrents/info/{torrent_id}")
                if info.get("status") == "downloaded":
                    links = [await _req(session, "POST", "/unrestrict/link", link=link) for link in info["links"]]
                    return [item["download"] for item in links]
                if info.get("status") in {"magnet_error", "error", "virus", "dead"}:
                    return []
                await asyncio.sleep(2)
            return []
        finally:
            await _req(session, "DELETE", f"/torrents/delete/{torrent_id}")
