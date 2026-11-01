from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from Mirror.config import Config


class Database:
    def __init__(self, url: str = Config.DATABASE_URL, name: str = Config.DB_NAME) -> None:
        self.client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
        self.db: AsyncIOMotorDatabase = self.client[name]
        self.settings = self.db["settings"]
        self.tasks = self.db["tasks"]

    async def get_settings(self, user_id: int) -> dict[str, Any]:
        document = await self.settings.find_one({"_id": user_id})
        return {key: value for key, value in (document or {}).items() if key != "_id"}

    async def set_setting(self, user_id: int, key: str, value: Any) -> None:
        await self.settings.update_one({"_id": user_id}, {"$set": {key: value}}, upsert=True)

    async def save_task(self, task_id: str, data: dict[str, Any]) -> None:
        await self.tasks.update_one({"_id": task_id}, {"$set": data}, upsert=True)

    async def close(self) -> None:
        self.client.close()
