# News, The Gathering

Compare ten headlines from each news outlet in a Django web app. Collectors save
publisher feeds and source pages to SQLite; Django views read those saved records and render HTML
templates. Optional Trafilatura extraction adds article text. The interface uses
ordinary HTML and CSS, with a small JavaScript file for synchronized filters and no build step.

## Run locally

Use Python 3.12 or newer supported by Django 5.2. The previous Python 3.9
Streamlit environment is no longer suitable. A separate `.venv-django` environment
keeps the migration independent of the old environment.

From this folder, for a new installation:

```bash
python3.12 -m venv .venv-django
source .venv-django/bin/activate
python -m pip install -r requirements.txt
python -m news.collect
python manage.py runserver 127.0.0.1:8000
```

This workspace already has `.venv-django` installed, so you can start it with:

```bash
source .venv-django/bin/activate
python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000. Existing `data/news.db` records work immediately;
there is no data migration or need to recollect. An empty installation shows an
empty state until the first collection. Opening the page never starts scraping
or creates the news database. Django's development server serves CSS locally.

A failed outlet does not prevent other outlets from being saved. The collector
exits with status 1 if any source fails, and the dashboard shows each outlet's
error and last successful collection.

In a second terminal, keep headlines updated even when no browser is open:

```bash
source .venv-django/bin/activate
python -m news.collect --interval 30
```

The interval starts after each collection finishes. Run one scheduled collector
per database. Continent checkboxes select or clear their outlets immediately. Outlet
checkboxes update their continent: full, partially selected, or empty. **All on**
and **All off** select or clear the entire catalog. Deselecting a continent hides
its outlets from the filter list; reselecting it reveals and selects them again.
Apply filters
to update the news selection; search matches
headlines and summaries without changing feed rankings. Filter selections live
in the URL, so a comparison can be bookmarked. Reload saved headlines fetches the
latest stored records with the current filters. The page does not refresh itself.
Search is limited to each outlet's current ten, and the saved headline metric
counts that selection before the search filter is applied. Each compact outlet
edition shows its lead story and two additional headlines; expand the remaining
stories to see the rest of its top ten and any extracted text.

Collect selected outlets or opt into full-text extraction:

```bash
python -m news.collect --source bbc --source g1
python -m news.collect --source bbc --extract
```

Extraction respects robots.txt and spaces article requests by at least one second
(or the declared crawl delay). Unavailable robots rules, blocked pages, and
paywalls leave the RSS headline intact. Extraction failures are logged and do not
fail headline collection. Stored article bodies are reused. The default is
headline, summary and link collection. Check publisher permissions before enabling
full-text display on a public service.

## What “top ten” means

The first ten unique valid entries in each publisher's feed, in feed order.
For CNN and CNN Space, headline discovery uses their current publisher pages
in page order instead of the legacy CNN RSS feed.
This is not a popularity score or an exact homepage ranking. Feeds can mix
sections and include opinion, live coverage or older pinned stories. Publication
time and collection time are separate. The dashboard warns about collections
older than two hours and feeds whose newest dated story is older than 48 hours.
Missing dates are shown as unavailable. All displayed times are UTC.
CNN's listing cards do not supply publication times, so the collector leaves
those dates unavailable instead of inventing a time from the story's URL.

Geography means the outlet's home base. The catalog includes at least three
distinct news organizations per inhabited continent, with CNN Space as an additional section:

| Continent | News organizations |
| --- | --- |
| Africa | Daily Maverick (South Africa), Premium Times (Nigeria), Nation (Kenya) |
| Asia | Al Jazeera (Qatar), CNA (Singapore), The Hindu (India) |
| Europe | BBC News, The Guardian (United Kingdom), DW (Germany), El País, El Mundo (Spain), Le Monde, France 24 (France), TASS, Kommersant (Russia) |
| North America | NPR, CNN (United States), CBC News (Canada), plus CNN Space |
| South America | g1/Globo, Folha (Brazil), Buenos Aires Herald, La Nación (Argentina), Cooperativa (Chile), El Tiempo (Colombia) |
| Oceania | ABC News, SBS News (Australia), RNZ (New Zealand) |

This gives 28 source sections from 27 news organizations. It is a starting
selection rather than a definitive ranking of leading outlets. CNN Space refers
to CNN’s Space & Science coverage and shares `organization="cnn"` with CNN.
Publisher page availability is reported by the collector like feed availability.
Languages remain as published; translation
and automatic same-story matching are not implemented.

## Understand the code

| File | Responsibility |
| --- | --- |
| `manage.py` | Django commands: runserver, check and collectstatic |
| `gathering/settings.py` | Django configuration and production environment settings |
| `gathering/urls.py` | Project URL routing |
| `dashboard/urls.py` | Dashboard and health check routes |
| `dashboard/views.py` | Filter selections, read records and prepare template context |
| `dashboard/countries.py` | Country flags displayed beside outlet languages |
| `dashboard/templates/dashboard/index.html` | Page layout and accessible HTML controls |
| `dashboard/static/dashboard/styles.css` | Colors, typography and responsive layout |
| `dashboard/static/dashboard/favicon.svg` | The masthead logo as the browser tab icon |
| `dashboard/static/dashboard/world-map.svg` | Decorative world map using public-domain Natural Earth coastlines |
| `dashboard/static/dashboard/filters.js` | Synchronize continent and outlet selections |
| `news/sources.py` | Outlet names, countries, continents, languages and feed URLs |
| `news/collector.py` | Download feeds, clean text, deduplicate and extract articles |
| `news/database.py` | SQLite schema, collector writes and read-only queries |
| `news/collect.py` | Command-line collection and periodic loop |
| `tests/test_pipeline.py` | Offline collection and storage tests |
| `tests/test_app.py` | Django client tests for page behavior and safe rendering |
| `tests/test_cnn_sources.py` | CNN page discovery, source validation and collection tests |
| `tests/test_source_catalog.py` | Unique source IDs and three distinct organizations per continent |

To add an outlet, add a `Source(...)` in `news/sources.py` with a unique ID and an
official RSS/Atom feed, then run the collector. CNN’s HTML discovery supports its
publisher listing pages; select `discovery="html"` and set the source website to
the listing page. The HTML selectors are specific to CNN; another publisher's
HTML layout needs its own parser. Source URLs are trusted project
configuration. Set `enabled=False` to hide an unavailable outlet. No schema
change is needed. Edit CSS variables in `styles.css` to change the theme.
When adding an outlet from a new country, add its flag in `dashboard/countries.py`.
Outlet names and arrows link to their publisher website. Country names appear
when hovering or focusing the flags beside each outlet's language.

The dashboard uses the existing database functions rather than Django models,
so collectors continue to work independently of Django. No Django migrations
are required. The app currently has no admin site, accounts or sessions.

## Storage and security

The database remains at `data/news.db`, ignored by Git. Override it with the
`NEWS_DB_PATH` environment variable. `articles` stores unique `(source_id, url)`
records and historical articles. `headlines` stores the latest successful ranking;
`source_status` tracks attempts, successes and errors. Collections use transactions
and failed sources preserve the previous ranking. Historical ranking snapshots
and automatic retention are not implemented.

Dashboard queries open SQLite with `mode=ro`; the collector owns schema creation
and updates. SQLite may still manage WAL shared-memory sidecar files, so this is
read-only access to the database contents, not a read-only volume mount. SQL
parameters are bound separately. Django templates escape publisher text and
article links are limited to HTTP(S). The dashboard only accepts GET/HEAD.
It has no login; anyone who can reach the service can view the news. The database
is not encrypted, and filesystem access still depends on operating-system
permissions. Database security and backups remain deployment responsibilities.

Back up with SQLite's backup API, or stop both processes before copying the DB.
Copying only the main file during active WAL writes can miss data. Do not put
SQLite on a network share.

## Docker and hosting

Create a `.env` file for Docker Compose and generate a fresh secret:

```bash
cp .env.example .env
python -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Paste the generated value after `DJANGO_SECRET_KEY=` in `.env`. Keep it private;
`.env` is ignored by Git and Docker builds. Then start both services:

