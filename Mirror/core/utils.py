from pathlib import Path


def human_size(size: float) -> str:
    if size < 0:
        raise ValueError("size cannot be negative")
    amount = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.2f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.2f} TiB"


def human_time(seconds: float) -> str:
    remaining = max(0, int(seconds))
    hours, remaining = divmod(remaining, 3600)
    minutes, secs = divmod(remaining, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def progress_bar(current: int, total: int, width: int = 12) -> str:
    if total <= 0 or width <= 0:
        return "░" * max(0, width)
    ratio = min(1.0, max(0.0, current / total))
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def safe_split(source: Path, max_bytes: int, output_dir: Path | None = None) -> list[Path]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size <= max_bytes:
        return [source]
    target_dir = Path(output_dir) if output_dir else source.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    with source.open("rb") as src:
        index = 1
        while chunk := src.read(max_bytes):
            part = target_dir / f"{source.name}.part{index:03d}"
            with part.open("wb") as dst:
                dst.write(chunk)
            parts.append(part)
            index += 1
    return parts
