"""PostgreSQL behavior. Runs only when DATABASE_URL points at a local test database."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DJANGO_DEBUG", "true")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gathering.settings")

import django

django.setup()

from django.db import DatabaseError, ProgrammingError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, override_settings
from django.urls import reverse

from dashboard.models import OutletSuggestion
from news import database
from news.locking import ADVISORY_LOCK_KEY, collector_lock, CollectorAlreadyRunning
from news.maintenance import create_backup, main as maintenance, verify_backup
from news.models import Article, CollectionRun, Edition
from support import POSTGRES, initialize_default_database
from test_storage import legacy_database

RLS_TABLES = {"articles", "source_status", "headlines", "editions", "edition_headlines",
              "collection_runs", "dashboard_outletsuggestion", "django_migrations"}


def story(name="one", **extra):
    return {"url": f"https://example.test/{name}", "title": f"Story {name}", "summary": "A summary", **extra}


@unittest.skipUnless(POSTGRES, "PostgreSQL only: set DATABASE_URL to a local test database")
class PostgresTestCase(unittest.TestCase):
    def setUp(self):
        initialize_default_database()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)


class StorageTests(PostgresTestCase):
    def test_default_alias_is_used_for_reads_and_ignores_sqlite_path(self):
        with patch.dict(os.environ, {"NEWS_DB_PATH": str(self.directory / "ignored.db")}):
            self.assertEqual(database._alias(), "default")
            self.assertEqual(database._alias(read_only=True), "default")
            database.save_collection("bbc", [story()])
            self.assertEqual(database.get_headlines("bbc")[0]["title"], "Story one")
        self.assertFalse((self.directory / "ignored.db").exists())

    def test_explicit_path_is_an_isolated_sqlite_database_without_credentials(self):
        path = self.directory / "isolated.db"
        database.initialize(path)
        database.save_collection("bbc", [story("file")], path)
        settings = connections[database._alias(path)].settings_dict
        self.assertEqual(settings["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual((settings["USER"], settings["PASSWORD"], settings["HOST"], settings["PORT"]), ("", "", "", ""))
        self.assertEqual(database.get_headlines("bbc", path)[0]["title"], "Story file")
        self.assertEqual(database.get_headlines("bbc"), [])
        self.assertEqual(database.get_dashboard(["bbc"], self.directory / "missing.db"), ({}, {"bbc": []}))

    def test_collections_editions_runs_and_pruning_round_trip(self):
        database.save_collection("bbc", [story(body="Body text")])
        database.save_collection("bbc", [story()])
        database.save_collection("bbc", [story(title="Revised")])
        self.assertEqual(len(database.get_editions("bbc")), 2)
        statuses, headlines = database.get_dashboard(["bbc", "cnn"])
        self.assertEqual(headlines["bbc"][0]["title"], "Revised")
        self.assertIs(headlines["bbc"][0]["has_body"], True)
        self.assertEqual(headlines["cnn"], [])
        self.assertEqual(database.get_body("bbc", story()["url"]), "Body text")
        self.assertEqual(database.record_run([{"source": "BBC", "count": 1, "error": ""}]), 1)
        self.assertEqual(database.prune(days=30, max_editions=1)["editions"], 1)

    def test_nul_characters_reach_postgres_clean(self):
        dirty = {"url": "https://example.test/nu\x00l", "title": "Ti\x00tle", "summary": "Sum\x00mary", "body": "Bo\x00dy"}
        database.save_collection("bbc", [dirty])
        row = database.get_headlines("bbc")[0]
        self.assertEqual((row["url"], row["title"], row["summary"], row["body"]),
                         ("https://example.test/nul", "Title", "Summary", "Body"))
        self.assertEqual(row["url_hash"], database._hash("https://example.test/nul"))
        self.assertEqual(database.get_body("bbc", dirty["url"]), "Body")
        database.record_failure("bbc", "Time\x00out")
        self.assertEqual(database.get_statuses()["bbc"]["error"], "Timeout")
        database.record_run([{"source": "B\x00BC", "error": "Ba\x00d", "warnings": ["Wa\x00rn"]}])
        diagnostics = CollectionRun.objects.get().diagnostics[0]
        self.assertEqual((diagnostics["source"], diagnostics["error"], diagnostics["warnings"]), ("BBC", "Bad", ["Warn"]))

    def test_prepared_statements_stay_off_for_the_transaction_pooler(self):
        self.assertIsNone(connection.get_connection_params()["prepare_threshold"])

    def test_waiting_collector_releases_its_connection(self):
        connection.ensure_connection()
        database.release_connection()
        self.assertIsNone(connection.connection)

    def test_file_tools_refuse_and_point_to_postgres_backups(self):
        target = self.directory / "backup.db"
        calls = (lambda: database.backup(target), lambda: database.export_snapshot(target),
                 lambda: create_backup(self.directory), lambda: verify_backup(target))
        for call in calls:
            with self.assertRaisesRegex(RuntimeError, "pg_dump"):
                call()
        with self.assertRaisesRegex(RuntimeError, "pg_dump"):
            with database.connect():
                self.fail("Opened a SQLite file for a PostgreSQL default")
        self.assertFalse(target.exists())
        for command in (["backup", "--directory", str(self.directory)], ["verify-backup", str(target)], ["export", str(target)]):
            with self.subTest(command=command), self.assertRaises(SystemExit) as raised, patch("sys.stderr"):
                maintenance(command)
            self.assertEqual(raised.exception.code, 1)

    def test_file_tools_still_work_with_an_explicit_sqlite_path(self):
        path, target = self.directory / "source.db", self.directory / "backup.db"
        database.initialize(path)
        database.save_collection("bbc", [story()], path)
        database.backup(target, path)
        with database.connect(target, read_only=True) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 1)


class ImportTests(PostgresTestCase):
    def source(self):
        path = self.directory / "source.db"
        database.initialize(path)
        database.save_collection("bbc", [story(body="Saved body")], path)
        database.save_collection("bbc", [story(title="Revised")], path)
        database.save_collection("g1", [story("g1")], path)
        database.record_run([{"source": "BBC", "count": 1, "error": ""}], path)
        with database._using(path) as alias:
            OutletSuggestion.objects.using(alias).create(name="Paper", website="https://paper.example/", country="Brazil")
        return path

    def test_sequences_are_reset_so_later_inserts_do_not_collide(self):
        result = database.import_sqlite(self.source())
        self.assertFalse(result["already_imported"])
        self.assertEqual((result["articles"], result["editions"], result["collection_runs"]), (2, 3, 1))
        before = Article.objects.order_by("-pk").first().pk
        database.save_collection("bbc", [story("after-import")])
        database.record_run([{"source": "BBC", "count": 1, "error": ""}])
        OutletSuggestion.objects.create(name="Second", website="https://second.example/", country="Chile")
        self.assertGreater(Article.objects.get(title="Story after-import").pk, before)
        self.assertEqual(Edition.objects.count(), 4)

    def test_import_repeats_idempotently_and_rejects_a_merge(self):
        source = self.source()
        database.import_sqlite(source)
        self.assertTrue(database.import_sqlite(source)["already_imported"])
        database.save_collection("bbc", [story("another")])
        with self.assertRaisesRegex(ValueError, "nonempty"):
            database.import_sqlite(source)

    def test_legacy_source_generates_editions_and_stays_unchanged(self):
        legacy = self.directory / "legacy.db"
        legacy_database(legacy)
        before = legacy.read_bytes()
        self.assertEqual(database.import_sqlite(legacy)["articles"], 2)
        self.assertEqual(legacy.read_bytes(), before)
        self.assertEqual(database.get_headlines("bbc")[0]["id"], 7)
        self.assertEqual(len(database.get_editions("bbc")), 1)
        database.save_collection("g1", [story("fresh")])
        self.assertGreater(Article.objects.get(source_id="g1").pk, 9)

    def test_failed_import_leaves_the_target_empty(self):
        path = self.directory / "broken.db"
        legacy_database(path, broken=True)
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE headlines")
            db.execute("CREATE TABLE headlines (source_id TEXT, rank INTEGER, article_id INTEGER)")
            db.execute("INSERT INTO headlines VALUES ('cnn', 1, 7)")
        with self.assertRaisesRegex(ValueError, "another outlet"):
            database.import_sqlite(path)
        self.assertEqual(Article.objects.count(), 0)


class LockTests(PostgresTestCase):
    def held(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                           "AND ((classid::bigint << 32) | objid::bigint) = %s", [ADVISORY_LOCK_KEY])
            return cursor.fetchone()[0]

    def test_second_collector_cannot_enter_and_exit_releases(self):
        with collector_lock():
            self.assertEqual(self.held(), 1)
            with self.assertRaises(CollectorAlreadyRunning):
                with collector_lock():
                    self.fail("The second collector entered a locked database")
            self.assertEqual(self.held(), 1)
        self.assertEqual(self.held(), 0)
        with collector_lock():
            pass

    def test_failure_releases_the_lock(self):
        with self.assertRaises(ValueError):
            with collector_lock():
                raise ValueError("Collection failed")
        self.assertEqual(self.held(), 0)


class RowLevelSecurityTests(PostgresTestCase):
    def enabled_tables(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                           "JOIN pg_namespace n ON n.oid = c.relnamespace "
                           "WHERE n.nspname = 'public' AND c.relkind = 'r'")
            return {name: (enabled, forced) for name, enabled, forced in cursor.fetchall()}

    def test_every_public_table_is_protected_without_policies(self):
        tables = self.enabled_tables()
        self.assertEqual(set(tables), RLS_TABLES)
        self.assertTrue(all(enabled and not forced for enabled, forced in tables.values()))
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'")
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_owner_reads_and_writes_while_other_roles_see_nothing(self):
        # Superusers bypass row level security, so the checks use ordinary roles,
        # as Supabase does. Everything, including the roles, is rolled back.
        database.save_collection("bbc", [story()])
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("CREATE ROLE gathering_test_owner NOSUPERUSER NOBYPASSRLS")
            cursor.execute("CREATE ROLE gathering_test_anon NOSUPERUSER NOBYPASSRLS")
            for table in RLS_TABLES:
                cursor.execute(f'ALTER TABLE "{table}" OWNER TO gathering_test_owner')
                cursor.execute(f'GRANT SELECT, INSERT ON "{table}" TO gathering_test_anon')
            cursor.execute("GRANT USAGE ON SCHEMA public TO gathering_test_owner, gathering_test_anon")

            cursor.execute("SET LOCAL ROLE gathering_test_owner")
            cursor.execute("SELECT count(*) FROM articles")
            self.assertEqual(cursor.fetchone()[0], 1)
            database.save_collection("g1", [story("owner-write")])
            cursor.execute("SELECT count(*) FROM articles")
            self.assertEqual(cursor.fetchone()[0], 2)

            cursor.execute("SET LOCAL ROLE gathering_test_anon")
            for table in RLS_TABLES:
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                self.assertEqual(cursor.fetchone()[0], 0, table)
            with self.assertRaisesRegex(ProgrammingError, "row-level security"), transaction.atomic():
                cursor.execute("INSERT INTO source_status (source_id, attempted_at, error) VALUES ('x', now(), '')")
            transaction.set_rollback(True)

    def test_migration_reverses_and_reapplies(self):
        self.addCleanup(database.initialize)
        MigrationExecutor(connection).migrate([("dashboard", "0001_initial")])
        self.assertFalse(any(enabled for enabled, _ in self.enabled_tables().values()))
        database.initialize()
        self.assertTrue(all(enabled for enabled, _ in self.enabled_tables().values()))


class WebTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        settings = override_settings(ALLOWED_HOSTS=["testserver"])
        settings.enable()
        self.addCleanup(settings.disable)
        self.client = Client()

    def test_dashboard_ready_article_and_suggestion_use_postgres(self):
        database.save_collection("bbc", [story(body="Saved article text")])
        self.assertIn("Story one", self.client.get(reverse("dashboard:index")).content.decode())
        self.assertEqual(self.client.get(reverse("dashboard:ready")).json()["fresh"], 1)
        article = Article.objects.get()
        response = self.client.get(reverse("dashboard:article", args=["bbc", article.pk]))
        self.assertIn("Saved article text", response.content.decode())
        self.client.post(reverse("dashboard:suggest_outlet"), {
            "name": "Paper", "website": "https://paper.example/", "country": "Brazil", "reason": ""})
        self.assertEqual(OutletSuggestion.objects.count(), 1)

    def test_unavailable_server_shows_the_friendly_state(self):
        failure = DatabaseError("connection refused")
        with patch("dashboard.views.database.get_dashboard", side_effect=failure), self.assertLogs("dashboard.views", level="ERROR"):
            response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("connection refused", response.content.decode())
