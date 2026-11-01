"""Archive tools: 7z-backed zip (password, volumes) and extraction of every supported/split format."""

import asyncio
import glob
import re
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

from Mirror.core.files import clean, is_archive, is_later_part
from Mirror.core.utils import files_in, run

_PERCENT = re.compile(r"(\d{1,3})%")


def _meter(total: int, report: Callable[[int], None] | None) -> Callable[[str], None]:
    def on_line(line: str) -> None:
        if report and (match := _PERCENT.search(line)):
            report(total * int(match.group(1)) // 100)
    return on_line


async def seven_zip(*args: str, total: int = 0, report: Callable[[int], None] | None = None,
                    cwd: Path | None = None) -> None:
    code, out = await run("7z", *args, "-y", "-bsp1", "-bso1", cwd=str(cwd) if cwd else None,
                          on_line=_meter(total, report))
    if code:
        if "Wrong password" in out or "Can not open encrypted archive" in out:
            raise ValueError("Wrong archive password")
        raise RuntimeError(out[-1200:] or "7z failed")


def _zip_plain(sources: list[Path], destination: Path, base: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for source in sources:
            for path in files_in(source):
                archive.write(path, arcname=str(path.relative_to(base)))


async def create_zip(sources: list[Path], destination: Path, password: str = "", split: int = 0,
                     report: Callable[[int], None] | None = None) -> list[Path]:
    """Zip ``sources`` into ``destination``; with ``split`` produce ``.zip.001`` volumes."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    base = sources[0].parent
    if not shutil.which("7z"):
        if password or split:
            raise RuntimeError("7z is required for password or split archives")
        await asyncio.to_thread(_zip_plain, sources, destination, base)
        return [destination]
    total = sum(sum(f.stat().st_size for f in files_in(s)) for s in sources)
    args = ["a", "-tzip", "-mx=0" if total > 2 * 1024**3 else "-mx=5", str(destination)]
    if password:
        args += [f"-p{password}", "-mem=AES256"]
    if split:
        args.append(f"-v{split}b")
    await seven_zip(*args, *(str(s.relative_to(base)) for s in sources), total=total, report=report, cwd=base)
    return sorted(destination.parent.glob(f"{glob.escape(destination.name)}*"))


def _extract_plain(source: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            if not (root / member.filename).resolve().is_relative_to(root):
                raise ValueError("Archive contains an unsafe path")
        archive.extractall(root)


async def extract_archive(source: Path, destination: Path, password: str = "",
                          report: Callable[[int], None] | None = None) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    if not shutil.which("7z"):
        await asyncio.to_thread(_extract_plain, source, destination)
    else:
        await seven_zip("x", f"-o{destination}", f"-p{password}", str(source), total=source.stat().st_size,
                        report=report)
    return files_in(destination)


def _volumes(first: Path) -> list[Path]:
    stem = re.sub(r"\.(part0*1\.rar|7z\.0*1|zip\.0*1|0*1|rar|zip)$", "", first.name, flags=re.I)
    return [p for p in first.parent.iterdir() if p == first or (p.name.startswith(f"{stem}.") and is_later_part(p))]


async def extract_all(path: Path, password: str = "", report: Callable[[int], None] | None = None) -> Path:
    """Extract ``path`` (archive or folder of archives) in place; source archives are removed."""
    if path.is_file():
        if not is_archive(path):
            return path
        target = path.parent / re.sub(r"(\.part0*1)?(\.[a-z0-9]{1,4}){1,2}$", "", path.name, flags=re.I)
        await extract_archive(path, target, password, report)
        for volume in _volumes(path):
            clean(volume)
        return target
    for archive in [p for p in files_in(path) if is_archive(p) and not is_later_part(p)]:
        if archive.exists():
            await extract_archive(archive, archive.parent, password, report)
            for volume in _volumes(archive):
                clean(volume)
    return path
