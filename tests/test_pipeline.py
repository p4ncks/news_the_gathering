"""Offline tests: no external publisher needs to be online."""
import tempfile
import unittest
import os
import time
import io
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from news import database
from news.collector import collect_all, collect_source, normalize_url, parse_feed, ArticleExtractor, body_needs_refresh, body_metadata_hash, SOURCE_DEADLINE
from news import network
from news.sources import SOURCES

FIXTURES = Path(__file__).parent / "fixtures"
os.environ.setdefault("DJANGO_DEBUG", "true")


def feed(items):
    return ("<rss version='2.0'><channel><title>Test</title>" + items + "</channel></rss>").encode()


def item(number, extra=""):
    return f"<item><title>Story {number}</title><link>https://example.com/{number}{extra}</link><description>&lt;b&gt;Hello&lt;/b&gt; world</description><pubDate>Wed, 21 Oct 2026 12:00:00 GMT</pubDate></item>"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "news.db"
        database.initialize(self.path)
        self.articles = parse_feed(feed(item(1) + item(2)))

    def tearDown(self):
        self.directory.cleanup()

    def test_feed_order_deduplication_limit_and_plain_text(self):
        articles = parse_feed(feed(item(1) + item(1, "?utm_source=x") + "".join(item(n) for n in range(2, 15))))
        self.assertEqual(len(articles), 10)
        self.assertEqual(articles[-1]["title"], "Story 10")
        self.assertEqual(articles[0]["summary"], "Hello world")
        self.assertEqual(articles[0]["published_at"], "2026-10-21T12:00:00+00:00")

    def test_invalid_feed_does_not_look_successful(self):
        with self.assertRaises(ValueError):
            parse_feed(b"<html>Access denied</html>")
        with self.assertRaises(ValueError):
            normalize_url("javascript:alert(1)")

    def test_representative_publisher_fixture(self):
        articles = parse_feed((FIXTURES / "publisher-feed.xml").read_bytes())
        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0]["url"], "https://example.com/news/discovery")
        self.assertEqual(articles[0]["summary"], "A fictional summary for a parser regression.")

    def test_invalid_configuration_stops_before_database_or_publisher_work(self):
        invalid_settings = ({"NEWS_RETENTION_DAYS": "0"}, {"NEWS_RETENTION_DAYS": "wrong"},
                            {"NEWS_MAX_EDITIONS": "-1"}, {"NEWS_MAX_EDITIONS": "1.5"},
                            {"NEWS_SOURCE_TIMEOUT_SECONDS": "nan"}, {"NEWS_SOURCE_TIMEOUT_SECONDS": "wrong"},
                            {"NEWS_MIN_EDITION_RATIO": "nan"}, {"NEWS_MIN_EDITION_RATIO": "2"})
        for values in invalid_settings:
            with self.subTest(values=values), patch.dict(os.environ, values), \
                    patch("news.collector.database.initialize") as initialize, patch("news.collector.download") as download:
                with self.assertRaises(ValueError):
                    collect_all(SOURCES[:1], self.path)
                initialize.assert_not_called()
                download.assert_not_called()

    def test_cli_reports_invalid_environment_without_starting_collection(self):
        from news.collect import main
        for values in ({"NEWS_RETENTION_DAYS": "0"}, {"NEWS_SOURCE_TIMEOUT_SECONDS": "wrong"}):
            errors = io.StringIO()
            with self.subTest(values=values), patch.dict(os.environ, values), \
                    patch("sys.argv", ["news.collect"]), patch("news.collect.collect_all") as collect, redirect_stderr(errors):
                with self.assertRaises(SystemExit) as stopped:
                    main()
                self.assertEqual(stopped.exception.code, 2)
                collect.assert_not_called()
                self.assertIn("error:", errors.getvalue())

    def test_benign_encoding_warning_keeps_usable_feed(self):
        content = '<rss version="2.0"><channel><title>Café</title><item><title>Café news</title><link>https://example.com/one</link></item></channel></rss>'.encode("latin-1")
        self.assertEqual(parse_feed(content)[0]["title"], "Café news")

    def test_truncated_feed_preserves_previous_edition_and_records_failure(self):
        database.save_collection("bbc", self.articles, self.path)
        with patch("news.collector.download", return_value=(FIXTURES / "truncated-feed.xml").read_bytes()):
            result = collect_source(SOURCES[0], self.path)
        self.assertIn("malformed or incomplete", result["error"])
        self.assertEqual(len(database.get_headlines("bbc", self.path)), 2)
        self.assertTrue(database.get_statuses(self.path)["bbc"]["error"])

    def test_suspicious_count_drop_is_rejected_but_can_be_explicitly_disabled(self):
        database.save_collection("bbc", parse_feed(feed("".join(item(i) for i in range(10)))), self.path)
        with patch("news.collector.download", return_value=feed(item(1) + item(2))):
            result = collect_source(SOURCES[0], self.path)
            self.assertIn("Suspicious edition shrink", result["error"])
            self.assertEqual(len(database.get_headlines("bbc", self.path)), 10)
            accepted = collect_source(SOURCES[0], self.path, minimum_edition_ratio=0)
        self.assertFalse(accepted["error"])
        self.assertEqual(len(database.get_headlines("bbc", self.path)), 2)

    def test_body_refreshes_on_changed_headline_or_age(self):
        article = {"title": "Current title", "summary": "Current summary"}
        metadata = {**article, "body": "Saved body", "body_fetched_at": datetime.now(timezone.utc).isoformat(),
                    "body_metadata_hash": body_metadata_hash(article)}
        self.assertFalse(body_needs_refresh(metadata, article))
        self.assertTrue(body_needs_refresh(metadata, {**article, "title": "Updated title"}))
        self.assertTrue(body_needs_refresh(metadata, {**article, "summary": "Updated summary"}))
        metadata["body_fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.assertTrue(body_needs_refresh(metadata, article))

    def test_failed_body_refresh_preserves_text_and_retries_next_collection(self):
        old = {**self.articles[0], "body": "Morning body",
               "body_fetched_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
               "body_metadata_hash": body_metadata_hash(self.articles[0])}
        database.save_collection("bbc", [old], self.path)
        changed_feed = feed(item(1).replace("Story 1", "Evening headline"))
        with patch("news.collector.download", return_value=changed_feed), \
                patch.object(ArticleExtractor, "extract", side_effect=[ValueError("Temporary failure"), "Evening body"]) as extract:
            self.assertFalse(collect_source(SOURCES[0], self.path, extract_articles=True)["error"])
            cached = database.get_body_metadata("bbc", old["url"], self.path)
            self.assertEqual(cached["body"], "Morning body")
            self.assertEqual(cached["body_metadata_hash"], old["body_metadata_hash"])
            self.assertFalse(collect_source(SOURCES[0], self.path, extract_articles=True)["error"])
        self.assertEqual(extract.call_count, 2)
        refreshed = database.get_body_metadata("bbc", old["url"], self.path)
        self.assertEqual(refreshed["body"], "Evening body")
        self.assertEqual(refreshed["body_metadata_hash"], body_metadata_hash({**self.articles[0], "title": "Evening headline"}))

    def test_timed_out_source_does_not_block_the_next_outlet(self):
        database.save_collection("bbc", self.articles, self.path)

        def response(url, max_bytes=None, redirect_guard=None):
            if url == SOURCES[0].feed_url:
                return network.run_with_deadline(time.sleep, (10,), SOURCE_DEADLINE.get())
            return feed(item(3))

        # Keep parser startup out of this short test budget; the real blocked
        # network worker must still be cancelled before the next outlet runs.
        with patch("news.collector.download", side_effect=response), \
                patch("news.collector.bounded_work", side_effect=lambda function, *args: function(*args)):
            results = collect_all(SOURCES[:2], self.path, timeout_seconds=0.5)
        self.assertIn("deadline", results[0]["error"])
        self.assertEqual(results[1]["count"], 1)
        self.assertEqual(len(database.get_headlines("bbc", self.path)), 2)

    def test_optional_extraction_deadline_still_saves_valid_headlines(self):
        old = {**self.articles[0], "body": "Previously saved body"}
        database.save_collection("bbc", [old], self.path)

        def blocked_extraction(url):
            return network.run_with_deadline(time.sleep, (10,), SOURCE_DEADLINE.get())

        with patch("news.collector.download", return_value=feed(item(1) + item(2))), \
                patch("news.collector.bounded_work", side_effect=lambda function, *args: function(*args)), \
                patch.object(ArticleExtractor, "extract", side_effect=blocked_extraction) as extract:
            result = collect_source(SOURCES[0], self.path, extract_articles=True, timeout_seconds=0.5)
        self.assertFalse(result["error"])
        self.assertEqual(result["count"], 2)
        self.assertTrue(any("deadline" in warning for warning in result["warnings"]))
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(database.get_body("bbc", old["url"], self.path), "Previously saved body")

    def test_url_keeps_meaningful_query(self):
        self.assertEqual(normalize_url("https://example.com/a?id=2&utm_medium=rss#part"), "https://example.com/a?id=2")

    def test_repeat_collection_preserves_text_and_changes_rank(self):
        self.articles[0]["body"] = "Previously extracted body"
        database.save_collection("bbc", self.articles, self.path)
        self.articles[0]["body"] = ""
        database.save_collection("bbc", list(reversed(self.articles)), self.path)
        rows = database.get_headlines("bbc", self.path)
        self.assertEqual(rows[0]["title"], "Story 2")
        self.assertEqual(rows[1]["body"], "Previously extracted body")
        with database.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 2)

    def test_failure_preserves_headlines_and_last_success(self):
        database.save_collection("bbc", self.articles, self.path)
        before = database.get_statuses(self.path)["bbc"]["succeeded_at"]
        with patch("news.collector.download", side_effect=TimeoutError("Timed out")):
            result = collect_source(SOURCES[0], self.path)
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(database.get_headlines("bbc", self.path)), 2)
        status = database.get_statuses(self.path)["bbc"]
        self.assertEqual(status["succeeded_at"], before)
        self.assertEqual(status["error"], "Timed out")

    def test_bad_write_rolls_back_whole_collection(self):
        database.save_collection("bbc", self.articles, self.path)
        with self.assertRaises(KeyError):
            database.save_collection("bbc", [self.articles[1], {"url": "https://example.com/bad"}], self.path)
        self.assertEqual(database.get_headlines("bbc", self.path)[0]["title"], "Story 1")

    def test_article_extraction_failure_does_not_lose_feed(self):
        with patch("news.collector.download", return_value=feed(item(1))), patch.object(ArticleExtractor, "extract", side_effect=ValueError("Blocked")):
            result = collect_source(SOURCES[0], self.path, extract_articles=True)
        self.assertEqual(result["count"], 1)
        self.assertEqual(database.get_headlines("bbc", self.path)[0]["body"], "")

    def test_robots_denial_prevents_article_download(self):
        with patch("news.collector.download", return_value=b"User-agent: *\nDisallow: /\n") as mocked:
            with self.assertRaises(ValueError):
                ArticleExtractor().extract("https://example.com/story")
        mocked.assert_called_once_with("https://example.com/robots.txt")

    def test_old_articles_stay_in_history_but_not_current_top_ten(self):
        database.save_collection("bbc", self.articles, self.path)
        database.save_collection("bbc", parse_feed(feed(item(3))), self.path)
        self.assertEqual(len(database.get_headlines("bbc", self.path)), 1)
        with database.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
