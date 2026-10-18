"""Timings for the endpoints a 100k-contact club actually hits.

Run directly: python3 tests/bench_scale.py [contacts] [companies]
Not a test: it reports milliseconds so a change can be judged, and it seeds
its own throwaway database.
"""
import asyncio
import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "bench-scale-secret-value-not-used-for-auth")

CONTACTS = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
COMPANIES = int(sys.argv[2]) if len(sys.argv) > 2 else 20_000


async def seed(db) -> None:
    random.seed(7)
    await db.execute("INSERT INTO users(id,email,name,role,is_active) VALUES (1,'a@yale.edu','A','admin',1)")
    await db.execute("INSERT INTO users(id,email,name,role,is_active) VALUES (2,'b@yale.edu','B','standard',1)")
    rows = [
        (
            f"Person {i}",
            f"person{i}@company{i % COMPANIES}.com",
            "Director of Operations",
            f"Company {i % COMPANIES}",
            f"company{i % COMPANIES}.com",
            None if i % 3 else 1,
        )
        for i in range(CONTACTS)
    ]
    await db.executemany(
        """INSERT INTO contacts (name,email,title,company,company_domain,owner_id)
           VALUES (?,?,?,?,?,?)""",
        rows,
    )
    await db.execute(
        "INSERT INTO campaigns (id,name,status,owner_user_id,sender_user_id) VALUES (1,'Bench','sent',1,1)"
    )
    # A send history for a tenth of the catalogue, which is what makes the
    # "last send" join on the contacts list expensive.
    await db.executemany(
        """INSERT INTO campaign_contacts (campaign_id,contact_id,email_subject,email_body,status,sent_at,sent_by_user_id)
           VALUES (1,?,'S','B','sent',CURRENT_TIMESTAMP,1)""",
        [(i,) for i in range(1, CONTACTS // 10)],
    )
    await db.commit()


async def timed(label: str, coro_factory, runs: int = 3) -> float:
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        await coro_factory()
        best = min(best, (time.perf_counter() - start) * 1000)
    print(f"{label:<46} {best:8.1f} ms")
    return best


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'bench.db'}"
        from app.database import get_db, init_db

        await init_db()
        db = await get_db()
        start = time.perf_counter()
        await seed(db)
        await db.close()
        print(f"seeded {CONTACTS} contacts / {COMPANIES} companies in {time.perf_counter() - start:.1f}s\n")

        from app.routers.campaigns import BuildFromTemplate, build_campaign_from_template
        from app.routers.contacts import companies_summary, list_contacts

        admin = {"id": 1, "role": "admin"}
        member = {"id": 2, "role": "standard"}

        await timed("GET /contacts (page 1, admin)", lambda: list_contacts(limit=100, user=admin))
        await timed("GET /contacts (page 1, member)", lambda: list_contacts(limit=100, user=member))
        await timed("GET /contacts?q= (search page)", lambda: list_contacts(q="Person 12345", limit=100, user=admin))
        await timed("GET /contacts (deep page, offset 150k)", lambda: list_contacts(limit=100, offset=150_000, user=admin))
        await timed("GET /contacts/companies/summary", lambda: companies_summary(user=admin))
        names = [f"Company {i}" for i in range(25)]
        await timed(
            "POST /campaigns/build (preview, 25 companies)",
            lambda: build_campaign_from_template(
                BuildFromTemplate(companies=names, subject="Hi {first}", body="Hello {first} at {company}.", preview_only=True),
                admin,
            ),
            runs=2,
        )
        built = await build_campaign_from_template(
            BuildFromTemplate(name="Bench build", companies=[f"Company {i}" for i in range(400)],
                              subject="Hi {first}", body="Hello {first} at {company}."),
            admin,
        )
        print(f"{'POST /campaigns/build (create, 400 companies)':<46} {built['created']:8d} recipients")

        from app.routers.campaigns import release_campaign
        await timed(
            f"POST /campaigns/release ({built['created']} rows)",
            lambda: release_campaign(built["campaign_id"], admin),
            runs=1,
        )


asyncio.run(main())
