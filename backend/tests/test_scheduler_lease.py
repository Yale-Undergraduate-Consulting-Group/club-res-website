"""Only one process runs scheduled jobs, and the lease moves when its holder stops or dies."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
temp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(temp.name) / "lease.db")
os.environ.setdefault("JWT_SECRET", "scheduler-lease-test-secret-value-1234")

from app.database import init_db  # noqa: E402
from app.services import scheduler_lease as lease  # noqa: E402


async def tests() -> None:
    await init_db()
    blue, green = "blue-process", "green-process"

    # Exactly one of two contenders holds it, and renewal keeps it.
    assert await lease.try_acquire(blue)
    assert not await lease.try_acquire(green)
    assert await lease.try_acquire(blue)
    assert not await lease.try_acquire(green)

    # A holder that stops releases it, so the other takes over at once.
    await lease.release(blue)
    assert await lease.try_acquire(green)
    assert not await lease.try_acquire(blue)

    # A holder that dies without releasing loses it when the lease expires.
    await lease.release(green)
    assert await lease.try_acquire(blue, ttl=0.2)
    assert not await lease.try_acquire(green)
    await asyncio.sleep(0.3)
    assert await lease.try_acquire(green)
    # Releasing someone else's lease does nothing.
    await lease.release(blue)
    assert not await lease.try_acquire(blue)
    await lease.release(green)

    # Concurrent contenders: exactly one wins.
    results = await asyncio.gather(*(lease.try_acquire(f"p{i}") for i in range(6)))
    assert sum(results) == 1, results
    winner = f"p{results.index(True)}"
    await lease.release(winner)

    # Jobs run only while this process holds the lease.
    ran = []

    async def job():
        ran.append(1)
        return "done"

    guarded = lease.leader_only(job)
    stop = asyncio.Event()
    holder = asyncio.create_task(lease.keep_lease(stop))
    for _ in range(50):
        if lease.is_leader():
            break
        await asyncio.sleep(0.01)
    assert lease.is_leader()
    assert await guarded() == "done" and ran == [1]
    # Stopping releases it for the next process and stops this one's jobs.
    stop.set()
    await holder
    assert not lease.is_leader()
    assert await guarded() is None and ran == [1]
    assert await lease.try_acquire(green)

    # While another process holds it, this one never becomes leader.
    stop = asyncio.Event()
    holder = asyncio.create_task(lease.keep_lease(stop))
    await asyncio.sleep(0.1)
    assert not lease.is_leader()
    stop.set()
    await holder
    assert await lease.try_acquire(green), "a non-holder's shutdown released the holder's lease"
    print("scheduler lease: ok")


if __name__ == "__main__":
    asyncio.run(tests())
