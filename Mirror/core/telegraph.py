"""Minimal async Telegraph client: HTML → nodes, auto-split into linked pages."""

import json
from html.parser import HTMLParser
from typing import Any

import aiohttp

API = "https://api.telegra.ph"
TAGS = {"a", "aside", "b", "blockquote", "br", "code", "em", "figcaption", "figure", "h3", "h4", "hr", "i",
        "iframe", "img", "li", "ol", "p", "pre", "s", "strong", "u", "ul", "video"}
PAGE_LIMIT = 40_000
_token = ""


class _Nodes(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: list[Any] = []
        self.stack: list[dict[str, Any]] = []

    def _append(self, node: Any) -> None:
        (self.stack[-1].setdefault("children", []) if self.stack else self.root).append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = "b" if tag == "strong" else tag
        if tag not in TAGS:
            return
        node: dict[str, Any] = {"tag": tag}
        if wanted := {k: v for k, v in attrs if k in {"href", "src"} and v}:
            node["attrs"] = wanted
        self._append(node)
        if tag not in {"br", "hr", "img"}:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = "b" if tag == "strong" else tag
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._append(data)


def html_to_nodes(html: str) -> list[Any]:
    parser = _Nodes()
    parser.feed(html)
    parser.close()
    return parser.root


def chunks(blocks: list[str], limit: int = PAGE_LIMIT) -> list[str]:
    pages, current = [], ""
    for block in blocks:
        if current and len(current) + len(block) > limit:
            pages.append(current)
            current = ""
        current += block
    return [*pages, current] if current else pages


async def _call(session: aiohttp.ClientSession, method: str, **data: Any) -> Any:
    async with session.post(f"{API}/{method}", data=data) as response:
        payload = await response.json(content_type=None)
    if not payload.get("ok"):
        raise RuntimeError(f"Telegraph: {payload.get('error')}")
    return payload["result"]


async def publish(title: str, blocks: list[str], author: str = "Mirror") -> str:
    """Publish ``blocks`` (HTML snippets) across as many pages as needed; returns the first URL."""
    global _token
    pages = chunks(blocks)
    if not pages:
        raise ValueError("nothing to publish")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        if not _token:
            _token = (await _call(session, "createAccount", short_name=author, author_name=author))["access_token"]
        made = [await _call(session, "createPage", access_token=_token, title=title, author_name=author,
                            content=json.dumps(html_to_nodes(page))) for page in pages]
        for index, page in enumerate(made):
            nav = "".join(f'<a href="{made[i]["url"]}">{label}</a> ' for i, label in
                          ((index - 1, "◀ Prev"), (index + 1, "Next ▶")) if 0 <= i < len(made))
            if nav:
                await _call(session, "editPage", access_token=_token, path=page["path"], title=title,
                            author_name=author, content=json.dumps(html_to_nodes(pages[index] + f"<p>{nav}</p>")))
    return str(made[0]["url"])
