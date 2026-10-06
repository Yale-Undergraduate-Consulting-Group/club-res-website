"""Shared cloud connector access stays administrative and folder-scoped."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET'] = 'connector-boundary-test-secret-not-production'


async def tests():
    import httpx
    from fastapi import FastAPI
    from app.database import get_db, init_db
    from app.jwt_utils import create_token
    from app.routers.attachments import router, attach_onedrive, OneDriveAttach

    with tempfile.TemporaryDirectory() as directory:
        os.environ['DATABASE_URL'] = 'sqlite:///' + directory + '/connectors.db'
        await init_db()
        db = await get_db()
        try:
            await db.execute("INSERT INTO users(id,email,role,is_active) VALUES(1,'member@yale.edu','standard',1),(2,'admin@yale.edu','admin',1)")
            await db.commit()
        finally:
            await db.close()
        app = FastAPI()
        app.include_router(router, prefix='/api/attachments')
        token = create_token(1, 'member@yale.edu')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/attachments/onedrive', headers={'Authorization': 'Bearer ' + token})
            assert response.status_code == 403
            response = await client.post(
                '/api/attachments/onedrive/attach',
                headers={'Authorization': 'Bearer ' + token},
                json={'item_id': 'allowed'},
            )
            assert response.status_code == 403

        with patch('app.services.graph_onedrive.list_folder', return_value=[{'id': 'allowed', 'folder': False}]), \
             patch('app.services.graph_onedrive.download_item') as download:
            try:
                await attach_onedrive(OneDriveAttach(item_id='outside'), {'id': 2, 'role': 'admin'})
                raise AssertionError('Out-of-folder OneDrive item accepted')
            except Exception as exc:
                assert getattr(exc, 'status_code', None) == 403
            download.assert_not_called()


if __name__ == '__main__':
    asyncio.run(tests())
    print('connector boundaries: ok')
