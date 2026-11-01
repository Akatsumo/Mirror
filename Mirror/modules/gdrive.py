"""Google Drive: upload/download/clone/count/delete/search with service-account rotation,
token fallback, resumable chunked transfers, bounded parallelism and retry/backoff."""

import asyncio
import io
import json
import logging
import mimetypes
import pickle
import random
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from Mirror.config import Config
from Mirror.core.app import app
from Mirror.core.files import excluded
from Mirror.core.links import gdrive_id
from Mirror.core.task import Status, Task, TaskError, Uploaded
from Mirror.core.utils import safe_name

logging.getLogger("googleapiclient.discovery").setLevel(logging.ERROR)
SCOPE = ["https://www.googleapis.com/auth/drive"]
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
CHUNK = 64 * 1024**2
WORKERS = 4
TRANSIENT = {408, 429, 500, 502, 503, 504}
RATE = {"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded", "downloadQuotaExceeded",
        "storageQuotaExceeded", "teamDriveFileLimitExceeded", "uploadLimitExceeded"}
EXPORT = {
    "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                             ".docx"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                                ".xlsx"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
}
FIELDS = "id, name, mimeType, size, shortcutDetails, driveId"


class TransferCancelledError(Exception):
    pass


class _TokenUnpickler(pickle.Unpickler):
    """Only lets google-auth credential classes through (token.pickle files may come from users)."""

    SAFE = {("datetime", "datetime"), ("datetime", "timedelta"), ("datetime", "timezone"), ("_codecs", "encode"),
            ("copyreg", "_reconstructor"), ("copy_reg", "_reconstructor"), ("builtins", "object"),
            ("__builtin__", "object"), ("builtins", "tuple"), ("__builtin__", "tuple"), ("builtins", "set"),
            ("builtins", "frozenset")}

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) in self.SAFE:
            return super().find_class(module, name)
        if module.startswith(("google.oauth2.", "google.auth.")):
            found = super().find_class(module, name)
            if isinstance(found, type):
                return found
        raise pickle.UnpicklingError(f"blocked {module}.{name} in token file")


def load_token(path: Path) -> Credentials:
    data = path.read_bytes()
    if data.lstrip().startswith(b"{"):
        creds = Credentials.from_authorized_user_info(json.loads(data), SCOPE)
    else:
        creds = _TokenUnpickler(io.BytesIO(data)).load()
    if not isinstance(creds, Credentials):
        raise TaskError("token file does not hold OAuth credentials")
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def reason_of(error: HttpError) -> str:
    try:
        return str(json.loads(error.content)["error"]["errors"][0]["reason"])
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def accounts() -> list[Path]:
    return sorted((app.data / "accounts").glob("*.json"))


