"""Database setup for YUCG Outreach."""
import aiosqlite
import os
from pathlib import Path
from urllib.parse import quote_plus

DB_PATH = Path(__file__).parent.parent / "clientreach.db"


def _build_database_url() -> str:
    explicit = (os.getenv("DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    host = (os.getenv("DB_HOST") or "").strip()
    if host:
        user = quote_plus(os.getenv("DB_USER") or "clientreach")
        password = quote_plus(os.getenv("DB_PASSWORD") or "")
        port = (os.getenv("DB_PORT") or "5432").strip()
        name = (os.getenv("DB_NAME") or "clientreach").strip()
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"
    return f"sqlite:///{DB_PATH}"


def database_url() -> str:
    """Live URL so tests can point at a temp file after import."""
    return _build_database_url()


DATABASE_URL = database_url()


def is_postgres() -> bool:
    return database_url().startswith("postgres")


def sqlite_file_path() -> Path:
    url = database_url()
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///") :])
    return DB_PATH


def row_to_dict(row):
    """Convert sqlite3.Row to dict (Row has no .get() method)."""
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(row.keys(), row))


async def get_db():
    """Get database connection (SQLite locally, Postgres when DATABASE_URL/DB_HOST is set)."""
    if is_postgres():
        from app.db_compat import connect_postgres

        return await connect_postgres(database_url())
    db = await aiosqlite.connect(str(sqlite_file_path()))
    # ponytail: one-file SQLite. WAL + timeout is the club ceiling; two API boxes need Postgres.
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    """Initialize database schema."""
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS outreach_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_contact_id INTEGER NOT NULL REFERENCES campaign_contacts(id) ON DELETE CASCADE,
                sender_id INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                tracking_token TEXT NOT NULL UNIQUE,
                rfc_message_id TEXT NOT NULL UNIQUE,
                gmail_message_id TEXT,
                gmail_thread_id TEXT,
                sent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS outreach_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL REFERENCES outreach_messages(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                detail TEXT,
                UNIQUE(message_id, kind, source_id)
            );
            CREATE TABLE IF NOT EXISTS gmail_sync_state (
                user_id INTEGER PRIMARY KEY,
                last_success_at TEXT,
                last_attempt_at TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_outreach_messages_sender ON outreach_messages(sender_id, sent_at);
            CREATE INDEX IF NOT EXISTS idx_outreach_messages_contact ON outreach_messages(campaign_contact_id);
        """)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                email TEXT UNIQUE NOT NULL,
                title TEXT,
                company TEXT,
                company_domain TEXT,
                linkedin_url TEXT,
                confidence TEXT DEFAULT 'medium',
                department TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS campaign_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER,
                contact_id INTEGER,
                email_subject TEXT,
                email_body TEXT,
                status TEXT DEFAULT 'pending',
                step_number INTEGER DEFAULT 1,
                sent_at TIMESTAMP,
                opened_at TIMESTAMP,
                replied_at TIMESTAMP,
                last_error TEXT,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id),
                FOREIGN KEY (contact_id) REFERENCES contacts(id),
                UNIQUE (campaign_id, contact_id)
            );

            CREATE TABLE IF NOT EXISTS email_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_contact_id INTEGER,
                event_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_contact_id) REFERENCES campaign_contacts(id)
            );

            CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company);
            CREATE INDEX IF NOT EXISTS idx_campaign_contacts_campaign ON campaign_contacts(campaign_id);
            CREATE INDEX IF NOT EXISTS idx_campaign_contacts_status ON campaign_contacts(status);
            CREATE INDEX IF NOT EXISTS idx_campaign_contacts_contact ON campaign_contacts(contact_id);

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT,
                picture TEXT,
                google_id TEXT UNIQUE,
                access_token TEXT,
                refresh_token TEXT,
                token_expires_at REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS login_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                name TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_login_log_user ON login_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_login_log_created ON login_log(created_at);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS generated_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                contact_id INTEGER NOT NULL,
                subject TEXT,
                body TEXT,
                signature TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            );

            CREATE TABLE IF NOT EXISTS custom_email_formats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                pattern TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_generated_emails_contact ON generated_emails(contact_id);
            CREATE INDEX IF NOT EXISTS idx_generated_emails_created ON generated_emails(created_at);
        """)
        await db.commit()
        # Older SQLite databases predate the table-level uniqueness rule above.
        # Preserve their history while preventing any new duplicate recipient row.
        if not is_postgres():
            await db.executescript("""
                CREATE TRIGGER IF NOT EXISTS prevent_duplicate_campaign_contact
                BEFORE INSERT ON campaign_contacts
                WHEN EXISTS (
                    SELECT 1 FROM campaign_contacts
                    WHERE campaign_id = NEW.campaign_id AND contact_id = NEW.contact_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'duplicate campaign recipient');
                END;
            """)
            await db.commit()
        # Migration: add user_id to existing generated_emails (if table exists without it)
        try:
            await db.execute("ALTER TABLE generated_emails ADD COLUMN user_id INTEGER REFERENCES users(id)")
            await db.commit()
        except Exception:
            pass  # Column already exists
        try:
            await db.execute("CREATE INDEX IF NOT EXISTS idx_generated_emails_user ON generated_emails(user_id)")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE generated_emails ADD COLUMN campaign_id INTEGER REFERENCES campaigns(id)"
            )
            await db.commit()
        except Exception:
            pass
        try:
            # Fingerprint of the exact inputs a draft was generated from, so a
            # repeated Generate with nothing changed is answered from the last
            # result instead of being billed as a fresh model call.
            await db.execute("ALTER TABLE generated_emails ADD COLUMN brief_hash TEXT")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_generated_emails_brief "
                "ON generated_emails(user_id, contact_id, brief_hash)"
            )
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_campaign_contacts_contact ON campaign_contacts(contact_id)"
            )
            await db.commit()
        except Exception:
            pass
        # Paging and company filtering at catalogue scale. The list orders by
        # created_at and filters on LOWER(TRIM(company)); without these the
        # first page of a 200k-row catalogue is a full scan.
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_contacts_created ON contacts(created_at DESC, id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_contacts_company_key ON contacts(LOWER(TRIM(company)))",
            "CREATE INDEX IF NOT EXISTS idx_campaign_contacts_sent ON campaign_contacts(contact_id, sent_at)",
        ):
            try:
                await db.execute(statement)
                await db.commit()
            except Exception:
                pass
        # Migration: add OAuth token columns for Gmail API
        for col, col_type in [("access_token", "TEXT"), ("refresh_token", "TEXT"), ("token_expires_at", "REAL")]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass  # Column already exists

        # Pipeline, notes, templates, sequences, profile analysis, sentiment
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS contact_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER NOT NULL,
                user_id INTEGER,
                note TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (contact_id) REFERENCES contacts(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS contact_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            );
            CREATE TABLE IF NOT EXISTS email_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                industry TEXT,
                use_case TEXT,
                user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS follow_up_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS follow_up_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id INTEGER NOT NULL,
                days_after INTEGER NOT NULL DEFAULT 0,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                step_order INTEGER DEFAULT 0,
                FOREIGN KEY (sequence_id) REFERENCES follow_up_sequences(id)
            );
            CREATE TABLE IF NOT EXISTS contact_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER UNIQUE NOT NULL,
                value_proposition TEXT,
                role_summary TEXT,
                online_sentiment TEXT,
                receptiveness_notes TEXT,
                industry TEXT,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            );
            CREATE TABLE IF NOT EXISTS email_sentiment_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                body TEXT,
                contact_id INTEGER,
                sentiment_score REAL,
                sentiment_label TEXT,
                industry_fit TEXT,
                suggested_improvements TEXT,
                parameters_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            );
            CREATE TABLE IF NOT EXISTS ab_test_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER,
                name TEXT,
                subject TEXT,
                body TEXT,
                variant_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
            );
            CREATE INDEX IF NOT EXISTS idx_contact_notes_contact ON contact_notes(contact_id);
            CREATE INDEX IF NOT EXISTS idx_contact_activities_contact ON contact_activities(contact_id);
            CREATE INDEX IF NOT EXISTS idx_follow_up_steps_sequence ON follow_up_steps(sequence_id);
        """)
        await db.commit()

        # Migration: add pipeline_status, owner_id, email_verified, contact_source to contacts
        for col, col_type in [
            ("pipeline_status", "TEXT DEFAULT 'cold'"),
            ("owner_id", "INTEGER REFERENCES users(id)"),
            ("email_verified", "INTEGER DEFAULT 0"),
            ("contact_source", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE contacts ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass

        # Migration: follow-up sequences on campaigns
        try:
            await db.execute("ALTER TABLE campaigns ADD COLUMN sequence_id INTEGER REFERENCES follow_up_sequences(id)")
            await db.commit()
        except Exception:
            pass
        for col, col_type in [("sequence_step_sent", "INTEGER DEFAULT 0"), ("last_sequence_sent_at", "TIMESTAMP")]:
            try:
                await db.execute(f"ALTER TABLE campaign_contacts ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass

        for col, col_type in [
            ("gmail_thread_id", "TEXT"),
            ("gmail_message_id", "TEXT"),
            ("sent_by_user_id", "INTEGER REFERENCES users(id)"),
        ]:
            try:
                await db.execute(f"ALTER TABLE campaign_contacts ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass

        # Roles, audit, API keys, notifications, 2FA
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                name TEXT,
                scopes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id INTEGER PRIMARY KEY,
                admin_digest INTEGER DEFAULT 1,
                campaign_summary INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
        """)
        await db.commit()

        # Migration: add role, is_active, totp_secret to users
        for col, col_type in [
            ("role", "TEXT DEFAULT 'standard'"),
            ("is_active", "INTEGER DEFAULT 1"),
            ("totp_secret", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass

        # Outreach campaigns (community = institution priorities, individual = per-user)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS outreach_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'individual',
                owner_id INTEGER REFERENCES users(id),
                description TEXT,
                priority INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS outreach_campaign_contacts (
                campaign_id INTEGER NOT NULL REFERENCES outreach_campaigns(id),
                contact_id INTEGER NOT NULL REFERENCES contacts(id),
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (campaign_id, contact_id)
            );
            CREATE INDEX IF NOT EXISTS idx_outreach_campaigns_owner ON outreach_campaigns(owner_id);
            CREATE INDEX IF NOT EXISTS idx_outreach_campaign_contacts_campaign ON outreach_campaign_contacts(campaign_id);
        """)
        await db.commit()

        # User profiles (projects, experience, role, handles)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                projects TEXT,
                experience TEXT,
                role_title TEXT,
                linkedin_url TEXT,
                slack_handle TEXT,
                other_handles TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

        # Projects (semester + client, e.g. Spring 2026 - Project Lego)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                semester TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_project_assignments (
                user_id INTEGER NOT NULL REFERENCES users(id),
                project_id INTEGER NOT NULL REFERENCES projects(id),
                role_in_project TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, project_id)
            );
            CREATE INDEX IF NOT EXISTS idx_user_project_assignments_user ON user_project_assignments(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_project_assignments_project ON user_project_assignments(project_id);
        """)
        await db.commit()

        from app.workspace_schema import initialize_workspace_schema
        await initialize_workspace_schema(db)

        # Pending 2FA setup (secret stored here until verified; then moved to users.totp_secret)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS pending_2fa_setup (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                secret TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

        # Slack OAuth tokens (per-user). With token rotation enabled on the
        # Slack app, the access token expires (12 hours) and is exchanged using
        # a single-use refresh token, so both the refresh token and the expiry
        # are part of the credential rather than optional extras.
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS user_slack_tokens (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                token_expires_at REAL,
                team_id TEXT,
                team_name TEXT,
                user_slack_id TEXT,
                scope TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
        # Slack retries an event when the acknowledgement is slow, so the ids
        # already answered are recorded rather than kept in memory: the live
        # box replaces its container and a member must not be answered twice.
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS slack_events (
                event_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            );
        """)
        await db.commit()
        for statement in (
            "ALTER TABLE user_slack_tokens ADD COLUMN refresh_token TEXT",
            "ALTER TABLE user_slack_tokens ADD COLUMN token_expires_at REAL",
        ):
            try:
                await db.execute(statement)
                await db.commit()
            except Exception:
                pass

        # Email attachments library (intro PDFs, past workstreams, etc.)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS email_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                display_name TEXT,
                storage_path TEXT NOT NULL UNIQUE,
                file_size INTEGER,
                mime_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                owner_user_id INTEGER REFERENCES users(id)
            );
        """)
        await db.commit()
        try:
            await db.execute("ALTER TABLE email_attachments ADD COLUMN owner_user_id INTEGER REFERENCES users(id)")
            await db.commit()
        except Exception:
            pass

        # Usage/telemetry events for Operations Intelligence (private, internal only)
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                resource_type TEXT,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_usage_events_user ON usage_events(user_id);
            CREATE INDEX IF NOT EXISTS idx_usage_events_type ON usage_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_usage_events_created ON usage_events(created_at);

            CREATE TABLE IF NOT EXISTS yucg_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                content_type TEXT,
                content_text TEXT,
                file_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()

        # First user becomes admin if no admins exist
        try:
            cursor = await db.execute("SELECT COUNT(*) as n FROM users WHERE role = 'admin'")
            if (await cursor.fetchone())["n"] == 0:
                await db.execute("UPDATE users SET role = 'admin' WHERE id = (SELECT MIN(id) FROM users)")
                await db.commit()
        except Exception:
            pass

        # YUCGoutreach company discovery (legacy tables were apollo_* — renamed below)
        try:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='apollo_discovery_runs'"
            )
            has_legacy_runs = await cur.fetchone()
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='yucgoutreach_discovery_runs'"
            )
            has_new_runs = await cur.fetchone()
            if has_legacy_runs and not has_new_runs:
                await db.execute("ALTER TABLE apollo_discovery_runs RENAME TO yucgoutreach_discovery_runs")
                await db.commit()
        except Exception:
            pass
        try:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='apollo_prospects'"
            )
            has_legacy_p = await cur.fetchone()
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='yucgoutreach_prospects'"
            )
            has_new_p = await cur.fetchone()
            if has_legacy_p and not has_new_p:
                await db.execute("ALTER TABLE apollo_prospects RENAME TO yucgoutreach_prospects")
                await db.commit()
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE yucgoutreach_prospects RENAME COLUMN apollo_score TO yucgoutreach_score"
            )
            await db.commit()
        except Exception:
            pass

        await db.executescript("""
            CREATE TABLE IF NOT EXISTS yucgoutreach_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                company_name TEXT NOT NULL,
                company_domain TEXT,
                linkedin_company_url TEXT,
                max_prospects INTEGER NOT NULL DEFAULT 25,
                worker_concurrency INTEGER NOT NULL DEFAULT 4,
                status TEXT NOT NULL DEFAULT 'queued',
                progress_pct REAL DEFAULT 0,
                progress_message TEXT,
                prospects_count INTEGER DEFAULT 0,
                research_json TEXT,
                error_message TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                lease_token TEXT,
                lease_expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_yucgoutreach_runs_user ON yucgoutreach_discovery_runs(user_id);
            CREATE INDEX IF NOT EXISTS idx_yucgoutreach_runs_status ON yucgoutreach_discovery_runs(status);

            CREATE TABLE IF NOT EXISTS yucgoutreach_prospects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES yucgoutreach_discovery_runs(id) ON DELETE CASCADE,
                first_name TEXT,
                last_name TEXT,
                email TEXT,
                company TEXT,
                contact_url TEXT,
                title TEXT,
                account_url TEXT,
                photo_url TEXT,
                account_link TEXT,
                phone TEXT,
                phone_code TEXT,
                verified INTEGER DEFAULT 0,
                qualification_notes TEXT,
                contact_profile_url TEXT,
                linkedin_url TEXT,
                fit_status TEXT,
                score REAL,
                country TEXT,
                employees TEXT,
                industry TEXT,
                keywords_1 TEXT,
                keywords_2 TEXT,
                yucgoutreach_score REAL,
                evidence_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_yucgoutreach_prospects_run ON yucgoutreach_prospects(run_id);

            CREATE TABLE IF NOT EXISTS yucg_prospect_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL UNIQUE,
                sector TEXT,
                why_attractive TEXT,
                suggested_engagement_theme TEXT,
                yale_yucg_hook TEXT,
                source_row INTEGER,
                extra_json TEXT,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_yucg_prospect_targets_sector ON yucg_prospect_targets(sector);

            CREATE TABLE IF NOT EXISTS outreach_flows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                company_name TEXT NOT NULL,
                company_domain TEXT,
                title_hints TEXT,
                angle TEXT,
                max_contacts INTEGER NOT NULL DEFAULT 25,
                run_id INTEGER REFERENCES yucgoutreach_discovery_runs(id),
                campaign_id INTEGER REFERENCES campaigns(id),
                status TEXT NOT NULL DEFAULT 'discovering',
                progress_message TEXT,
                imported_count INTEGER NOT NULL DEFAULT 0,
                drafted_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                lease_token TEXT,
                lease_expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_outreach_flows_user ON outreach_flows(user_id);
            CREATE INDEX IF NOT EXISTS idx_outreach_flows_status ON outreach_flows(status);

            -- Bulk company register: the browsable pool Find people and the
            -- recommender draw from, ingested from free public registers.
            -- Tiers: us_public (SEC tickers), us_private (Form D filers),
            -- uk (Companies House). No paid provider feeds this table.
            CREATE TABLE IF NOT EXISTS company_register (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_key TEXT NOT NULL,
                tier TEXT NOT NULL,
                country TEXT NOT NULL DEFAULT 'US',
                company_name TEXT NOT NULL,
                company_domain TEXT,
                sector_code TEXT,
                sector_label TEXT,
                region TEXT,
                employees INTEGER,
                employees_source TEXT,
                last_event_at TEXT,
                last_event_amount REAL,
                last_event_kind TEXT,
                officer_count INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source, source_key)
            );
            CREATE INDEX IF NOT EXISTS idx_company_register_tier ON company_register(tier, country);
            CREATE INDEX IF NOT EXISTS idx_company_register_sector ON company_register(sector_label);
            CREATE INDEX IF NOT EXISTS idx_company_register_name ON company_register(company_name);
            CREATE INDEX IF NOT EXISTS idx_company_register_event ON company_register(last_event_at DESC);

            -- Officers named on a Form D filing: real, dated, company-reported.
            CREATE TABLE IF NOT EXISTS company_register_people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                register_id INTEGER NOT NULL REFERENCES company_register(id) ON DELETE CASCADE,
                full_name TEXT NOT NULL,
                relationship TEXT,
                -- board | executive | working | unknown. See
                -- classify_person_level: a filing's "Director" is a board seat.
                person_level TEXT,
                observed_at TEXT,
                source_url TEXT,
                UNIQUE (register_id, full_name)
            );
            CREATE INDEX IF NOT EXISTS idx_company_register_people ON company_register_people(register_id);

            CREATE TABLE IF NOT EXISTS company_register_ingests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                batch_key TEXT NOT NULL,
                rows_seen INTEGER NOT NULL DEFAULT 0,
                rows_written INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                detail TEXT,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source, batch_key)
            );
        """)
        await db.commit()

        # Two members working the same company is the failure a club notices:
        # the recipient sees two cold emails from the same society in a week.
        # A claim is a soft signal, not a lock - it says who is on this
        # company so the other person can ask, and it goes stale on its own
        # rather than needing anyone to remember to release it.
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS company_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_key TEXT NOT NULL UNIQUE,
                company_name TEXT NOT NULL,
                member_id INTEGER NOT NULL REFERENCES users(id),
                claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                released_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_company_claims_member ON company_claims(member_id);
        """)
        await db.commit()

        # Databases created before the column existed. Must run after the
        # register tables above, or the ALTER hits a table that is not there
        # yet, the exception is swallowed, and a fresh database silently
        # never gets the column.
        try:
            await db.execute("ALTER TABLE company_register_people ADD COLUMN person_level TEXT")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE company_register ADD COLUMN prominence_rank INTEGER")
            await db.commit()
        except Exception:
            pass

        for col, col_type in [
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("lease_token", "TEXT"),
            ("lease_expires_at", "TIMESTAMP"),
        ]:
            try:
                await db.execute(f"ALTER TABLE yucgoutreach_discovery_runs ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass

        for col, col_type in [
            ("email_verification_status", "TEXT"),
            ("ai_verdict", "TEXT"),
            ("ai_reason", "TEXT"),
            ("contact_source", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE yucgoutreach_prospects ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass

        if is_postgres():
            campaign_columns = {r["column_name"] for r in await (await db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'campaigns'"
            )).fetchall()}
        else:
            campaign_columns = {r["name"] for r in await (await db.execute("PRAGMA table_info(campaigns)")).fetchall()}
        for column in ("owner_user_id", "sender_user_id"):
            if column not in campaign_columns:
                await db.execute(f"ALTER TABLE campaigns ADD COLUMN {column} INTEGER REFERENCES users(id)")
        try:
            await db.execute("ALTER TABLE campaigns ADD COLUMN attachment_ids_json TEXT")
            await db.commit()
        except Exception:
            pass
        await db.commit()

        try:
            await db.execute("ALTER TABLE campaigns ADD COLUMN released_by INTEGER")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE campaign_contacts ADD COLUMN last_error TEXT")
            await db.commit()
        except Exception:
            pass


        await db.executescript("""
            CREATE TABLE IF NOT EXISTS company_email_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_domain TEXT NOT NULL,
                company_name TEXT,
                pattern_key TEXT NOT NULL,
                pattern_template TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                sample_count INTEGER DEFAULT 0,
                verified_samples INTEGER DEFAULT 0,
                failed_samples INTEGER DEFAULT 0,
                sources_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_domain, pattern_key)
            );
            CREATE INDEX IF NOT EXISTS idx_company_email_patterns_domain ON company_email_patterns(company_domain);

            CREATE TABLE IF NOT EXISTS contact_discovery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scrape_run_id TEXT NOT NULL,
                email TEXT,
                name TEXT,
                title TEXT,
                company TEXT,
                contact_source TEXT,
                source_url TEXT,
                linkedin_url TEXT,
                discovery_context TEXT,
                ai_verdict TEXT,
                ai_reason TEXT,
                ai_source_note TEXT,
                ai_model TEXT,
                email_verification_status TEXT,
                confidence TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_contact_discovery_logs_run ON contact_discovery_logs(scrape_run_id);
        """)
        await db.commit()

        # Migration: bounce feedback against a learned format.
        try:
            await db.execute(
                "ALTER TABLE company_email_patterns ADD COLUMN failed_samples INTEGER DEFAULT 0"
            )
            await db.commit()
        except Exception:
            pass

        for col, col_type in [
            ("email_verification_status", "TEXT"),
            ("email_pattern", "TEXT"),
            ("ai_verdict", "TEXT"),
            ("ai_reason", "TEXT"),
            ("ai_source_note", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE contacts ADD COLUMN {col} {col_type}")
                await db.commit()
            except Exception:
                pass

        # Migration: citable real client names + an explicit opt-in flag so a
        # project only ever surfaces in an outreach citation suggestion once
        # someone has confirmed it is not under NDA. Default 0 keeps every
        # existing project silent until re-tagged.
        try:
            await db.execute("ALTER TABLE projects ADD COLUMN client_name TEXT")
            await db.commit()
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE projects ADD COLUMN discussable INTEGER NOT NULL DEFAULT 0")
            await db.commit()
        except Exception:
            pass

        await db.executescript("""
            CREATE TABLE IF NOT EXISTS stored_objects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                created_by INTEGER,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS outreach_release_targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                row_index INTEGER,
                company TEXT NOT NULL,
                company_domain TEXT,
                sector TEXT,
                contact_type TEXT,
                incentive_score REAL,
                verification_source_url TEXT,
                find_status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (release_id) REFERENCES outreach_releases(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS outreach_release_people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                target_id INTEGER,
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (release_id) REFERENCES outreach_releases(id) ON DELETE CASCADE
            );
        """)
        from app.services.contact_intelligence_schema import init_contact_intelligence_schema
        # Deep research is gone: its tables stay in databases that already have
        # them (member data is not dropped by a refactor), and new ones never
        # create them.
        from app.services.roster_schema import init_roster_schema
        from app.services.mail_domain_map import ensure_mail_domain_schema
        await init_contact_intelligence_schema(db)
        await init_roster_schema(db)
        await ensure_mail_domain_schema(db)
        await db.commit()
    finally:
        await db.close()
