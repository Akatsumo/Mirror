import asyncio
import zipfile
from pathlib import Path


async def create_zip(files: list[Path], destination: Path, password: str | None = None) -> Path:
    if password:
        await _seven_zip(["a", "-y", f"-p{password}", str(destination), *(str(path) for path in files)])
    else:
        def write_archive() -> None:
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    archive.write(path, arcname=path.name)
        await asyncio.to_thread(write_archive)
    return destination


async def extract_archive(source: Path, destination: Path, password: str | None = None) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(source) and not password:
        def extract() -> None:
            root = destination.resolve()
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    target = (root / member.filename).resolve()
                    if not target.is_relative_to(root):
                        raise ValueError("Archive contains an unsafe path")
                archive.extractall(root)
        await asyncio.to_thread(extract)
    else:
        args = ["x", "-y", f"-o{destination}"]
        if password:
            args.append(f"-p{password}")
        args.append(str(source))
        await _seven_zip(args)
    return [path for path in destination.rglob("*") if path.is_file()]


async def _seven_zip(arguments: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        "7z", *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    output, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(output.decode(errors="replace")[-1200:] or "7z operation failed")
