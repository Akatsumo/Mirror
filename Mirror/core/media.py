import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from Mirror.core.utils import run

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    video: bool = False
    audio: bool = False
    title: str = ""
    artist: str = ""


async def probe(path: Path) -> MediaInfo:
    code, out = await run("ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
                          str(path), tail=100_000)
    if code:
        return MediaInfo()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return MediaInfo()
    fmt = data.get("format", {})
    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
    info = MediaInfo(float(fmt.get("duration") or 0), title=tags.get("title", ""), artist=tags.get("artist", ""))
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic"):
            info.video, info.width, info.height = True, int(stream.get("width") or 0), int(stream.get("height") or 0)
        elif stream.get("codec_type") == "audio":
            info.audio = True
    return info


def kind_of(path: Path, info: MediaInfo) -> str:
    mime = mimetypes.guess_type(path.name)[0] or ""
    if path.suffix.lower() in PHOTO_EXT and path.stat().st_size < 10 * 1024**2:
        return "photo"
    if info.video and info.duration and (mime.startswith("video") or path.suffix.lower() in {".mkv", ".ts", ".m4v"}):
        return "video"
    if info.audio and not info.video and info.duration:
        return "audio"
    return "document"


async def _ffmpeg(*args: str) -> bool:
    code, _ = await run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args)
    return code == 0


async def thumbnail(path: Path, out: Path, duration: float, layout: str = "") -> Path | None:
    """Single frame (middle) or a ``CxR`` grid of evenly spaced frames."""
    if layout and "x" in layout:
        cols, rows = (max(1, int(v)) for v in layout.lower().split("x", 1))
        rate = cols * rows / max(duration, 1)
        ok = await _ffmpeg("-i", str(path), "-vf", f"fps={rate:.5f},scale=320:-1,tile={cols}x{rows}",
                           "-frames:v", "1", str(out))
    else:
        ok = await _ffmpeg("-ss", str(duration / 2), "-i", str(path), "-frames:v", "1", "-vf", "scale=320:-1",
                           str(out))
    return out if ok and out.exists() else None


async def screenshots(path: Path, folder: Path, count: int, duration: float) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    shots = []
    for index in range(1, count + 1):
        target = folder / f"{path.stem[:50]}_ss{index:02d}.jpg"
        if await _ffmpeg("-ss", str(duration * index / (count + 1)), "-i", str(path), "-frames:v", "1", str(target)):
            shots.append(target)
    return shots


async def sample(path: Path, out: Path, duration: float, total: int = 60, part: int = 4) -> Path | None:
    """Stitch ``total`` seconds made of ``part``-second slices spread across the video."""
    if duration <= total:
        return None
    every = duration / max(1, total // part)
    keep = f"lt(mod(t\\,{every:.3f})\\,{part})"
    ok = await _ffmpeg("-i", str(path), "-vf", f"select='{keep}',setpts=N/FRAME_RATE/TB",
                       "-af", f"aselect='{keep}',asetpts=N/SR/TB", "-c:v", "libx264", "-preset", "veryfast",
                       "-c:a", "aac", str(out))
    return out if ok else None


async def convert(path: Path, ext: str) -> Path:
    target = path.with_suffix(f".{ext.lstrip('.')}")
    if target == path:
        return path
    if await _ffmpeg("-i", str(path), "-map", "0", "-c", "copy", str(target)) or await _ffmpeg("-i", str(path),
                                                                                          str(target)):
        path.unlink(missing_ok=True)
        return target
    target.unlink(missing_ok=True)
    return path


async def split_video(path: Path, limit: int, duration: float, folder: Path, equal: bool = False) -> list[Path]:
    """Lossless time-based split keeping every part under ``limit`` bytes."""
    size = path.stat().st_size
    if size <= limit or duration <= 0:
        return [path]
    count = -(-size // limit)
    target_bytes = size / count if equal else limit * 0.97
    span = duration * target_bytes / size
    folder.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    start = 0.0
    while start < duration - 1:
        part = folder / f"{path.stem}.part{len(parts) + 1:03d}{path.suffix}"
        length = span
        for _ in range(4):
            if not await _ffmpeg("-ss", f"{start:.3f}", "-i", str(path), "-t", f"{length:.3f}", "-map", "0",
                                 "-c", "copy", "-avoid_negative_ts", "1", str(part)):
                raise RuntimeError(f"ffmpeg could not split {path.name}")
            if part.stat().st_size <= limit:
                break
            length *= 0.9
        else:
            raise RuntimeError(f"could not fit {path.name} parts under the split size")
        parts.append(part)
        start += (await probe(part)).duration or length
    path.unlink(missing_ok=True)
    return parts
