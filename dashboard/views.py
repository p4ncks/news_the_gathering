"""Read saved news, prepare the comparison, then render ordinary Django HTML."""
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

from django.conf import settings
from django.db import DatabaseError
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from news import database
from news.sources import CONTINENTS, SOURCES

from .countries import COUNTRY_FLAGS
from .suggestions import suggestion_context

LOG = logging.getLogger(__name__)


def parse_timestamp(value):
    """Keep a malformed publisher date from breaking the whole page."""
    if not value:
        return None
    try:
        date = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
    except (AttributeError, TypeError, ValueError):
        return None


def timestamp_label(value):
    date = parse_timestamp(value)
    return date_format(date.astimezone(timezone.utc), "d M Y · H:i") + " UTC" if date else _("Not available")


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


def selection_url(request, **changes):
    """Change one display option without losing the bookmarked selection."""
    parameters = request.GET.copy()
    for name, value in changes.items():
        if value is None:
            parameters.pop(name, None)
        else:
            parameters[name] = value
    query = parameters.urlencode()
    return ("?" + query if query else "?") + "#headlines"


@require_safe
@never_cache
def index(request):
    # An explicit filter submission may contain no checked boxes. Initial visits
    # default to all outlets; saved query URLs preserve the submitted selection.
    filtered = any(key in request.GET for key in ("filtered", "continent", "outlet"))
    visible_continents = set(request.GET.getlist("continent") if filtered else CONTINENTS)
    available = [source for source in SOURCES if source.enabled and source.continent in visible_continents]
    selected_ids = request.GET.getlist("outlet") if filtered else [source.id for source in available]
    sources = [source for source in available if source.id in selected_ids]
    selected_ids = {source.id for source in sources}
    # The enhanced form synchronizes continent state before submitting. Without
    # JavaScript, keep a newly checked continent visible so its outlets can be
    # chosen on the next submission, including after an empty selection.
    if request.GET.get("enhanced") == "1":
        visible_continents = {source.continent for source in sources}
    visible_continents.intersection_update(CONTINENTS)
    query = request.GET.get("q", "").strip()
    compact = request.GET.get("view") == "compact"
    show_empty = request.GET.get("show_empty") == "1"
    freshness_hours = float(settings.NEWS_FRESHNESS_HOURS)

    database_unavailable = False
    try:
        statuses, headlines = database.get_dashboard([source.id for source in sources])
    except (DatabaseError, sqlite3.Error, OSError):
        LOG.exception("Could not read saved headlines")
        statuses, headlines = {}, {}
        database_unavailable = True

    cards = []
    headline_count = 0
    collection_dates = []
    for source in sources:
        articles = headlines.get(source.id, [])
        headline_count += len(articles)
        status = statuses.get(source.id, {})
        collected_at = parse_timestamp(status.get("succeeded_at"))
        if collected_at:
            collection_dates.append(collected_at)
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
            "flag": COUNTRY_FLAGS.get(source.country, ""),
            # Publisher failures can contain internal paths or credentialed URLs.
            # Expose the condition and timestamps, never the raw exception.
            "status": {"error": bool(status.get("error")), "succeeded_at": status.get("succeeded_at")},
            "collected_label": timestamp_label(status.get("succeeded_at")),
            "attempted_label": timestamp_label(status.get("attempted_at")),
            "collection_stale": older_than(status.get("succeeded_at"), freshness_hours),
            "feed_stale": older_than(max(published_dates).isoformat(), 48) if published_dates else False,
            "articles": matches,
            "has_headlines": bool(articles),
        })

    match_count = sum(len(card["articles"]) for card in cards)
    matching_outlet_count = sum(bool(card["articles"]) for card in cards)
    hidden_empty_count = len(cards) - matching_outlet_count if query else 0
    if query and not show_empty:
        cards = [card for card in cards if card["articles"]]

    return render(request, "dashboard/index.html", {
        **suggestion_context(request),
        "continent_options": [{
            "name": name,
            "selected": name in visible_continents,
            "partial": 0 < sum(source.continent == name for source in sources)
                       < sum(source.enabled and source.continent == name for source in SOURCES),
        } for name in CONTINENTS],
        # Keep all controls in the HTML so the browser can reveal a continent's
        # outlets immediately, while hiding groups outside the active selection.
        "outlet_options": [{"id": source.id, "name": source.name, "continent": source.continent,
                            "selected": source.id in selected_ids,
                            "visible": source.continent in visible_continents} for source in SOURCES if source.enabled],
        "no_visible_outlets": not visible_continents,
        "query": query,
        "outlet_count": len(sources),
        "continent_count": len({source.continent for source in sources}),
        "headline_count": headline_count,
        "latest_collection_label": timestamp_label(max(collection_dates)) if collection_dates else _("Not available"),
        "freshness_hours": freshness_hours,
        "match_count": match_count,
        "matching_outlet_count": matching_outlet_count,
        "no_matches": bool(query and sources and headline_count and not match_count),
        "hidden_empty_count": hidden_empty_count,
        "show_empty": show_empty,
        "empty_toggle_url": selection_url(request, show_empty=None if show_empty else "1"),
        "clear_search_url": selection_url(request, q=None, show_empty=None),
        "compact": compact,
        "cards_url": selection_url(request, view=None),
        "compact_url": selection_url(request, view="compact"),
        "cards": cards,
        "sources": sorted(SOURCES, key=lambda source: source.name.casefold()),
        "no_sources": not sources,
        "no_headlines": bool(sources) and not headline_count,
        "database_unavailable": database_unavailable,
    }, status=503 if database_unavailable else 200)


