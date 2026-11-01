import asyncio
from pathlib import Path
from typing import Any

from Mirror.config import Config


def _upload_sync(path: Path, folder_id: str, credentials_file: str) -> str:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = Credentials.from_service_account_file(
        credentials_file, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    metadata: dict[str, Any] = {"name": path.name}
    if folder_id:
        metadata["parents"] = [folder_id]
    result = service.files().create(
        body=metadata,
        media_body=MediaFileUpload(str(path), resumable=True),
        fields="id,webViewLink",
    ).execute()
    return str(result.get("webViewLink") or f"https://drive.google.com/file/d/{result['id']}/view")


async def upload_to_drive(path: Path, folder_id: str = Config.GDRIVE_FOLDER_ID) -> str:
    if not Config.GOOGLE_SERVICE_ACCOUNT_FILE:
        raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_FILE to enable Google Drive uploads")
    return await asyncio.to_thread(_upload_sync, path, folder_id, Config.GOOGLE_SERVICE_ACCOUNT_FILE)
