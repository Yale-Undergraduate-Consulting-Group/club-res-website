"""Exercise image identity and database initialization without outbound services."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def smoke():
    assert os.geteuid() != 0, 'Application image must run as non-root'
    from app.database import init_db, get_db
    await init_db()
    db = await get_db()
    try:
        await db.execute('SELECT count(*) FROM users')
        await db.execute('SELECT count(*) FROM workspace_documents')
        await db.execute('SELECT count(*) FROM outreach_dispatches')
    finally:
        await db.close()
    import main
    assert main.app is not None
    print('Non-root image imports application and initializes workspace database')


if __name__ == '__main__':
    asyncio.run(smoke())
