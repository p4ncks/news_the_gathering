# News, The Gathering

Compare ten headlines from each news outlet in a Python web app. Collectors save
publisher feeds to SQLite; the interface reads that database. Optional Trafilatura
extraction adds article text. No JavaScript application code or database server is required.

## Run locally

Use Python 3.12 for a new environment. Local tests also passed on the existing Python 3.9 environment, but Apple’s bundled Python emits a LibreSSL/urllib3 compatibility warning; use a current Python build for deployment.
From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m news.collect
streamlit run app.py
```

Open http://localhost:8501. A failed outlet does not prevent other outlets from
being saved. The collector exits with status 1 if any source fails, and the app
shows each source's error and last successful collection.

In a second terminal, keep headlines updated even when no browser is open:

```bash
source .venv/bin/activate
python -m news.collect --interval 30
```

The interval is measured after each collection finishes. Run only one scheduled
collector per database. The web app's reload button reads the latest saved data;
it does not scrape. The page does not refresh automatically.

Collect selected outlets or opt into full-text extraction:

```bash
python -m news.collect --source bbc --source g1
python -m news.collect --source bbc --extract
```

Extraction respects robots.txt and spaces article requests by at least one second
(or the declared crawl delay). Unavailable robots rules, blocked pages, and
paywalls leave the RSS headline intact. Extraction failures are logged; they do
not make a successful headline collection fail. Stored article bodies are reused.
The default is headline/summary/link collection, not full-text republication.
Check publisher permissions before enabling full-text display on a public service.

## What “top ten” means

The first ten unique valid entries in each publisher's feed, in feed order.
This is **not** a claim about popularity, homepage prominence, or which stories
matter most. Feeds can mix sections and sometimes include opinion, live coverage,
or older pinned stories. Publication time and collection time are separate.
The app warns about collections older than two hours and feeds whose newest dated
story is older than 48 hours. Missing dates are shown as unavailable.

Geography means the outlet's home base. This starter includes BBC, The Guardian,
NPR, g1/Globo, Folha, Al Jazeera, CNA, Daily Maverick and ABC Australia. It covers
six inhabited continents; it is not a definitive ranking of their leading outlets.
CNN is configured but disabled because its legacy feed may be frozen. Verify a
current source before enabling it. “CNN Space” needs a specific publication/feed
identified before it can be added. Languages remain as published; no translation
or automatic same-story matching is implemented yet.

## Understand and modify the code

| File | Responsibility |
| --- | --- |
| `news/sources.py` | Outlet names, countries, continents, languages and feed URLs |
| `news/collector.py` | Download feeds, clean text, deduplicate, extract articles |
| `news/database.py` | SQLite schema and reads/writes |
| `news/collect.py` | Command-line collection and periodic loop |
| `app.py` | Streamlit comparison interface |
| `tests/test_pipeline.py` | Offline collection and storage tests |
| `tests/test_app.py` | UI filtering and empty-state tests |

To add an outlet, add a `Source(...)` in `news/sources.py`, using a stable unique ID
and an official RSS/Atom feed. Then run the collector. Feed URLs are trusted project
configuration, not user-submitted URLs. Set `enabled=False` to hide an unavailable
outlet. No schema change is needed. Change theme colors in `.streamlit/config.toml`.

`articles` stores unique `(source_id, url)` records and keeps historical articles.
`headlines` stores the latest successful ranking. `source_status` tracks attempts,
successes and errors. Each successful source collection is a single transaction;
failed collections preserve the previous ranking. This version keeps historical
articles, but not historical ranking snapshots. Add retention when the database grows.

The database defaults to `data/news.db` (ignored by Git). Override with
`NEWS_DB_PATH=/absolute/path/news.db`. Back up with SQLite's backup API or stop both
processes before copying the database; copying only the main file while WAL writes
are active may miss recent data. Do not place the SQLite database on a network share.

## Access from anywhere

Localhost is only this computer. For a persistent online instance, run the included
Docker Compose setup on a server with persistent disk:

```bash
docker compose up --build -d
```

It starts the Python web app and a collector that runs every 30 minutes. Both use
the `news-data` volume; do not delete that volume unless you intend to erase the DB.
The web service listens on port 8501. Put an HTTPS reverse proxy (with WebSocket
support) in front of it and point your domain at the server. Add authentication
at the proxy if you want private access. This app has no built-in login.
For a private local setup bind the Compose port to `127.0.0.1:8501:8501`.

This repository is deployment-ready scaffolding, **not an already deployed site**.
A hosting account/server and domain are still needed. Keep one web instance and one
collector sharing a local volume; move to PostgreSQL before scaling across hosts.
Free hosts with ephemeral filesystems can lose SQLite data on restarts.

## Validate

```bash
python -m unittest discover -s tests -v
```

Tests cover feed ordering, duplicate removal, input cleaning, failed refreshes,
transaction rollback, article history, robots denial and extraction failure.
They run offline. Feed availability changes, so verify live collection separately.
Dependencies have bounded versions; `requirements.lock.txt` records the versions
used during local verification. The Docker image uses the broader compatible
requirements to allow platform-specific dependency resolution.

## Reference documentation

- [Trafilatura Python usage](https://trafilatura.readthedocs.io/en/latest/usage-python.html)
- [Streamlit Docker deployment](https://docs.streamlit.io/deploy/tutorials/docker)
