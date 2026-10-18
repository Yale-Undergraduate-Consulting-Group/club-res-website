"""Club-wide officer/director metadata. Not a second CRM and not a paid people dump."""


async def init_roster_schema(db):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS company_rosters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_key TEXT NOT NULL UNIQUE,
            company_name TEXT NOT NULL,
            company_domain TEXT,
            ticker TEXT,
            cik TEXT,
            source_status TEXT NOT NULL DEFAULT 'pending',
            last_crawled_at TEXT,
            last_verified_at TEXT,
            next_verify_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_error TEXT,
            people_count INTEGER NOT NULL DEFAULT 0,
            current_count INTEGER NOT NULL DEFAULT 0,
            next_email_check_at TEXT,
            domain_checked_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_company_rosters_due
            ON company_rosters(next_verify_at, source_status);
        CREATE INDEX IF NOT EXISTS idx_company_rosters_name
            ON company_rosters(company_name);

        CREATE TABLE IF NOT EXISTS company_roster_people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roster_id INTEGER NOT NULL REFERENCES company_rosters(id) ON DELETE CASCADE,
            normalized_name TEXT NOT NULL,
            full_name TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            role_type TEXT NOT NULL DEFAULT 'officer',
            source TEXT NOT NULL,
            source_url TEXT,
            accession TEXT,
            inferred_email TEXT,
            employment TEXT NOT NULL DEFAULT 'current',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            missed_checks INTEGER NOT NULL DEFAULT 0,
            email_status TEXT,
            email_checked_at TEXT,
            verdict TEXT,
            verdict_reason TEXT,
            adjudicated_at TEXT,
            email_provider_checked_at TEXT,
            UNIQUE(roster_id, normalized_name)
        );
        CREATE INDEX IF NOT EXISTS idx_roster_people_employment
            ON company_roster_people(roster_id, employment);
        CREATE INDEX IF NOT EXISTS idx_roster_people_email
            ON company_roster_people(inferred_email);
    """)
    people_columns = {
        row["name"] for row in await (await db.execute("PRAGMA table_info(company_roster_people)")).fetchall()
    }
    if people_columns and "email_provider_checked_at" not in people_columns:
        await db.execute("ALTER TABLE company_roster_people ADD COLUMN email_provider_checked_at TEXT")
    if people_columns and "verdict" not in people_columns:
        await db.execute("ALTER TABLE company_roster_people ADD COLUMN verdict TEXT")
        await db.execute("ALTER TABLE company_roster_people ADD COLUMN verdict_reason TEXT")
        await db.execute("ALTER TABLE company_roster_people ADD COLUMN adjudicated_at TEXT")
    if people_columns and "email_status" not in people_columns:
        await db.execute("ALTER TABLE company_roster_people ADD COLUMN email_status TEXT")
        await db.execute("ALTER TABLE company_roster_people ADD COLUMN email_checked_at TEXT")
    roster_columns = {
        row["name"] for row in await (await db.execute("PRAGMA table_info(company_rosters)")).fetchall()
    }
    if roster_columns and "next_email_check_at" not in roster_columns:
        await db.execute("ALTER TABLE company_rosters ADD COLUMN next_email_check_at TEXT")
    if roster_columns and "domain_checked_at" not in roster_columns:
        await db.execute("ALTER TABLE company_rosters ADD COLUMN domain_checked_at TEXT")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_rosters_email_due ON company_rosters(next_email_check_at)"
    )
