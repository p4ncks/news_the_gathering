"""Collector locks release on normal exit and on collection errors."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psycopg

from news.collect import main
from news.locking import collector_lock, CollectorAlreadyRunning, CollectorLockError


@unittest.skipIf(os.name == "nt", "The local Linux/macOS collector uses flock")
class CollectorLockTests(unittest.TestCase):
    def test_second_collector_cannot_enter_and_exit_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"NEWS_DB_ENGINE": "sqlite",
                           "NEWS_COLLECTOR_LOCK": str(Path(directory) / "collector.lock")}
            with patch.dict(os.environ, environment):
                os.environ.pop("DATABASE_URL", None)
                with collector_lock():
                    with self.assertRaises(CollectorAlreadyRunning):
                        with collector_lock():
                            self.fail("The second collector entered a locked database")
                with collector_lock():
                    pass

    def test_failure_releases_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"NEWS_DB_ENGINE": "sqlite",
                "NEWS_COLLECTOR_LOCK": str(Path(directory) / "collector.lock")}):
                os.environ.pop("DATABASE_URL", None)
                with self.assertRaises(ValueError):
                    with collector_lock():
                        raise ValueError("Collection failed")
                with collector_lock():
                    pass


class AdvisoryLockConfigurationTests(unittest.TestCase):
    """No server here; tests/test_postgres.py exercises the real lock."""
    def environment(self, port):
        environment = patch.dict(os.environ, {"DATABASE_URL": f"postgresql://u:hunter2@db.example.com:{port}/news"})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("NEWS_DB_ENGINE", None)

    def test_transaction_pooler_port_is_refused_before_connecting(self):
        self.environment(6543)
        with patch("psycopg.connect") as connect, self.assertRaisesRegex(CollectorLockError, "transaction pooler"):
            with collector_lock():
                self.fail("A collector entered through the transaction pooler")
        connect.assert_not_called()

    def test_cli_exits_cleanly_when_the_port_is_refused(self):
        self.environment(6543)
        with patch("sys.argv", ["collect", "--source", "nasa"]), patch("news.collect.collect_all") as collect:
            with self.assertLogs("news.collect", level="ERROR") as logs:
                self.assertEqual(main(), 1)
        collect.assert_not_called()
        self.assertIn("6543", logs.output[0])

    def test_lock_uses_a_dedicated_connection_and_closes_it(self):
        self.environment(5432)
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (True,)
        with patch("psycopg.connect", return_value=connection) as connect:
            with collector_lock():
                connection.close.assert_not_called()
        self.assertEqual(connect.call_args.kwargs["sslmode"], "require")
        self.assertTrue(connect.call_args.kwargs["autocommit"])
        self.assertIn("pg_try_advisory_lock", connection.execute.call_args.args[0])
        connection.close.assert_called_once()

    def test_lock_held_elsewhere_raises_and_closes(self):
        self.environment(5432)
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (False,)
        with patch("psycopg.connect", return_value=connection), self.assertRaises(CollectorAlreadyRunning):
            with collector_lock():
                self.fail("The second collector entered a locked database")
        connection.close.assert_called_once()

    def test_connection_failure_is_a_clear_error(self):
        self.environment(5432)
        with patch("psycopg.connect", side_effect=psycopg.OperationalError("server unavailable")):
            with self.assertRaisesRegex(CollectorLockError, "server unavailable"):
                with collector_lock():
                    self.fail("Entered without a lock")
