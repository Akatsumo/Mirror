import asyncio
import zipfile
from types import SimpleNamespace

from aiohttp import web

from Mirror.core import pipeline
from Mirror.core.app import app
from Mirror.core.flags import parse_args
from Mirror.core.task import Uploaded


async def test_submit_download_zip_upload_report(tmp_path, monkeypatch):
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=b"data" * 1000, headers={"Content-Disposition": 'attachment; filename="f.txt"'})

    server = web.Application()
    server.router.add_get("/f", handler)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    await app.db.connect("", "t")
    monkeypatch.setattr(type(app), "downloads", property(lambda _: tmp_path / "dl"))
    sent, uploaded = [], []

    async def fake_send(chat, text, markup=None, reply_to=None):
        sent.append(text)

    async def fake_upload(task, path, dest):
        with zipfile.ZipFile(path) as archive:
            uploaded.append((path.name, archive.namelist(), dest))
        return Uploaded("Google Drive", "https://drive.google.com/file/d/x", files=1)

    monkeypatch.setattr(pipeline, "send", fake_send)
    monkeypatch.setattr(pipeline.gdrive, "upload", fake_upload)
    message = SimpleNamespace(from_user=SimpleNamespace(id=7, first_name="U"), chat=SimpleNamespace(id=-1), id=3,
                              text=f"/mirror http://127.0.0.1:{port}/f -z -up gd:FOLDER", caption=None)
    try:
        task = await pipeline.submit(message, "mirror", "", parse_args(message.text))
        await asyncio.wait_for(task.handle, 10)
    finally:
        await runner.cleanup()
    assert uploaded == [("f.txt.zip", ["f.txt"], "FOLDER")]
    assert any("f.txt.zip" in text and "Google Drive: 1 file" in text for text in sent), sent
    assert not task.dir.exists() and len(app.tasks) == 0 and await app.db.tasks() == []
    assert (await app.db.user(7))["used"] == 4000
