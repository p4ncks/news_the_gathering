"""Backups must restore correctly, survive writes and rotate only owned archives."""
import gzip
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DJANGO_DEBUG", "true")

from news import database
from news.maintenance import create_backup, verify_backup


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "news.db"
        environment = patch.dict(os.environ, {"NEWS_DB_ENGINE": "sqlite", "NEWS_DB_PATH": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)
        database.initialize()
        database.save_collection("bbc", [{
            "url": "https://www.bbc.com/news/test", "title": "A saved article",
            "summary": "Backup round trip", "published_at": None,
        }])

    def test_backup_restores_all_current_records_and_rotates(self):
        backups = self.directory / "backups"
        backups.mkdir()
        unrelated = backups / "my-important-file.gz"
        unrelated.write_bytes(b"leave this alone")
        original = create_backup(backups, keep=2)
        self.assertEqual(original["rows"]["headlines"], 1)
        self.assertTrue(original["verified"])
        create_backup(backups, keep=2)
        create_backup(backups, keep=2)
        self.assertEqual(len(list(backups.glob("news-*.sqlite3.gz"))), 2)
        self.assertEqual(unrelated.read_bytes(), b"leave this alone")

    def test_corrupt_archive_is_rejected(self):
        broken = self.directory / "broken.sqlite3.gz"
        broken.write_bytes(b"not gzip data")
        with self.assertRaises((OSError, ValueError)):
            verify_backup(broken)

    def test_verification_checks_database_contents_instead_of_trusting_the_name(self):
        archive = create_backup(self.directory / "backups")
        renamed = self.directory / "renamed.gz"
        renamed.write_bytes(Path(archive["path"]).read_bytes())
        self.assertEqual(verify_backup(renamed)["kind"], "sqlite")
        invalid = self.directory / "not-a-database.gz"
        with gzip.open(invalid, "wb") as destination:
            destination.write(b"CREATE TABLE example (id INTEGER);")
        with self.assertRaises(sqlite3.DatabaseError):
            verify_backup(invalid)

    def test_backup_is_a_consistent_snapshot_even_with_wal(self):
        with database.connect() as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE articles SET title='Committed title'")
        archive = create_backup(self.directory / "backups")
        restored = self.directory / "restored.db"
        with gzip.open(archive["path"], "rb") as source:
            restored.write_bytes(source.read())
        with sqlite3.connect(restored) as connection:
            self.assertEqual(connection.execute("SELECT title FROM articles").fetchone()[0],
                             "Committed title")
        self.assertEqual(verify_backup(archive["path"])["rows"]["articles"], 1)

    def test_rotation_rejects_zero_retained_backups(self):
        with self.assertRaises(ValueError):
            create_backup(self.directory / "backups", keep=0)
