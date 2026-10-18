"""Additive SQLite evidence migration; never promotes legacy confidence to proof."""


async def init_contact_intelligence_schema(db):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, domain TEXT, aliases_json TEXT NOT NULL DEFAULT '[]',
            parent_id INTEGER REFERENCES organizations(id), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_key TEXT NOT NULL UNIQUE,
            organization_id INTEGER REFERENCES organizations(id), name TEXT NOT NULL,
            normalized_name TEXT NOT NULL, title TEXT, profile_url TEXT,
            identity TEXT NOT NULL DEFAULT 'unreviewed', employment TEXT NOT NULL DEFAULT 'unknown',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS person_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL REFERENCES people(id),
            owner_id INTEGER NOT NULL, project_id INTEGER, access_scope TEXT NOT NULL DEFAULT 'private',
            source_url TEXT NOT NULL, source_type TEXT NOT NULL, excerpt TEXT NOT NULL,
            observed_at TEXT NOT NULL, content_hash TEXT NOT NULL, facts_json TEXT NOT NULL DEFAULT '[]',
            method TEXT NOT NULL, expires_at TEXT, superseded INTEGER NOT NULL DEFAULT 0,
            UNIQUE(person_id, owner_id, source_url, content_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_person_evidence_scope ON person_evidence(person_id, owner_id);
        CREATE TABLE IF NOT EXISTS email_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER NOT NULL REFERENCES people(id),
            email TEXT NOT NULL COLLATE NOCASE UNIQUE, company_domain TEXT,
            origin TEXT NOT NULL, pattern_key TEXT, rank INTEGER NOT NULL DEFAULT 0,
            selected INTEGER NOT NULL DEFAULT 0, method TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_person_selected_address ON email_candidates(person_id) WHERE selected = 1;
        CREATE TABLE IF NOT EXISTS email_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL REFERENCES email_candidates(id),
            actor_id INTEGER NOT NULL, verifier TEXT NOT NULL, check_type TEXT NOT NULL,
            result TEXT NOT NULL, reason TEXT NOT NULL, source_ids_json TEXT NOT NULL DEFAULT '[]',
            checked_at TEXT NOT NULL, expires_at TEXT, cost_units INTEGER NOT NULL DEFAULT 0,
            provider_request_id TEXT, event_key TEXT UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_email_checks_candidate ON email_checks(candidate_id, checked_at);
        CREATE TABLE IF NOT EXISTS catalog_evidence (
            contact_id INTEGER PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
            person_id INTEGER NOT NULL REFERENCES people(id), candidate_id INTEGER NOT NULL REFERENCES email_candidates(id)
        );
        CREATE TABLE IF NOT EXISTS person_assessments (
            person_id INTEGER NOT NULL REFERENCES people(id), actor_id INTEGER NOT NULL,
            project_key INTEGER NOT NULL DEFAULT 0, identity TEXT NOT NULL, employment TEXT NOT NULL,
            project_fit TEXT NOT NULL, reason TEXT NOT NULL, conflicts_json TEXT NOT NULL DEFAULT '[]',
            source_ids_json TEXT NOT NULL DEFAULT '[]', checked_at TEXT NOT NULL, expires_at TEXT,
            disposition TEXT NOT NULL DEFAULT 'pending', PRIMARY KEY(person_id,actor_id,project_key)
        );
        CREATE TABLE IF NOT EXISTS email_pattern_samples (
            company_domain TEXT NOT NULL, email TEXT NOT NULL COLLATE NOCASE,
            pattern_key TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
            provenance TEXT NOT NULL, PRIMARY KEY(company_domain,email)
        );
        CREATE TABLE IF NOT EXISTS verification_provider_control (
            provider TEXT PRIMARY KEY, disabled INTEGER NOT NULL DEFAULT 0,
            changed_by INTEGER, changed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS verification_provider_days (
            day TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0,
            allowance INTEGER NOT NULL CHECK(allowance BETWEEN 0 AND 25)
        );
        CREATE TABLE IF NOT EXISTS verification_reservations (
            id TEXT PRIMARY KEY, day TEXT NOT NULL, actor_id INTEGER NOT NULL,
            address_hash TEXT NOT NULL, person_id INTEGER, manual INTEGER NOT NULL,
            reason TEXT, status TEXT NOT NULL DEFAULT 'reserved', created_at TEXT NOT NULL,
            UNIQUE(day,person_id,manual)
        );
        CREATE TABLE IF NOT EXISTS verification_cache (
            address_hash TEXT NOT NULL, actor_id INTEGER NOT NULL, result_json TEXT NOT NULL,
            expires_at TEXT NOT NULL, PRIMARY KEY(address_hash,actor_id)
        );
        CREATE TABLE IF NOT EXISTS candidate_suppressions (
            email TEXT PRIMARY KEY COLLATE NOCASE,
            state TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS discovery_run_owners (
            scrape_run_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS company_mail_domains (
            website_domain TEXT NOT NULL,
            mail_domain TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'observed',
            company_name TEXT,
            mx_ok INTEGER,
            catch_all INTEGER,
            hard_to_reach INTEGER NOT NULL DEFAULT 0,
            sample_count INTEGER NOT NULL DEFAULT 0,
            bounce_count INTEGER NOT NULL DEFAULT 0,
            reply_count INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0.5,
            sources_json TEXT NOT NULL DEFAULT '[]',
            note TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (website_domain, mail_domain)
        );
        CREATE INDEX IF NOT EXISTS idx_company_mail_domains_mail
            ON company_mail_domains(mail_domain);
    """)
    for table in ('generated_emails', 'outreach_dispatches'):
        columns = {row['name'] for row in await (await db.execute(f'PRAGMA table_info({table})')).fetchall()}
        if columns and 'evidence_json' not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{{}}'")


async def migrate_catalog_evidence(db):
    from app.services.contact_intelligence import ingest_contact
    rows = await (await db.execute('''SELECT c.* FROM contacts c LEFT JOIN catalog_evidence e
        ON e.contact_id=c.id WHERE e.contact_id IS NULL ORDER BY c.id''')).fetchall()
    for row in rows:
        contact = dict(row)
        # Historical verified flags and model verdicts cannot establish mailbox or identity.
        await ingest_contact(db, contact=contact, actor_id=int(contact.get('owner_id') or 0),
                             origin='imported_without_evidence')
