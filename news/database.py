"""SQLite storage shared by the Django website and collectors.

Django owns the schema. Public functions return plain dictionaries so the
collector and templates stay independent of model objects. Only explicit
initialize/maintenance commands create or migrate a database; reads never do.
"""
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "news.db"
RETENTION_DAYS = 30
MAX_EDITIONS = 48
MAX_RUNS = 1500


def database_path(path=None):
    return Path(path or os.environ.get("NEWS_DB_PATH", DEFAULT_PATH))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date(value):
    if not value:
        return None
    date = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)


def _serialize(row):
    return {key: (_date(value).isoformat() if value is not None and key.endswith("_at") else value)
            for key, value in row.items()}


def _django():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gathering.settings")
    import django
    from django.apps import apps
    if not apps.ready:
        django.setup()


def _alias(path=None, read_only=False):
    """An explicit path gets an isolated SQLite alias, useful for imports/tests."""
    _django()
    from django.db import connections
    default = connections.databases["default"]
    if path is None and "NEWS_DB_PATH" not in os.environ:
        if not read_only or str(default["NAME"]) == ":memory:":
            return "default"
        path = _sqlite_path(default["NAME"])
    path = database_path(path).resolve()
    alias = "news_" + hashlib.sha256(str(path).encode()).hexdigest()[:20] + ("_ro" if read_only else "")
    if alias not in connections.databases:
        configuration = deepcopy(default)
        configuration.update(NAME=str(path), OPTIONS={"timeout": 30}, CONN_MAX_AGE=0, ATOMIC_REQUESTS=False)
        if read_only:
            configuration["NAME"] = path.as_uri() + "?mode=ro"
            configuration["OPTIONS"]["uri"] = True
        connections.databases[alias] = configuration
    return alias


def _sqlite_path(name):
    name = str(name)
    return Path(unquote(urlsplit(name).path)) if name.startswith("file:") else Path(name)


def _models():
    _django()
    from .models import Article, CollectionRun, CurrentHeadline, Edition, EditionHeadline, SourceStatus
    return Article, CurrentHeadline, SourceStatus, Edition, EditionHeadline, CollectionRun


def _exists(alias):
    from django.db import connections
    connection = connections[alias]
    name = str(connection.settings_dict["NAME"])
    return name == ":memory:" or _sqlite_path(name).is_file()


@contextmanager
def _using(path=None, read_only=False):
    from django.db import connections
    alias = _alias(path, read_only)
    query_only = None
    try:
        # A separate URI connection cannot address SQLite's private :memory: DB.
        if read_only and str(connections[alias].settings_dict["NAME"]) == ":memory:":
            with connections[alias].cursor() as cursor:
                query_only = cursor.execute("PRAGMA query_only").fetchone()[0]
                cursor.execute("PRAGMA query_only = ON")
        yield alias
    finally:
        if query_only is not None:
            with connections[alias].cursor() as cursor:
                cursor.execute("PRAGMA query_only = " + str(query_only))
        # Temporary path aliases should not keep deleted test/import files open.
        if alias != "default" and not connections[alias].in_atomic_block:
            connections[alias].close()


@contextmanager
def connect(path=None, read_only=False):
    """SQLite compatibility/inspection helper; normal application writes use ORM.

    Keeping this helper lets existing local scripts inspect the same table names.
    """
    _django()
    path = database_path(path)
    if read_only:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    else:
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
    """Create or upgrade storage explicitly. Legacy SQLite data is kept intact."""
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor
    with _using(path) as alias:
        connection = connections[alias]
        # Collectors do not have Django's request lifecycle to renew connections
        # between scheduling waits.
        if not connection.in_atomic_block and str(connection.settings_dict["NAME"]) != ":memory:":
            connection.close_if_unusable_or_obsolete()
        name = str(connection.settings_dict["NAME"])
        if name != ":memory:":
            Path(name).parent.mkdir(parents=True, exist_ok=True)
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode = WAL")
        executor = MigrationExecutor(connection)
        # News and private outlet suggestions share the configured database.
        # Web requests only read existing storage; schema changes stay explicit.
        targets = [node for app in ("news", "dashboard")
                   for node in executor.loader.graph.leaf_nodes(app)]
        executor.migrate(targets)


