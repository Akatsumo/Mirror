# Mirror Bot

A compact Python 3.11 async Telegram mirror/leech bot (Pyrogram + Motor).

**Features:** direct link, yt-dlp, aria2 and Telegram-file downloads; leech with split, thumbnail, prefix and document/media mode; optional user session for 4 GB uploads; Google Drive upload; zip/extract (7z, password); live `/status`; per-user settings in MongoDB.

## Setup

```sh
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Copy values from `.env.example` into your environment (see `Mirror/config.py`). `SESSION_STRING` is optional: with it the upload limit is 4 GiB, without it 2 GiB. Keep it secret.

```sh
python -m Mirror
```

Commands: `/mirror URL` (`--aria2` for aria2), `/ytdl URL`, `/leech URL` or reply to a file, `/status`, `/cancel ID`, `/usettings`, `/help`.

Install `7z` for encrypted archives. Google Drive needs `GOOGLE_SERVICE_ACCOUNT_FILE`. aria2 needs a running RPC service.
