"""catalog index + week release tables

Revision ID: 0001
Revises:
Create Date: 2026-09-07
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS stored_objects (
            id SERIAL PRIMARY KEY,
            kind TEXT NOT NULL,
            s3_key TEXT NOT NULL UNIQUE,
            sha256 TEXT,
            byte_size INTEGER,
            content_type TEXT,
            owner_user_id INTEGER,
            source TEXT NOT NULL DEFAULT 'upload',
            graph_item_id TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS outreach_releases (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_by INTEGER,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS outreach_release_targets (
            id SERIAL PRIMARY KEY,
            release_id INTEGER NOT NULL REFERENCES outreach_releases(id) ON DELETE CASCADE,
            row_index INTEGER,
            company TEXT NOT NULL,
            company_domain TEXT,
            sector TEXT,
            contact_type TEXT,
            incentive_score REAL,
            verification_source_url TEXT,
            find_status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS outreach_release_people (
            id SERIAL PRIMARY KEY,
            release_id INTEGER NOT NULL REFERENCES outreach_releases(id) ON DELETE CASCADE,
            target_id INTEGER REFERENCES outreach_release_targets(id) ON DELETE SET NULL,
            contact_id INTEGER,
            full_name TEXT,
            title TEXT,
            email TEXT,
            company_domain TEXT,
            email_status TEXT NOT NULL DEFAULT 'inferred',
            vendor TEXT,
            vendor_check TEXT,
            source_url TEXT,
            blurb TEXT,
            kept INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outreach_release_people")
    op.execute("DROP TABLE IF EXISTS outreach_release_targets")
    op.execute("DROP TABLE IF EXISTS outreach_releases")
    op.execute("DROP TABLE IF EXISTS stored_objects")