@require_safe
@never_cache
def article(request, source_id, article_id):
    """Load one saved article body only when a reader asks for it."""
    source = next((source for source in SOURCES if source.enabled and source.id == source_id), None)
    if source is None:
        raise Http404("Article not found")
    try:
        saved = database.get_article(source_id, article_id)
    except (DatabaseError, sqlite3.Error, OSError):
        LOG.exception("Could not read saved article")
        return render(request, "dashboard/article.html", {"source": source, "unavailable": True}, status=503)
    if saved is None:
        raise Http404("Article not found")
    fingerprint = saved.get("body_metadata_hash", "")
    current_fingerprint = hashlib.sha256((saved["title"] + "\0" + saved["summary"]).encode("utf-8")).hexdigest()
    # Older imports lack a version fingerprint; their collection timestamp is
    # not proof of when the text was extracted. Avoid presenting it as fresh.
    saved_at = saved.get("body_fetched_at") if fingerprint else None
    return render(request, "dashboard/article.html", {
        "source": source,
        "article": saved,
        "published_label": timestamp_label(saved.get("published_at")),
        "body_saved_label": timestamp_label(saved_at),
        "body_version_unknown": bool(saved.get("body") and not fingerprint),
        "body_metadata_changed": bool(saved.get("body") and fingerprint and fingerprint != current_fingerprint),
        "publisher_url": saved["url"] if safe_article_link(saved["url"]) else "",
    })


@require_safe
def health(request):
    """Process liveness; an empty news database is a valid first launch."""
    return JsonResponse({"status": "ok"})


@require_safe
@never_cache
def ready(request):
    """Report usable editions separately from the web process's liveness."""
    enabled = [source for source in SOURCES if source.enabled]
    try:
        statuses, headlines = database.get_dashboard([source.id for source in enabled])
    except (DatabaseError, sqlite3.Error, OSError):
        LOG.exception("Readiness could not read saved news")
        return JsonResponse({"status": "unavailable", "reason": "database_unavailable"}, status=503)

    hours = float(getattr(settings, "NEWS_FRESHNESS_HOURS", 2))
    minimum = int(getattr(settings, "NEWS_MIN_FRESH_SOURCES", 1))
    counts = {"fresh": 0, "stale": 0, "failed": 0, "missing": 0}
    successes = []
    for source in enabled:
        status = statuses.get(source.id, {})
        succeeded = parse_timestamp(status.get("succeeded_at"))
        if succeeded:
            successes.append(succeeded)
        if status.get("error"):
            counts["failed"] += 1
        if not succeeded or not headlines.get(source.id):
            counts["missing"] += 1
        elif older_than(succeeded, hours):
            counts["stale"] += 1
        else:
            counts["fresh"] += 1
    usable = counts["fresh"] >= max(1, minimum)
    healthy = usable and counts["fresh"] == len(enabled) and not counts["failed"]
    return JsonResponse({
        "status": "ok" if healthy else "degraded" if usable else "unavailable",
        "sources": len(enabled),
        **counts,
        "latest_success_at": max(successes).isoformat() if successes else None,
        "freshness_hours": hours,
    }, status=200 if usable else 503)