def _hash(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _trim_editions(alias, source_id, maximum=MAX_EDITIONS):
    Edition = _models()[3]
    editions = Edition.objects.using(alias).filter(source_id=source_id)
    keep = list(editions.order_by("-collected_at", "-id").values_list("id", flat=True)[:maximum])
    obsolete = editions.exclude(id__in=keep)
    removed = obsolete.count()
    obsolete.delete()
    return removed


def save_collection(source_id, articles, path=None):
    if not articles:
        raise ValueError("Refusing to replace headlines with an empty collection")
    if len(articles) > 10:
        raise ValueError("A collection may contain at most ten headlines")
    if not source_id or len(source_id) > 64:
        raise ValueError("A source ID must contain 1 to 64 characters")
    from django.db import transaction
    Article, Headline, Status, Edition, EditionHeadline, _ = _models()
    now = _date(utc_now())
    with _using(path) as alias, transaction.atomic(using=alias):
        saved, seen = [], set()
        for incoming in articles:
            url = incoming["url"]
            if url in seen:
                raise ValueError("A ranked edition cannot contain duplicate URLs")
            seen.add(url)
            row = Article.objects.using(alias).select_for_update().filter(source_id=source_id, url_hash=_hash(url)).first()
            if row is None:
                row = Article(source_id=source_id, url=url, url_hash=_hash(url), first_seen_at=now)
            elif row.url != url:
                raise ValueError("URL digest collision; refusing to merge different URLs")
            row.title = incoming["title"]
            row.summary = incoming["summary"]
            row.published_at = _date(incoming.get("published_at")) or row.published_at
            row.collected_at = row.last_seen_at = now
            body = incoming.get("body", "")
            if body:
                fetched = incoming.get("body_fetched_at")
                if fetched:
                    row.body_fetched_at = _date(fetched)
                    if "body_metadata_hash" in incoming:
                        row.body_metadata_hash = incoming["body_metadata_hash"]
                elif body != row.body or row.body_fetched_at is None:
                    row.body_fetched_at = now
                row.body = body
            # An empty/failed refresh preserves the old body and its real age.
            row.save(using=alias)
            saved.append(row)
        Headline.objects.using(alias).filter(source_id=source_id).delete()
        Headline.objects.using(alias).bulk_create([
            Headline(source_id=source_id, rank=rank, article_id=row.pk) for rank, row in enumerate(saved, 1)
        ])
        Status.objects.using(alias).update_or_create(source_id=source_id,
            defaults={"attempted_at": now, "succeeded_at": now, "error": ""})
        latest = Edition.objects.using(alias).filter(source_id=source_id).order_by("-collected_at", "-id").first()
        fields = ("rank", "url", "title", "summary", "published_at")
        previous = list(latest.headlines.using(alias).order_by("rank").values_list(*fields)) if latest else []
        current = [(rank, row.url, row.title, row.summary, row.published_at) for rank, row in enumerate(saved, 1)]
        if previous != current:
            edition = Edition.objects.using(alias).create(source_id=source_id, collected_at=now)
            EditionHeadline.objects.using(alias).bulk_create([
                EditionHeadline(edition_id=edition.pk, article_id=row.pk, rank=rank, url=row.url,
                                title=row.title, summary=row.summary, published_at=row.published_at)
                for rank, row in enumerate(saved, 1)
            ])
        maximum = int(os.environ.get("NEWS_MAX_EDITIONS", MAX_EDITIONS))
        if maximum < 1:
            raise ValueError("NEWS_MAX_EDITIONS must be positive")
        _trim_editions(alias, source_id, maximum)


def record_failure(source_id, error, path=None):
    Status = _models()[2]
    with _using(path) as alias:
        Status.objects.using(alias).update_or_create(source_id=source_id,
            defaults={"attempted_at": _date(utc_now()), "error": str(error)[:500]})


def get_headlines(source_id, path=None):
    """Compatibility read including bodies; the dashboard uses get_dashboard()."""
    Headline = _models()[1]
    with _using(path, read_only=True) as alias:
        if not _exists(alias):
            return []
        rows = Headline.objects.using(alias).filter(source_id=source_id).select_related("article").order_by("rank")[:10]
        return [_article_dict(row.article, rank=row.rank) for row in rows]


def _article_dict(article, **extra):
    values = {field.name: getattr(article, field.name) for field in article._meta.fields}
    return _serialize({**values, "has_body": bool(article.body), **extra})


def get_statuses(path=None):
    Status = _models()[2]
    with _using(path, read_only=True) as alias:
        if not _exists(alias):
            return {}
        return {row["source_id"]: _serialize(row) for row in Status.objects.using(alias).values()}


def _dashboard_rows(alias, source_ids=None):
    """Read statuses and headline metadata from one consistent SQL snapshot."""
    from django.db import connections, transaction
    fields = ("id", "source_id", "url", "title", "summary", "published_at", "collected_at",
              "first_seen_at", "last_seen_at", "body_fetched_at")
    quote = connections[alias].ops.quote_name
    selected = ", ".join("a." + quote(name) for name in fields)
    sql = ("SELECT s.source_id AS status_source_id, s.attempted_at, s.succeeded_at, s.error, " + selected +
           ", h." + quote("rank") + ", CASE WHEN a.body <> '' THEN 1 ELSE 0 END AS has_body "
           "FROM source_status s LEFT JOIN headlines h ON h.source_id=s.source_id "
           "LEFT JOIN articles a ON a.id=h.article_id")
    parameters = []
    if source_ids is not None:
        if not source_ids:
            return []
        parameters = list(source_ids)
        sql += " WHERE s.source_id IN (" + ",".join(["%s"] * len(parameters)) + ")"
    sql += " ORDER BY s.source_id, h." + quote("rank")
    with transaction.atomic(using=alias), connections[alias].cursor() as cursor:
        cursor.execute(sql, parameters)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def get_dashboard(source_ids, path=None):
    source_ids = list(dict.fromkeys(source_ids))
    with _using(path, read_only=True) as alias:
        statuses, headlines = {}, {source_id: [] for source_id in source_ids}
        if not _exists(alias):
            return statuses, headlines
        for row in _dashboard_rows(alias, source_ids):
            source_id = row.pop("status_source_id")
            statuses[source_id] = _serialize({"source_id": source_id, **{key: row.pop(key) for key in ("attempted_at", "succeeded_at", "error")}})
            if row["id"] is not None:
                row["has_body"] = bool(row["has_body"])
                headlines[source_id].append(_serialize(row))
        return statuses, headlines


def get_article(source_id, article_id, path=None):
    Article = _models()[0]
    with _using(path, read_only=True) as alias:
        if not _exists(alias):
            return None
        row = Article.objects.using(alias).filter(source_id=source_id, pk=article_id).first()
        return _article_dict(row) if row else None


def get_editions(source_id, limit=5, path=None):
    """Read immutable ranked snapshots without loading current article bodies."""
    Edition = _models()[3]
    limit = max(0, min(int(limit), 50))
    with _using(path, read_only=True) as alias:
        if not _exists(alias):
            return []
        editions = Edition.objects.using(alias).filter(source_id=source_id).order_by(
            "-collected_at", "-id").prefetch_related("headlines")[:limit]
        return [{"id": edition.pk, "source_id": edition.source_id,
                 "collected_at": _date(edition.collected_at).isoformat(),
                 "headlines": [_serialize({"article_id": row.article_id, "rank": row.rank, "url": row.url,
                      "title": row.title, "summary": row.summary, "published_at": row.published_at})
                      for row in edition.headlines.all()]}
                for edition in editions]


def get_body_metadata(source_id, url, path=None):
    Article = _models()[0]
    with _using(path, read_only=True) as alias:
        if not _exists(alias):
            return None
        row = Article.objects.using(alias).filter(source_id=source_id, url_hash=_hash(url)).values(
            "url", "body", "body_fetched_at", "body_metadata_hash", "title", "summary").first()
        if row is None or row.pop("url") != url:
            return None
        return _serialize(row)


def get_body(source_id, url, path=None):
    row = get_body_metadata(source_id, url, path)
    return row["body"] if row else ""


def record_run(results, path=None, started_at=None, finished_at=None):
    Run = _models()[5]
    finished = _date(finished_at or utc_now())
    diagnostics = [{"source": str(row.get("source", ""))[:100], "source_id": str(row.get("source_id", ""))[:64],
                    "count": int(row.get("count", 0)), "error": str(row.get("error", ""))[:500],
                    "warnings": [str(warning)[:500] for warning in row.get("warnings", [])[:10]],
                    "duration_ms": max(0, int(row.get("duration_ms", 0)))} for row in results]
    failed = sum(bool(row["error"]) for row in diagnostics)
    with _using(path) as alias:
        row = Run.objects.using(alias).create(started_at=_date(started_at) or finished, finished_at=finished,
            sources_attempted=len(diagnostics), sources_failed=failed,
            sources_succeeded=len(diagnostics) - failed, diagnostics=diagnostics)
        keep = list(Run.objects.using(alias).order_by("-finished_at", "-id").values_list("id", flat=True)[:MAX_RUNS])
        Run.objects.using(alias).exclude(id__in=keep).delete()
        return row.pk


def prune(path=None, days=RETENTION_DAYS, max_editions=MAX_EDITIONS):
    """Explicit maintenance: web reads never remove history or run migrations."""
    if days < 1 or max_editions < 1:
        raise ValueError("Retention days and maximum editions must be positive")
    from django.db import transaction
    Article, _, _, Edition, _, Run = _models()
    cutoff = _date(utc_now()) - timedelta(days=days)
    with _using(path) as alias, transaction.atomic(using=alias):
        old_editions = Edition.objects.using(alias).filter(collected_at__lt=cutoff)
        removed_editions = old_editions.count()
        old_editions.delete()
        for source_id in Edition.objects.using(alias).order_by().values_list("source_id", flat=True).distinct():
            removed_editions += _trim_editions(alias, source_id, max_editions)
        removed_articles, _ = Article.objects.using(alias).filter(last_seen_at__lt=cutoff,
            current_headlines__isnull=True, edition_headlines__isnull=True).delete()
        removed_runs, _ = Run.objects.using(alias).filter(finished_at__lt=cutoff).delete()
        return {"articles": removed_articles, "editions": removed_editions, "runs": removed_runs}


def backup(destination, path=None, current_only=False):
    """Consistent SQLite backup including committed WAL data; never overwrite."""
    from django.db import connections
    with _using(path, read_only=True) as alias:
        connection = connections[alias]
        source = _sqlite_path(connection.settings_dict["NAME"]).resolve()
        destination = Path(destination).resolve()
        if destination == source or destination.exists():
            raise FileExistsError("Backup destination must be a new file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Claim the name atomically so two simultaneous backups cannot overwrite
        # one another between the existence check and sqlite3.connect().
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(destination) as copied:
                original.backup(copied)
                if current_only:
                    # Website snapshots contain public news only. Full backups
                    # keep private reader suggestions for disaster recovery.
                    tables = {row[0] for row in copied.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "dashboard_outletsuggestion" in tables:
                        copied.execute("DELETE FROM dashboard_outletsuggestion")
                    copied.execute("DELETE FROM edition_headlines")
                    copied.execute("DELETE FROM editions")
                    copied.execute("DELETE FROM collection_runs")
                    copied.execute("DELETE FROM articles WHERE id NOT IN (SELECT article_id FROM headlines)")
                copied.commit()
                copied.execute("PRAGMA journal_mode = DELETE")
                copied.execute("VACUUM")
                if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity check failed")
                if copied.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("Backup contains broken article references")
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination


def export_snapshot(destination, path=None):
    """Export only current editions to a portable, standalone SQLite database."""
    return backup(destination, path, current_only=True)


def import_sqlite(source_path, path=None):
    """Import a read-only SQLite backup into empty configured storage.

    Works for both the original schema and current backups. IDs, timestamps,
    bodies, statuses and retained snapshots are preserved. Repeating an identical
    import is a no-op; a different nonempty target is rejected rather than merged.
    Stop collectors while importing. The source file is never migrated or changed.
    """
    from django.db import connections, transaction
    from dashboard.models import OutletSuggestion
    Article, Headline, Status, Edition, EditionHeadline, Run = _models()
    source_path = Path(source_path).resolve()
    target_alias = _alias(path)
    target_connection = connections[target_alias]
    if _sqlite_path(target_connection.settings_dict["NAME"]).resolve() == source_path:
        raise ValueError("Import source and target must be different databases")
    model_tables = [(Article, "articles"), (Status, "source_status"), (Headline, "headlines"),
                    (Edition, "editions"), (EditionHeadline, "edition_headlines"), (Run, "collection_runs"),
                    (OutletSuggestion, "dashboard_outletsuggestion")]
    with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as original:
        original.row_factory = sqlite3.Row
        original.execute("BEGIN")
        if original.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Import source failed its integrity check")
        if original.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Import source contains broken references")
        tables = {row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"articles", "headlines", "source_status"}.issubset(tables):
            raise ValueError("Import source is not a News, The Gathering database")
        article_columns = {row[1] for row in original.execute("PRAGMA table_info(articles)")}
        if not {"id", "source_id", "url", "title", "summary", "published_at", "collected_at", "body"}.issubset(article_columns):
            raise ValueError("Import source has an unrecognized articles schema")
        expected = {table: original.execute("SELECT count(*) FROM " + table).fetchone()[0]
                    for _, table in model_tables if table in tables}

        def records(model, table):
            """Stream bounded batches rather than loading an archive into RAM."""
            cursor = original.execute("SELECT * FROM " + table)
            fields = {field.attname for field in model._meta.fields}
            while True:
                batch = cursor.fetchmany(200)
                if not batch:
                    break
                for row in batch:
                    values = {key: value for key, value in dict(row).items() if key in fields}
                    if model is Article:
                        digest = _hash(values["url"])
                        if values.get("url_hash", digest) != digest:
                            raise ValueError("Import source contains an invalid URL digest")
                        values["url_hash"] = digest
                        values.setdefault("first_seen_at", values["collected_at"])
                        values.setdefault("last_seen_at", values["collected_at"])
                        values.setdefault("body_fetched_at", values["collected_at"] if values["body"] else None)
                        values.setdefault("body_metadata_hash", "")
                    if model is Run and isinstance(values.get("diagnostics"), str):
                        values["diagnostics"] = json.loads(values["diagnostics"])
                    yield {key: _date(value) if key.endswith("_at") else value for key, value in values.items()}

        initialize(path)
        with _using(path) as alias, transaction.atomic(using=alias):
            nonempty = any(model.objects.using(alias).exists() for model, _ in model_tables)
            if nonempty:
                for model, table in model_tables:
                    if table not in expected:
                        if model is OutletSuggestion and model.objects.using(alias).exists():
                            raise ValueError("Import target contains suggestions absent from this backup")
                        continue
                    if model.objects.using(alias).count() != expected[table]:
                        raise ValueError("Import target is nonempty and differs from this backup")
                    for values in records(model, table):
                        lookup = {"pk": values["id"]} if "id" in values else {"source_id": values["source_id"]}
                        if model is Headline and "id" not in values:
                            lookup["rank"] = values["rank"]
                        actual = model.objects.using(alias).filter(**lookup).values(*values).first()
                        if actual is None or _serialize(actual) != _serialize(values):
                            raise ValueError("Import target is nonempty and differs from this backup")
                return {**expected, "already_imported": True}
            for model, table in model_tables:
                if table not in tables:
                    continue
                batch = []
                for values in records(model, table):
                    batch.append(model(**values))
                    if len(batch) == 200:
                        model.objects.using(alias).bulk_create(batch, batch_size=100)
                        batch = []
                if batch:
                    model.objects.using(alias).bulk_create(batch, batch_size=100)
            for headline in Headline.objects.using(alias).select_related("article"):
                if headline.source_id != headline.article.source_id:
                    raise ValueError("Import source contains a headline assigned to another outlet")
            if "editions" not in tables:
                for source_id in Headline.objects.using(alias).order_by().values_list("source_id", flat=True).distinct():
                    headlines = list(Headline.objects.using(alias).filter(source_id=source_id).select_related("article").order_by("rank"))
                    status = Status.objects.using(alias).filter(source_id=source_id).first()
                    seen = status.succeeded_at if status and status.succeeded_at else max(h.article.last_seen_at for h in headlines)
                    edition = Edition.objects.using(alias).create(source_id=source_id, collected_at=seen)
                    EditionHeadline.objects.using(alias).bulk_create([
                        EditionHeadline(edition_id=edition.pk, article_id=h.article_id, rank=h.rank,
                            title=h.article.title, summary=h.article.summary, url=h.article.url,
                            published_at=h.article.published_at) for h in headlines
                    ])
            actual = {table: model.objects.using(alias).count() for model, table in model_tables if table in expected}
            if actual != expected:
                raise ValueError("Imported row counts differ; rolling back the import")
            return {**actual, "already_imported": False}
