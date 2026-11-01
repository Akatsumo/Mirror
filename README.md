# Mirror

[![CI](https://github.com/OWNER/mirror/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Pyrogram](https://img.shields.io/badge/pyrogram-2.0-2CA5E0)
![MongoDB](https://img.shields.io/badge/mongo-motor-47A248)
![Ruff](https://img.shields.io/badge/lint-ruff-D7FF64)
![License](https://img.shields.io/badge/license-MIT-green)

Fast, fully asynchronous Telegram **mirror / leech** bot. Pyrogram + Motor + uvloop, one event loop,
blocking SDKs (Google, qBittorrent, aria2, JDownloader, yt-dlp) confined to worker threads.
Feature scope follows the best-known mirror/leech bots; every line here is an independent implementation.

## Features

| Area | What you get |
|---|---|
| **Direct links** | aiohttp streaming, **8 parallel ranged segments** for big files, per-link `-au/-ap` auth, `.netrc`, custom headers |
| **Link generators** | Mediafire (files+folders), Gofile, Pixeldrain, 1fichier, Streamtape, Doodstream, WeTransfer, send.cm/hxfile (XFileSharing), Linkbox, Terabox, Krakenfiles, Solidfiles, Upload.ee, Yandex Disk, OneDrive, Buzzheavier, FileLions, StreamWish (table-driven) |
| **Torrents** | qBittorrent (shared poller, metadata stage, file selector, stall re-announce, recheck, dead-torrent timeout, seed ratio/time) or aria2 (select files, seeding); Real-Debrid cached magnets |
| **Usenet** | SABnzbd: NZB links/files, file selection, server menu, global options |
| **Extractors** | yt-dlp (quality/audio buttons, cookies, netrc, thumbnail embed, per-user/task options), gallery-dl, JDownloader (links + DLC), Mega.nz (megatools) |
| **Telegram sources** | replied media, `-i N` consecutive messages, public/private/topic links and ranges via the optional user session |
| **Google Drive** | upload/download/clone/count/delete/search across drives, service-account rotation, token fallback, resumable chunked + parallel transfers, stop-duplicate, index/view links |
| **rclone** | download/upload/server-side clone, button remote browser, per-user `rclone.conf`, flags, file selection, `rclone serve http` of all remotes |
| **Leech** | split (lossless ffmpeg for video, byte split otherwise, equal parts), thumbnails incl. CxR grids, media groups, prefix/suffix/remname/substitution, destination chat/topic, bot/user/mix session by size (4 GB with premium session), hybrid leech+mirror |
| **DDL** | Gofile and Streamtape, several hosts concurrently |
| **Processing** | zip (password, volumes), extract any 7z-supported/split archive with password, join `.001` parts, ffmpeg custom args, video/audio conversion, sample clips, screenshots |
| **Control** | paged status with filters, step and per-user view, `/cancel_ID`, cancel-all by status, separate download/upload/total queues with force start, limits per engine, storage threshold, daily quota, task cap per user |
| **Settings** | `/usettings` (every option + thumbnail, rclone.conf, token.pickle, cookies in Mongo), `/bsetting` (live config, private files, aria2/qBit/SAB options, usenet servers) |
| **More** | RSS with include/exclude filters, torrent search (API + qBit plugins) on Telegraph, Drive search on Telegraph, auth/sudo/ban, force-subscribe, broadcast, shell/exec, stats/ping/log, restart with upstream update, restore of interrupted tasks |

## Architecture

```
Mirror/
├── __main__.py        boot: db → overrides → handlers → clients → restore
├── config.py          env-driven sections (base classes of Config), Mongo overrides
├── core/
│   ├── app.py         clients, registry, queues, handler decorators, session picker
│   ├── auth.py        roles, filters, force-sub, limits & quota
│   ├── db.py          Motor wrapper (+ in-memory fallback used by tests)
│   ├── files.py       rename rules, split/join, archive detection, hardlink trees
│   ├── flags.py       task flag parser
│   ├── links.py       link classification (Telegram, Drive, rclone, magnet…)
│   ├── media.py       ffprobe, thumbnails/grids, screenshots, samples, video split
│   ├── pipeline.py    queue → download → process → upload → report → seed
│   ├── prefs.py       per-user options/files, private files
│   ├── task.py        Task, Status, Registry, Limiter, TaskQueue
│   ├── telegraph.py   HTML → Telegraph pages
│   ├── ui.py          keyboards, ask(), choose(), file selector
│   └── utils.py       sizes, time, subprocess streaming, options parsing
└── modules/
    ├── direct.py resolvers.py debrid.py aria2.py qbit.py sabnzbd.py ytdlp.py gallery.py
    ├── jdownloader.py mega.py telegram.py gdrive.py rclone.py      (sources)
    ├── leech.py ddl.py archive.py                                  (outputs/processing)
    └── mirror.py status.py usettings.py bsettings.py rss.py search.py admin.py  (commands)
```

## Quick start

```bash
git clone <your fork> mirror && cd mirror
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # needs ffmpeg, 7z, rclone (optional: aria2c, qbittorrent-nox, megatools)
cp .env.example config.env               # fill API_ID, API_HASH, BOT_TOKEN, OWNER_ID, DATABASE_URL
bash start.sh                            # START_LOCAL_ENGINES=1 also launches aria2c + qbittorrent-nox
```

## Docker

```bash
cp .env.example .env                     # fill the Telegram keys
docker compose up -d --build             # bot + mongo + aria2 + qbittorrent
docker compose --profile usenet --profile jd up -d   # + sabnzbd + jdownloader
```

All engines mount `./downloads` at `/app/downloads`, so paths reported by qBittorrent/SABnzbd/JD are valid inside the bot.
Private files (`rclone.conf`, `token.pickle`, `accounts.zip`, `cookies.txt`, `.netrc`, `gallery-dl.conf`,
`list_drives.txt`) are uploaded with `/bsetting → Private files`, stored in Mongo and restored at boot.

## Commands

| Command | Purpose |
|---|---|
| `/mirror` `/m` · `/leech` `/l` | auto-detected engine (direct, generators, torrent, NZB, Drive, rclone, Mega, Telegram) |
| `/qbmirror` `/qbleech` · `/aria2mirror` `/aria2leech` | force the torrent engine |
| `/ytdl` `/ytdlleech` · `/gdl` `/gdlleech` | yt-dlp · gallery-dl |
| `/nzbmirror` `/nzbleech` · `/jdmirror` `/jdleech` | SABnzbd · JDownloader |
| `/clone` · `/count` · `/del` · `/list [-f\|-d] name` | Drive/rclone clone, count, delete (sudo), search |
| `/status [me]` · `/cancel ID` · `/cancel_ID` · `/cancelall` | status board and cancellation |
| `/usettings` · `/bsetting` · `/rss` · `/search query` | settings, owner settings, feeds, torrent search |
| `/authorize` `/unauthorize` `/addsudo` `/rmsudo` `/ban` `/unban` `/users` `/setquota` | access (id argument or reply; in groups without id the chat is used) |
| `/stats` `/ping` `/log` `/restart` `/shell` `/exec` `/broadcast` | admin (shell/exec/broadcast: owner) |

Every command accepts `CMD_SUFFIX` (e.g. `/mirror2`).

### Flags

Syntax: `/cmd [links…] [-flag [value]]…`. Links may also follow flags. Greedy flags (`-n -m -ns -opt -rcf -h`)
take every word up to the next flag/link, so names with spaces need no quotes. Optional-value flags
(`-z -e -sd -sv -ss`) take a value only when the next word is neither a flag nor a link, so put the link first
when using them without a value. Bulk files (`-b`) may carry flags per line; they override the command flags.

| Flag | Meaning |
|---|---|
| `-n` | new name (files need extension) |
| `-z` | zip, optional password |
| `-e` | extract, optional password |
| `-up` | upload destination: gd[:id] rc[:remote:path] tg[:chat[:topic]] ddl[:sites] |
| `-rcf` | rclone flags --key:value&#124;--flag |
| `-au` | direct link username |
| `-ap` | direct link password |
| `-h` | direct link headers Key: Value&#124;Key2: Value |
| `-s` | select files / yt-dlp quality before download |
| `-i` | process N consecutive messages starting at the reply |
| `-m` | put multi links into one folder |
| `-b` | links from replied text/file, one per line |
| `-sd` | seed after download: ratio:minutes |
| `-sp` | leech split size, e.g. 1.9GB |
| `-es` | equal-size split parts |
| `-t` | thumbnail: Telegram/http link or 'none' |
| `-tl` | thumbnail grid layout, e.g. 3x3 |
| `-sv` | sample video duration:part (seconds) |
| `-ss` | screenshots count |
| `-doc` | upload as document |
| `-med` | upload as media |
| `-mg` | send split parts as media group |
| `-ut` | upload with user session |
| `-bt` | upload with bot session |
| `-f` | skip both queues |
| `-fd` | skip download queue |
| `-fu` | skip upload queue |
| `-opt` | yt-dlp/gallery-dl options key:value&#124;key:value |
| `-ns` | name substitution: pattern/replacement&#124;pattern2 |
| `-cv` | convert videos to extension (mp4, mkv...) |
| `-ca` | convert audios to extension (mp3, flac...) |
| `-j` | join split parts (.001/.002...) before processing |
| `-ff` | quoted ffmpeg output args for every media file, e.g. "-c:v libx265 -crf 28" |
| `-hl` | hybrid: leech to Telegram and mirror to cloud |
Examples:

```text
/leech https://host/movie.mkv -n Movie (2024).mkv -sp 1.9GB -t https://i.imgur.com/x.jpg
/mirror magnet:?xt=… -s -sd 2:120 -up rc:media:Torrents
/mirror -z secret -up gd:1AbC…            (reply to a Telegram file)
/leech -i 10 -m Season 1 -mg             (reply to the first of 10 messages)
/ytdl https://youtu.be/x -opt playlistend:5|writesubtitles:True -up ddl:gofile,streamtape
/mirror https://t.me/c/1234567/10-20 -e pass
```

Upload destinations for `-up`: `gd`, `gd:<id>`, `mt:<id>` (your token.pickle), `sa:<id>`, `tp:<id>`,
`rc`, `rc:remote:path`, `mrcc:remote:path` (your rclone.conf), `ddl`, `ddl:gofile,streamtape`,
`tg:<chat>[:<topic>]`, a bare chat id, or a name from `UPLOAD_PATHS`/your `upload_paths`.

## Configuration

Values come from the environment (or `config.env` via `start.sh`). Everything not marked *boot only* can be
edited live in `/bsetting`; edits are typed, applied immediately and stored in Mongo (`config` collection).
Sizes accept `2GB`, `1.5g`, `700MiB`.

| Key | Default | Description |
|---|---|---|
| **Telegram** | | |
| `API_ID` | — | Telegram API id (my.telegram.org) *(boot only)* |
| `API_HASH` | — | Telegram API hash *(boot only)* |
| `BOT_TOKEN` | — | Bot token from @BotFather *(boot only)* |
| `SESSION_STRING` | — | Optional Pyrogram user session: private links + 4 GB uploads *(boot only)* |
| `OWNER_ID` | — | Owner user id |
| `CMD_SUFFIX` | — | Suffix for every command (multi-bot groups) |
| **Database** | | |
| `DATABASE_URL` | — | MongoDB URL; empty = in-memory (nothing persists) *(boot only)* |
| `DATABASE_NAME` | `mirror` | Mongo database name *(boot only)* |
| **Access** | | |
| `AUTHORIZED_CHATS` | — | Chat/user ids allowed to use the bot |
| `SUDO_USERS` | — | Sudo user ids |
| `FSUB_CHATS` | — | Chats users must join (force-subscribe) |
| **Limits** | | |
| `MAX_USER_TASKS` | — | Parallel tasks per user (0 = no limit) |
| `DIRECT_LIMIT` | — | Max size for direct/aria2/rclone/JD/mega |
| `TORRENT_LIMIT` | — | Max torrent size |
| `NZB_LIMIT` | — | Max NZB size |
| `YTDL_LIMIT` | — | Max yt-dlp/gallery-dl size |
| `GDRIVE_LIMIT` | — | Max Drive download size |
| `CLONE_LIMIT` | — | Max clone size |
| `LEECH_LIMIT` | — | Max leech size |
| `STORAGE_THRESHOLD` | — | Free disk to keep (task refused below) |
| `DAILY_QUOTA` | — | Bytes per user per day (override: /setquota) |
| `PLAYLIST_LIMIT` | — | Max playlist items |
| **Queue** | | |
| `QUEUE_ALL` | — | Total active transfers (0 = unlimited) |
| `QUEUE_DOWNLOAD` | `4` | Parallel downloads |
| `QUEUE_UPLOAD` | `4` | Parallel uploads |
| **Status** | | |
| `STATUS_LIMIT` | `4` | Tasks per status page |
| `STATUS_UPDATE_INTERVAL` | `10` | Status refresh seconds |
| **Paths** | | |
| `DOWNLOAD_DIR` | `downloads` | Working directory (wiped at boot) |
| `DATA_DIR` | `data` | Private files, user files, accounts |
| **Leech** | | |
| `LEECH_SPLIT_SIZE` | — | Part size (0 = max allowed: 2 GB bot, 4 GB premium user) |
| `AS_DOCUMENT` | — | Upload everything as document |
| `EQUAL_SPLITS` | — | Equal-size parts |
| `MEDIA_GROUP` | — | Send parts as album |
| `LEECH_PREFIX` | — | Filename prefix |
| `LEECH_SUFFIX` | — | Filename suffix (before extension) |
| `LEECH_REMNAME` | — | Regex words removed from names (a|b) |
| `LEECH_DUMP_CHAT` | — | Default leech destination chat[:topic] |
| `UPLOAD_SESSION` | `mix` | bot, user or mix (mix: user only for parts > 2 GB) |
| `THUMBNAIL_LAYOUT` | — | Multi-frame thumbnail grid, e.g. 3x3 |
| `NAME_SUBSTITUTE` | — | pattern/replacement|pattern2 |
| `EXCLUDED_EXTENSIONS` | — | Extensions never uploaded |
| **Upload** | | |
| `DEFAULT_UPLOAD` | `gd` | Mirror target: gd, rc or ddl |
| `STOP_DUPLICATE` | — | Refuse when the name already exists at destination |
| `UPLOAD_PATHS` | — | Named destinations {"movies": "rc:media:Movies"} |
| `USER_CREDS` | — | Use the user's own rclone.conf/token.pickle by default |
| **GDrive** | | |
| `GDRIVE_ID` | — | Drive folder / shared drive id (root = My Drive) |
| `IS_TEAM_DRIVE` | — | Destination is a shared drive (no public sharing) |
| `USE_SERVICE_ACCOUNTS` | — | Rotate data/accounts/*.json |
| `INDEX_URL` | — | Index base URL |
| `DRIVES` | — | Extra search targets: name|id|index |
| **Rclone** | | |
| `RCLONE_PATH` | — | Default remote:path |
| `RCLONE_FLAGS` | — | --key:value|--flag |
| `RCLONE_SERVE_URL` | — | Public base URL of rclone serve (links) |
| `RCLONE_SERVE_PORT` | — | Serve all remotes over HTTP (0 = off) |
| `RCLONE_SERVE_USER` | — | Serve basic-auth user |
| `RCLONE_SERVE_PASS` | — | Serve basic-auth password |
| **Torrent** | | |
| `TORRENT_ENGINE` | `qbit` | qbit or aria2 for /mirror torrents |
| `DIRECT_ENGINE` | `aiohttp` | aiohttp or aria2 for plain links |
| `TORRENT_TIMEOUT` | — | Seconds without progress before a torrent is dead (0 = never) |
| `QBIT_HOST` | `http://localhost:8090` | qBittorrent Web UI |
| `QBIT_USER` | — | qBittorrent user |
| `QBIT_PASS` | — | qBittorrent password |
| `ARIA2_HOST` | `http://localhost` | aria2 RPC host |
| `ARIA2_PORT` | `6800` | aria2 RPC port |
| `ARIA2_SECRET` | — | aria2 RPC secret |
| `DEBRID_TOKEN` | — | Real-Debrid token (cached magnets download over HTTP) |
| **Usenet** | | |
| `SABNZBD_HOST` | `http://localhost:8070` | SABnzbd URL |
| `SABNZBD_API_KEY` | — | SABnzbd API key |
| **JDownloader** | | |
| `JD_EMAIL` | — | My.JDownloader email |
| `JD_PASS` | — | My.JDownloader password |
| `JD_DEVICE` | `mirror` | JDownloader device name |
| **Extractors** | | |
| `YT_DLP_OPTIONS` | — | Global yt-dlp options (JSON) |
| `GALLERY_DL_OPTIONS` | — | Global gallery-dl options (JSON) |
| **DDL** | | |
| `DDL_SITES` | `['gofile']` | Default DDL hosts |
| `GOFILE_TOKEN` | — | Gofile account token |
| `STREAMTAPE_LOGIN` | — | Streamtape API login |
| `STREAMTAPE_KEY` | — | Streamtape API key |
| **Resolvers** | | |
| `FILELION_API` | — | FileLions API key |
| `STREAMWISH_API` | — | StreamWish API key |
| `TERABOX_COOKIE` | — | Terabox cookie (ndus=...) |
| **Rss** | | |
| `RSS_CHAT` | — | Chat for RSS posts (default: subscribing chat) |
| `RSS_INTERVAL` | `900` | RSS poll seconds |
| **Search** | | |
| `SEARCH_API_URL` | — | Torrent-API-py compatible base URL |
| `SEARCH_PLUGINS` | — | qBittorrent search plugin URLs |
| `SEARCH_LIMIT` | `50` | Max search results |
| **Misc** | | |
| `RESTORE_TASKS` | `True` | Re-run interrupted tasks after restart |
| `LOG_LEVEL` | `INFO` | Logging level |
| `UPSTREAM_REPO` | — | Git repo pulled by update.py at boot *(boot only)* |
| `UPSTREAM_BRANCH` | `main` | Upstream branch *(boot only)* |
Mongo collections: `config` (overrides + engine options), `users` (settings, roles, quota/usage),
`files` (thumbnails, user rclone.conf/token/cookies, private files), `rss`, `tasks` (interrupted-task restore).

## Optional user session (SESSION_STRING)

The bot works with a bot token alone. Adding a Pyrogram **user** session string enables:

* private/restricted message links (`t.me/c/…`, topics, ranges) for chats the account has joined;
* uploads up to **4 GB** when the account is premium. `UPLOAD_SESSION=mix` uses the user session only for parts
  larger than 2 GB, `user` always, `bot` never; `-ut`/`-bt` override per task. User-session uploads need a group
  or channel destination (`LEECH_DUMP_CHAT` or `-up tg:<chat>`), private chats always use the bot.

Generate one with any Pyrogram session generator and keep it secret; it is never written to disk.

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && python -m compileall -q Mirror && pytest
```

Tests cover pure logic and engine state machines with fakes: flag parser, config coercion/overrides,
queues/limiters, task registry, DB layer (in-memory collection), rename/split/join, archive round trips,
volume grouping, RSS filters, link detection, engine routing, upload destinations, Drive retry/rotation/
resumable upload/clone/count/token safety, qBittorrent metadata/stall/dead/selection/seed handling,
resolver parsing, Telegraph conversion, yt-dlp format selection, leech planning and limits.

## FAQ

**Is a database required?** No, but without `DATABASE_URL` settings live in memory and vanish on restart.

**qBittorrent says forbidden.** Set `QBIT_USER`/`QBIT_PASS` to the Web UI credentials (linuxserver prints a
temporary password in its log) or whitelist the bot subnet in the Web UI.

**Where do service accounts go?** Upload `accounts.zip` (JSON keys) in `/bsetting → Private files` and set
`USE_SERVICE_ACCOUNTS=true`. Accounts are picked at random per task and rotated on quota errors.

**Is a user token.pickle safe to accept?** Token files are loaded with a restricted unpickler that only admits
google-auth credential classes; JSON authorized-user files are accepted too.

**Why does a direct link fail with "web page, not a file"?** The host needs a generator that is not in the
table, a login (`-au/-ap`, `.netrc`, cookies) or a custom header (`-h`).

**How do restarts handle running tasks?** Active tasks are stored in Mongo; after a restart their commands are
re-run when `RESTORE_TASKS=true`, otherwise the user is told the task was interrupted.

## License

[MIT](LICENSE)
