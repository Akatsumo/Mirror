import asyncio
import contextlib
from dataclasses import dataclass, field
from html import escape

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageNotModified, RPCError
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from Mirror.core.app import app
from Mirror.core.utils import human_size, new_id

Row = list[tuple[str, str]]
_answers: dict[tuple[int, int], asyncio.Future[Message | None]] = {}
_choices: dict[str, tuple[int, asyncio.Future[str | None]]] = {}
_selections: dict[str, "_Selection"] = {}
PAGE = 8


def kb(*rows: Row) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text, url=data) if data.startswith(("http://", "https://", "tg://"))
         else InlineKeyboardButton(text, callback_data=data[:64]) for text, data in row]
        for row in rows if row
    ])


def grid(items: Row, cols: int = 2) -> list[Row]:
    return [items[i:i + cols] for i in range(0, len(items), cols)]


async def send(chat_id: int | str, text: str, markup: InlineKeyboardMarkup | None = None,
               reply_to: int | None = None) -> Message | None:
    for _ in range(3):
        try:
            return await app.bot.send_message(chat_id, text[:4096], reply_markup=markup, reply_to_message_id=reply_to,
                                              disable_web_page_preview=True)
        except FloodWait as wait:
            await asyncio.sleep(float(wait.value) + 1)
        except RPCError:
            return None
    return None


async def edit(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
    for _ in range(3):
        try:
            await message.edit_text(text[:4096], reply_markup=markup, disable_web_page_preview=True)
            return True
        except MessageNotModified:
            return True
        except FloodWait as wait:
            await asyncio.sleep(float(wait.value) + 1)
        except RPCError:
            return False
    return False


async def delete(*messages: Message | None) -> None:
    for message in messages:
        if message:
            with contextlib.suppress(RPCError):
                await message.delete()

def mention(user_id: int, name: str = "") -> str:
    return f'<a href="tg://user?id={user_id}">{escape(name or str(user_id))}</a>'


async def ask(message: Message, user_id: int, prompt: str, timeout: int = 120) -> Message | None:
    """Prompt and wait for the next message from ``user_id`` in the same chat (``/cancel`` aborts)."""
    key = (message.chat.id, user_id)
    if old := _answers.pop(key, None):
        old.cancel()
    future: asyncio.Future[Message | None] = asyncio.get_running_loop().create_future()
    _answers[key] = future
    note = await send(message.chat.id, f"{prompt}\n\n<i>Send /cancel to abort · {timeout}s</i>")
    try:
        reply = await asyncio.wait_for(future, timeout)
    except (TimeoutError, asyncio.CancelledError):
        reply = None
    finally:
        _answers.pop(key, None)
        await delete(note)
    if reply and (reply.text or "").strip().lower().startswith("/cancel"):
        return None
    return reply


@app.message(filters.incoming & ~filters.service, group=-1)
async def _collect(_: Client, message: Message) -> None:
    if not message.from_user:
        return
    future = _answers.get((message.chat.id, message.from_user.id))
    if future and not future.done():
        future.set_result(message)
        message.stop_propagation()


async def choose(message: Message, user_id: int, text: str, options: Row, cols: int = 2,
                 timeout: int = 120) -> str | None:
    """Show ``options`` as buttons and return the chosen value (None on cancel/timeout)."""
    token = new_id(6)
    future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
    _choices[token] = (user_id, future)
    values = [value for _, value in options]
    rows = grid([(label, f"ch|{token}|{i}") for i, (label, _) in enumerate(options)], cols)
    note = await send(message.chat.id, text, kb(*rows, [("✖ Cancel", f"ch|{token}|x")]), reply_to=message.id)
    try:
        picked = await asyncio.wait_for(future, timeout)
    except TimeoutError:
        picked = None
    finally:
        _choices.pop(token, None)
        await delete(note)
    return values[int(picked)] if picked not in (None, "x") else None


@app.callback("ch")
async def _choice(_: Client, query: CallbackQuery) -> None:
    _, token, value = str(query.data).split("|", 2)
    entry = _choices.get(token)
    if not entry:
        await query.answer("Expired", show_alert=True)
        return
    if query.from_user.id != entry[0]:
        await query.answer("Not yours", show_alert=True)
        return
    await query.answer()
    if not entry[1].done():
        entry[1].set_result(value)


@dataclass
class _Selection:
    user_id: int
    files: list[tuple[str, str, int]]
    chosen: set[str]
    future: asyncio.Future[set[str] | None]
    page: int = 0
    message: Message | None = field(default=None, repr=False)

    def render(self, token: str) -> tuple[str, InlineKeyboardMarkup]:
        pages = max(1, -(-len(self.files) // PAGE))
        self.page %= pages
        chunk = self.files[self.page * PAGE:(self.page + 1) * PAGE]
        size = sum(s for i, _, s in self.files if i in self.chosen)
        text = (f"<b>Select files</b> · {len(self.chosen)}/{len(self.files)} · {human_size(size)}\n"
                f"Page {self.page + 1}/{pages}")
        rows = [[(f"{'✅' if fid in self.chosen else '⬜'} {name[-40:]} · {human_size(sz)}", f"fs|{token}|t|{fid}")]
                for fid, name, sz in chunk]
        nav = [("◀", f"fs|{token}|p|-1"), ("All", f"fs|{token}|a|1"), ("None", f"fs|{token}|a|0"),
               ("▶", f"fs|{token}|p|1")]
        return text, kb(*rows, nav, [("✔ Done", f"fs|{token}|d|0"), ("✖ Cancel", f"fs|{token}|x|0")])


async def select_files(message: Message | None, user_id: int, files: list[tuple[str, str, int]],
                       timeout: int = 600) -> set[str] | None:
    """Interactive file picker; ``files`` are ``(id, name, size)``; returns chosen ids or None (cancel).

    Without a message (RSS/restored tasks) or with a single file everything is selected."""
    if message is None or len(files) < 2:
        return {f[0] for f in files}
    token = new_id(6)
    sel = _Selection(user_id, files, {f[0] for f in files}, asyncio.get_running_loop().create_future())
    _selections[token] = sel
    text, markup = sel.render(token)
    sel.message = await send(message.chat.id, text, markup, reply_to=message.id)
    try:
        return await asyncio.wait_for(sel.future, timeout)
    except TimeoutError:
        return {f[0] for f in files}
    finally:
        _selections.pop(token, None)
        await delete(sel.message)


@app.callback("fs")
async def _select(_: Client, query: CallbackQuery) -> None:
    _, token, action, arg = str(query.data).split("|", 3)
    sel = _selections.get(token)
    if not sel or query.from_user.id != sel.user_id:
        await query.answer("Expired or not yours", show_alert=True)
        return
    await query.answer()
    if action == "t":
        sel.chosen ^= {arg}
    elif action == "p":
        sel.page += int(arg)
    elif action == "a":
        sel.chosen = {f[0] for f in sel.files} if arg == "1" else set()
    elif action in {"d", "x"}:
        if not sel.future.done():
            sel.future.set_result(sel.chosen if action == "d" and sel.chosen else None)
        return
    if sel.message:
        await edit(sel.message, *sel.render(token))
