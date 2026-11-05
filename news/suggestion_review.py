"""Read-only feed discovery and catalog drafts for private outlet suggestions."""
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser
from django.utils.text import slugify
from django.utils.translation import gettext, override

from dashboard.countries import COUNTRY_FLAGS
from news import collector, database, network
from news.sources import SOURCES

COMMON_FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/arc/outboundfeeds/rss/")
FEED_TYPES = {"application/rss+xml", "application/atom+xml"}
MAX_ADVERTISED_FEEDS = 20


def website_host(url):
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        return host.removeprefix("www.")
    except ValueError:
        return ""


def same_site(first, second):
    return bool(first and second) and (
        first == second or first.endswith("." + second) or second.endswith("." + first)
    )


def matching_sources(website):
    host = website_host(website)
    return [source for source in SOURCES
            if any(same_site(host, website_host(url)) for url in (source.website, source.feed_url))]


class FeedLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base = ""
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base" and not self.base:
            self.base = attrs.get("href") or ""
        if tag != "link" or "alternate" not in (attrs.get("rel") or "").lower().split():
            return
        media_type = (attrs.get("type") or "").split(";", 1)[0].strip().lower()
        if media_type in FEED_TYPES and attrs.get("href"):
            self.links.append(attrs["href"])


def advertised_feeds(content, website):
    parser = FeedLinks()
    parser.feed(content.decode("utf-8", errors="replace"))
    base = urljoin(website, parser.base)
    urls = []
    for href in parser.links:
        try:
            url = collector.normalize_url(urljoin(base, href))
        except ValueError:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def common_feeds(website):
    parts = urlsplit(website)
    return [urlunsplit((parts.scheme, parts.netloc, path, "", "")) for path in COMMON_FEED_PATHS]


@dataclass(frozen=True)
class FeedReport:
    url: str
    item_count: int
    newest_item_date: str | None
    sample_titles: list[str]
    language_code: str


def inspect_feed(content, url):
    # Streams prevent feedparser from treating response bytes as a URL or filename.
    records = collector.parse_feed(BytesIO(content), limit=None)
    language = feedparser.parse(BytesIO(content)).feed.get("language", "")
    dates = [record["published_at"] for record in records if record["published_at"]]
    return FeedReport(url, len(records), max(dates, default=None),
                      [record["title"] for record in records[:3]], language.strip())


def fetch_and_parse(extractor, url, parser):
    # Each attempt has its own budget, so a slow homepage cannot hide working feeds.
    token = collector.SOURCE_DEADLINE.set(time.monotonic() + network.DEFAULT_DOWNLOAD_SECONDS)
    try:
        content = extractor.fetch(url)
        return collector.bounded_work(parser, content, url)
    finally:
        collector.SOURCE_DEADLINE.reset(token)


def country_name(value):
    value = value.strip().casefold()
    for country in COUNTRY_FLAGS:
        if country.casefold() == value:
            return country
    with override("pt-br"):
        for country in COUNTRY_FLAGS:
            if gettext(country).casefold() == value:
                return country
    return ""


def source_draft(suggestion, report):
    country = country_name(suggestion["country"])
    # The flag list supplies canonical country names; the catalog supplies continents.
    continent = next((source.continent for source in SOURCES if source.country == country), "TODO: continent")
    country = country or "TODO: country" + (f" (suggested: {suggestion['country']})" if suggestion["country"] else "")
    code_parts = report.language_code.replace("_", "-").split("-")
    language_code = "-".join([code_parts[0].lower()] + [part.upper() if len(part) == 2 else part for part in code_parts[1:]])
    language = next((source.language for source in SOURCES
                     if language_code and source.language_code.split("-")[0].lower() == code_parts[0].lower()), "TODO: language")
    source_id = slugify(suggestion["name"]) or "TODO-source-id"
    if any(source.id == source_id for source in SOURCES):
        source_id += "-TODO-unique-id"
    fields = {
        "id": source_id, "name": suggestion["name"], "continent": continent,
        "country": country, "language": language, "feed_url": report.url,
        "website": suggestion["website"], "discovery": "rss",
        "language_code": language_code or "TODO: language_code", "edition_scope": "TODO: edition scope",
    }
    return "Source(\n" + "".join(f"    {key}={value!r},\n" for key, value in fields.items()) + "),"


def terminal_safe(line):
    """Escape control characters, including newlines, from public input and remote pages.

    Without this, a suggested website or feed could send terminal escape codes
    that hide or rewrite lines of the review.
    """
    return "".join(character if character.isprintable() else repr(character)[1:-1] for character in line)


def review_suggestion(suggestion, already_catalogued=False):
    status = " [already in the catalog]" if already_catalogued else ""
    lines = [f"Suggestion #{suggestion['id']}: {suggestion['name']!r}{status}",
             f"Website: {suggestion['website']}", f"Country: {suggestion['country'] or 'TODO: country'}",
             f"Submitted: {suggestion['submitted_at']}", f"Reason: {suggestion['reason']!r}"]
    extractor = collector.ArticleExtractor()
    candidates = []
    try:
        advertised = fetch_and_parse(extractor, suggestion["website"], advertised_feeds)
        candidates.extend(advertised[:MAX_ADVERTISED_FEEDS])
        if len(advertised) > MAX_ADVERTISED_FEEDS:
            lines.append(f"Reviewing the first {MAX_ADVERTISED_FEEDS} advertised feeds; {len(advertised)} found.")
    except (OSError, ValueError) as error:
        lines.append(f"Page discovery failed: {error}")
    try:
        candidates.extend(common_feeds(suggestion["website"]))
    except ValueError as error:
        lines.append(f"Common feed discovery failed: {error}")
    candidates.extend(source.feed_url for source in matching_sources(suggestion["website"]) if source.feed_url)
    reports = []
    for url in dict.fromkeys(candidates):
        try:
            report = fetch_and_parse(extractor, url, inspect_feed)
        except (OSError, ValueError) as error:
            lines.extend([f"Feed: {url}", f"  Unavailable: {error}"])
            continue
        reports.append(report)
        lines.extend([f"Feed: {report.url}", f"  Items: {report.item_count}",
                      f"  Newest item: {report.newest_item_date or 'unknown'}"])
        lines.extend(f"  Sample: {title!r}" for title in report.sample_titles)
    if reports:
        best = max(reports, key=lambda report: (report.newest_item_date or "", report.item_count))
        lines.append("Draft for review (newest feed, then most items):")
        lines.extend(source_draft(suggestion, best).splitlines())
    else:
        lines.append('No usable feed found. Consider discovery="html" and review the publisher page with a suitable HTML headline parser.')
    return "\n".join(terminal_safe(line) for line in lines)


def review_suggestions(include_catalogued=False):
    suggestions = database.get_outlet_suggestions()
    reports = []
    skipped = 0
    for suggestion in suggestions:
        already_catalogued = bool(matching_sources(suggestion["website"]))
        if already_catalogued and not include_catalogued:
            skipped += 1
            continue
        reports.append(review_suggestion(suggestion, already_catalogued))
    if not reports:
        reports.append("No suggestions to review.")
    if skipped:
        reports.append(f"Skipped {skipped} suggestion(s) already in the catalog; use --all to include them.")
    return "\n\n".join(reports)
