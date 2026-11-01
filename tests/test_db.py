from Mirror.core.db import DB


async def make() -> DB:
    db = DB()
    await db.connect("", "test")
    return db


async def test_overrides_and_engine_opts():
    db = await make()
    await db.set_override("A", 1)
    await db.set_override("B", [2])
    await db.unset_override("A")
    assert await db.overrides() == {"B": [2]}
    await db.set_engine_opt("qbit", "x", "1")
    assert await db.engine_opts("qbit") == {"x": "1"} and await db.engine_opts("aria2") == {}


async def test_users_usage_and_roles():
    db = await make()
    await db.update_user(5, sudo=True, prefix="[x]")
    await db.unset_user(5, "prefix")
    assert await db.user(5) == {"sudo": True}
    assert [d["_id"] for d in await db.users(sudo=True)] == [5]
    assert await db.add_usage(5, "d1", 10) == 10
    assert await db.add_usage(5, "d1", 5) == 15
    assert await db.add_usage(5, "d2", 1) == 1


async def test_files_feeds_tasks():
    db = await make()
    await db.put_file(1, "thumb", b"img")
    await db.put_file(0, "rclone.conf", b"conf")
    assert await db.get_file(1, "thumb") == b"img" and len(await db.files()) == 2
    await db.del_file(1, "thumb")
    assert await db.get_file(1, "thumb") is None
    await db.put_feed({"_id": "1:a", "user_id": 1, "url": "u"})
    assert (await db.feeds(1))[0]["url"] == "u" and await db.feeds(2) == []
    await db.del_feed("1:a")
    await db.add_task("t", chat_id=1)
    assert (await db.tasks())[0]["chat_id"] == 1
    await db.clear_tasks()
    assert await db.tasks() == []
