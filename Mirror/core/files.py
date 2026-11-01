import glob
import math
import os
import re
import shutil
from pathlib import Path

from Mirror.core.utils import files_in, safe_name

ARCHIVE_EXT = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".tbz2", ".txz", ".zst", ".iso",
               ".cab", ".arj", ".lzh", ".wim", ".dmg", ".xar", ".z")
_PART = re.compile(r"\.(?:part0*1\.rar|7z\.0*1|zip\.0*1|0*1)$", re.I)
_LATER_PART = re.compile(r"\.(?:part(?!0*1\.rar$)\d+\.rar|r\d{2,3}|z\d{2,3}|(?:7z\.|zip\.)?(?!0*1$)\d{3})$", re.I)


def substitute(name: str, rules: str) -> str:
    """Apply ``pattern/replacement|pattern2`` rules (missing replacement removes the match)."""
    for rule in filter(None, (r.strip() for r in rules.split("|"))):
        pattern, _, repl = rule.partition("/")
        try:
            name = re.sub(pattern, repl, name)
        except re.error:
            name = name.replace(pattern, repl)
    return name


def final_name(name: str, prefix: str = "", suffix: str = "", remname: str = "", rules: str = "") -> str:
    if rules:
        name = substitute(name, rules)
    if remname:
        name = substitute(name, "|".join(p.split("/")[0] for p in remname.split("|")))
    stem, ext = os.path.splitext(name)
    if len(ext) > 6:
        stem, ext = name, ""
    stem = re.sub(r"\s{2,}", " ", stem).strip()
    return safe_name(f"{prefix}{stem}{suffix}{ext}")


def apply_names(root: Path, prefix: str = "", suffix: str = "", remname: str = "", rules: str = "") -> Path:
    """Rename every file under ``root`` (and ``root`` itself if it is a file) in place."""
    if not (prefix or suffix or remname or rules):
        return root
    for path in files_in(root):
        new = path.with_name(final_name(path.name, prefix, suffix, remname, rules))
        if new != path and not new.exists():
            path.rename(new)
            if path == root:
                root = new
    return root


def excluded(path: Path, extensions: list[str]) -> bool:
    exts = {e.lower().lstrip(".") for e in extensions}
    return bool(exts) and path.suffix.lower().lstrip(".") in exts


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(ARCHIVE_EXT) or bool(_PART.search(name))


def is_later_part(path: Path) -> bool:
    return bool(_LATER_PART.search(path.name))


def split_sizes(total: int, limit: int, equal: bool) -> list[int]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if total <= limit:
        return [total]
    if equal:
        count = math.ceil(total / limit)
        base, extra = divmod(total, count)
        return [base + (1 if i < extra else 0) for i in range(count)]
    return [limit] * (total // limit) + ([total % limit] if total % limit else [])


def split_file(source: Path, limit: int, equal: bool = False, out: Path | None = None) -> list[Path]:
    """Byte split into ``name.001``, ``name.002``... (joinable with ``cat``)."""
    if not source.is_file():
        raise FileNotFoundError(source)
    sizes = split_sizes(source.stat().st_size, limit, equal)
    if len(sizes) == 1:
        return [source]
    folder = out or source.parent
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    with source.open("rb") as src:
        for index, size in enumerate(sizes, 1):
            part = folder / f"{source.name}.{index:03d}"
            with part.open("wb") as dst:
                left = size
                while left:
                    chunk = src.read(min(left, 8 << 20))
                    dst.write(chunk)
                    left -= len(chunk)
            parts.append(part)
    return parts


def join_parts(root: Path) -> list[Path]:
    """Join ``x.001, x.002...`` groups under ``root`` into ``x``; returns joined files."""
    joined = []
    for first in sorted(root.rglob("*.001")):
        base = first.with_suffix("")
        if is_archive(base) or base.exists():
            continue
        parts = sorted(first.parent.glob(f"{glob.escape(base.name)}.[0-9][0-9][0-9]"))
        with base.open("wb") as dst:
            for part in parts:
                with part.open("rb") as src:
                    shutil.copyfileobj(src, dst, 8 << 20)
        for part in parts:
            part.unlink()
        joined.append(base)
    return joined


def link_tree(source: Path, target: Path) -> Path:
    """Hard-link (fallback copy) ``source`` into ``target`` so seeding data stays untouched."""
    dest = target / source.name
    if source.is_file():
        target.mkdir(parents=True, exist_ok=True)
        _link(source, dest)
        return dest
    for path in files_in(source):
        item = dest / path.relative_to(source)
        item.parent.mkdir(parents=True, exist_ok=True)
        _link(path, item)
    return dest


def _link(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def clean(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def single_root(folder: Path) -> Path:
    """Return the only entry of ``folder`` when there is exactly one, else ``folder``."""
    entries = [p for p in folder.iterdir() if not p.name.startswith(".")] if folder.is_dir() else []
    return entries[0] if len(entries) == 1 else folder
