"""RSS or publisher-page discovery → normalized records → optional extraction → SQLite."""
import calendar
import hashlib
import logging
import math
import os
import re
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.sax import SAXParseException

import feedparser
import trafilatura
from lxml import html as lxml_html

from news import database, network

LOG = logging.getLogger(__name__)
USER_AGENT = "NewsTheGathering/0.1 (personal news reader)"
MAX_BYTES = 5_000_000
# CNN's public homepage includes enough embedded images/settings to exceed 5 MB.
LISTING_MAX_BYTES = 10_000_000
SOURCE_DEADLINE = ContextVar("source_deadline", default=None)


def preparation_limits(timeout_seconds=None, minimum_edition_ratio=None):
    """Validate settings before a request or database write can begin."""
    try:
        timeout = float(os.environ.get("NEWS_SOURCE_TIMEOUT_SECONDS", "60") if timeout_seconds is None else timeout_seconds)
        ratio = float(os.environ.get("NEWS_MIN_EDITION_RATIO", "0.5") if minimum_edition_ratio is None else minimum_edition_ratio)
    except (TypeError, ValueError):
        raise ValueError("NEWS_SOURCE_TIMEOUT_SECONDS and NEWS_MIN_EDITION_RATIO must be numbers") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("NEWS_SOURCE_TIMEOUT_SECONDS / --source-timeout must be a finite positive number")
    if not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise ValueError("NEWS_MIN_EDITION_RATIO / --min-edition-ratio must be between 0 and 1")
    return timeout, ratio


def retention_limits():
    values = []
    for name, default in (("NEWS_RETENTION_DAYS", "30"), ("NEWS_MAX_EDITIONS", "48")):
        try:
            value = int(os.environ.get(name, default))
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a positive integer") from None
        if value < 1:
            raise ValueError(f"{name} must be a positive integer")
        values.append(value)
    return tuple(values)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_text(value):
    parser = PlainText()
    parser.feed(value or "")
    return " ".join(" ".join(parser.parts).split())


def normalize_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        raise ValueError("Article link must be an HTTP(S) URL")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def download(url, max_bytes=MAX_BYTES, redirect_guard=None):
    return network.download(url, max_bytes, USER_AGENT, SOURCE_DEADLINE.get(), redirect_guard)


def bounded_work(function, *args):
    """Parsers and text extraction share the source's enforceable time budget."""
    deadline = SOURCE_DEADLINE.get()
    if deadline is None:
        return function(*args)
    return network.run_with_deadline(function, args, deadline)


def parse_feed(content):
    feed = feedparser.parse(content)
    # feedparser salvages completed entries from cut-off XML. Such an edition
    # must not replace a healthy saved one. Encoding-override warnings are benign.
    if isinstance(feed.get("bozo_exception"), SAXParseException):
        raise ValueError("Feed XML is malformed or incomplete; keeping the previous edition")
    records, seen = [], set()
    for entry in feed.entries:
        title = clean_text(entry.get("title", ""))
        try:
            url = normalize_url(entry.get("link", ""))
        except ValueError:
            continue
        if not title or url in seen:
            continue
        seen.add(url)
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        try:
            date = datetime.fromtimestamp(calendar.timegm(published), timezone.utc).isoformat() if published else None
        except (ValueError, OverflowError, OSError):
            date = None
        records.append({"url": url, "title": title,
                        "summary": clean_text(entry.get("summary", ""))[:1000],
                        "published_at": date, "body": ""})
        if len(records) == 10:
            break
    if not records:
        raise ValueError("Feed contains no usable headlines; it may be blocked or no longer available")
    return records


def parse_html_headlines(content, page_url):
    """Read CNN's headline cards in page order, ignoring navigation and image links.

    CNN's old RSS is frozen. Its homepage and dedicated Space page publish
    ordered headline cards in HTML, including full headlines and some summaries.
    A dated article URL identifies a story but does not supply a publication time.
    """
    document = lxml_html.fromstring(content, parser=lxml_html.HTMLParser(encoding="utf-8"))
    links = document.xpath(
        "//*[@role='main' or @data-track-zone='main' or self::main]"
        "//a[@data-link-type='article' or @data-link-type='live-story']"
    )
    records, seen = [], set()
    for link in links:
        # An image-credit link often precedes the actual headline for a card.
        headlines = link.xpath(".//*[@data-editable='headline']")
        if not headlines:
            continue
        try:
            url = normalize_url(urljoin(page_url, link.get("href", "")))
        except ValueError:
            continue
        parts = urlsplit(url)
        if parts.hostname not in {"cnn.com", "www.cnn.com", "edition.cnn.com"}:
            continue
        match = re.match(r"/(\d{4}/\d{2}/\d{2})/[^/]+/.+", parts.path)
        if not match:
            continue
        try:
            datetime.strptime(match.group(1), "%Y/%m/%d")
        except ValueError:
            continue
        # CNN uses both hosts for the same stories; canonicalize for deduplication.
        url = urlunsplit(("https", "www.cnn.com", parts.path, "", ""))
        title = clean_text(lxml_html.tostring(headlines[0], encoding="unicode"))
        if not title or url in seen:
            continue
        seen.add(url)
        descriptions = link.xpath(".//*[@data-editable='description']")
        summary = clean_text(lxml_html.tostring(descriptions[0], encoding="unicode")) if descriptions else ""
        records.append({"url": url, "title": title, "summary": summary[:1000],
                        "published_at": None, "body": ""})
        if len(records) == 10:
            break
    if not records:
        raise ValueError("Publisher page contains no usable headlines; its layout may have changed")
    return records


