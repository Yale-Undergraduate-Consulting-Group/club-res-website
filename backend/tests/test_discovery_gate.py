"""One HTML crawl at a time on a small box. From backend/: python tests/test_discovery_gate.py"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ["DISCOVERY_JOBS"] = "1"

from app.services import discovery_gate as gate  # noqa: E402

gate._sem = None
gate._sem_n = None


async def _run() -> None:
    order: list[str] = []

    async def job(name: str, hold: float) -> None:
        async with gate.discovery_job():
            order.append(f"start-{name}")
            await asyncio.sleep(hold)
            order.append(f"end-{name}")

    await asyncio.gather(job("a", 0.12), job("b", 0.01))
    assert order == ["start-a", "end-a", "start-b", "end-b"] or order == [
        "start-b",
        "end-b",
        "start-a",
        "end-a",
    ], order
    # Never interleaved starts
    starts = [i for i, x in enumerate(order) if x.startswith("start-")]
    ends = [i for i, x in enumerate(order) if x.startswith("end-")]
    assert ends[0] < starts[1], order


def test_discovery_jobs_do_not_overlap() -> None:
    asyncio.run(_run())


def test_small_box_is_one_slot() -> None:
    assert gate.discovery_job_slots() == 1


if __name__ == "__main__":
    test_small_box_is_one_slot()
    test_discovery_jobs_do_not_overlap()
    print("ok")
