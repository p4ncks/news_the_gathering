"""Read saved news, prepare the comparison, then render ordinary Django HTML."""
import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from news import database
from news.sources import CONTINENTS, SOURCES

LOG = logging.getLogger(__name__)


def parse_timestamp(value):
    """Keep a malformed publisher date from breaking the whole page."""
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
    except (TypeError, ValueError):
        return None


def timestamp_label(value):
    date = parse_timestamp(value)
    return date.astimezone(timezone.utc).strftime("%d %b %Y · %H:%M UTC") if date else "Not available"


def older_than(value, hours):
    date = parse_timestamp(value)
    return bool(date and (datetime.now(timezone.utc) - date).total_seconds() > hours * 3600)


def safe_article_link(url):
    # Templates escape HTML; this additionally rejects executable URL schemes
    # from any records inserted outside the normal collector.
    try:
        parts = urlsplit(url)
        return bool(parts.scheme in ("http", "https") and parts.hostname and not parts.username)
    except ValueError:
        return False


@require_safe
@never_cache
def index(request):
    # An explicit filter submission may contain no checked boxes. Initial visits
    # default to all outlets; saved query URLs preserve the submitted selection.
    filtered = any(key in request.GET for key in ("filtered", "continent", "outlet"))
    continents = request.GET.getlist("continent") if filtered else CONTINENTS
    available = [source for source in SOURCES if source.enabled and source.continent in continents]
    selected_ids = request.GET.getlist("outlet") if filtered else [source.id for source in available]
    sources = [source for source in available if source.id in selected_ids]
    # Render a single consistent selection even for manually edited query URLs.
    # A continent is active when at least one of its outlets is selected.
    selected_ids = {source.id for source in sources}
    continents = {source.continent for source in sources}
    query = request.GET.get("q", "").strip()

    database_unavailable = False
    try:
        statuses = database.get_statuses()
        headlines = {source.id: database.get_headlines(source.id) for source in sources}
    except sqlite3.Error:
        LOG.exception("Could not read saved headlines")
        statuses, headlines = {}, {}
        database_unavailable = True

    cards = []
    headline_count = 0
    for source in sources:
        articles = headlines.get(source.id, [])
        headline_count += len(articles)
        status = statuses.get(source.id, {})
        published_dates = [parse_timestamp(article.get("published_at")) for article in articles]
        published_dates = [date for date in published_dates if date]
        matches = []
        for article in articles:
            if query.casefold() not in (article["title"] + " " + article["summary"]).casefold():
                continue
            matches.append({
                **article,
                "url": article["url"] if safe_article_link(article["url"]) else "",
                "published_label": timestamp_label(article.get("published_at")),
                "summary_short": article["summary"][:300] + ("…" if len(article["summary"]) > 300 else ""),
            })
        cards.append({
            "source": source,
            "status": status,
            "collected_label": timestamp_label(status.get("succeeded_at")),
            "attempted_label": timestamp_label(status.get("attempted_at")),
            "collection_stale": older_than(status.get("succeeded_at"), 2),
            "feed_stale": older_than(max(published_dates).isoformat(), 48) if published_dates else False,
            "articles": matches,
            "has_headlines": bool(articles),
        })

    return render(request, "dashboard/index.html", {
        "continent_options": [{
            "name": name,
            "selected": name in continents,
            "partial": 0 < sum(source.continent == name for source in sources)
                       < sum(source.enabled and source.continent == name for source in SOURCES),
        } for name in CONTINENTS],
        # Keep all controls in the HTML so the browser can reveal a continent's
        # outlets immediately, while hiding groups outside the active selection.
        "outlet_options": [{"id": source.id, "name": source.name, "continent": source.continent,
                            "selected": source.id in selected_ids,
                            "visible": source.continent in continents} for source in SOURCES if source.enabled],
        "no_visible_outlets": not continents,
        "query": query,
        "outlet_count": len(sources),
        "continent_count": len({source.continent for source in sources}),
        "headline_count": headline_count,
        "cards": cards,
        "sources": sorted(SOURCES, key=lambda source: source.name.casefold()),
        "no_sources": not sources,
        "no_headlines": bool(sources) and not headline_count,
        "database_unavailable": database_unavailable,
    })


@require_safe
def health(request):
    """Process liveness; an empty news database is a valid first launch."""
    return JsonResponse({"status": "ok"})
