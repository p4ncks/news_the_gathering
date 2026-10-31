"""Prevent two scheduled collectors from replacing the same editions at once."""
from contextlib import contextmanager
import os
from pathlib import Path

from news import database

# Advisory locks are per database, so one fixed key identifies the collector.
ADVISORY_LOCK_KEY = 7263540182
TRANSACTION_POOLER_PORT = "6543"


class CollectorAlreadyRunning(RuntimeError):
    pass


class CollectorLockError(RuntimeError):
    """The lock could not be attempted, which is different from it being taken."""


@contextmanager
def _file_lock():
    """Own a local file lock for the duration of one SQLite collection run."""
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


@contextmanager
def _advisory_lock(settings):
    """Hold a session lock on a dedicated connection for one PostgreSQL run."""
    options = settings["OPTIONS"]
    if str(settings["PORT"] or options.get("port") or "") == TRANSACTION_POOLER_PORT:
        raise CollectorLockError(
            "The collector cannot use port 6543: a session lock is meaningless through a "
            "transaction pooler. Point DATABASE_URL at the session pooler on port 5432.")
    import psycopg
    parameters = {**options, "dbname": settings["NAME"], "user": settings["USER"],
                  "password": settings["PASSWORD"], "host": settings["HOST"], "port": settings["PORT"]}
    try:
        connection = psycopg.connect(autocommit=True, **{key: value for key, value in parameters.items() if value != ""})
    except psycopg.Error as error:
        raise CollectorLockError(f"Could not connect to take the collector lock: {error}") from None
    try:
        acquired = connection.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,)).fetchone()[0]
        if not acquired:
            raise CollectorAlreadyRunning("Another collector already owns this database.")
        yield
    finally:
        # Ending the session releases the lock.
        connection.close()


@contextmanager
def collector_lock():
    from gathering.database_config import database_settings
    settings = database_settings()
    lock = _file_lock() if settings["ENGINE"].endswith("sqlite3") else _advisory_lock(settings)
    with lock:
        yield
