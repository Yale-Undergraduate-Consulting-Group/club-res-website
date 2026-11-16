"""POST /api/yucgoutreach/company-sheet: an uploaded .xlsx comes back as text
rows (header first) for the page to read companies from; nothing is stored.
A non-workbook is refused."""
import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'company-sheet-secret-xxxxxxxxxxxxxxxxxxx')


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from fastapi.testclient import TestClient
        from openpyxl import Workbook
        from app.database import get_db, init_db
        from app.jwt_utils import create_token
        import main

        asyncio.run(init_db())

        async def seed() -> None:
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO users (id,email,name,role,is_active) VALUES (1,'a@yale.edu','A','standard',1)"
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

        book = Workbook()
        sheet = book.active
        sheet.append(['Company', 'Website'])
        sheet.append(['Globex', 'globex.com'])
        sheet.append(['Initech', None])
        buffer = io.BytesIO()
        book.save(buffer)

        url = '/api/yucgoutreach/company-sheet'
        xlsx = ('companies.xlsx', buffer.getvalue(),
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        with TestClient(main.app) as client:
            assert client.post(url, files={'file': xlsx}).status_code == 401
            client.cookies.set('yucg_session', create_token(1, 'a@yale.edu', 'A', None, 'standard'))
            resp = client.post(url, files={'file': xlsx})
            assert resp.status_code == 200, resp.text[:300]
            assert resp.json() == {'rows': [['Company', 'Website'], ['Globex', 'globex.com'], ['Initech', '']]}, resp.json()
            resp = client.post(url, files={'file': ('x.xlsx', b'not a workbook', 'application/octet-stream')})
            assert resp.status_code == 400, resp.text[:300]


if __name__ == '__main__':
    tests()
    print('company sheet: ok')
