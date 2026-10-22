"""RSS discovery → normalized records → optional Trafilatura extraction → SQLite."""
import calendar
import logging
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import requests
import trafilatura

from news import database

LOG = logging.getLogger(__name__)
USER_AGENT = "NewsTheGathering/0.1 (personal news reader)"
MAX_BYTES = 5_000_000


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


def download(url):
    with requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(10, 20), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("Response exceeds 5 MB limit")
            chunks.append(chunk)
        return b"".join(chunks)


def parse_feed(content):
    feed = feedparser.parse(content)
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
        date = datetime.fromtimestamp(calendar.timegm(published), timezone.utc).isoformat() if published else None
        records.append({"url": url, "title": title,
                        "summary": clean_text(entry.get("summary", ""))[:1000],
                        "published_at": date, "body": ""})
        if len(records) == 10:
            break
    if not records:
        raise ValueError("Feed contains no usable headlines; it may be blocked or no longer available")
    return records


class ArticleExtractor:
    """One instance per collection: cache robots rules and space requests per host."""
    def __init__(self):
        self.rules = {}
        self.last_request = {}

    def extract(self, url):
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
            raise ValueError("Article extraction disallowed by robots.txt")
        delay = max(1, robots.crawl_delay(USER_AGENT) or 0)
        time.sleep(max(0, delay - (time.monotonic() - self.last_request.get(origin, 0))))
        try:
            html = download(url)
            return trafilatura.extract(html, url=url, include_comments=False, favor_precision=True) or ""
        finally:
            self.last_request[origin] = time.monotonic()


def collect_source(source, path=None, extract_articles=False, extractor=None):
    try:
        articles = parse_feed(download(source.feed_url))
        if extract_articles:
            extractor = extractor or ArticleExtractor()
            for article in articles:
                article["body"] = database.get_body(source.id, article["url"], path)
                if not article["body"]:
                    try:
                        article["body"] = extractor.extract(article["url"])
                    except Exception as error:
                        LOG.warning("%s: extraction skipped: %s", source.name, error)
        database.save_collection(source.id, articles, path)
        return {"source": source.name, "count": len(articles), "error": ""}
    except Exception as error:
        LOG.warning("%s: collection failed: %s", source.name, error)
        database.record_failure(source.id, error, path)
        return {"source": source.name, "count": 0, "error": str(error)}


def collect_all(sources, path=None, extract_articles=False):
    database.initialize(path)
    extractor = ArticleExtractor()
    return [collect_source(source, path, extract_articles, extractor) for source in sources if source.enabled]
