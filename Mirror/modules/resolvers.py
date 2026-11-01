"""Direct-link generators: one small coroutine per host, picked from a domain table."""

import base64
import random
import re
import string
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import NoReturn
from urllib.parse import parse_qs, quote, urlparse

import aiohttp

from Mirror.config import Config
from Mirror.core.task import TaskError

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


@dataclass
class Resolved:
    files: list[tuple[str, str]]
    headers: dict[str, str] = field(default_factory=dict)
    folder: str = ""


Resolver = Callable[[aiohttp.ClientSession, str], Awaitable[Resolved]]


async def _text(session: aiohttp.ClientSession, url: str, **kw: object) -> str:
    async with session.get(url, **kw) as response:  # type: ignore[arg-type]
        return await response.text()


async def _json(session: aiohttp.ClientSession, method: str, url: str, **kw: object) -> dict:
    async with session.request(method, url, **kw) as response:  # type: ignore[arg-type]
        return await response.json(content_type=None)


def _find(pattern: str, text: str, error: str) -> str:
    if match := re.search(pattern, text, re.S):
        return match.group(1)
    raise TaskError(error)


def fail(message: str) -> NoReturn:
    raise TaskError(message)


def one(url: str, name: str = "") -> Resolved:
    return Resolved([(url, name)])


