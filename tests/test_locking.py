"""Collector locks release on normal exit and on collection errors."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from news.locking import collector_lock, CollectorAlreadyRunning


@unittest.skipIf(os.name == "nt", "The local Linux/macOS collector uses flock")
class CollectorLockTests(unittest.TestCase):
    def test_second_collector_cannot_enter_and_exit_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"NEWS_DB_ENGINE": "sqlite",
                           "NEWS_COLLECTOR_LOCK": str(Path(directory) / "collector.lock")}
            with patch.dict(os.environ, environment):
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
                with self.assertRaises(ValueError):
                    with collector_lock():
                        raise ValueError("Collection failed")
                with collector_lock():
                    pass
