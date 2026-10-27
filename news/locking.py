"""Prevent two scheduled collectors from replacing the same editions at once."""
from contextlib import contextmanager
import os
from pathlib import Path

from news import database


class CollectorAlreadyRunning(RuntimeError):
    pass


@contextmanager
def collector_lock():
    """Own a local file lock for the duration of one SQLite collection run."""
    from gathering.database_config import database_settings
    database_settings()
    import fcntl
    lock_path = Path(os.environ.get("NEWS_COLLECTOR_LOCK") or
                     str(database.database_path().with_suffix(".collector.lock")))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectorAlreadyRunning("Another collector already owns this database.") from None
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
