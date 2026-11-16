"""Import the club's OneDrive export into Resources. Admin-only: it needs the
server's database and storage configuration, and the files it stores are owned
by an admin account.

    python scripts/import_resources.py <export.zip> [--dry-run] [--as admin@yale.edu]

DATABASE_URL picks the database. Files go to DOCUMENTS_BUCKET (the register's
private bucket); with no bucket, and APP_ENV unset, local or beta, to
DOCUMENTS_LOCAL_DIR (default /tmp/yucg-documents). Re-running updates and never
duplicates. --dry-run reads only the zip's table and file list and prints what
would be imported and who may see it; it writes nothing and stores nothing.
It logs counts, client and folder names; never file text, revenue or contacts.
"""
import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# The import never issues a session; a throwaway secret lets the app modules load.
os.environ.setdefault("JWT_SECRET", secrets.token_hex(32))

from app.services.resource_import import run_import  # noqa: E402


def report(summary: dict) -> None:
    print(f"Table rows: {summary['table_rows']}, matched to a folder: {summary['matched']}")
    print(f"Resources: {summary['resources']}; files: {summary['files']} ({summary['admin_only_files']} admin-only statements of work)")
    print("Visibility (kind/visibility: resources):")
    for key, count in summary["visibility"].items():
        print(f"  {key}: {count}")
    print(f"Folders with no table row ({len(summary['folders_without_row'])}; imported as private):")
    for name in summary["folders_without_row"]:
        print(f"  {name}")
    print(f"Table rows with no folder ({len(summary['rows_without_folder'])}; imported without files):")
    for name in summary["rows_without_folder"]:
        print(f"  {name}")
    if summary.get("team_names"):
        names = summary["team_names"]
        print(f"Team names: {names['names']}, would match a member account today: {names['would_match']}")
    if "team" in summary:
        print(f"Team names matched: {summary['team']['matched']}, left as text: {summary['team']['unmatched']}")
    if "file_results" in summary:
        print(f"Files: {summary['file_results']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("zip", help="the OneDrive export")
    parser.add_argument("--dry-run", action="store_true", help="plan only; write and store nothing")
    parser.add_argument("--as", dest="admin", help="email of the admin account that owns the files (default: the first admin)")
    args = parser.parse_args()
    report(asyncio.run(run_import(args.zip, dry_run=args.dry_run, admin_email=args.admin)))


if __name__ == "__main__":
    main()
