"""Mirror: async Telegram mirror/leech bot.

Features
    downloads  direct (aiohttp, per-link auth) · aria2 · qBittorrent (+debrid cache) · SABnzbd
               yt-dlp · gallery-dl · JDownloader · Telegram files/links · Google Drive · rclone
    uploads    Telegram leech (split, thumbs, media groups, user/bot/mix session) · Google Drive
               (service accounts, clone/count/delete/search) · rclone · DDL (Gofile, Streamtape)
    tools      zip/extract (7z), samples, screenshots, conversion, rename rules, stop-duplicate
    control    status pages, queues, limits/quota, RSS, torrent search, user/bot settings,
               auth/sudo/ban, restore of incomplete tasks, live config overrides stored in Mongo

Every option is read from the environment once, may be overridden from Mongo (/bsetting)
and is accessed flat as ``Config.NAME``. Sections are the base classes of ``Config``.
"""

import json
import os
from typing import Any, get_type_hints


class Size(int):
    """Byte size; env/edits accept human forms such as ``2GB`` or ``1.5g``."""


class Telegram:
    API_ID: int = 0
    API_HASH: str = ""
    BOT_TOKEN: str = ""
    SESSION_STRING: str = ""
    OWNER_ID: int = 0
    CMD_SUFFIX: str = ""


class Database:
    DATABASE_URL: str = ""
    DATABASE_NAME: str = "mirror"


class Access:
    AUTHORIZED_CHATS: list[int] = []
    SUDO_USERS: list[int] = []
    FSUB_CHATS: list[int] = []


class Limits:
    MAX_USER_TASKS: int = 0
    DIRECT_LIMIT: Size = Size(0)
    TORRENT_LIMIT: Size = Size(0)
    NZB_LIMIT: Size = Size(0)
    YTDL_LIMIT: Size = Size(0)
    GDRIVE_LIMIT: Size = Size(0)
    CLONE_LIMIT: Size = Size(0)
    LEECH_LIMIT: Size = Size(0)
    STORAGE_THRESHOLD: Size = Size(0)
    DAILY_QUOTA: Size = Size(0)
    PLAYLIST_LIMIT: int = 0


class Queue:
    QUEUE_ALL: int = 0
    QUEUE_DOWNLOAD: int = 4
    QUEUE_UPLOAD: int = 4


class Status:
    STATUS_LIMIT: int = 4
    STATUS_UPDATE_INTERVAL: int = 10


class Paths:
    DOWNLOAD_DIR: str = "downloads"
    DATA_DIR: str = "data"


class Leech:
    LEECH_SPLIT_SIZE: Size = Size(0)
    AS_DOCUMENT: bool = False
    EQUAL_SPLITS: bool = False
    MEDIA_GROUP: bool = False
    LEECH_PREFIX: str = ""
    LEECH_SUFFIX: str = ""
    LEECH_REMNAME: str = ""
    LEECH_DUMP_CHAT: str = ""
    UPLOAD_SESSION: str = "mix"
    THUMBNAIL_LAYOUT: str = ""
    NAME_SUBSTITUTE: str = ""
    EXCLUDED_EXTENSIONS: list[str] = []


class Upload:
    DEFAULT_UPLOAD: str = "gd"
    STOP_DUPLICATE: bool = False
    UPLOAD_PATHS: dict[str, Any] = {}
    USER_CREDS: bool = False


class GDrive:
    GDRIVE_ID: str = ""
    IS_TEAM_DRIVE: bool = False
    USE_SERVICE_ACCOUNTS: bool = False
    INDEX_URL: str = ""
    DRIVES: list[str] = []


class Rclone:
    RCLONE_PATH: str = ""
    RCLONE_FLAGS: str = ""
    RCLONE_SERVE_URL: str = ""
    RCLONE_SERVE_PORT: int = 0
    RCLONE_SERVE_USER: str = ""
    RCLONE_SERVE_PASS: str = ""


class Torrent:
    TORRENT_ENGINE: str = "qbit"
    DIRECT_ENGINE: str = "aiohttp"
    TORRENT_TIMEOUT: int = 0
    QBIT_HOST: str = "http://localhost:8090"
    QBIT_USER: str = ""
    QBIT_PASS: str = ""
    ARIA2_HOST: str = "http://localhost"
    ARIA2_PORT: int = 6800
    ARIA2_SECRET: str = ""
    DEBRID_TOKEN: str = ""


class Usenet:
    SABNZBD_HOST: str = "http://localhost:8070"
    SABNZBD_API_KEY: str = ""


