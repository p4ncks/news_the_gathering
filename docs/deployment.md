# Running Django in production

[Back to the README](../README.md)

Publishing this repository on GitHub shares the source code. Running the website
requires a Python host, persistent local storage and the processes described here.
This release uses SQLite for news, edition history and private outlet suggestions.

## Configuration

Install Python 3.12 and the project dependencies in a virtual environment. Create
a private Django secret:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Set environment variables for both the web process and collector. For example:

```ini
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=YOUR_GENERATED_PRIVATE_KEY
DJANGO_ALLOWED_HOSTS=news.example.com,localhost,127.0.0.1
NEWS_DB_PATH=/absolute/persistent/path/news.db
NEWS_RETENTION_DAYS=30
NEWS_MAX_EDITIONS=48
NEWS_FRESHNESS_HOURS=2
NEWS_MIN_FRESH_SOURCES=1
NEWS_SOURCE_TIMEOUT_SECONDS=60
NEWS_MIN_EDITION_RATIO=0.5
```

Production refuses to start without `DJANGO_SECRET_KEY`. Keep real environment
files, database files and backups outside Git. Django reads environment variables
and does not automatically load `.env`; use the process runner's environment
configuration. See [`.env.example`](../.env.example) for available settings.

The web process and collector must use the same SQLite file. Its directory must
be writable by those processes, including SQLite's journal files. Use persistent
local disk and filesystem permissions appropriate for the service account.

## Start the application

Initialize the schema and build static assets before accepting traffic:

```bash
python -m news.maintenance init
python manage.py collectstatic --noinput
python manage.py check --deploy
gunicorn gathering.wsgi:application --bind 127.0.0.1:8000 --workers 2
```

Run the collector as a separate process with the same environment:

```bash
python -m news.collect --interval 30
```

Configure your process runner to restart both processes after failures or host
restarts. The collector owns a file lock during each run. Web requests read saved
news and accept private outlet suggestions; they never start collection or run
schema migrations.

## Docker

The Dockerfile and Compose configuration package the web process and collector
with a shared SQLite volume. Set a generated secret in `.env` before starting:

```bash
cp .env.example .env
# Edit .env and set DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS.
docker compose build
docker compose run --rm collector python -m news.maintenance init
docker compose up -d
```

Compose reads `.env` and passes the configured variables into the containers.
Its web port binds to `127.0.0.1:8000` on the host; configure an HTTPS reverse proxy
as described below. The named volume holds the SQLite file, and Docker logs
rotate at 10 MB per file, with three files per service. Preserve the volume when
updating containers and back it up before removing it.

## HTTPS

Place Gunicorn behind an HTTPS reverse proxy and keep port 8000 private. After
that proxy is configured, enable:

```ini
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_TRUST_PROXY=true
```

Set `DJANGO_TRUST_PROXY=true` only when the proxy strips client-supplied forwarded
headers and sets `X-Forwarded-Proto` itself. Review `check --deploy` results against
your actual HTTPS configuration. The internal `/health/` liveness probe remains
HTTP-compatible when redirects are enabled.

## Health and data freshness

- `/health/` reports process liveness.
- `/ready/` reports aggregate fresh, stale, failed and missing source counts. It
  returns 200 when enough fresh saved editions are usable, with `degraded` status
  for partial failures, or 503 when availability falls below the configured
  minimum. `NEWS_MIN_FRESH_SOURCES` defaults to one.

The dashboard shows each outlet's last successful update and a quiet delayed
status when collection fails or becomes stale. Raw collector errors remain in
server logs and stored run diagnostics.

## Backups and recovery

Create a compressed, verified backup:

```bash
python -m news.maintenance backup --directory /absolute/backup/path --keep 7
python -m news.maintenance verify-backup /absolute/backup/path/news-TIMESTAMP.sqlite3.gz
```

The backup command uses SQLite's online backup API, restores the archive into a
temporary database, and checks integrity and foreign keys before rotation. It
keeps seven archives by default. Invoke it from your own backup schedule and keep
at least one copy on another device.

Before upgrading an existing database, stop collectors and create a backup, then
run `python -m news.maintenance init`. See the
[project guide](project-guide.md#upgrade-an-existing-database) for upgrade details
and [storage commands](project-guide.md#storage-history-and-backups) for importing
restored SQLite files and exporting current news.

Full backups retain private outlet suggestions. Public news exports omit those
submissions. A host that discards its local files cannot preserve either news
history or suggestions, so choose persistent storage for the SQLite file.
