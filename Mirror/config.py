"""Features: direct-link, yt-dlp, aria2 and Telegram downloads; leech uploads with
splitting, thumbnails and document/media modes; Google Drive and archive helpers.
"""

import os


def _int(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _ids(name: str) -> set[int]:
    return {int(value.strip()) for value in os.getenv(name, "").split(",") if value.strip()}


class Config:
    # Telegram application credentials from my.telegram.org.
    API_ID = _int("API_ID")
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    OWNER_ID = _int("OWNER_ID")
    SUDO_USERS = _ids("SUDO_USERS")
    AUTHORIZED_CHATS = _ids("AUTHORIZED_CHATS")

    # MongoDB connection and database name.
    DATABASE_URL = os.getenv("DATABASE_URL", "mongodb://localhost:27017")
    DB_NAME = os.getenv("DB_NAME", "mirror_bot")

    # A user session raises the upload part limit to 4 GiB; bot sessions use 2 GiB.
    SESSION_STRING = os.getenv("SESSION_STRING", "").strip() or None
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
    LEECH_SPLIT_SIZE = _int("LEECH_SPLIT_SIZE", 4 * 1024**3 if SESSION_STRING else 2 * 1024**3)
    UPLOAD_AS_DOC = os.getenv("UPLOAD_AS_DOC", "false").lower() in {"1", "true", "yes"}
    GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")

    # Keep resource use bounded while allowing multiple transfers at once.
    CONCURRENT_TASKS = max(1, _int("CONCURRENT_TASKS", 2))
    PROGRESS_INTERVAL = max(1, _int("PROGRESS_INTERVAL", 5))
    ARIA2_RPC_URL = os.getenv("ARIA2_RPC_URL", "http://localhost:6800/jsonrpc")
    ARIA2_SECRET = os.getenv("ARIA2_SECRET", "")
    GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
