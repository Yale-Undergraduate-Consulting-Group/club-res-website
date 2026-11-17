"""The nightly SQLite backup restores: ops/backup_sqlite.py, offline, synthetic data.

The box runs it as `backup_sqlite.py /data/clientreach.db --destination <snapshot>`
while the app writes in WAL mode; ops/restore-drill.sh then restores the S3 copy.
This proves the local half: a snapshot taken under a live WAL writer has every
committed row, matches its checksum, passes integrity_check, and restores."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'ops'))
from backup_sqlite import backup, rehearse


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.live = self.dir / 'live.db'
        self.writer = sqlite3.connect(self.live)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute('CREATE TABLE members (id INTEGER PRIMARY KEY, email TEXT)')
        self.writer.executemany('INSERT INTO members (email) VALUES (?)', [(f'm{i}@example.test',) for i in range(500)])
        self.writer.commit()
        # Committed but not yet checkpointed into the main file: only in the WAL.
        self.writer.execute("INSERT INTO members (email) VALUES ('wal-only@example.test')")
        self.writer.commit()

    def tearDown(self):
        self.writer.close()

    def test_snapshot_of_a_live_wal_database_restores_every_committed_row(self):
        snapshot = self.dir / 'snapshot.db'
        manifest = backup(self.live, snapshot)
        self.assertEqual(manifest['sha256'], hashlib.sha256(snapshot.read_bytes()).hexdigest())
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        restored = self.dir / 'restored.db'
        backup(snapshot, restored)
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT count(*) FROM members').fetchone()[0], 501)
            self.assertEqual(db.execute("SELECT count(*) FROM members WHERE email='wal-only@example.test'").fetchone()[0], 1)

    def test_rehearsal_reports_row_counts_and_never_overwrites(self):
        result = rehearse(self.live)
        self.assertTrue(result['restored'])
        self.assertEqual(result['row_counts'], {'members': 501})
        existing = self.dir / 'existing.db'
        existing.write_bytes(b'keep me')
        with self.assertRaises(ValueError):
            backup(self.live, existing)
        self.assertEqual(existing.read_bytes(), b'keep me')


if __name__ == '__main__':
    unittest.main()
