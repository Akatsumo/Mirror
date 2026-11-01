"""yt-dlp engine: in-process library run in a worker thread, quality buttons, audio formats."""

import asyncio
import threading
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from Mirror.config import Config
from Mirror.core.files import single_root
from Mirror.core.prefs import private
from Mirror.core.task import Status, Task, TaskError
from Mirror.core.ui import choose
from Mirror.core.utils import human_size, parse_opts

HEIGHTS = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)
AUDIO = (("MP3 320", "mp3", "320"), ("MP3 192", "mp3", "192"), ("MP3 128", "mp3", "128"), ("M4A", "m4a", "0"),
         ("OPUS", "opus", "0"), ("FLAC", "flac", "0"), ("WAV", "wav", "0"), ("ALAC", "alac", "0"),
         ("Vorbis", "vorbis", "0"))
PARTIAL = (".part", ".ytdl", ".temp")
IMAGES = (".webp", ".jpg", ".png")


def base_options(task: Task, cancel: threading.Event) -> dict[str, Any]:
    def hook(state: dict[str, Any]) -> None:
        if cancel.is_set():
            raise DownloadCancelled("cancelled")
        if state.get("status") == "downloading":
            total = state.get("total_bytes") or state.get("total_bytes_estimate") or 0
            task.progress(int(state.get("downloaded_bytes") or 0), int(total) or None, float(state.get("speed") or 0))

    name = task.args.name.rsplit(".", 1)[0] if task.args.name else "%(title,id).180B"
    opts: dict[str, Any] = {
        "outtmpl": {"default": str(task.dir / f"{name}.%(ext)s"),
                    "pl_video": str(task.dir / "%(playlist_title,playlist_id)s" / f"{name}.%(ext)s")},
        "quiet": True, "no_warnings": True, "noprogress": True, "progress_hooks": [hook], "retries": 10,
        "fragment_retries": 10, "concurrent_fragment_downloads": 8, "overwrites": True, "ignoreerrors": "only_download",
        "writethumbnail": True, "merge_output_format": "mkv", "trim_file_name": 200,
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                           {"key": "FFmpegMetadata", "add_metadata": True},
                           {"key": "EmbedThumbnail", "already_have_thumbnail": False}],
    }
    if cookies := (task.prefs.file("cookies") if task.prefs else None) or private("cookies.txt"):
        opts["cookiefile"] = str(cookies)
    if netrc := private(".netrc"):
        opts["usenetrc"], opts["netrc_location"] = True, str(netrc)
    if Config.PLAYLIST_LIMIT:
        opts["playlistend"] = Config.PLAYLIST_LIMIT
    return opts


def merged_options(task: Task) -> dict[str, Any]:
    user = task.prefs.ytdl_opts if task.prefs else Config.YT_DLP_OPTIONS
    return {**user, **parse_opts(task.args.opts)}


def _estimate(info: dict[str, Any], height: int) -> int:
    formats = info.get("formats") or []
    video = max((f.get("filesize") or f.get("filesize_approx") or 0 for f in formats
                 if f.get("height") == height and f.get("vcodec") != "none"), default=0)
    audio = max((f.get("filesize") or f.get("filesize_approx") or 0 for f in formats
                 if f.get("vcodec") == "none" and f.get("acodec") != "none"), default=0)
    return int(video + audio)


def quality_options(info: dict[str, Any]) -> list[tuple[str, str]]:
    heights = {f.get("height") for f in info.get("formats") or [] if f.get("vcodec") not in (None, "none")}
    playlist = info.get("_type") == "playlist"
    options = [("Best video", "v:best")]
    for height in HEIGHTS:
        if playlist or height in heights:
            size = "" if playlist else f" · {human_size(_estimate(info, height))}"
            options.append((f"{height}p{size}", f"v:{height}"))
    options += [(label, f"a:{codec}:{quality}") for label, codec, quality in AUDIO]
    return options


def apply_choice(opts: dict[str, Any], choice: str) -> None:
    kind, _, rest = choice.partition(":")
    if kind == "v":
        opts["format"] = "bv*+ba/b" if rest == "best" else f"bv*[height<={rest}]+ba/b[height<={rest}]"
        return
    codec, _, quality = rest.partition(":")
    opts["format"] = "ba/b"
    opts.pop("merge_output_format", None)
    pp = {"key": "FFmpegExtractAudio", "preferredcodec": codec, "preferredquality": quality}
    opts["postprocessors"] = [pp, *opts["postprocessors"]]
    if codec in {"wav", "alac"}:
        opts["postprocessors"] = [p for p in opts["postprocessors"] if p["key"] != "EmbedThumbnail"]


async def _info(url: str, opts: dict[str, Any]) -> dict[str, Any]:
    def extract() -> dict[str, Any]:
        with YoutubeDL({**opts, "extract_flat": "in_playlist", "skip_download": True}) as ydl:
            return ydl.extract_info(url, download=False) or {}
    try:
        return await asyncio.to_thread(extract)
    except DownloadError as exc:
        raise TaskError(str(exc).replace("ERROR: ", "")[:500]) from exc


async def download(task: Task) -> Path:
    task.dir.mkdir(parents=True, exist_ok=True)
    cancel = threading.Event()
    task.cancel_hooks.append(cancel.set)
    opts = {**base_options(task, cancel), **merged_options(task)}
    task.set_status(Status.WAIT)
    info = await _info(task.args.link, opts)
    title = info.get("title") or info.get("id") or "yt-dlp"
    entries = info.get("entries") or []
    if Config.PLAYLIST_LIMIT and len(entries) > Config.PLAYLIST_LIMIT:
        raise TaskError(f"Playlist has {len(entries)} items; limit is {Config.PLAYLIST_LIMIT}")
    await task.meta(task.args.name or str(title), int(info.get("filesize") or info.get("filesize_approx") or 0))
    if "format" not in opts and task.message:
        choice = await choose(task.message, task.user_id, f"<b>{title}</b>\nChoose quality:", quality_options(info), 3)
        if choice is None:
            raise TaskError("Quality selection cancelled")
        apply_choice(opts, choice)
    task.set_status(Status.DOWNLOAD)

    def fetch() -> None:
        with YoutubeDL(opts) as ydl:
            if ydl.download([task.args.link]) and not any(task.dir.rglob("*")):
                raise TaskError("yt-dlp finished without files")
    try:
        await asyncio.to_thread(fetch)
    except DownloadCancelled as exc:
        raise asyncio.CancelledError from exc
    except DownloadError as exc:
        raise TaskError(str(exc).replace("ERROR: ", "")[:500]) from exc
    finally:
        cancel.set()
    files = [p for p in task.dir.rglob("*") if p.is_file()]
    stems = {p.with_suffix("") for p in files if p.suffix.lower() not in IMAGES + PARTIAL}
    for path in files:
        suffix = path.suffix.lower()
        if suffix in PARTIAL or (suffix in IMAGES and path.with_suffix("") in stems):
            path.unlink()
    if not any(p.is_file() for p in task.dir.rglob("*")):
        raise TaskError("yt-dlp produced no files")
    return single_root(task.dir)
