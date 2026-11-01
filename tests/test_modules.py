from types import SimpleNamespace

import pytest

from Mirror.config import Config
from Mirror.core.app import BOT_LIMIT, app
from Mirror.core.flags import parse_args
from Mirror.core.telegraph import chunks, html_to_nodes
from Mirror.core.ui import grid, kb
from Mirror.modules import direct, leech, mega, rclone, resolvers, ytdlp
from Mirror.modules.mirror import merge


def test_resolver_table():
    assert resolvers.resolver_for("https://www.mediafire.com/file/x") is resolvers.mediafire
    assert resolvers.resolver_for("https://send.cm/abc") is resolvers.xfilesharing
    assert resolvers.resolver_for("https://www.1024tera.com/s/1x") is resolvers.terabox
    assert resolvers.resolver_for("https://example.com/file.zip") is None


async def test_streamtape_and_onedrive(monkeypatch):
    page = ("document.getElementById('robotlink').innerHTML = '//streamtape.com/get_video?id=1&token='"
            " + ('xyzabcTOKEN').substring(1).substring(2);")

    async def fake_text(session, url, **kw):
        return page
    monkeypatch.setattr(resolvers, "_text", fake_text)
    result = await resolvers.streamtape(None, "https://streamtape.com/e/abc")
    assert result.files[0][0] == "https://streamtape.com/get_video?id=1&token=abcTOKEN&stream=1"
    drive = await resolvers.onedrive(None, "https://1drv.ms/u/s!abc")
    assert drive.files[0][0].startswith("https://api.onedrive.com/v1.0/shares/u!")


async def test_unknown_host_resolves_none():
    assert await resolvers.resolve("https://example.com/x.bin") is None


def test_telegraph_nodes():
    nodes = html_to_nodes('<p><b>Hi</b> <a href="https://x">link</a><br>t</p><script>x</script>')
    assert nodes[0]["tag"] == "p" and nodes[0]["children"][0] == {"tag": "b", "children": ["Hi"]}
    assert nodes[0]["children"][2]["attrs"] == {"href": "https://x"}
    assert chunks(["a" * 6, "b" * 6, "c"], limit=10) == ["a" * 6, "b" * 6 + "c"]


def test_mega_progress():
    parsed = mega.parse_progress("x.mkv: 50.00% - 1.0 MiB (1048576 bytes) of 2.0 MiB (512.0 KiB/s)")
    assert parsed == (1048576, 2 * 1024**2, 512 * 1024)
    assert mega.parse_progress("nothing") is None


def test_ytdlp_quality_and_choice():
    info = {"formats": [{"height": 720, "vcodec": "avc1", "filesize": 100}, {"vcodec": "none", "acodec": "opus",
                                                                             "filesize": 10}]}
    labels = [value for _, value in ytdlp.quality_options(info)]
    assert "v:720" in labels and "v:1080" not in labels and "a:mp3:320" in labels
    opts = {"postprocessors": [{"key": "EmbedThumbnail"}], "merge_output_format": "mkv"}
    ytdlp.apply_choice(opts, "a:wav:0")
    assert opts["format"] == "ba/b" and opts["postprocessors"][0]["preferredcodec"] == "wav"
    assert all(p["key"] != "EmbedThumbnail" for p in opts["postprocessors"])
    video = {"postprocessors": []}
    ytdlp.apply_choice(video, "v:480")
    assert "height<=480" in video["format"]


def test_leech_plan(task_factory, monkeypatch):
    assert leech.parse_dest("tg:-1001:5", 1) == (-1001, 5)
    assert leech.parse_dest("@chan|9", 1) == ("@chan", 9)
    assert leech.parse_dest("", 7) == (7, None)
    task = task_factory("/l x -sp 100MB", mode="leech")
    chat, _, _, limit = leech.plan(task)
    assert chat == -100 and limit == 100 * 1024**2
    private = task_factory("/l x -ut", mode="leech")
    private.chat_id = 42
    assert leech.plan(private)[2] == "bot" and leech.plan(private)[3] == BOT_LIMIT
    monkeypatch.setattr(app, "user", object())
    assert app.uploader(BOT_LIMIT + 1, "mix") is app.user
    monkeypatch.setattr(app, "bot", "bot", raising=False)
    assert app.uploader(10, "mix") == "bot"


def test_rclone_helpers(tmp_path):
    assert rclone.parse_flags("--buffer-size:64M|--fast-list") == ["--buffer-size", "64M", "--fast-list"]
    assert rclone.join("r:", "a") == "r:a" and rclone.join("r:x", "a") == "r:x/a"
    assert rclone.parent("r:x/y") == "r:x" and rclone.parent("r:x") == "r:"
    conf = tmp_path / "rclone.conf"
    conf.write_text("[gd]\ntype = drive\n")
    assert rclone.remote_type(conf, "gd") == "drive" and rclone.remote_type(conf, "zz") == ""


def test_direct_helpers():
    assert direct.headers_of("A: 1|B:2|bad") == {"A": "1", "B": "2"}
    assert direct.segments(10, 3) == [(0, 3), (4, 7), (8, 9)]
    response = SimpleNamespace(headers={"Content-Disposition": "attachment; filename*=UTF-8''na%20me.mkv"},
                               url="https://x/y")
    assert direct.filename_of(response, "f") == "na me.mkv"
    assert direct.auth_for("https://x", "u", "p") == {"Authorization": "Basic dTpw"}


def test_keyboard_and_merge():
    markup = kb([("a", "x|1"), ("site", "https://e.com")], [])
    assert markup.inline_keyboard[0][1].url == "https://e.com" and len(markup.inline_keyboard) == 1
    assert grid([("a", "1")] * 5, 2)[-1] == [("a", "1")]
    merged = merge(parse_args("/m -z pw -up rc"), parse_args("https://one -n x", has_command=False))
    assert merged.links == ["https://one"] and merged.zip_pass == "pw" and merged.name == "x" and merged.up == "rc"


async def test_limits(task_factory, monkeypatch):
    from Mirror.core import auth

    await app.db.connect("", "t")
    monkeypatch.setattr(Config, "OWNER_ID", 99)
    monkeypatch.setattr(Config, "TORRENT_LIMIT", 100)
    task = task_factory(engine="qbit")
    assert "Torrent Limit" in await auth.check_limits(task, 200)
    assert await auth.check_limits(task, 50) is None
    monkeypatch.setattr(Config, "DAILY_QUOTA", 60)
    await app.db.add_usage(1, auth.today(), 20)
    assert "quota" in await auth.check_limits(task, 50)
    task.user_id = 99
    assert await auth.check_limits(task, 10**12) is None
    assert auth.is_authorized(99, 0) and not auth.is_authorized(5, 0)
    monkeypatch.setattr(Config, "AUTHORIZED_CHATS", [-100])
    assert auth.is_authorized(5, -100)
    auth.Roles.banned.add(5)
    assert not auth.is_authorized(5, -100)
    auth.Roles.banned.discard(5)


@pytest.mark.parametrize("module", ["Mirror.__main__", "Mirror.modules.bsettings", "Mirror.modules.admin"])
def test_imports(module):
    __import__(module)