async def mediafire(s: aiohttp.ClientSession, url: str) -> Resolved:
    if "/folder/" in url:
        key = url.split("/folder/")[1].split("/")[0]
        data = await _json(s, "GET", "https://www.mediafire.com/api/1.5/folder/get_content.php",
                           params={"folder_key": key, "content_type": "files", "response_format": "json"})
        files = data["response"]["folder_content"]["files"]
        return Resolved([((await mediafire(s, f["links"]["normal_download"])).files[0][0], f["filename"])
                         for f in files], folder=key)
    page = await _text(s, url)
    return one(_find(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"', page, "Mediafire file not found"))


async def gofile(s: aiohttp.ClientSession, url: str) -> Resolved:
    token = (await _json(s, "POST", "https://api.gofile.io/accounts"))["data"]["token"]
    content_id = urlparse(url).path.rstrip("/").split("/")[-1]
    headers = {"Authorization": f"Bearer {token}"}
    data = await _json(s, "GET", f"https://api.gofile.io/contents/{content_id}", params={"wt": "4fd6sg89d7s6"},
                       headers=headers)
    if data.get("status") != "ok":
        raise TaskError(f"Gofile: {data.get('status')}")
    node = data["data"]
    items = node["children"].values() if node.get("type") == "folder" else [node]
    files = [(c["link"], c["name"]) for c in items if c.get("type") == "file"]
    return Resolved(files, {"Cookie": f"accountToken={token}"}, node.get("name", ""))


async def pixeldrain(s: aiohttp.ClientSession, url: str) -> Resolved:
    kind, code = urlparse(url).path.strip("/").split("/")[:2]
    if kind == "l":
        data = await _json(s, "GET", f"https://pixeldrain.com/api/list/{code}")
        return Resolved([(f"https://pixeldrain.com/api/file/{f['id']}?download", f["name"]) for f in data["files"]],
                        folder=data.get("title", code))
    return one(f"https://pixeldrain.com/api/file/{code}?download")


async def onefichier(s: aiohttp.ClientSession, url: str) -> Resolved:
    link, _, password = url.partition("::")
    data = {"dl_no_ssl": "on", "dlinline": "on", **({"pass": password} if password else {})}
    async with s.post(link, data=data) as response:
        page = await response.text()
    if "you must wait" in page.lower():
        raise TaskError("1fichier: wait limit reached, try later")
    return one(_find(r'href="(https://[a-z0-9-]+\.1fichier\.com/[^"]+)"', page, "1fichier link not found"))


async def streamtape(s: aiohttp.ClientSession, url: str) -> Resolved:
    page = await _text(s, url.replace("/e/", "/v/"))
    match = re.search(r"robotlink'\)\.innerHTML\s*=\s*'([^']+)'\s*\+\s*\('([^']+)'\)((?:\.substring\(\d+\))*)", page)
    if not match:
        raise TaskError("Streamtape link not found")
    tail = match.group(2)
    for cut in re.findall(r"\d+", match.group(3)):
        tail = tail[int(cut):]
    return one(f"https:{match.group(1)}{tail}&stream=1")


async def doodstream(s: aiohttp.ClientSession, url: str) -> Resolved:
    parsed = urlparse(url)
    embed = f"{parsed.scheme}://{parsed.netloc}/e/{parsed.path.rstrip('/').split('/')[-1]}"
    page = await _text(s, embed)
    pass_md5 = _find(r"(/pass_md5/[^'\"]+)", page, "Doodstream token not found")
    base = await _text(s, f"{parsed.scheme}://{parsed.netloc}{pass_md5}", headers={"Referer": embed})
    noise = "".join(random.choices(string.ascii_letters + string.digits, k=10))
    title = re.search(r"<title>(.*?)(?: - DoodStream)?</title>", page)
    return Resolved([(f"{base}{noise}?token={pass_md5.split('/')[-1]}&expiry={int(time.time() * 1000)}",
                      f"{title.group(1).strip()}.mp4" if title else "")], {"Referer": embed})


async def wetransfer(s: aiohttp.ClientSession, url: str) -> Resolved:
    async with s.get(url) as response:
        final = str(response.url)
    parts = urlparse(final).path.strip("/").split("/")
    transfer, security = parts[1], parts[-1]
    data = await _json(s, "POST", f"https://wetransfer.com/api/v4/transfers/{transfer}/download",
                       json={"security_hash": security, "intent": "entire_transfer"})
    return one(data.get("direct_link") or fail("WeTransfer link expired"))


async def xfilesharing(s: aiohttp.ClientSession, url: str) -> Resolved:
    """send.cm, hxfile and other XFileSharing hosts: post the download form, read the redirect."""
    page = await _text(s, url)
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
    if not fields:
        raise TaskError("Download form not found")
    fields.setdefault("op", "download2")
    async with s.post(url, data=fields, allow_redirects=False) as response:
        if location := response.headers.get("Location"):
            return one(location)
        body = await response.text()
    return one(_find(r'href="(https?://[^"]+?(?:/d/|\.(?:mkv|mp4|avi|zip|rar|7z))[^"]*)"', body,
                     "Download link not found"))


async def linkbox(s: aiohttp.ClientSession, url: str) -> Resolved:
    token = urlparse(url).path.rstrip("/").split("/")[-1]
    files: list[tuple[str, str]] = []

    async def walk(pid: int, prefix: str) -> str:
        data = (await _json(s, "GET", "https://www.linkbox.to/api/file/share_out_list/",
                            params={"shareToken": token, "pageSize": 1000, "pid": pid}))["data"]
        if item := data.get("itemInfo"):
            files.append((item["url"], f"{item['name']}.{item.get('sub_type', '')}".rstrip(".")))
        for entry in data.get("list") or []:
            if entry.get("type") == "dir":
                await walk(entry["id"], f"{prefix}{entry['name']}/")
            elif entry.get("url"):
                files.append((entry["url"], f"{prefix}{entry['name']}.{entry.get('sub_type', '')}".rstrip(".")))
        return str(data.get("shareInfo", {}).get("name", ""))
    folder = await walk(0, "")
    return Resolved(files, folder=folder if len(files) > 1 else "")


async def terabox(s: aiohttp.ClientSession, url: str) -> Resolved:
    if not Config.TERABOX_COOKIE:
        raise TaskError("Terabox needs TERABOX_COOKIE (ndus=...)")
    headers = {"Cookie": Config.TERABOX_COOKIE}
    async with s.get(url, headers=headers) as response:
        final, page = str(response.url), await response.text()
    token = _find(r'fn%28%22([0-9A-Fa-f]+)%22%29', page, "Terabox jsToken not found")
    surl = parse_qs(urlparse(final).query).get("surl", [final.rstrip("/").split("/")[-1].lstrip("1")])[0]
    data = await _json(s, "GET", "https://www.terabox.com/share/list", headers=headers,
                       params={"app_id": "250528", "jsToken": token, "shorturl": surl, "root": "1"})
    if data.get("errno"):
        raise TaskError(f"Terabox error {data.get('errno')}")
    return Resolved([(f["dlink"], f["server_filename"]) for f in data["list"] if not int(f.get("isdir", 0))], headers)


async def krakenfiles(s: aiohttp.ClientSession, url: str) -> Resolved:
    page = await _text(s, url)
    action = _find(r'<form[^>]+id="dl-form"[^>]+action="([^"]+)"', page, "Krakenfiles form not found")
    token = _find(r'name="token" value="([^"]+)"', page, "Krakenfiles token not found")
    data = await _json(s, "POST", f"https:{action}" if action.startswith("//") else action, data={"token": token})
    return one(data["url"])


async def solidfiles(s: aiohttp.ClientSession, url: str) -> Resolved:
    return one(_find(r'"downloadUrl":"([^"]+)"', await _text(s, url), "Solidfiles link not found"))


async def uploadee(s: aiohttp.ClientSession, url: str) -> Resolved:
    return one(_find(r'id="d_l" href="([^"]+)"', await _text(s, url), "Upload.ee link not found"))


async def yandex(s: aiohttp.ClientSession, url: str) -> Resolved:
    data = await _json(s, "GET", "https://cloud-api.yandex.net/v1/disk/public/resources/download",
                       params={"public_key": url})
    return one(data.get("href") or fail("Yandex file not found"))


async def onedrive(_: aiohttp.ClientSession, url: str) -> Resolved:
    token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return one(f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content")


async def buzzheavier(s: aiohttp.ClientSession, url: str) -> Resolved:
    async with s.get(f"{url.rstrip('/')}/download", headers={"HX-Request": "true", "Referer": url},
                     allow_redirects=False) as response:
        target = response.headers.get("hx-redirect")
    if not target:
        raise TaskError("Buzzheavier link not found")
    return one(target if target.startswith("http") else f"https://buzzheavier.com{target}")

def _api_host(api: str, key_of: Callable[[], str]) -> Resolver:
    async def resolve(s: aiohttp.ClientSession, url: str) -> Resolved:
        key = key_of()
        if not key:
            raise TaskError("API key missing for this host")
        code = urlparse(url).path.rstrip("/").split("/")[-1]
        data = await _json(s, "GET", f"{api}/api/file/direct_link", params={"key": key, "file_code": code})
        versions = data.get("result", {}).get("versions") or []
        if not versions:
            raise TaskError(data.get("msg", "No direct link"))
        best = max(versions, key=lambda v: int(v.get("size", 0) or 0))
        return one(best["url"])
    return resolve


SITES: tuple[tuple[tuple[str, ...], Resolver], ...] = (
    (("mediafire.com",), mediafire),
    (("gofile.io",), gofile),
    (("pixeldrain.com", "pixeldra.in"), pixeldrain),
    (("1fichier.com",), onefichier),
    (("streamtape.", "strtape.", "streamta.pe", "strcloud."), streamtape),
    (("dood.", "doodstream.", "d000d.", "d0000d.", "ds2play.", "dooood."), doodstream),
    (("wetransfer.com", "we.tl"), wetransfer),
    (("send.cm", "hxfile.co", "racaty.", "1cloudfile.com", "usersdrive.com"), xfilesharing),
    (("linkbox.to", "lbx.to", "linkbox.cloud"), linkbox),
    (("terabox.", "1024tera.", "teraboxapp.", "4funbox.", "mirrobox.", "nephobox.", "momerybox."), terabox),
    (("krakenfiles.com",), krakenfiles),
    (("solidfiles.com",), solidfiles),
    (("upload.ee",), uploadee),
    (("disk.yandex.", "yadi.sk"), yandex),
    (("1drv.ms", "onedrive.live.com"), onedrive),
    (("buzzheavier.com",), buzzheavier),
    (("filelions.", "vidhide", "alions."), _api_host("https://api.filelions.com", lambda: Config.FILELION_API)),
    (("streamwish.", "embedwish.", "wishembed.", "swdyu."),
     _api_host("https://api.streamwish.com", lambda: Config.STREAMWISH_API)),
)


def resolver_for(url: str) -> Resolver | None:
    host = (urlparse(url).hostname or "").lower()
    return next((fn for domains, fn in SITES if any(d in host for d in domains)), None)


async def resolve(url: str) -> Resolved | None:
    """Return direct links for a supported host, ``None`` when the URL is not a known host."""
    fn = resolver_for(url)
    if fn is None:
        return None
    async with aiohttp.ClientSession(headers=UA, timeout=aiohttp.ClientTimeout(total=60)) as session:
        try:
            result = await fn(session, url)
        except (aiohttp.ClientError, KeyError, ValueError, IndexError) as exc:
            raise TaskError(f"Could not resolve {urlparse(url).hostname}: {exc}") from exc
    if not result.files:
        raise TaskError("No downloadable files found")
    return Resolved([(quote(u, safe=":/?&=%#@+!~,;") if " " in u else u, n) for u, n in result.files],
                    {**UA, **result.headers}, result.folder)
