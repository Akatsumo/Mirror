import asyncio

from Mirror.core.task import Limiter, Registry, Status, TaskQueue


async def test_limiter_blocks_and_force_bypasses():
    gate = Limiter(lambda: 1)
    await gate.acquire()
    waiter = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    await gate.acquire(force=True)
    assert gate.active == 2
    await gate.release()
    await gate.release()
    await asyncio.wait_for(waiter, 1)
    assert gate.active == 1


async def test_limiter_live_limit_change():
    limit = [1]
    gate = Limiter(lambda: limit[0])
    await gate.acquire()
    waiter = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0.01)
    limit[0] = 0
    await gate.wake()
    await asyncio.wait_for(waiter, 1)


async def test_queue_slots_set_status(task_factory):
    queue = TaskQueue(lambda: 1, lambda: 1, lambda: 0)
    first, second = task_factory(), task_factory()
    order = []

    async def job(task, name):
        async with queue.slot("dl", task):
            order.append(name)
            await asyncio.sleep(0.02)

    a = asyncio.create_task(job(first, "a"))
    await asyncio.sleep(0)
    b = asyncio.create_task(job(second, "b"))
    await asyncio.sleep(0.005)
    assert second.status == Status.QUEUE_DL
    await asyncio.gather(a, b)
    assert order == ["a", "b"] and queue.gates["dl"].active == 0


async def test_cancel_while_queued_releases_total(task_factory):
    queue = TaskQueue(lambda: 1, lambda: 1, lambda: 5)
    held = task_factory()
    async with queue.slot("dl", held):
        blocked = asyncio.create_task(queue.slot("dl", task_factory()).__aenter__())
        await asyncio.sleep(0.01)
        blocked.cancel()
        await asyncio.gather(blocked, return_exceptions=True)
        assert queue.total.active == 1
    assert queue.total.active == 0


def test_registry_and_progress(task_factory):
    registry = Registry()
    task = task_factory()
    task.set_status(Status.DOWNLOAD, 100)
    task.progress(50, speed=10)
    assert task.percent == 50 and task.eta == 5
    registry.add(task)
    assert registry.select("dl") == [task] and registry.select("up") == []
    assert registry.select("all", user_id=2) == [] and registry.count_user(1) == 1
    assert registry.speeds() == (10, 0)
    hooks = []
    task.cancel_hooks.append(lambda: hooks.append(1))
    assert registry.cancel_where("dl") == 0 and hooks == [1]
    registry.remove(task.id)
    assert len(registry) == 0


async def test_meta_hook_runs_once(task_factory):
    task = task_factory()
    calls = []

    async def hook(t):
        calls.append((t.name, t.size))
    task.meta_hook = hook
    await task.meta("file", 10)
    await task.meta("other", 20)
    assert calls == [("file", 10)] and task.name == "other"
