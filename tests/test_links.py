import pytest

from Mirror.core.links import gdrive_id, is_gdrive, is_magnet, is_nzb, is_rclone, is_tg_link, is_torrent, parse_tg_link


def test_tg_links():
    assert parse_tg_link("https://t.me/c/123456/77").chat == -100123456
    ranged = parse_tg_link("https://t.me/somechan/10-12")
    assert ranged.chat == "somechan" and ranged.ids == [10, 11, 12]
    topic = parse_tg_link("https://t.me/c/999/5/42")
    assert topic.thread == 5 and topic.first == 42
    assert parse_tg_link("tg://openmessage?user_id=42&message_id=9").ids == [9]
    assert is_tg_link("t.me/abc/1") and not is_tg_link("https://example.com/1")
    with pytest.raises(ValueError):
        parse_tg_link("https://example.com")


def test_gdrive():
    file_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert gdrive_id(f"https://drive.google.com/file/d/{file_id}/view") == file_id
    assert gdrive_id(f"https://drive.google.com/drive/folders/{file_id}?usp=sharing") == file_id
    assert gdrive_id(f"https://drive.google.com/open?id={file_id}") == file_id
    assert gdrive_id(f"mt:{file_id}") == file_id
    assert is_gdrive(f"sa:{file_id}") and is_gdrive("https://docs.google.com/x")
    with pytest.raises(ValueError):
        gdrive_id("https://drive.google.com/")


def test_kinds():
    assert is_magnet("magnet:?xt=urn:btih:x") and is_torrent("https://a/b.torrent?x=1")
    assert is_nzb("https://a/b.nzb") and not is_nzb("https://a/b.zip")
    assert is_rclone("gdrive:Movies/x") and is_rclone("mrcc:remote:path") and is_rclone("remote:")
    assert not is_rclone("https://a:b@c") and not is_rclone("magnet:?xt=1") and not is_rclone("plain")
