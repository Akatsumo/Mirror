from types import SimpleNamespace

import pytest

from Mirror.config import Config
from Mirror.core.task import Status, TaskError
from Mirror.modules import qbit


def info(state, progress=0.0, completed=0, size=100, **extra):
    base = {"state": state, "progress": progress, "completed": completed, "size": size, "dlspeed": 5, "num_seeds": 1,
            "num_leechs": 2, "name": "Linux ISO", "hash": "h", "content_path": "/nowhere", "uploaded": 0,
            "upspeed": 0, "ratio": 0.0, "seeding_time": 0}
    return SimpleNamespace(**{**base, **extra})


@pytest.fixture
def fake_qbit(monkeypatch):
    calls = []

    async def call(name, *args, **kwargs):
        calls.append(name)
        if name == "torrents_files":
            return [SimpleNamespace(index=0, name="a", size=60, priority=1),
                    SimpleNamespace(index=1, name="b", size=40, priority=1)]
        return None

    def feed(snapshots):
        queue = list(snapshots)

        async def get(_):
            return queue.pop(0) if queue else None
        monkeypatch.setattr(qbit.watcher, "get", get)
    monkeypatch.setattr(qbit, "call", call)
    return calls, feed


async def test_follow_metadata_stall_recheck_complete(fake_qbit, task_factory):
    calls, feed = fake_qbit
    task = task_factory("/qm magnet:?xt=urn:btih:abc")
    metas = []

    async def hook(t):
        metas.append((t.name, t.size))
    task.meta_hook = hook
    feed([info("metaDL", size=0), info("downloading", 0.5, 50), info("stalledDL", 1.0, 100),
          info("checkingDL", 1.0, 100), info("uploading", 1.0, 100)])
    done = await qbit._follow(task, "h")
    assert done.state == "uploading" and metas == [("Linux ISO", 100)]
    assert "torrents_recheck" in calls and task.extra == "S:1 L:2"


async def test_follow_error_and_disappear(fake_qbit, task_factory):
    _, feed = fake_qbit
    feed([info("error")])
    with pytest.raises(TaskError, match="qBittorrent error"):
        await qbit._follow(task_factory(), "h")
    feed([])
    with pytest.raises(TaskError, match="disappeared"):
        await qbit._follow(task_factory(), "h")


async def test_dead_torrent_timeout(fake_qbit, task_factory, monkeypatch):
    _, feed = fake_qbit
    monkeypatch.setattr(Config, "TORRENT_TIMEOUT", 5)
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(qbit.time, "monotonic", lambda: next(clock))
    feed([info("metaDL", size=0)] * 5)
    with pytest.raises(TaskError, match="Dead torrent"):
        await qbit._follow(task_factory(), "h")
    monkeypatch.setattr(Config, "TORRENT_TIMEOUT", 0)


async def test_selection_sets_priorities(fake_qbit, task_factory, monkeypatch):
    calls, feed = fake_qbit

    async def pick(message, user_id, files, timeout=600):
        return {"0"}
    monkeypatch.setattr(qbit, "select_files", pick)
    task = task_factory("/qm magnet:?xt=urn:btih:abc -s")
    feed([info("stoppedDL", 0.0, 0), info("downloading", 0.5, 30, size=60), info("stalledUP", 1.0, 60, size=60)])
    await qbit._follow(task, "h")
    assert calls.count("torrents_file_priority") == 1 and "torrents_start" in calls
    assert task.size == 60 and task.status == Status.DOWNLOAD


async def test_seed_until_limits(fake_qbit, task_factory):
    calls, feed = fake_qbit
    task = task_factory()
    feed([info("uploading", 1, 100, uploaded=50, ratio=0.5), info("stoppedUP", 1, 100)])
    await qbit._seed(task, "h", "tag")
    assert task.status == Status.SEED and "torrents_delete" in calls and qbit.watcher.users == 0
