import ast
import asyncio
import contextlib
import json
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_SIZE = re.compile(r"^\s*([\d.]+)\s*([kmgtp]?)(i?b?)\s*$", re.I)


def human_size(size: float) -> str:
    if size < 0:
        raise ValueError("size cannot be negative")
    amount = float(size)
    for unit in UNITS:
        if amount < 1024 or unit == UNITS[-1]:
            return f"{int(amount)} B" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} {UNITS[-1]}"


def human_time(seconds: float) -> str:
    left = max(0, int(seconds))
    parts = []
    for label, span in (("d", 86400), ("h", 3600), ("m", 60)):
        value, left = divmod(left, span)
        if value or parts:
            parts.append(f"{value}{label}")
    return " ".join([*parts, f"{left}s"])


def progress_bar(current: float, total: float, width: int = 12) -> str:
    if total <= 0 or width <= 0:
        return "░" * max(0, width)
    filled = round(min(1.0, max(0.0, current / total)) * width)
    return "█" * filled + "░" * (width - filled)


def parse_size(text: str | int) -> int:
    if isinstance(text, int):
        return text
    if not str(text).strip():
        return 0
    match = _SIZE.match(str(text))
    if not match:
        raise ValueError(f"invalid size: {text!r}")
    number, unit, _ = match.groups()
    return int(float(number) * 1024 ** " kmgtp".index(unit.lower() or " "))


def parse_value(text: str) -> Any:
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        lowered = text.lower()
        return {"true": True, "false": False, "none": None}.get(lowered, text)


def parse_opts(text: str) -> dict[str, Any]:
    """Options as JSON or ``key:value|key2:value2`` (values are Python literals when possible)."""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    result: dict[str, Any] = {}
    for item in text.split("|"):
        key, sep, value = item.partition(":")
        if key.strip() and sep:
            result[key.strip()] = parse_value(value.strip())
    return result


def new_id(length: int = 8) -> str:
    return secrets.token_hex(length // 2 + 1)[:length]


def safe_name(name: str, limit: int = 240) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "file"
    if len(name.encode()) <= limit:
        return name
    stem, dot, ext = name.rpartition(".")
    ext = f".{ext}" if dot and len(ext) < 10 else ""
    return (stem if dot else name).encode()[: limit - len(ext.encode())].decode(errors="ignore") + ext


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def files_in(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file())


async def run(*cmd: str, on_line: Callable[[str], None] | None = None, cwd: str | None = None,
              tail: int = 40) -> tuple[int, str]:
    """Run a command, stream \\r/\\n separated lines to ``on_line``; kill it when cancelled."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    lines: list[str] = []
    buffer = b""
    assert proc.stdout
    try:
        while chunk := await proc.stdout.read(4096):
            buffer += chunk
            *done, buffer = re.split(rb"[\r\n]", buffer)
            for raw in done:
                if line := raw.decode(errors="replace").strip():
                    lines = [*lines[-tail + 1:], line]
                    if on_line:
                        on_line(line)
        if buffer.strip():
            lines.append(buffer.decode(errors="replace").strip())
        return await proc.wait(), "\n".join(lines)
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


async def check_output(*cmd: str) -> str:
    code, out = await run(*cmd, tail=10_000)
    if code:
        raise RuntimeError(out[-1500:] or f"{cmd[0]} exited with {code}")
    return out