class Drive:
    def __init__(self, token: Path | None = None, use_sa: bool = False, progress: Callable[[int], None] | None = None,
                 skip_ext: Iterable[str] = ()) -> None:
        self.token = token
        self.accounts = accounts() if use_sa else []
        self.sa_index = random.randrange(len(self.accounts)) if self.accounts else 0
        self.switches = 0
        self.cancel = threading.Event()
        self.files = self.folders = 0
        self.root = ""
        self._progress = progress or (lambda _: None)
        self._skip = list(skip_ext)
        self._gen = 0
        self._local = threading.local()
        self._lock = threading.Lock()

    @property
    def using_sa(self) -> bool:
        return bool(self.accounts)

    @property
    def service(self) -> Any:
        if getattr(self._local, "gen", -1) != self._gen:
            self._local.service = build("drive", "v3", credentials=self._credentials(), cache_discovery=False)
            self._local.gen = self._gen
        return self._local.service

    def _credentials(self) -> Any:
        if self.accounts:
            path = str(self.accounts[self.sa_index])
            return service_account.Credentials.from_service_account_file(path, scopes=SCOPE)
        if self.token and self.token.exists():
            return load_token(self.token)
        raise TaskError("No Google Drive credentials: upload token.pickle or service accounts")

    def progress(self, amount: int) -> None:
        with self._lock:
            self._progress(amount)

    def _switch(self) -> bool:
        with self._lock:
            if not self.accounts or self.switches >= len(self.accounts) - 1:
                return False
            self.sa_index = (self.sa_index + 1) % len(self.accounts)
            self.switches += 1
            self._gen += 1
            return True

    def _fallback(self) -> bool:
        """Service accounts cannot see the item: retry once with token.pickle."""
        if self.accounts and self.token and self.token.exists():
            self.accounts, self._gen = [], self._gen + 1
            return True
        return False

    def _check(self) -> None:
        if self.cancel.is_set():
            raise TransferCancelledError

    def call(self, make: Callable[[Any], Any]) -> Any:
        """Execute a request built against the current service with backoff and SA rotation."""
        for attempt in range(8):
            self._check()
            try:
                return make(self.service).execute()
            except HttpError as err:
                reason = reason_of(err)
                if reason in RATE and self._switch():
                    continue
                if err.resp.status in TRANSIENT or reason in {"userRateLimitExceeded", "rateLimitExceeded"}:
                    time.sleep(min(2 ** attempt + random.random(), 40))
                    continue
                raise
            except (TimeoutError, ConnectionError, OSError):
                time.sleep(min(2 ** attempt, 30))
        raise TaskError("Google Drive keeps failing, try again later")

    def with_fallback(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except HttpError as err:
            if err.resp.status == 404 and self._fallback():
                return fn()
            raise

    def meta(self, file_id: str) -> dict[str, Any]:
        return self.call(lambda s: s.files().get(fileId=file_id, supportsAllDrives=True, fields=FIELDS))

    def resolve(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("mimeType") == SHORTCUT and (target := item.get("shortcutDetails", {}).get("targetId")):
            return {**self.meta(target), "name": item["name"]}
        return item

    def children(self, folder_id: str, query: str = "") -> list[dict[str, Any]]:
        items, token = [], None
        q = f"'{folder_id}' in parents and trashed = false{query}"
        while True:
            page = self.call(lambda s, t=token: s.files().list(
                q=q, supportsAllDrives=True, includeItemsFromAllDrives=True, pageSize=1000, pageToken=t,
                fields=f"nextPageToken, files({FIELDS})", orderBy="folder, name"))
            items += page.get("files", [])
            if not (token := page.get("nextPageToken")):
                return items

    def find(self, name: str, parent: str) -> dict[str, Any] | None:
        found = self.children(parent, f" and name = '{escape(name)}'")
        return found[0] if found else None

    def mkdir(self, name: str, parent: str) -> str:
        body = {"name": name, "mimeType": FOLDER, "parents": [parent]}
        folder = self.call(lambda s: s.files().create(body=body, supportsAllDrives=True, fields="id"))
        with self._lock:
            self.folders += 1
            self.root = self.root or str(folder["id"])
        return str(folder["id"])

    def share(self, file_id: str) -> None:
        if not Config.IS_TEAM_DRIVE:
            body = {"role": "reader", "type": "anyone"}
            self.call(lambda s: s.permissions().create(fileId=file_id, body=body, supportsAllDrives=True))

    def _pool(self, fn: Callable[[Any], Any], items: list[Any]) -> None:
        if len(items) < 2:
            for item in items:
                fn(item)
            return
        with ThreadPoolExecutor(WORKERS) as pool:
            for future in [pool.submit(fn, item) for item in items]:
                future.result()

    def upload(self, path: Path, parent: str) -> str:
        if path.is_file():
            return self._upload_file(path, parent)
        folder = self.mkdir(path.name, parent)
        entries = sorted(path.iterdir())
        for sub in (e for e in entries if e.is_dir()):
            self.upload(sub, folder)
        self._pool(lambda f: self._upload_file(f, folder), [e for e in entries if e.is_file() and not
                                                             excluded(e, self._skip)])
        return folder

    def _upload_file(self, path: Path, parent: str) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = {"name": path.name, "parents": [parent]}
        size = path.stat().st_size
        if not size:
            media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
            return str(self.call(lambda s: s.files().create(body=body, media_body=media, supportsAllDrives=True,
                                                            fields="id"))["id"])
        while True:
            sent, retries = 0, 0
            request = self.service.files().create(body=body, supportsAllDrives=True, fields="id",
                                                  media_body=MediaFileUpload(str(path), mimetype=mime, resumable=True,
                                                                             chunksize=CHUNK))
            try:
                response = None
                while response is None:
                    self._check()
                    try:
                        status, response = request.next_chunk()
                    except HttpError as err:
                        if err.resp.status in TRANSIENT and retries < 10:
                            retries += 1
                            time.sleep(min(2 ** retries, 30))
                            continue
                        raise
                    if status:
                        self.progress(status.resumable_progress - sent)
                        sent = status.resumable_progress
                self.progress(size - sent)
                with self._lock:
                    self.files += 1
                return str(response["id"])
            except HttpError as err:
                self.progress(-sent)
                if reason_of(err) in RATE and self._switch():
                    continue
                raise

    def download(self, item: dict[str, Any], dest: Path) -> Path:
        item = self.resolve(item)
        if item["mimeType"] != FOLDER:
            return self._download_file(item, dest)
        folder = dest / safe_name(item["name"])
        folder.mkdir(parents=True, exist_ok=True)
        entries = [self.resolve(e) for e in self.children(item["id"])]
        for sub in (e for e in entries if e["mimeType"] == FOLDER):
            self.download(sub, folder)
        self._pool(lambda e: self._download_file(e, folder),
                   [e for e in entries if e["mimeType"] != FOLDER and not excluded(Path(e["name"]), self._skip)])
        return folder

    def _download_file(self, item: dict[str, Any], dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        name, mime, file_id = safe_name(item["name"]), item["mimeType"], item["id"]
        if mime.startswith("application/vnd.google-apps"):
            export, ext = EXPORT.get(mime, ("application/pdf", ".pdf"))
            name = safe_name(name + ext)

            def make(s: Any) -> Any:
                return s.files().export_media(fileId=file_id, mimeType=export)
        else:
            def make(s: Any) -> Any:
                return s.files().get_media(fileId=file_id, supportsAllDrives=True, acknowledgeAbuse=True)
        target = dest / name
        while True:
            got = 0
            try:
                with target.open("wb") as handle:
                    loader, done = MediaIoBaseDownload(handle, make(self.service), chunksize=CHUNK), False
                    while not done:
                        self._check()
                        status, done = loader.next_chunk(num_retries=5)
                        self.progress(status.resumable_progress - got)
                        got = status.resumable_progress
                with self._lock:
                    self.files += 1
                return target
            except HttpError as err:
                self.progress(-got)
                if reason_of(err) in RATE and self._switch():
                    continue
                if reason_of(err) == "fileNotDownloadable" and mime.startswith("application/vnd.google-apps"):
                    export = "application/pdf"
                    continue
                raise

    def clone(self, item: dict[str, Any], parent: str) -> str:
        item = self.resolve(item)
        if item["mimeType"] != FOLDER:
            return self._copy(item, parent) or ""
        folder = self.mkdir(item["name"], parent)
        entries = [self.resolve(e) for e in self.children(item["id"])]
        for sub in (e for e in entries if e["mimeType"] == FOLDER):
            self.clone(sub, folder)
        self._pool(lambda e: self._copy(e, folder),
                   [e for e in entries if e["mimeType"] != FOLDER and not excluded(Path(e["name"]), self._skip)])
        return folder

    def _copy(self, item: dict[str, Any], parent: str) -> str | None:
        try:
            copied = self.call(lambda s: s.files().copy(fileId=item["id"], body={"parents": [parent]},
                                                        supportsAllDrives=True, fields="id"))
        except HttpError as err:
            if reason_of(err) == "cannotCopyFile":
                return None
            raise
        with self._lock:
            self.files += 1
        self.progress(int(item.get("size", 0)))
        return str(copied["id"])

    def count(self, item: dict[str, Any]) -> int:
        item = self.resolve(item)
        if item["mimeType"] != FOLDER:
            self.files += 1
            return int(item.get("size", 0))
        self.folders += 1
        return sum(self.count(e) for e in self.children(item["id"]))

    def delete(self, file_id: str) -> None:
        self.call(lambda s: s.files().delete(fileId=file_id, supportsAllDrives=True))

    def search(self, words: str, drive: str, exact: bool = False, kind: str = "") -> list[dict[str, Any]]:
        terms = [f"name = '{escape(words)}'"] if exact else [f"name contains '{escape(w)}'" for w in words.split()]
        if kind:
            terms.append(f"mimeType {'=' if kind == 'folders' else '!='} '{FOLDER}'")
        query = " and ".join([*terms, "trashed = false"])
        common = {"pageSize": 200, "fields": f"files({FIELDS})", "orderBy": "folder, name"}
        if drive == "root":
            make = lambda s: s.files().list(q=f"{query} and 'me' in owners", spaces="drive", **common)  # noqa: E731
        elif len(drive) <= 23:
            make = lambda s: s.files().list(q=query, corpora="drive", driveId=drive, supportsAllDrives=True,  # noqa: E731
                                            includeItemsFromAllDrives=True, **common)
        else:
            make = lambda s: s.files().list(q=f"'{drive}' in parents and {query}", supportsAllDrives=True,  # noqa: E731
                                            includeItemsFromAllDrives=True, **common)
        return list(self.call(make).get("files", []))


# --- async API ---------------------------------------------------------------------------------------

@dataclass
class Target:
    drive: Drive
    id: str
    index: str


def target_for(dest: str, prefs: Any, progress: Callable[[int], None] | None = None) -> Target:
    """``mt:`` user token · ``sa:`` service accounts · ``tp:`` owner token · bare → global default."""
    user_token = prefs.file("token") if prefs else None
    owner_token = app.data / "token.pickle"
    prefix, raw = (dest[:3], dest[3:]) if dest[:3] in {"mt:", "sa:", "tp:"} else ("", dest)
    if not prefix and user_token and prefs and prefs.values.get("user_creds"):
        prefix = "mt:"
    if prefix == "mt:" and not user_token:
        raise TaskError("Upload your token.pickle in /usettings first")
    token = user_token if prefix == "mt:" else owner_token
    use_sa = prefix == "sa:" or (not prefix and Config.USE_SERVICE_ACCOUNTS)
    skip = prefs.excluded_ext if prefs else Config.EXCLUDED_EXTENSIONS
    index = (prefs.index_url if prefs and prefix == "mt:" else Config.INDEX_URL).rstrip("/")
    return Target(Drive(token, use_sa, progress, skip), raw, index)


def links(item_id: str, name: str, folder: bool, index: str) -> tuple[str, str, str]:
    url = f"https://drive.google.com/{'drive/folders/' if folder else 'file/d/'}{item_id}"
    if not index:
        return url, "", ""
    path = f"{index}/{quote(name)}{'/' if folder else ''}"
    view = "" if folder else f"{path}?a=view"
    return url, path, view


def _bind(task: Task, target: Target) -> None:
    def bump(amount: int) -> None:
        task.progress(task.done + amount)
    target.drive._progress = bump
    task.cancel_hooks.append(target.drive.cancel.set)


async def _run(task: Task, fn: Callable[[], Any]) -> Any:
    try:
        return await asyncio.to_thread(fn)
    except TransferCancelledError as exc:
        raise asyncio.CancelledErrorError from exc
    except HttpError as err:
        raise TaskError(f"Google Drive: {reason_of(err) or err.reason}") from err


async def find_duplicate(name: str, dest: str, prefs: Any) -> str | None:
    target = target_for(dest or (prefs.gdrive_id if prefs else Config.GDRIVE_ID), prefs)
    found = await asyncio.to_thread(lambda: target.drive.with_fallback(lambda: target.drive.find(name, target.id)))
    return links(found["id"], name, found["mimeType"] == FOLDER, target.index)[0] if found else None


async def _guarded(task: Task, drive: Drive, fn: Callable[[], str]) -> str:
    """Run an upload/clone; on cancel or failure remove the partially created root folder."""
    def work() -> str:
        item_id = fn()
        drive.share(item_id)
        return item_id
    try:
        return str(await _run(task, work))
    except (asyncio.CancelledErrorError, TaskError):
        if drive.root:
            drive.cancel.clear()
            await asyncio.shield(asyncio.to_thread(drive.delete, drive.root))
        raise


async def upload(task: Task, path: Path, dest: str) -> Uploaded:
    target = target_for(dest or task.prefs.gdrive_id, task.prefs)
    if not target.id:
        raise TaskError("No Google Drive destination: set GDRIVE_ID or use -up gd:<folder id>")
    _bind(task, target)
    drive = target.drive
    item_id = await _guarded(task, drive, lambda: drive.upload(path, target.id))
    url, index, view = links(item_id, path.name, path.is_dir(), target.index)
    return Uploaded("Google Drive", url, index, view, drive.files, drive.folders)


def _source(link: str, dest: str = "") -> str:
    return link[:3] if link[:3] in {"mt:", "sa:", "tp:"} else dest


async def download(task: Task) -> Path:
    target = target_for(_source(task.args.link), task.prefs)
    file_id = gdrive_id(task.args.link)
    _bind(task, target)
    drive = target.drive
    meta = await _run(task, lambda: drive.with_fallback(lambda: drive.resolve(drive.meta(file_id))))
    size = await _run(task, lambda: drive.count(meta))
    drive.files = drive.folders = 0
    await task.meta(task.args.name or meta["name"], size)
    task.set_status(Status.DOWNLOAD, size)
    if task.args.name:
        meta["name"] = task.args.name
    return await _run(task, lambda: drive.download(meta, task.dir))


async def clone(task: Task, dest: str) -> Uploaded:
    dest = dest or task.prefs.gdrive_id
    target = target_for(dest if dest[:3] in {"mt:", "sa:", "tp:"} else _source(task.args.link) + dest, task.prefs)
    file_id = gdrive_id(task.args.link)
    _bind(task, target)
    drive = target.drive
    meta = await _run(task, lambda: drive.with_fallback(lambda: drive.resolve(drive.meta(file_id))))
    size = await _run(task, lambda: drive.count(meta))
    drive.files = drive.folders = 0
    await task.meta(meta["name"], size)
    task.set_status(Status.CLONE, size)
    item_id = await _guarded(task, drive, lambda: drive.clone(meta, target.id))
    url, index, view = links(item_id, meta["name"], meta["mimeType"] == FOLDER, target.index)
    return Uploaded("Google Drive", url, index, view, drive.files, drive.folders)


async def count(link: str, prefs: Any) -> tuple[str, bool, int, int, int]:
    drive = target_for(_source(link), prefs).drive
    meta = await asyncio.to_thread(lambda: drive.with_fallback(lambda: drive.resolve(drive.meta(gdrive_id(link)))))
    size = await asyncio.to_thread(drive.count, meta)
    return meta["name"], meta["mimeType"] == FOLDER, size, drive.files, max(0, drive.folders - 1)


async def delete(link: str, prefs: Any) -> None:
    drive = target_for(_source(link), prefs).drive
    await asyncio.to_thread(lambda: drive.with_fallback(lambda: drive.delete(gdrive_id(link))))


def drive_list(prefs: Any) -> list[tuple[str, str, str]]:
    """Configured search targets ``(name, id, index)`` from GDRIVE_ID, DRIVES and list_drives.txt."""
    entries = [("Main", Config.GDRIVE_ID, Config.INDEX_URL)] if Config.GDRIVE_ID else []
    lines = list(Config.DRIVES)
    if (path := app.data / "list_drives.txt").exists():
        lines += path.read_text().splitlines()
    for line in lines:
        parts = [p.strip() for p in line.replace("|", " " ).split()]
        if len(parts) >= 2:
            entries.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    if prefs and prefs.file("token") and prefs.gdrive_id:
        entries.append(("Mine", f"mt:{prefs.gdrive_id}", prefs.index_url))
    return entries


async def search(words: str, prefs: Any, kind: str = "") -> list[tuple[str, dict[str, Any], str]]:
    results = []
    for name, folder, index in drive_list(prefs):
        target = target_for(folder, prefs)
        try:
            found = await asyncio.to_thread(target.drive.search, words, target.id, False, kind)
        except (HttpError, TaskError):
            continue
        results += [(name, item, index) for item in found]
    return results
