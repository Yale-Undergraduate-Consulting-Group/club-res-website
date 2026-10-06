#!/usr/bin/env python3
"""Online SQLite backup and isolated restore rehearsal; never overwrites a target."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile


def backup(source: Path, destination: Path) -> dict:
    if not source.is_file() or destination.exists():
        raise ValueError('Existing source and a new destination are required')
    source = source.resolve()
    destination = destination.resolve()
    with destination.open("xb"):
        pass
    destination.chmod(0o600)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
            if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
    destination.chmod(0o600)
    checksum = hashlib.sha256()
    with destination.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return {'bytes': destination.stat().st_size, 'sha256': checksum.hexdigest()}


def rehearse(source: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix='yucg-restore-') as directory:
        snapshot = Path(directory) / 'snapshot.db'
        result = backup(source, snapshot)
        restored = Path(directory) / 'restored.db'
        backup(snapshot, restored)
        with closing(sqlite3.connect(restored)) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            # Identifiers come only from SQLite's own schema; quote embedded quotes.
            result['row_counts'] = {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
        result['restored'] = True
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(backup(args.source, args.destination) if args.destination else rehearse(args.source)))
