"""Task argument parser.

Syntax: ``/cmd [links...] [-flag [value]]...``. Flags may appear before or after links.
``BOOL`` flags take no value, ``VALUE`` flags take one token (``GREEDY`` ones take every
token up to the next flag/link, so unquoted names with spaces work), ``OPTIONAL`` flags
take a value only when the next token is neither a flag nor a link.
"""

import shlex
from dataclasses import dataclass, field
from typing import Literal

from Mirror.core.links import is_magnet, is_tg_link, is_url
from Mirror.core.utils import parse_size

Kind = Literal["bool", "value", "greedy", "optional"]

FLAGS: dict[str, tuple[str, Kind, str]] = {
    "-n": ("name", "greedy", "new name (files need extension)"),
    "-z": ("zip", "optional", "zip, optional password"),
    "-e": ("extract", "optional", "extract, optional password"),
    "-up": ("up", "value", "upload destination: gd[:id] rc[:remote:path] tg[:chat[:topic]] ddl[:sites]"),
    "-rcf": ("rcf", "greedy", "rclone flags --key:value|--flag"),
    "-au": ("auth_user", "value", "direct link username"),
    "-ap": ("auth_pass", "value", "direct link password"),
    "-h": ("headers", "greedy", "direct link headers Key: Value|Key2: Value"),
    "-s": ("select", "bool", "select files / yt-dlp quality before download"),
    "-i": ("multi", "value", "process N consecutive messages starting at the reply"),
    "-m": ("same_dir", "greedy", "put multi links into one folder"),
    "-b": ("bulk", "bool", "links from replied text/file, one per line"),
    "-sd": ("seed", "optional", "seed after download: ratio:minutes"),
    "-sp": ("split", "value", "leech split size, e.g. 1.9GB"),
    "-es": ("equal", "bool", "equal-size split parts"),
    "-t": ("thumb", "value", "thumbnail: Telegram/http link or 'none'"),
    "-tl": ("layout", "value", "thumbnail grid layout, e.g. 3x3"),
    "-sv": ("sample", "optional", "sample video duration:part (seconds)"),
    "-ss": ("shots", "optional", "screenshots count"),
    "-doc": ("doc", "bool", "upload as document"),
    "-med": ("media", "bool", "upload as media"),
    "-mg": ("group", "bool", "send split parts as media group"),
    "-ut": ("user_session", "bool", "upload with user session"),
    "-bt": ("bot_session", "bool", "upload with bot session"),
    "-f": ("force", "bool", "skip both queues"),
    "-fd": ("force_dl", "bool", "skip download queue"),
    "-fu": ("force_up", "bool", "skip upload queue"),
    "-opt": ("opts", "greedy", "yt-dlp/gallery-dl options key:value|key:value"),
    "-ns": ("ns", "greedy", "name substitution: pattern/replacement|pattern2"),
    "-cv": ("convert_video", "value", "convert videos to extension (mp4, mkv...)"),
    "-ca": ("convert_audio", "value", "convert audios to extension (mp3, flac...)"),
    "-j": ("join", "bool", "join split parts (.001/.002...) before processing"),
    "-ff": ("ffmpeg", "value", "quoted ffmpeg output args for every media file, e.g. \"-c:v libx265 -crf 28\""),
    "-hl": ("hybrid", "bool", "hybrid: leech to Telegram and mirror to cloud"),
}


class FlagError(ValueError):
    pass


@dataclass
class TaskArgs:
    links: list[str] = field(default_factory=list)
    name: str = ""
    zip: bool = False
    zip_pass: str = ""
    extract: bool = False
    extract_pass: str = ""
    up: str = ""
    rcf: str = ""
    auth_user: str = ""
    auth_pass: str = ""
    headers: str = ""
    select: bool = False
    multi: int = 0
    same_dir: str = ""
    bulk: bool = False
    seed: bool = False
    ratio: float | None = None
    seed_time: int | None = None
    split: int = 0
    equal: bool = False
    thumb: str = ""
    layout: str = ""
    sample: str = ""
    shots: int = 0
    doc: bool = False
    media: bool = False
    group: bool = False
    user_session: bool = False
    bot_session: bool = False
    force: bool = False
    force_dl: bool = False
    force_up: bool = False
    opts: str = ""
    ns: str = ""
    convert_video: str = ""
    convert_audio: str = ""
    join: bool = False
    ffmpeg: str = ""
    hybrid: bool = False

    @property
    def link(self) -> str:
        return self.links[0] if self.links else ""


def _is_link(token: str) -> bool:
    return is_url(token) or is_magnet(token) or is_tg_link(token)


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _assign(args: TaskArgs, attr: str, kind: Kind, value: str | None) -> None:
    if attr == "zip":
        args.zip, args.zip_pass = True, value or ""
    elif attr == "extract":
        args.extract, args.extract_pass = True, value or ""
    elif attr == "seed":
        args.seed = True
        ratio, _, minutes = (value or "").partition(":")
        args.ratio = float(ratio) if ratio else None
        args.seed_time = int(float(minutes) * 60) if minutes else None
    elif attr == "sample":
        args.sample = value or "60:4"
    elif attr == "shots":
        args.shots = int(value or 10)
    elif attr == "multi":
        args.multi = int(value or 0)
    elif attr == "split":
        args.split = parse_size(value or "0")
    elif kind == "bool":
        setattr(args, attr, True)
    else:
        setattr(args, attr, value or "")


def parse_args(text: str, has_command: bool = True) -> TaskArgs:
    """Parse a command message (``/cmd ...``) into :class:`TaskArgs`."""
    lines = text.strip().splitlines()
    head = _tokens(lines[0]) if lines else []
    if has_command and head:
        head = head[1:]
    tokens = head + [line.strip() for line in lines[1:] if line.strip()]
    args = TaskArgs()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token not in FLAGS:
            if token.startswith("-") and len(token) > 1 and not token[1:].replace(".", "").isdigit():
                raise FlagError(f"unknown flag {token}")
            args.links.append(token)
            continue
        attr, kind, _ = FLAGS[token]
        value: str | None = None
        if kind == "value":
            if index >= len(tokens):
                raise FlagError(f"{token} needs a value")
            value, index = tokens[index], index + 1
        elif kind == "greedy":
            taken = []
            while index < len(tokens) and tokens[index] not in FLAGS and not _is_link(tokens[index]):
                taken.append(tokens[index])
                index += 1
            if not taken:
                raise FlagError(f"{token} needs a value")
            value = " ".join(taken)
        elif kind == "optional" and index < len(tokens):
            nxt = tokens[index]
            if nxt not in FLAGS and not _is_link(nxt):
                value, index = nxt, index + 1
        try:
            _assign(args, attr, kind, value)
        except ValueError as exc:
            raise FlagError(f"bad value for {token}: {value}") from exc
    return args


def flags_help() -> str:
    return "\n".join(f"<code>{flag}</code> {desc}" for flag, (_, _, desc) in FLAGS.items())
