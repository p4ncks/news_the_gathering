"""Database configuration shared by Django, collectors and maintenance commands."""
import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def _postgres_settings(url):
    # Error messages never repeat the URL, because it contains the password.
    try:
        parts = urlsplit(url)
        port = parts.port
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
    except ValueError:
        raise ImproperlyConfigured(
            "DATABASE_URL is malformed. Percent-encode special characters in the password.") from None
    name = unquote(parts.path.lstrip("/"))
    if not name:
        raise ImproperlyConfigured("DATABASE_URL must name a database.")
    host = unquote(parts.hostname or "")
    effective_host = query.get("host") or host
    options = {"connect_timeout": 10}
    if effective_host not in LOCAL_HOSTS and not effective_host.startswith("/"):
        options["sslmode"] = "require"
    options.update(query)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": host,
        "PORT": str(port or ""),
        # Pooled connections: no reuse between requests, no named cursors.
        "CONN_MAX_AGE": 0,
        "DISABLE_SERVER_SIDE_CURSORS": True,
        "OPTIONS": options,
    }


def database_settings():
    url = os.environ.get("DATABASE_URL", "").strip()
    engine = os.environ.get("NEWS_DB_ENGINE", "").strip().lower()
    if engine not in ("", "sqlite", "postgresql"):
        raise ImproperlyConfigured("Unsupported NEWS_DB_ENGINE. Remove it or set it to sqlite or postgresql.")
    if url:
        if engine == "sqlite":
            raise ImproperlyConfigured("NEWS_DB_ENGINE=sqlite conflicts with DATABASE_URL. Remove one of them.")
        if url.partition(":")[0].lower() not in ("postgres", "postgresql"):
            raise ImproperlyConfigured("DATABASE_URL must use the postgres or postgresql scheme.")
        return _postgres_settings(url)
    if engine == "postgresql":
        raise ImproperlyConfigured("NEWS_DB_ENGINE=postgresql requires DATABASE_URL.")
    path = os.environ.get("NEWS_DB_PATH") or Path(__file__).resolve().parent.parent / "data" / "news.db"
    return {"ENGINE": "django.db.backends.sqlite3", "NAME": str(path),
            "OPTIONS": {"timeout": 10}}
