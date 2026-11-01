import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_GD_ID = re.compile(r"^[-\w]{19,}$")
_TG = re.compile(
    r"^(?:https?://)?(?:t|telegram)\.me/(?:(c)/)?([\w\d_]+)/(?:(\d+)/)?(\d+)(?:-(\d+))?/?(?:\?.*)?$"
)


@dataclass(frozen=True)
class TgLink:
    chat: int | str
    first: int
    last: int
    thread: int | None = None

    @property
    def ids(self) -> list[int]:
        return list(range(self.first, self.last + 1))


def is_url(text: str) -> bool:
    return bool(re.match(r"^(https?|ftp)://\S+$", text.strip(), re.I))


def is_magnet(text: str) -> bool:
    return text.strip().lower().startswith("magnet:?")


def is_torrent(text: str) -> bool:
    return is_magnet(text) or urlparse(text).path.lower().endswith(".torrent")


def is_nzb(text: str) -> bool:
    return urlparse(text).path.lower().endswith(".nzb")


def is_gdrive(text: str) -> bool:
    text = text.strip()
    if text.startswith(("mt:", "sa:", "tp:")):
        text = text[3:]
    return "drive.google.com" in text or "docs.google.com" in text or bool(_GD_ID.match(text))


def gdrive_id(link: str) -> str:
    link = link.strip()
    if link[:3] in {"mt:", "sa:", "tp:"}:
        link = link[3:]
    if _GD_ID.match(link):
        return link
    for pattern in (r"/d/([-\w]+)", r"/folders/([-\w]+)"):
        if match := re.search(pattern, link):
            return match.group(1)
    if ids := parse_qs(urlparse(link).query).get("id"):
        return ids[0]
    raise ValueError("Google Drive id not found in link")


def is_rclone(text: str) -> bool:
    text = text.strip()
    return bool(re.match(r"^(mrcc:)?[\w\- .]+:[^/]", text + "x")) and not is_url(text) and not is_magnet(text)


def is_tg_link(text: str) -> bool:
    return bool(_TG.match(text.strip())) or text.startswith("tg://openmessage")


def parse_tg_link(link: str) -> TgLink:
    link = link.strip()
    if link.startswith("tg://openmessage"):
        query = parse_qs(urlparse(link).query)
        chat = int((query.get("user_id") or query.get("chat_id") or ["0"])[0])
        msg = int(query.get("message_id", ["0"])[0])
        return TgLink(chat, msg, msg)
    match = _TG.match(link)
    if not match:
        raise ValueError("not a Telegram message link")
    private, chat, thread, first, last = match.groups()
    chat_ref: int | str = int(f"-100{chat}") if private else chat
    start = int(first)
    end = max(start, int(last)) if last else start
    return TgLink(chat_ref, start, end, int(thread) if thread else None)
