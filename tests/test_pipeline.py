from Mirror.config import Config
from Mirror.core.pipeline import destination, detect, final_name


def test_detect_engines(monkeypatch):
    monkeypatch.setattr(Config, "TORRENT_ENGINE", "qbit")
    monkeypatch.setattr(Config, "DIRECT_ENGINE", "aiohttp")
    assert detect("magnet:?xt=urn:btih:a", "") == "qbit"
    assert detect("magnet:?xt=urn:btih:a", "aria2") == "aria2"
    assert detect("/tmp/x.torrent", "") == "qbit"
    assert detect("https://a/b.nzb", "") == "sab"
    assert detect("https://youtu.be/x", "ytdl") == "ytdl"
    assert detect("https://mega.nz/file/x", "") == "mega"
    assert detect("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWx/view", "") == "gdrive"
    assert detect("remote:folder", "") == "rclone"
    assert detect("https://t.me/c/1/2", "") == "telegram"
    assert detect("anything", "", has_messages=True) == "telegram"
    assert detect("https://x/y.dlc", "") == "jd"
    assert detect("https://x/y.bin", "") == "direct"
    monkeypatch.setattr(Config, "DIRECT_ENGINE", "aria2")
    assert detect("https://x/y.bin", "") == "aria2"
    assert detect("https://www.mediafire.com/file/abc", "") == "direct"
    monkeypatch.setattr(Config, "TORRENT_ENGINE", "aria2")
    assert detect("magnet:?xt=1", "") == "aria2"


def test_destination(task_factory):
    assert destination(task_factory("/m x", default_upload="rc")) == ("rc", "")
    assert destination(task_factory("/m x -up gd:abc")) == ("gd", "abc")
    assert destination(task_factory("/m x -up mt:1AbCdEfGhIjKlMnOpQrStUv")) == ("gd", "mt:1AbCdEfGhIjKlMnOpQrStUv")
    assert destination(task_factory("/m x -up remote:path")) == ("rc", "remote:path")
    assert destination(task_factory("/m x -up ddl:gofile,streamtape")) == ("ddl", "gofile,streamtape")
    assert destination(task_factory("/m x -up -1001234")) == ("tg", "-1001234")
    named = task_factory("/m x -up movies", upload_paths={"movies": "rc:media:Movies"})
    assert destination(named) == ("rc", "media:Movies")


def test_final_name(task_factory):
    task = task_factory("/m x -z")
    task.name = "folder"
    assert final_name(task) == "folder.zip"
    task = task_factory("/m x -e")
    task.name = "pack.rar"
    assert final_name(task) == "pack"
