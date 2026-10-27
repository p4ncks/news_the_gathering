"""Storage contracts use temporary databases only; no publisher or live DB access."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_DEBUG", "true")

from news import database


LEGACY_SCHEMA = """
CREATE TABLE articles (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', published_at TEXT,
    collected_at TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', UNIQUE(source_id, url)
);
CREATE TABLE headlines (
    source_id TEXT NOT NULL, rank INTEGER NOT NULL,
    article_id INTEGER NOT NULL REFERENCES articles(id), PRIMARY KEY(source_id, rank)
);
CREATE TABLE source_status (
    source_id TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, succeeded_at TEXT,
    error TEXT NOT NULL DEFAULT ''
);
"""


def legacy_database(path, broken=False):
    with sqlite3.connect(path) as db:
        db.executescript(LEGACY_SCHEMA)
        for identity in (7, 9):
            db.execute("INSERT INTO articles VALUES (?,?,?,?,?,?,?,?)", (
                identity, "bbc", f"https://example.test/{identity}", f"Story {identity}", "Summary",
                "2026-10-20T09:00:00+00:00", "2026-10-24T10:00:00+00:00", "Saved article text"))
        db.execute("INSERT INTO headlines VALUES (?,?,?)", ("cnn" if broken else "bbc", 1, 7))
        db.execute("INSERT INTO source_status VALUES (?,?,?,?)", (
            "bbc", "2026-10-24T10:30:00+00:00", "2026-10-24T10:00:00+00:00", "Temporary publisher outage"))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "news.db"
        database.initialize(self.path)

    def article(self, name="one", **extra):
        return {"url": f"https://example.test/{name}", "title": f"Story {name}", "summary": "A summary", **extra}

    def count(self, table):
        with database.connect(self.path, read_only=True) as db:
            return db.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def test_legacy_upgrade_preserves_history_ids_bodies_ranks_and_status(self):
        legacy = Path(self.directory.name) / "legacy.db"
        legacy_database(legacy)
        database.initialize(legacy)
        database.initialize(legacy)
        rows = database.get_headlines("bbc", legacy)
        self.assertEqual(rows[0]["id"], 7)
        self.assertEqual(rows[0]["body"], "Saved article text")
        self.assertEqual(rows[0]["first_seen_at"], "2026-10-24T10:00:00+00:00")
        self.assertEqual(rows[0]["last_seen_at"], rows[0]["collected_at"])
        self.assertEqual(rows[0]["body_metadata_hash"], "")
        self.assertEqual(database.get_statuses(legacy)["bbc"]["error"], "Temporary publisher outage")
        self.assertEqual(len(database.get_editions("bbc", path=legacy)), 1)
        with database.connect(legacy, read_only=True) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 2)
            self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())

    def test_bad_legacy_import_rolls_back_schema_and_records(self):
        legacy = Path(self.directory.name) / "broken.db"
        legacy_database(legacy, broken=True)
        with self.assertRaises(RuntimeError):
            database.initialize(legacy)
        with sqlite3.connect(legacy) as db:
            self.assertNotIn("url_hash", [row[1] for row in db.execute("PRAGMA table_info(articles)")])
            self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT source_id FROM headlines").fetchone()[0], "cnn")

    def test_url_identity_keeps_long_urls_and_case_distinct(self):
        lower = self.article("a" * 3000)
        upper = self.article("A" * 3000)
        database.save_collection("bbc", [lower, upper], self.path)
        self.assertEqual(self.count("articles"), 2)
        self.assertEqual([row["url"] for row in database.get_headlines("bbc", self.path)], [lower["url"], upper["url"]])

    def test_first_seen_and_body_freshness_survive_failed_refresh(self):
        before = "2026-10-20T10:00:00+00:00"
        after = "2026-10-24T10:00:00+00:00"
        with patch("news.database.utc_now", return_value=before):
            database.save_collection("bbc", [self.article(body="Original body", body_fetched_at=before,
                                                          body_metadata_hash="a" * 64)], self.path)
        with patch("news.database.utc_now", return_value=after):
            database.save_collection("bbc", [self.article(title="Updated headline", body="")], self.path)
        row = database.get_headlines("bbc", self.path)[0]
        self.assertEqual(row["first_seen_at"], before)
        self.assertEqual(row["last_seen_at"], after)
        self.assertEqual(row["body_fetched_at"], before)
        self.assertEqual(row["body_metadata_hash"], "a" * 64)
        self.assertEqual(row["body"], "Original body")

    def test_snapshots_are_immutable_and_unchanged_collections_do_not_duplicate(self):
        database.save_collection("bbc", [self.article()], self.path)
        database.save_collection("bbc", [self.article()], self.path)
        self.assertEqual(self.count("editions"), 1)
        database.save_collection("bbc", [self.article(title="Updated title", summary="Updated summary")], self.path)
        snapshots = database.get_editions("bbc", path=self.path)
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[1]["headlines"][0]["title"], "Story one")
        self.assertEqual(snapshots[0]["headlines"][0]["summary"], "Updated summary")

    def test_snapshot_bound_uses_configured_maximum(self):
        with patch.dict(os.environ, {"NEWS_MAX_EDITIONS": "3"}):
            for number in range(6):
                database.save_collection("bbc", [self.article(str(number))], self.path)
        self.assertEqual(self.count("editions"), 3)
        self.assertEqual(self.count("edition_headlines"), 3)
        self.assertEqual(database.get_headlines("bbc", self.path)[0]["title"], "Story 5")

    def test_dashboard_is_one_read_snapshot_and_does_not_select_body(self):
        database.save_collection("bbc", [self.article(body="Long body" * 1000)], self.path)
        database._django()
        from django.db import connections
        alias = database._alias(self.path, read_only=True)
        statements = []
        def inspect(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)
        with connections[alias].execute_wrapper(inspect):
            statuses, headlines = database.get_dashboard(["bbc", "missing"], self.path)
        selects = [query for query in statements if query.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(selects), 1)
        self.assertNotIn("a.body,", selects[0])
        self.assertEqual(statuses["bbc"]["succeeded_at"], headlines["bbc"][0]["collected_at"])
        self.assertTrue(headlines["bbc"][0]["has_body"])
        self.assertNotIn("body", headlines["bbc"][0])
        self.assertEqual(headlines["missing"], [])
        self.assertEqual(database.get_article("bbc", headlines["bbc"][0]["id"], self.path)["body"], "Long body" * 1000)
        self.assertIsNone(database.get_article("g1", headlines["bbc"][0]["id"], self.path))

    def test_read_alias_rejects_writes_and_missing_reads_create_nothing(self):
        from django.db import DatabaseError, connections
        with database._using(self.path, read_only=True) as alias:
            with self.assertRaises(DatabaseError):
                with connections[alias].cursor() as cursor:
                    cursor.execute("DELETE FROM articles")
        missing = Path(self.directory.name) / "does-not-exist" / "news.db"
        self.assertEqual(database.get_headlines("bbc", missing), [])
        self.assertEqual(database.get_dashboard(["bbc"], missing), ({}, {"bbc": []}))
        self.assertFalse(missing.parent.exists())

    def test_prune_preserves_active_headlines_and_retained_snapshot_references(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        old = (now - timedelta(days=60)).isoformat()
        with patch("news.database.utc_now", return_value=old):
            database.save_collection("bbc", [self.article("obsolete")], self.path)
            database.save_collection("g1", [self.article("still-active")], self.path)
        database.save_collection("bbc", [self.article("current")], self.path)
        database.save_collection("cnn", [self.article("snapshot-protected")], self.path)
        database.save_collection("cnn", [self.article("cnn-current")], self.path)
        with database.connect(self.path) as db:
            db.execute("UPDATE articles SET last_seen_at=? WHERE url=?", (old, self.article("snapshot-protected")["url"]))
        result = database.prune(self.path)
        self.assertEqual(result["articles"], 1)
        with database.connect(self.path, read_only=True) as db:
            names = {row[0] for row in db.execute("SELECT title FROM articles")}
            self.assertNotIn("Story obsolete", names)
            self.assertIn("Story still-active", names)
            self.assertIn("Story snapshot-protected", names)
            self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())

    def test_run_diagnostics_keep_metrics_and_failure_counts(self):
        results = [{"source": "BBC", "source_id": "bbc", "count": 10, "error": "", "warnings": ["Old timestamp"], "duration_ms": 14},
                   {"source": "CNN", "source_id": "cnn", "count": 0, "error": "Timed out", "duration_ms": 20}]
        database.record_run(results, self.path)
        from news.models import CollectionRun
        with database._using(self.path) as alias:
            row = CollectionRun.objects.using(alias).get()
            self.assertEqual((row.sources_attempted, row.sources_succeeded, row.sources_failed), (2, 1, 1))
            self.assertEqual(row.diagnostics[0]["warnings"], ["Old timestamp"])
            self.assertEqual(row.diagnostics[1]["duration_ms"], 20)

    def test_backup_is_consistent_and_compact_export_keeps_current_only(self):
        database.save_collection("bbc", [self.article("old", body="Historical body")], self.path)
        database.save_collection("bbc", [self.article("new", body="Current body")], self.path)
        backup = Path(self.directory.name) / "backup.db"
        snapshot = Path(self.directory.name) / "snapshot.db"
        database.backup(backup, self.path)
        database.export_snapshot(snapshot, self.path)
        self.assertEqual(database.get_headlines("bbc", backup)[0]["body"], "Current body")
        with database.connect(backup, read_only=True) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 2)
        with database.connect(snapshot, read_only=True) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM editions").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        with self.assertRaises(FileExistsError):
            database.backup(backup, self.path)

    def test_import_legacy_readonly_source_and_repeat_idempotently(self):
        legacy = Path(self.directory.name) / "legacy.db"
        legacy_database(legacy)
        before = legacy.read_bytes()
        result = database.import_sqlite(legacy, self.path)
        self.assertEqual(result["articles"], 2)
        self.assertFalse(result["already_imported"])
        again = database.import_sqlite(legacy, self.path)
        self.assertTrue(again["already_imported"])
        self.assertEqual(legacy.read_bytes(), before)
        self.assertEqual(database.get_headlines("bbc", self.path)[0]["id"], 7)
        self.assertEqual(len(database.get_editions("bbc", path=self.path)), 1)

    def test_import_current_backup_preserves_all_history_and_rejects_merge(self):
        source = Path(self.directory.name) / "source.db"
        database.initialize(source)
        database.save_collection("bbc", [self.article()], source)
        database.save_collection("bbc", [self.article(title="Revised")], source)
        database.record_run([{"source": "BBC", "count": 1, "error": ""}], source)
        result = database.import_sqlite(source, self.path)
        self.assertEqual(result["editions"], 2)
        self.assertEqual(result["collection_runs"], 1)
        self.assertTrue(database.import_sqlite(source, self.path)["already_imported"])
        database.save_collection("bbc", [self.article("another")], self.path)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            database.import_sqlite(source, self.path)

    def test_all_ranks_and_snapshots_roll_back_on_invalid_collection(self):
        database.save_collection("bbc", [self.article()], self.path)
        with self.assertRaises(KeyError):
            database.save_collection("bbc", [self.article("new"), {"url": "https://example.test/bad"}], self.path)
        self.assertEqual(self.count("editions"), 1)
        self.assertEqual(self.count("articles"), 1)
        self.assertEqual(database.get_headlines("bbc", self.path)[0]["title"], "Story one")
