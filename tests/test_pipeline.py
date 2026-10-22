"""Offline tests: no external publisher needs to be online."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from news import database
from news.collector import collect_source, normalize_url, parse_feed, ArticleExtractor
from news.sources import SOURCES


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
