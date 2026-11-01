import copy
import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

log = logging.getLogger(__name__)
Doc = dict[str, Any]


class _Cursor:
    def __init__(self, docs: list[Doc]) -> None:
        self._docs = docs

    async def to_list(self, length: int | None = None) -> list[Doc]:
        return self._docs[:length] if length else self._docs


class MemoryCollection:
    """In-memory stand-in for a Motor collection (used without DATABASE_URL and in tests)."""

    def __init__(self) -> None:
        self.docs: dict[Any, Doc] = {}

    @staticmethod
    def _match(doc: Doc, query: Doc) -> bool:
        return all(doc.get(k) == v for k, v in query.items())

    async def find_one(self, query: Doc) -> Doc | None:
        return next((copy.deepcopy(d) for d in self.docs.values() if self._match(d, query)), None)

    def find(self, query: Doc | None = None) -> _Cursor:
        return _Cursor([copy.deepcopy(d) for d in self.docs.values() if self._match(d, query or {})])

    async def update_one(self, query: Doc, update: Doc, upsert: bool = False) -> None:
        doc = next((d for d in self.docs.values() if self._match(d, query)), None)
        if doc is None:
            if not upsert:
                return
            doc = dict(query)
            self.docs[doc["_id"]] = doc
        for key, value in update.get("$set", {}).items():
            doc[key] = copy.deepcopy(value)
        for key in update.get("$unset", {}):
            doc.pop(key, None)
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value

    async def delete_one(self, query: Doc) -> None:
        for key, doc in list(self.docs.items()):
            if self._match(doc, query):
                del self.docs[key]
                return

    async def delete_many(self, query: Doc) -> None:
        self.docs = {k: d for k, d in self.docs.items() if not self._match(d, query)}


class DB:
    def __init__(self) -> None:
        self._client: AsyncIOMotorClient | None = None
        self._cols: dict[str, Any] = {}
        self.persistent = False

    async def connect(self, url: str, name: str) -> None:
        if url:
            self._client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=8000)
            await self._client.admin.command("ping")
            self._cols = {n: self._client[name][n] for n in ("config", "users", "files", "rss", "tasks")}
            self.persistent = True
        else:
            log.warning("DATABASE_URL empty: settings are kept in memory only")
            self._cols = {n: MemoryCollection() for n in ("config", "users", "files", "rss", "tasks")}

    def close(self) -> None:
        if self._client:
            self._client.close()

    def col(self, name: str) -> Any:
        return self._cols[name]

    async def _get(self, col: str, _id: Any) -> Doc:
        return await self.col(col).find_one({"_id": _id}) or {}

    async def _set(self, col: str, _id: Any, values: Doc) -> None:
        await self.col(col).update_one({"_id": _id}, {"$set": values}, upsert=True)

    async def overrides(self) -> Doc:
        return (await self._get("config", "bot")).get("values", {})

    async def set_override(self, key: str, value: Any) -> None:
        await self._set("config", "bot", {"values": {**await self.overrides(), key: value}})

    async def unset_override(self, key: str) -> None:
        values = await self.overrides()
        values.pop(key, None)
        await self._set("config", "bot", {"values": values})

    async def engine_opts(self, engine: str) -> Doc:
        return (await self._get("config", f"engine:{engine}")).get("values", {})

    async def set_engine_opt(self, engine: str, key: str, value: Any) -> None:
        await self._set("config", f"engine:{engine}", {"values": {**await self.engine_opts(engine), key: value}})

    async def user(self, user_id: int) -> Doc:
        doc = await self._get("users", user_id)
        doc.pop("_id", None)
        return doc

    async def update_user(self, user_id: int, **values: Any) -> None:
        await self._set("users", user_id, values)

    async def unset_user(self, user_id: int, *keys: str) -> None:
        await self.col("users").update_one({"_id": user_id}, {"$unset": dict.fromkeys(keys, "")})

    async def users(self, **query: Any) -> list[Doc]:
        return await self.col("users").find(query).to_list(None)

    async def add_usage(self, user_id: int, day: str, size: int) -> int:
        doc = await self.user(user_id)
        used = (doc.get("used", 0) if doc.get("used_day") == day else 0) + size
        await self.update_user(user_id, used=used, used_day=day)
        return used

    async def put_file(self, owner: int, kind: str, data: bytes) -> None:
        await self._set("files", f"{owner}:{kind}", {"owner": owner, "kind": kind, "data": data})

    async def get_file(self, owner: int, kind: str) -> bytes | None:
        return (await self._get("files", f"{owner}:{kind}")).get("data")

    async def del_file(self, owner: int, kind: str) -> None:
        await self.col("files").delete_one({"_id": f"{owner}:{kind}"})

    async def files(self, owner: int | None = None) -> list[Doc]:
        return await self.col("files").find({} if owner is None else {"owner": owner}).to_list(None)

    async def feeds(self, user_id: int | None = None) -> list[Doc]:
        return await self.col("rss").find({} if user_id is None else {"user_id": user_id}).to_list(None)

    async def put_feed(self, feed: Doc) -> None:
        await self._set("rss", feed["_id"], {k: v for k, v in feed.items() if k != "_id"})

    async def del_feed(self, feed_id: str) -> None:
        await self.col("rss").delete_one({"_id": feed_id})

    async def add_task(self, task_id: str, **values: Any) -> None:
        await self._set("tasks", task_id, values)

    async def remove_task(self, task_id: str) -> None:
        await self.col("tasks").delete_one({"_id": task_id})

    async def tasks(self) -> list[Doc]:
        return await self.col("tasks").find({}).to_list(None)

    async def clear_tasks(self) -> None:
        await self.col("tasks").delete_many({})
