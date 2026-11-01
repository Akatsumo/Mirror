import os

from aiohttp import web

from Mirror.modules import direct


async def test_parallel_and_plain_download(task_factory):
    payload = os.urandom(3 * 1024 * 1024 + 123)

    async def handler(request: web.Request) -> web.StreamResponse:
        return web.Response(body=payload, headers={"Content-Disposition": 'attachment; filename="blob.bin"'}) \
            if request.path == "/plain" else await _ranged(request)

    async def _ranged(request: web.Request) -> web.StreamResponse:
        header = request.headers.get("Range")
        if not header:
            return web.Response(body=payload, headers={"Accept-Ranges": "bytes"})
        start, end = (int(x) for x in header.removeprefix("bytes=").split("-"))
        return web.Response(status=206, body=payload[start:end + 1], headers={"Accept-Ranges": "bytes"})

    app = web.Application()
    app.router.add_get("/{name}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        direct.PARALLEL_MIN = 1024 * 1024
        task = task_factory(f"/m http://127.0.0.1:{port}/big.bin")
        path = await direct.download(task)
        assert path.read_bytes() == payload and task.done == len(payload)
        task = task_factory(f"/m http://127.0.0.1:{port}/plain")
        path = await direct.download(task)
        assert path.name == "blob.bin" and path.read_bytes() == payload
    finally:
        direct.PARALLEL_MIN = 64 * 1024**2
        await runner.cleanup()
