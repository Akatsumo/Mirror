import json
import pickle
from pathlib import Path

import httplib2
import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

from Mirror.core.task import TaskError
from Mirror.modules import gdrive


def http_error(status: int, reason: str = "") -> HttpError:
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return HttpError(httplib2.Response({"status": status}), body)


class FakeDrive(gdrive.Drive):
    """Drive whose ``service`` is a dummy; requests are plain callables."""

    @property
    def service(self):
        return f"svc-{self.sa_index}"


class Req:
    def __init__(self, outcome):
        self.outcome = outcome

    def execute(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_links_and_escape():
    assert gdrive.links("ID", "a b", True, "https://idx") == ("https://drive.google.com/drive/folders/ID",
                                                               "https://idx/a%20b/", "")
    url, _, view = gdrive.links("ID", "f.mkv", False, "https://idx")
    assert url.endswith("/file/d/ID") and view == "https://idx/f.mkv?a=view"
    assert gdrive.escape("it's \\ ok") == "it\\'s \\\\ ok"
    assert gdrive.reason_of(http_error(403, "dailyLimitExceeded")) == "dailyLimitExceeded"


def test_rotates_service_accounts_on_quota(monkeypatch):
    monkeypatch.setattr(gdrive.time, "sleep", lambda _: None)
    drive = FakeDrive()
    drive.accounts, drive.sa_index = [Path("a.json"), Path("b.json"), Path("c.json")], 0
    seen = []

    def make(service):
        seen.append(service)
        return Req(http_error(403, "dailyLimitExceeded") if len(seen) < 3 else {"ok": 1})
    assert drive.call(make) == {"ok": 1}
    assert seen == ["svc-0", "svc-1", "svc-2"] and drive.switches == 2


def test_quota_without_accounts_raises():
    drive = FakeDrive()
    with pytest.raises(HttpError):
        drive.call(lambda _: Req(http_error(403, "dailyLimitExceeded")))


def test_transient_retry_then_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr(gdrive.time, "sleep", sleeps.append)
    drive = FakeDrive()
    outcomes = [http_error(500), http_error(429, "rateLimitExceeded"), {"id": "x"}]
    assert drive.call(lambda _: Req(outcomes.pop(0))) == {"id": "x"} and len(sleeps) == 2


def test_cancel_stops_calls():
    drive = FakeDrive()
    drive.cancel.set()
    with pytest.raises(gdrive.TransferCancelledError):
        drive.call(lambda _: Req({}))


def test_not_found_falls_back_to_token(tmp_path):
    token = tmp_path / "token.pickle"
    token.write_bytes(b"x")
    drive = FakeDrive(token=token)
    drive.accounts = [Path("a.json")]
    calls = []

    def fn():
        calls.append(drive.using_sa)
        if drive.using_sa:
            raise http_error(404, "notFound")
        return "ok"
    assert drive.with_fallback(fn) == "ok" and calls == [True, False]


def test_token_unpickler(tmp_path):
    creds = Credentials(token="t", refresh_token="r", token_uri="https://oauth2.googleapis.com/token",
                        client_id="c", client_secret="s", scopes=gdrive.SCOPE)
    good = tmp_path / "token.pickle"
    good.write_bytes(pickle.dumps(creds))
    with good.open("rb") as handle:
        loaded = gdrive._TokenUnpickler(handle).load()
    assert isinstance(loaded, Credentials) and loaded.refresh_token == "r"

    class Evil:
        def __reduce__(self):
            import os
            return os.system, ("echo pwned",)
    bad = tmp_path / "bad.pickle"
    bad.write_bytes(pickle.dumps(Evil()))
    with pytest.raises(pickle.UnpicklingError):
        gdrive.load_token(bad)


def test_json_token(tmp_path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "r",
                                "token": "t", "expiry": "2999-01-01T00:00:00Z"}))
    assert gdrive.load_token(path).refresh_token == "r"


def test_target_prefixes(tmp_path, monkeypatch, task_factory):
    from Mirror.core.app import app
    monkeypatch.setattr(type(app), "data", property(lambda _: tmp_path))
    prefs = task_factory().prefs
    target = gdrive.target_for("sa:FOLDER", prefs)
    assert target.id == "FOLDER"
    with pytest.raises(TaskError):
        gdrive.target_for("mt:FOLDER", prefs)
    prefs.files["token"] = tmp_path / "u.pickle"
    assert gdrive.target_for("mt:X", prefs).drive.token == tmp_path / "u.pickle"


class Status:
    def __init__(self, done):
        self.resumable_progress = done


class FakeUpload:
    def __init__(self, script):
        self.script = script

    def next_chunk(self):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeFiles:
    def __init__(self, drive):
        self.drive = drive

    def create(self, **kw):
        if "media_body" in kw and kw["media_body"].resumable():
            return FakeUpload(self.drive.scripts.pop(0))
        return Req({"id": f"folder-{kw['body']['name']}"})

    def list(self, **kw):
        token = kw.get("pageToken")
        pages = self.drive.tree.get(kw["q"].split("'")[1], [[]])
        index = int(token or 0)
        page = {"files": pages[index]}
        if index + 1 < len(pages):
            page["nextPageToken"] = str(index + 1)
        return Req(page)

    def copy(self, **kw):
        if kw["fileId"] == "locked":
            return Req(http_error(403, "cannotCopyFile"))
        return Req({"id": f"copy-{kw['fileId']}"})


class TreeDrive(gdrive.Drive):
    scripts: list
    tree: dict

    @property
    def service(self):
        return type("S", (), {"files": lambda _: FakeFiles(self)})()

def test_resumable_upload_retries_and_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive.time, "sleep", lambda _: None)
    data = tmp_path / "f.bin"
    data.write_bytes(b"x" * 1000)
    progress = []
    drive = TreeDrive(progress=progress.append)
    drive.accounts = [Path("a"), Path("b")]
    drive.scripts = [[(Status(400), None), http_error(403, "userRateLimitExceeded")],
                     [http_error(503), (Status(600), None), (None, {"id": "done"})]]
    assert drive._upload_file(data, "parent") == "done"
    assert sum(progress) == 1000 and drive.switches == 1 and drive.files == 1


def test_clone_count_with_paging_and_skips(monkeypatch):
    drive = TreeDrive()
    folder = {"id": "root", "name": "Root", "mimeType": gdrive.FOLDER}
    drive.tree = {
        "root": [[{"id": "a", "name": "a.mkv", "mimeType": "video/x-matroska", "size": "10"}],
                 [{"id": "sub", "name": "Sub", "mimeType": gdrive.FOLDER},
                  {"id": "locked", "name": "l.bin", "mimeType": "application/octet-stream", "size": "5"}]],
        "sub": [[{"id": "b", "name": "b.nfo", "mimeType": "text/plain", "size": "1"}]],
    }
    assert drive.count(folder) == 16 and drive.files == 3 and drive.folders == 2
    cloner = TreeDrive(skip_ext=["nfo"])
    cloner.tree = drive.tree
    assert cloner.clone(folder, "dest") == "folder-Root"
    assert cloner.files == 1 and cloner.folders == 2 and cloner.root == "folder-Root"
