"""Explicit database maintenance: initialization, retention and rotating backups."""
import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone

from news import database


def _copy_stream(source, destination):
    shutil.copyfileobj(source, destination, length=1024 * 1024)


def verify_backup(path):
    """Test gzip integrity and restore SQLite to a temporary database for checks."""
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="news-restore-check-") as directory:
        restored = Path(directory) / "restored"
        with gzip.open(path, "rb") as source, restored.open("wb") as destination:
            _copy_stream(source, destination)
        with sqlite3.connect(restored.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restored SQLite backup failed its integrity check.")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Restored SQLite backup contains broken foreign keys.")
            counts = {}
            for table in ("articles", "headlines", "source_status"):
                counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            return {"verified": True, "kind": "sqlite", "rows": counts}


def create_backup(directory, keep=7):
    """Create and verify a backup before rotating only this command's older files."""
    from gathering.database_config import database_settings
    database_settings()
    if keep < 1:
        raise ValueError("Keep at least one backup.")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    extension = ".sqlite3.gz"
    name = datetime.now(timezone.utc).strftime("news-%Y%m%dT%H%M%S%fZ") + extension
    final = directory / name
    with tempfile.TemporaryDirectory(prefix=".news-backup-", dir=directory) as temporary:
        temporary = Path(temporary)
        raw = temporary / "database"
        source_path = database.database_path()
        if not source_path.is_file():
            raise ValueError("There is no news database to back up.")
        with sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(raw) as destination:
                source.backup(destination)
        compressed = temporary / name
        with raw.open("rb") as source, gzip.open(compressed, "wb", compresslevel=6) as destination:
            _copy_stream(source, destination)
        compressed.chmod(0o600)
        verification = verify_backup(compressed)
        os.replace(compressed, final)
    previous = sorted(directory.glob("news-????????T????????????Z" + extension), reverse=True)
    for obsolete in previous[keep:]:
        obsolete.unlink()
    return {"path": str(final), **verification}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create tables or safely upgrade the existing schema")
    cleanup = commands.add_parser("prune", help="Apply the configured history limits")
    cleanup.add_argument("--days", type=int, default=int(os.environ.get("NEWS_RETENTION_DAYS", "30")))
    cleanup.add_argument("--max-editions", type=int, default=int(os.environ.get("NEWS_MAX_EDITIONS", "48")))
    backup = commands.add_parser("backup", help="Create a verified compressed backup")
    backup.add_argument("--directory", type=Path, required=True)
    backup.add_argument("--keep", type=int, default=7)
    verify = commands.add_parser("verify-backup", help="Verify an archive using a temporary restore")
    verify.add_argument("path", type=Path)
    importer = commands.add_parser("import-sqlite", help="Import a SQLite backup into an empty configured database")
    importer.add_argument("path", type=Path)
    snapshot = commands.add_parser("export", help="Export the current editions to a standalone SQLite snapshot")
    snapshot.add_argument("path", type=Path)
    editions = commands.add_parser("editions", help="Inspect the retained ranked editions for an outlet")
    editions.add_argument("--source", required=True)
    editions.add_argument("--limit", type=int, default=5)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "init":
            database.initialize()
            result = {"initialized": True}
        elif arguments.command == "prune":
            if arguments.days < 1 or arguments.max_editions < 1:
                parser.error("Retention days and edition count must both be positive.")
            result = database.prune(days=arguments.days, max_editions=arguments.max_editions)
        elif arguments.command == "backup":
            result = create_backup(arguments.directory, arguments.keep)
        elif arguments.command == "import-sqlite":
            result = database.import_sqlite(arguments.path)
        elif arguments.command == "export":
            result = {"path": str(database.export_snapshot(arguments.path))}
        elif arguments.command == "editions":
            if not 1 <= arguments.limit <= 48:
                parser.error("Choose an edition limit between 1 and 48.")
            result = database.get_editions(arguments.source, arguments.limit)
        else:
            result = verify_backup(arguments.path)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        parser.exit(1, f"Maintenance failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