```bash
docker compose up --build -d
```

The web service runs Django through Gunicorn with debug disabled; WhiteNoise
serves the collected CSS. The collector runs every 30 minutes. Both use the
persistent `news-data` volume. Existing local `data/news.db` is separate from this
Docker volume; a first Docker launch populates its own database with the collector.
Do not delete the volume unless you intend to erase its database.

The Compose web port binds to `127.0.0.1:8000` on the host. For online access, run
it on a server behind an HTTPS reverse proxy on that host and set
`DJANGO_ALLOWED_HOSTS` to include your domain. Add proxy authentication for private
access. Enable `DJANGO_SECURE_SSL_REDIRECT=true` after TLS is configured. Set
`DJANGO_TRUST_PROXY=true` only when the proxy strips incoming forwarded-protocol
headers and sets `X-Forwarded-Proto` itself. If HTTPS redirects are enabled, adapt
the HTTP health check to use your HTTPS endpoint. Django itself reads environment
variables; it does not automatically load `.env` outside Compose.

For hosting without Docker:

```bash
export DJANGO_DEBUG=false
export DJANGO_SECRET_KEY='your-generated-private-secret'
export DJANGO_ALLOWED_HOSTS='your-domain.example,localhost,127.0.0.1'
python manage.py collectstatic --noinput
python manage.py check --deploy
gunicorn gathering.wsgi:application --bind 127.0.0.1:8000 --workers 2
```

Review deploy-check warnings for your HTTPS/proxy configuration. `/health/` is a
process liveness endpoint; it does not assert feed freshness or scrape publishers.
The site is not deployed by these repository changes. A host and persistent disk
are still needed for access from anywhere. Keep web and collector on one host
sharing local disk; consider PostgreSQL before scaling across hosts. Hosts with
ephemeral filesystems can lose SQLite data on restarts.

## Validate

```bash
python manage.py check
python -m unittest discover -s tests -v
```

The tests use temporary databases and require no publisher connections. They cover
collection/storage behavior and Django filtering, empty states, stale-feed notices,
HTML escaping and read-only requests. Feed availability changes, so verify live
collection separately. `requirements.lock.txt` records the Python 3.12 versions
used during verification. The Docker image uses bounded `requirements.txt` to
allow platform-specific dependency resolution.

## Documentation

- [Django 5.2](https://docs.djangoproject.com/en/5.2/)
- [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)
- [Trafilatura Python usage](https://trafilatura.readthedocs.io/en/latest/usage-python.html)
