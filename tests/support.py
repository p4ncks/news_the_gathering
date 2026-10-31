"""Isolate tests that use the configured default database.

SQLite tests point at a temporary file. PostgreSQL has one shared database, which
these tests empty, so anything but a local database named for testing is refused.
"""
import os
import unittest

from gathering.database_config import database_settings
from news import database

POSTGRES = bool(os.environ.get("DATABASE_URL", "").strip())


def sqlite_only(reason):
    return unittest.skipIf(POSTGRES, "SQLite only: " + reason)


def initialize_default_database():
    if not POSTGRES:
        database.initialize()
        return
    settings = database_settings()
    if settings["HOST"] not in ("localhost", "127.0.0.1", "::1") or "test" not in settings["NAME"]:
        raise RuntimeError("Tests empty the database: use a local DATABASE_URL whose name contains 'test'.")
    database.initialize()
    from django.db import connections
    connection = connections["default"]
    tables = ", ".join(connection.ops.quote_name(name) for name in connection.introspection.django_table_names(only_existing=True))
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