class ArticleExtractor:
    """One instance per collection: cache robots rules and space requests per host."""
    def __init__(self):
        self.rules = {}
        self.last_request = {}

    def allow_request(self, url):
        """Check and pace the original URL and every redirected HTML destination."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.rules:
            robots = RobotFileParser()
            # Fail closed when robots.txt cannot be read. RSS headlines still work.
            robots.parse(download(origin + "/robots.txt").decode("utf-8", errors="replace").splitlines())
            self.rules[origin] = robots
            self.last_request[origin] = time.monotonic()
        robots = self.rules[origin]
        if not robots.can_fetch(USER_AGENT, url):
            raise ValueError("Page collection disallowed by robots.txt")
        delay = max(1, robots.crawl_delay(USER_AGENT) or 0)
        pause = max(0, delay - (time.monotonic() - self.last_request.get(origin, 0)))
        deadline = SOURCE_DEADLINE.get()
        if deadline is not None and pause >= network.remaining_seconds(deadline):
            raise network.DeadlineExceeded("Robots pacing would exceed source preparation deadline")
        time.sleep(pause)
        self.last_request[origin] = time.monotonic()

    def fetch(self, url, max_bytes=MAX_BYTES):
        """Fetch only when robots permits each request, including redirect hops."""
        self.allow_request(url)
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        try:
            return download(url, max_bytes=max_bytes, redirect_guard=self.allow_request)
        finally:
            self.last_request[origin] = time.monotonic()

    def extract(self, url):
        html = self.fetch(url)
        return bounded_work(extract_text, html, url)


def extract_text(html, url):
    return trafilatura.extract(html, url=url, include_comments=False, favor_precision=True) or ""


def body_metadata_hash(article):
    return hashlib.sha256((article["title"] + "\0" + article["summary"]).encode("utf-8")).hexdigest()


def body_needs_refresh(metadata, article, hours=24):
    if not metadata or not metadata.get("body"):
        return True
    if metadata.get("body_metadata_hash") != body_metadata_hash(article):
        return True
    try:
        fetched = metadata.get("body_fetched_at")
        if isinstance(fetched, str):
            fetched = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
        if fetched is None:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - fetched).total_seconds() >= hours * 3600
    except (TypeError, ValueError):
        return True


def collect_source(source, path=None, extract_articles=False, extractor=None,
                   timeout_seconds=None, minimum_edition_ratio=None):
    started = time.monotonic()
    timeout_seconds, ratio = preparation_limits(timeout_seconds, minimum_edition_ratio)
    token = SOURCE_DEADLINE.set(started + timeout_seconds)
    warnings = []
    result = {"source": source.name, "source_id": source.id, "count": 0, "error": "", "warnings": warnings}
    try:
        if source.discovery == "html":
            extractor = extractor or ArticleExtractor()
            articles = bounded_work(parse_html_headlines, extractor.fetch(source.website, LISTING_MAX_BYTES), source.website)
        elif source.discovery == "rss":
            articles = bounded_work(parse_feed, download(source.feed_url))
        else:
            raise ValueError(f"Unknown discovery method: {source.discovery}")
        _, previous = database.get_dashboard([source.id], path)
        previous_count = len(previous.get(source.id, []))
        if previous_count >= 5 and len(articles) < previous_count * ratio:
            raise ValueError(f"Suspicious edition shrink: {previous_count} to {len(articles)} headlines; keeping the previous edition")
        # A complete validated edition is usable even if optional body extraction
        # later exhausts the remaining budget. Workers have already been joined.
        network.remaining_seconds(SOURCE_DEADLINE.get())
        if extract_articles:
            extractor = extractor or ArticleExtractor()
            for article in articles:
                if time.monotonic() >= SOURCE_DEADLINE.get():
                    warnings.append("Source preparation deadline reached; remaining article extraction skipped")
                    break
                metadata = database.get_body_metadata(source.id, article["url"], path)
                article["body"] = metadata.get("body", "") if metadata else ""
                if metadata and metadata.get("body_fetched_at"):
                    article["body_fetched_at"] = metadata["body_fetched_at"]
                    article["body_metadata_hash"] = metadata.get("body_metadata_hash", "")
                if body_needs_refresh(metadata, article):
                    try:
                        body = extractor.extract(article["url"])
                        if body:
                            article["body"] = body
                            article["body_fetched_at"] = datetime.now(timezone.utc).isoformat()
                            article["body_metadata_hash"] = body_metadata_hash(article)
                        else:
                            warnings.append("Article extraction returned no accessible text; keeping any saved body")
                    except Exception as error:
                        warnings.append(f"Article extraction skipped: {error}")
                        LOG.warning("%s: extraction skipped: %s", source.name, error)
                        if isinstance(error, network.DeadlineExceeded) or time.monotonic() >= SOURCE_DEADLINE.get():
                            break
        database.save_collection(source.id, articles, path)
        result["count"] = len(articles)
    except Exception as error:
        LOG.warning("%s: collection failed: %s", source.name, error)
        result["error"] = str(error)
        try:
            database.record_failure(source.id, error, path)
        except Exception:
            LOG.exception("%s: could not save failure status", source.name)
    finally:
        SOURCE_DEADLINE.reset(token)
        result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def collect_all(sources, path=None, extract_articles=False, timeout_seconds=None, minimum_edition_ratio=None):
    timeout_seconds, minimum_edition_ratio = preparation_limits(timeout_seconds, minimum_edition_ratio)
    retention_days, max_editions = retention_limits()
    database.initialize(path)
    started_at = database.utc_now()
    extractor = ArticleExtractor()
    results = [collect_source(source, path, extract_articles, extractor, timeout_seconds, minimum_edition_ratio)
               for source in sources if source.enabled]
    database.record_run(results, path=path, started_at=started_at, finished_at=database.utc_now())
    database.prune(path=path, days=retention_days, max_editions=max_editions)
    return results
