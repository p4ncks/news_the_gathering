"""SQLite storage. A successful collection replaces ranks, but retains article history."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "news.db"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path=None):
    path = Path(path or os.environ.get("NEWS_DB_PATH", DEFAULT_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path=None):
    with connect(path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                collected_at TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                UNIQUE(source_id, url)
            );
            CREATE TABLE IF NOT EXISTS headlines (
                source_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                article_id INTEGER NOT NULL REFERENCES articles(id),
                PRIMARY KEY(source_id, rank)
            );
            CREATE TABLE IF NOT EXISTS source_status (
                source_id TEXT PRIMARY KEY,
                attempted_at TEXT NOT NULL,
                succeeded_at TEXT,
                error TEXT NOT NULL DEFAULT ''
            );
        """)


def save_collection(source_id, articles, path=None):
    if not articles:
        raise ValueError("Refusing to replace headlines with an empty collection")
    now = utc_now()
    with connect(path) as db:
        db.execute("DELETE FROM headlines WHERE source_id = ?", (source_id,))
        for rank, article in enumerate(articles, 1):
            db.execute("""
                INSERT INTO articles (source_id, url, title, summary, published_at, collected_at, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, url) DO UPDATE SET
                    title=excluded.title, summary=excluded.summary,
                    published_at=COALESCE(excluded.published_at, articles.published_at),
                    collected_at=excluded.collected_at,
                    body=CASE WHEN excluded.body != '' THEN excluded.body ELSE articles.body END
            """, (source_id, article["url"], article["title"], article["summary"],
                  article.get("published_at"), now, article.get("body", "")))
            article_id = db.execute("SELECT id FROM articles WHERE source_id=? AND url=?",
                                    (source_id, article["url"])).fetchone()[0]
            db.execute("INSERT INTO headlines VALUES (?, ?, ?)", (source_id, rank, article_id))
        db.execute("""INSERT INTO source_status VALUES (?, ?, ?, '')
            ON CONFLICT(source_id) DO UPDATE SET attempted_at=excluded.attempted_at,
            succeeded_at=excluded.succeeded_at, error=''""", (source_id, now, now))


def record_failure(source_id, error, path=None):
    with connect(path) as db:
        db.execute("""INSERT INTO source_status(source_id, attempted_at, error) VALUES (?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET attempted_at=excluded.attempted_at,
            error=excluded.error""", (source_id, utc_now(), str(error)[:500]))


def get_headlines(source_id, path=None):
    with connect(path) as db:
        return [dict(row) for row in db.execute("""
            SELECT a.*, h.rank FROM headlines h JOIN articles a ON a.id=h.article_id
            WHERE h.source_id=? ORDER BY h.rank LIMIT 10
        """, (source_id,))]


def get_statuses(path=None):
    with connect(path) as db:
        return {row["source_id"]: dict(row) for row in db.execute("SELECT * FROM source_status")}


def get_body(source_id, url, path=None):
    with connect(path) as db:
        row = db.execute("SELECT body FROM articles WHERE source_id=? AND url=?", (source_id, url)).fetchone()
        return row[0] if row else ""
