"""Additive schema for account-bound jobs and club workspace records."""
async def initialize_workspace_schema(db):
    await db.executescript('''
    CREATE TABLE IF NOT EXISTS outreach_dispatches (
      dispatch_key TEXT PRIMARY KEY, campaign_contact_id INTEGER NOT NULL,
      sender_user_id INTEGER NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
      body TEXT NOT NULL, signature TEXT NOT NULL DEFAULT '', signature_image_url TEXT,
      delay_days INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'ready', claimed_at TEXT, completed_at TEXT,
      last_error TEXT, gmail_message_id TEXT
    );
    CREATE TABLE IF NOT EXISTS membership_invitations (
      id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE, created_by INTEGER NOT NULL REFERENCES users(id),
      role TEXT NOT NULL DEFAULT 'standard', project_ids TEXT NOT NULL DEFAULT '[]',
      state TEXT NOT NULL DEFAULT 'pending', delivery_state TEXT NOT NULL DEFAULT 'ready',
      expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, sent_at INTEGER,
      accepted_at INTEGER, accepted_user_id INTEGER, delivery_error TEXT
    );
    CREATE INDEX IF NOT EXISTS invitations_email ON membership_invitations(email);
    CREATE TABLE IF NOT EXISTS oauth_challenges (
      state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, purpose TEXT NOT NULL,
      user_id INTEGER, invitation_id INTEGER, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS workspace_documents (
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
      owner_user_id INTEGER NOT NULL REFERENCES users(id), project_id INTEGER REFERENCES projects(id),
      visibility TEXT NOT NULL DEFAULT 'private', current_version INTEGER NOT NULL DEFAULT 0,
      revision INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS workspace_document_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL REFERENCES workspace_documents(id),
      uploaded_by INTEGER NOT NULL REFERENCES users(id), object_key TEXT NOT NULL UNIQUE,
      filename TEXT NOT NULL, byte_size INTEGER NOT NULL, content_type TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending', s3_version_id TEXT, storage_bucket TEXT, presign_expires_at INTEGER, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS workspace_document_shares (
      id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL REFERENCES workspace_documents(id),
      token_hash TEXT NOT NULL UNIQUE, created_by INTEGER NOT NULL REFERENCES users(id),
      expires_at INTEGER NOT NULL, revoked_at INTEGER
    );
    /* The assistant's index, chunk, thread and message tables are no longer
       created: the chatbot was removed. Existing databases keep theirs - the
       club's production data is not dropped by a schema pass - but nothing
       reads or writes them. */
    CREATE INDEX IF NOT EXISTS dispatch_contact_state ON outreach_dispatches(campaign_contact_id,state);
    /* The drain counts today's claims per sender on every tick. */
    CREATE INDEX IF NOT EXISTS dispatch_sender_claimed ON outreach_dispatches(sender_user_id,claimed_at);
    CREATE INDEX IF NOT EXISTS documents_owner_date ON workspace_documents(owner_user_id,created_at);
    CREATE INDEX IF NOT EXISTS documents_project_visibility ON workspace_documents(project_id,visibility);
    CREATE INDEX IF NOT EXISTS invitation_email_state ON membership_invitations(email,state);
    ''')
    from app.database import is_postgres
    if not is_postgres():
        version_columns = {r['name'] for r in await (await db.execute('PRAGMA table_info(workspace_document_versions)')).fetchall()}
        if 'storage_bucket' not in version_columns:
            await db.execute('ALTER TABLE workspace_document_versions ADD COLUMN storage_bucket TEXT')
        if 'presign_expires_at' not in version_columns:
            await db.execute('ALTER TABLE workspace_document_versions ADD COLUMN presign_expires_at INTEGER')
        message_columns = {r['name'] for r in await (await db.execute('PRAGMA table_info(outreach_messages)')).fetchall()}
        if 'dispatch_key' not in message_columns:
            await db.execute('ALTER TABLE outreach_messages ADD COLUMN dispatch_key TEXT')
        await db.execute('CREATE UNIQUE INDEX IF NOT EXISTS message_dispatch_key ON outreach_messages(dispatch_key) WHERE dispatch_key IS NOT NULL')
        columns = {r['name'] for r in await (await db.execute('PRAGMA table_info(outreach_dispatches)')).fetchall()}
        if 'delay_days' not in columns:
            await db.execute('ALTER TABLE outreach_dispatches ADD COLUMN delay_days INTEGER NOT NULL DEFAULT 0')
    await db.commit()
