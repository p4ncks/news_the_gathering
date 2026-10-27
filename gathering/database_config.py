"""Database configuration shared by Django, collectors and maintenance commands."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def database_settings():
    engine = os.environ.get("NEWS_DB_ENGINE", "sqlite").lower()
    if engine != "sqlite":
        raise ImproperlyConfigured("This release supports SQLite only. Remove NEWS_DB_ENGINE or set it to sqlite.")
    path = os.environ.get("NEWS_DB_PATH") or Path(__file__).resolve().parent.parent / "data" / "news.db"
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": str(path),
            "OPTIONS": {"timeout": 10}}
