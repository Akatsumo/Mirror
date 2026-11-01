from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from Mirror.config import Config, Size, coerce
from Mirror.core.app import app

USER_OPTIONS: dict[str, tuple[str, Any, str]] = {
    "as_doc": ("Upload as document", bool, "AS_DOCUMENT"),
    "media_group": ("Split parts as media group", bool, "MEDIA_GROUP"),
    "equal_split": ("Equal split parts", bool, "EQUAL_SPLITS"),
    "split_size": ("Leech split size", Size, "LEECH_SPLIT_SIZE"),
    "prefix": ("Leech prefix", str, "LEECH_PREFIX"),
    "suffix": ("Leech suffix", str, "LEECH_SUFFIX"),
    "remname": ("Remove words (regex a|b)", str, "LEECH_REMNAME"),
    "name_sub": ("Name substitution pat/rep|pat", str, "NAME_SUBSTITUTE"),
    "thumb_layout": ("Thumbnail grid layout (3x3)", str, "THUMBNAIL_LAYOUT"),
    "session": ("Upload session bot/user/mix", str, "UPLOAD_SESSION"),
    "leech_dest": ("Leech destination chat[:topic]", str, "LEECH_DUMP_CHAT"),
    "default_upload": ("Default mirror upload gd/rc/ddl", str, "DEFAULT_UPLOAD"),
    "gdrive_id": ("Google Drive folder id", str, "GDRIVE_ID"),
    "index_url": ("Index URL", str, "INDEX_URL"),
    "rclone_path": ("Rclone remote:path", str, "RCLONE_PATH"),
    "rclone_flags": ("Rclone flags", str, "RCLONE_FLAGS"),
    "stop_duplicate": ("Stop duplicates", bool, "STOP_DUPLICATE"),
    "user_creds": ("Use my rclone.conf/token by default", bool, "USER_CREDS"),
    "upload_paths": ("Named upload paths {name: dest}", dict[str, Any], "UPLOAD_PATHS"),
    "excluded_ext": ("Excluded extensions", list[str], "EXCLUDED_EXTENSIONS"),
    "ytdl_opts": ("yt-dlp options", dict[str, Any], "YT_DLP_OPTIONS"),
    "gallery_opts": ("gallery-dl options", dict[str, Any], "GALLERY_DL_OPTIONS"),
    "ddl_sites": ("DDL sites (gofile streamtape)", list[str], "DDL_SITES"),
    "gofile_token": ("Gofile token", str, "GOFILE_TOKEN"),
    "streamtape_login": ("Streamtape login", str, "STREAMTAPE_LOGIN"),
    "streamtape_key": ("Streamtape key", str, "STREAMTAPE_KEY"),
}
USER_FILES = {"thumb": "thumb.jpg", "rclone": "rclone.conf", "token": "token.pickle", "cookies": "cookies.txt"}
PRIVATE_FILES = ("rclone.conf", "token.pickle", "cookies.txt", ".netrc", "accounts.zip", "gallery-dl.conf",
                 "list_drives.txt")


@dataclass
class Prefs:
    user_id: int
    values: dict[str, Any] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    def __getattr__(self, key: str) -> Any:
        if key in USER_OPTIONS:
            return self.values[key]
        raise AttributeError(key)

    def file(self, kind: str) -> Path | None:
        return self.files.get(kind)


def user_dir(user_id: int) -> Path:
    return app.data / "users" / str(user_id)


def private(name: str) -> Path | None:
    path = app.data / name
    return path if path.exists() else None


def parse_option(key: str, raw: str) -> Any:
    return coerce(USER_OPTIONS[key][1], raw)


async def prefs(user_id: int) -> Prefs:
    doc = await app.db.user(user_id)
    values = {}
    for key, (_, kind, cfg) in USER_OPTIONS.items():
        default = getattr(Config, cfg)
        value = doc.get(key, default)
        if kind == dict[str, Any] and key in doc:
            value = {**default, **value}
        values[key] = value
    folder = user_dir(user_id)
    files = {kind: folder / name for kind, name in USER_FILES.items() if (folder / name).exists()}
    return Prefs(user_id, values, files)


async def save_user_file(user_id: int, kind: str, data: bytes | None) -> None:
    path = user_dir(user_id) / USER_FILES[kind]
    if data is None:
        path.unlink(missing_ok=True)
        await app.db.del_file(user_id, kind)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if kind == "thumb":
        data = await _fit_thumb(path)
    await app.db.put_file(user_id, kind, data)


async def _fit_thumb(path: Path) -> bytes:
    """Telegram thumbnails must be small JPEGs (≤320px)."""
    from Mirror.core.utils import run

    tmp = path.with_suffix(".tmp.jpg")
    code, _ = await run("ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vf",
                        "scale='min(320,iw)':-2", "-q:v", "4", str(tmp))
    if code == 0 and tmp.exists():
        tmp.replace(path)
    return path.read_bytes()


async def save_private_file(name: str, data: bytes | None) -> None:
    path = app.data / name
    if data is None:
        path.unlink(missing_ok=True)
        await app.db.del_file(0, name)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        await app.db.put_file(0, name, data)
    if name == "accounts.zip":
        _unpack_accounts()


def _unpack_accounts() -> None:
    import shutil
    import zipfile

    target = app.data / "accounts"
    shutil.rmtree(target, ignore_errors=True)
    source = app.data / "accounts.zip"
    if source.exists():
        target.mkdir(parents=True)
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                if member.filename.endswith(".json") and not member.is_dir():
                    (target / Path(member.filename).name).write_bytes(archive.read(member))


async def restore_files() -> None:
    """Write every stored user/private file from Mongo back to the data dir."""
    for doc in await app.db.files():
        owner, kind = doc["owner"], doc["kind"]
        path = app.data / kind if owner == 0 else user_dir(owner) / USER_FILES.get(kind, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(doc["data"])
    _unpack_accounts()