class JDownloader:
    JD_EMAIL: str = ""
    JD_PASS: str = ""
    JD_DEVICE: str = "mirror"


class Extractors:
    YT_DLP_OPTIONS: dict[str, Any] = {}
    GALLERY_DL_OPTIONS: dict[str, Any] = {}


class DDL:
    DDL_SITES: list[str] = ["gofile"]
    GOFILE_TOKEN: str = ""
    STREAMTAPE_LOGIN: str = ""
    STREAMTAPE_KEY: str = ""


class Resolvers:
    FILELION_API: str = ""
    STREAMWISH_API: str = ""
    TERABOX_COOKIE: str = ""


class Rss:
    RSS_CHAT: str = ""
    RSS_INTERVAL: int = 900


class Search:
    SEARCH_API_URL: str = ""
    SEARCH_PLUGINS: list[str] = []
    SEARCH_LIMIT: int = 50


class Misc:
    RESTORE_TASKS: bool = True
    LOG_LEVEL: str = "INFO"
    UPSTREAM_REPO: str = ""
    UPSTREAM_BRANCH: str = "main"


BOOT_ONLY = frozenset({"API_ID", "API_HASH", "BOT_TOKEN", "SESSION_STRING", "DATABASE_URL", "DATABASE_NAME",
                       "UPSTREAM_REPO", "UPSTREAM_BRANCH"})
SECRETS = frozenset({
    "API_HASH", "BOT_TOKEN", "SESSION_STRING", "DATABASE_URL", "QBIT_PASS", "ARIA2_SECRET", "DEBRID_TOKEN",
    "SABNZBD_API_KEY", "JD_PASS", "GOFILE_TOKEN", "STREAMTAPE_KEY", "RCLONE_SERVE_PASS", "FILELION_API",
    "STREAMWISH_API", "TERABOX_COOKIE",
})
TRUE = frozenset({"1", "true", "yes", "on", "y"})


def coerce(kind: Any, raw: Any) -> Any:
    """Convert ``raw`` (usually a string) to the option type ``kind``."""
    if not isinstance(raw, str):
        return Size(raw) if kind is Size else raw
    raw = raw.strip()
    if kind is bool:
        return raw.lower() in TRUE
    if kind is Size:
        from Mirror.core.utils import parse_size

        return Size(parse_size(raw))
    if kind is int:
        return int(raw or 0)
    if kind is float:
        return float(raw or 0)
    if kind == list[int]:
        return [int(x) for x in raw.replace(",", " " ).split()]
    if kind == list[str]:
        return [x for x in raw.replace(",", " " ).split() if x]
    if kind == dict[str, Any]:
        return json.loads(raw) if raw else {}
    return raw


class Config(Telegram, Database, Access, Limits, Queue, Status, Paths, Leech, Upload, GDrive, Rclone,
             Torrent, Usenet, JDownloader, Extractors, DDL, Resolvers, Rss, Search, Misc):
    @classmethod
    def sections(cls) -> dict[str, list[str]]:
        return {base.__name__: list(base.__annotations__) for base in cls.__bases__}

    @classmethod
    def types(cls) -> dict[str, Any]:
        hints: dict[str, Any] = {}
        for base in cls.__bases__:
            hints.update(get_type_hints(base))
        return hints

    @classmethod
    def default(cls, key: str) -> Any:
        for base in cls.__bases__:
            if key in base.__annotations__:
                return getattr(base, key)
        raise KeyError(key)

    @classmethod
    def load_env(cls, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        for key, kind in cls.types().items():
            value = env.get(key, "")
            setattr(cls, key, coerce(kind, value) if value.strip() else cls.default(key))

    @classmethod
    def set(cls, key: str, raw: Any) -> Any:
        kind = cls.types().get(key)
        if kind is None:
            raise KeyError(key)
        value = coerce(kind, raw)
        setattr(cls, key, value)
        return value

    @classmethod
    def reset(cls, key: str, env: dict[str, str] | None = None) -> Any:
        env = os.environ if env is None else env
        value = env.get(key, "")
        if value.strip():
            return cls.set(key, value)
        setattr(cls, key, cls.default(key))
        return getattr(cls, key)

    @classmethod
    def apply(cls, overrides: dict[str, Any]) -> list[str]:
        applied = []
        for key, value in overrides.items():
            if key in BOOT_ONLY or key not in cls.types():
                continue
            cls.set(key, value)
            applied.append(key)
        return applied

    @classmethod
    def show(cls, key: str) -> str:
        value = getattr(cls, key)
        if key in SECRETS:
            return "•••" if value else ""
        return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


Config.load_env()
