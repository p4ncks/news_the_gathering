# The Gathering — project guide

[Back to the README](../README.md)

Run these commands from the project root. The app uses Python 3.12, Django,
ordinary HTML/CSS and a small JavaScript file. Collection and web requests share
one local SQLite database; browsing never starts a collector or runs migrations.

## Run locally

```bash
python3.12 -m venv .venv-django
source .venv-django/bin/activate
python -m pip install -r requirements.txt
export DJANGO_DEBUG=true
python -m news.maintenance init
python -m news.collect
python manage.py runserver 127.0.0.1:8000
```

Open [localhost:8000](http://127.0.0.1:8000/). To keep collecting, use a second
terminal with the same environment activated:

```bash
export DJANGO_DEBUG=true
python -m news.collect --interval 30
```

The collector waits 30 minutes after each completed run. A file lock prevents
another collector from replacing the same editions simultaneously. Each source
has a 60-second preparation budget by default. A failed collection preserves its
last successful edition, and the run records counts, duration, warnings and errors.

`runserver` opts into local development unless `DJANGO_DEBUG` is already set.
Other commands require either `DJANGO_DEBUG=true` for local work or the production
settings described in the [deployment guide](deployment.md). Django does not load
`.env` automatically.

## Upgrade an existing database

Stop older collectors first. Back up `data/news.db`, or the file configured with
`NEWS_DB_PATH`, before initialization:

```bash
export DJANGO_DEBUG=true
python -m news.maintenance backup --directory data/backups --keep 7
python -m news.maintenance init
```

Initialization can upgrade the original three-table SQLite schema while keeping
article IDs, text, rankings and statuses. It creates an initial edition snapshot,
verifies row counts and rejects unrecognized schemas. Repeated initialization is
safe. Keep the verified backup until you have checked the upgraded application.

## Use the news desk

- Select continents to select their outlets; individual choices update each
  continent's full, partial or empty state. Apply commits the selection.
- Search current titles and summaries in their original languages. Search does
  not cover history or translate the publisher's content.
- Switch between front-page cards and compact headlines, and use outlet jump
  links to navigate. Filters and display choices are saved in the URL.
- The desktop filter column and headline panes scroll independently. On phones,
  filters collapse and cards use their natural height.
- Flags appear beside country names. Hover or focus shows a country tooltip;
  Escape dismisses it. Publisher text carries its language code.
- Switch the interface between English and Portuguese without changing the
  outlet names, publisher text or applied filters.
- The saved-text reader loads one article only when requested. It shows the
  extraction time and warns when the saved body may predate its metadata.
- Suggest an outlet using its name and website. Country and explanation are
  optional. Suggestions remain private and do not add sources automatically.

Without JavaScript, choose a continent and submit to reveal its outlets, then
choose outlets and submit again. The language selector and mobile filter
disclosure also work without JavaScript.

## Collection and data quality

“Top ten” means the first ten unique usable entries in a configured publisher
feed or listing page, in publisher order. It is not a popularity ranking or a
claim that every feed reproduces its homepage. Each card identifies its edition
scope and collection time. Geography describes the publisher's home base.

The catalog contains 27 organizations and 28 sections across six inhabited
continents; CNN Space is a section of CNN. Edit `news/sources.py` to change the
catalog. RSS sources use the shared feed parser; HTML sources need a compatible
listing-page adapter.

Collect selected sources or enable optional text extraction:

```bash
python -m news.collect --source bbc --source g1
python -m news.collect --source bbc --source-timeout 90
python -m news.collect --source bbc --extract
```

Collectors reject damaged XML and suspicious count drops. By default, an edition
with at least five headlines cannot be replaced by fewer than half that count.
After investigating a legitimately shorter feed, `--min-edition-ratio 0` disables
this guard for that run. Benign encoding warnings can still produce usable feeds.

The downloader checks redirects, rejects private and reserved network addresses,
and preserves TLS hostname verification. HTML extraction respects robots rules
and pacing. Disposable workers enforce preparation deadlines and do not write to
the database.

Article bodies refresh after 24 hours or a changed headline/summary. A failed
refresh preserves the previous body, fetch time and version fingerprint. Raw HTML
and publisher images are not retained.

## Storage, history and backups

The default file is `data/news.db`, ignored by Git. Set `NEWS_DB_PATH` to use
another local path. Keep the web app and collector pointed at the same file.

- Articles deduplicate by outlet and normalized URL, preserving URL path case.
- First-seen, last-seen, publication and body-fetch timestamps stay separate.
- Current rankings replace atomically. Changed editions create immutable ranked
  snapshots; unchanged editions do not duplicate them.
- Default retention is 30 days and at most 48 changed editions per outlet. Current
  headlines and retained snapshot references protect articles from pruning.
  Run diagnostics also have age and count limits.
- The dashboard reads statuses and rankings in one metadata-only SQL snapshot.
  Its SQLite reader connections are read-only.

```bash
python -m news.maintenance editions --source bbc --limit 5
python -m news.maintenance prune --days 30 --max-editions 48
python -m news.maintenance backup --directory data/backups --keep 7
python -m news.maintenance verify-backup data/backups/YOUR_BACKUP.sqlite3.gz
python -m news.maintenance export /tmp/current-news.db
```

Backups use SQLite's online backup API, including committed WAL data. Each archive
is compressed, restored to a temporary database, and checked for integrity and
foreign-key errors before rotation. The command retains seven archives by
default; it runs when invoked, rather than installing a backup schedule.

Full backups preserve private outlet suggestions. Current-news exports exclude
suggestions, historical editions and run diagnostics. To import a restored SQLite
file into an empty destination:

```bash
NEWS_DB_PATH=/absolute/path/new-news.db python -m news.maintenance import-sqlite /absolute/path/restored-news.db
```

The source is read-only. The import validates references and row counts, preserves
IDs and dates, treats an identical repeat as a no-op, and rejects a different
nonempty destination. Stop collectors while importing.

Keep an off-device backup. Do not copy only a live WAL database file or put
SQLite on a network share. Deleted rows make space reusable; shrinking the file
itself is a separate compaction operation.

## Languages and suggestions

Edit `locale/pt_BR/LC_MESSAGES/django.po`, then rebuild its catalog:

```bash
python scripts/compile_translations.py
```

The compiler needs no external gettext program. Suggestions are stored in
`dashboard_outletsuggestion` and can be inspected with a private SQLite tool.
There is no public submissions list or automatic source approval.

## Understand the code

| File | Responsibility |
| --- | --- |
| `news/sources.py` | Outlets, languages and edition scopes |
| `news/collector.py` | Parsing, validation, text refresh and edition collection |
| `news/network.py` | Guarded downloads, redirects and worker deadlines |
| `news/models.py`, `news/database.py` | SQLite records, reads, imports and history |
| `news/migrations/` | Versioned schema and original SQLite upgrade |
| `news/collect.py`, `news/locking.py` | Scheduled CLI and collector ownership |
| `news/maintenance.py` | Initialization, retention, backups and exports |
| `dashboard/views.py` | Filters, article reader and health routes |
| `dashboard/forms.py`, `dashboard/suggestions.py` | Private outlet submissions |
| `dashboard/templates/dashboard/` | Dashboard and reader HTML |
| `dashboard/static/dashboard/` | Theme, filters, logo, flags and map |
| `gathering/database_config.py` | Local SQLite path configuration |
| `gathering/settings.py` | Django configuration |

## Validate

```bash
export DJANGO_DEBUG=true
python -m unittest discover -s tests -v
python manage.py check
python manage.py makemigrations --check --dry-run
```

The main suite uses isolated temporary databases and mocked networking. Browser
regressions use an isolated fixture server:

```bash
python -m pip install -r requirements-test.txt
python -m playwright install chromium
python -m unittest discover -s tests/browser -v
```

The [CI workflow](../.github/workflows/checks.yml) defines unit and Chromium browser
checks. `requirements.lock.txt` records the pinned base Python environment.
