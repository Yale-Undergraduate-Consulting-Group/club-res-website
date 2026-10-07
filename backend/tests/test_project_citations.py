"""Citable club-project facts for outreach drafting: only ever surfaces a
project an admin has explicitly marked `discussable` - an NDA-covered or
unmarked project must never leak into a suggestion, even when its own text
keyword-matches the query company.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'project-citations-secret-xxxxxxxxxxxx')


async def _seed_users(db) -> None:
    await db.execute(
        """INSERT INTO users(id,email,name,role,is_active)
           VALUES (1,'aaron@yale.edu','Aaron Combs','admin',1),
                  (2,'blair@yale.edu','Blair Cross','standard',1)"""
    )
    await db.commit()


async def only_discussable_projects_ever_surface() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.routers.admin import ProjectCreate, create_project
        from app.routers.projects import suggest_citations

        await init_db()
        db = await get_db()
        try:
            await _seed_users(db)
        finally:
            await db.close()

        admin_user = {'id': 1}
        discussable = await create_project(
            ProjectCreate(
                name='Spring 2026 - Project Lego', semester='Spring 2026',
                description='Go-to-market plan for Acme Robotics expansion',
                client_name='Acme Robotics', discussable=True,
            ),
            admin_user,
        )
        nda = await create_project(
            ProjectCreate(
                name='Fall 2025 - Project Falcon', semester='Fall 2025',
                description='Confidential pricing study for Acme Robotics rival',
                client_name='Acme Robotics Rival',  # discussable left at its default: False
            ),
            admin_user,
        )
        assert discussable['discussable'] is True
        assert nda['discussable'] is False

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO user_project_assignments(user_id,project_id,role_in_project) VALUES (1,?,?)",
                (discussable['id'], 'Market Analyst'),
            )
            await db.execute(
                "INSERT INTO user_project_assignments(user_id,project_id,role_in_project) VALUES (2,?,?)",
                (nda['id'], 'Engagement Lead'),
            )
            await db.commit()
        finally:
            await db.close()

        # Query text matches BOTH projects' client_name/description - the
        # NDA one must still never appear.
        for query in ('Acme Robotics', 'Acme Robotics Rival', ''):
            result = await suggest_citations(company=query, user={'id': 1})
            project_ids = {p['id'] for p in result['projects']}
            assert discussable['id'] in project_ids, (query, result['projects'])
            assert nda['id'] not in project_ids, (query, result['projects'])
            assert all(p['client_name'] != 'Acme Robotics Rival' for p in result['projects'])
            assert all(t['client_name'] != 'Acme Robotics Rival' for t in result['team_experience'])

        result = await suggest_citations(company='Acme Robotics', user={'id': 1})
        assert result['team_experience'], result
        assert any(
            t['user_name'] == 'Aaron Combs' and t['role_in_project'] == 'Market Analyst'
            and t['client_name'] == 'Acme Robotics'
            for t in result['team_experience']
        ), result['team_experience']
        assert not any(t['user_name'] == 'Blair Cross' for t in result['team_experience'])

        print('only discussable projects ever surface: ok')


async def patching_discussable_makes_a_project_citable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.routers.admin import ProjectCreate, ProjectUpdate, create_project, update_project, list_projects
        from app.routers.projects import suggest_citations

        await init_db()
        db = await get_db()
        try:
            await _seed_users(db)
        finally:
            await db.close()

        admin_user = {'id': 1}
        project = await create_project(
            ProjectCreate(name='Spring 2026 - Project Comet', semester='Spring 2026',
                          description='Undisclosed engagement'),
            admin_user,
        )
        assert project['discussable'] is False

        # Not discussable yet: a query matching the client name it's about
        # to receive finds nothing.
        before = await suggest_citations(company='Northstar Media', user={'id': 1})
        assert project['id'] not in {p['id'] for p in before['projects']}

        patched = await update_project(
            project['id'], ProjectUpdate(discussable=True, client_name='Northstar Media'), admin_user
        )
        assert patched['discussable'] == 1
        assert patched['client_name'] == 'Northstar Media'

        listing = await list_projects(admin_user)
        assert any(p['id'] == project['id'] and p['discussable'] == 1 and p['client_name'] == 'Northstar Media'
                   for p in listing), listing

        after = await suggest_citations(company='Northstar Media', user={'id': 1})
        assert project['id'] in {p['id'] for p in after['projects']}, after['projects']

        print('patching discussable makes a project citable: ok')


async def keyword_match_ranks_above_a_more_recent_unrelated_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.routers.admin import ProjectCreate, create_project
        from app.routers.projects import suggest_citations

        await init_db()
        db = await get_db()
        try:
            await _seed_users(db)
        finally:
            await db.close()

        admin_user = {'id': 1}
        matching = await create_project(
            ProjectCreate(name='Fall 2025 - Project Delta', semester='Fall 2025',
                          description='Retail strategy for Wovenly Inc',
                          client_name='Wovenly Inc', discussable=True),
            admin_user,
        )
        newer_unrelated = await create_project(
            ProjectCreate(name='Spring 2026 - Project Echo', semester='Spring 2026',
                          description='Ops review for a different client',
                          client_name='Some Other Client', discussable=True),
            admin_user,
        )
        assert newer_unrelated['id'] != matching['id']

        result = await suggest_citations(company='Wovenly Inc', user={'id': 1})
        ids = [p['id'] for p in result['projects']]
        assert ids[0] == matching['id'], ids
        assert matching['id'] in ids and newer_unrelated['id'] in ids

        # No signal at all: falls back to most-recent first.
        blank = await suggest_citations(company='', user={'id': 1})
        blank_ids = [p['id'] for p in blank['projects']]
        assert blank_ids[0] == newer_unrelated['id'], blank_ids

        print('keyword match ranks above recency: ok')


if __name__ == '__main__':
    asyncio.run(only_discussable_projects_ever_surface())
    asyncio.run(patching_discussable_makes_a_project_citable())
    asyncio.run(keyword_match_ranks_above_a_more_recent_unrelated_project())
