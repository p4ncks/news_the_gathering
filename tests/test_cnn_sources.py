"""CNN discovery stays on the publisher's pages and fails without replacing cached news."""
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from news import database
from news.collector import ArticleExtractor, collect_source, parse_html_headlines
from news.sources import SOURCES

os.environ.setdefault("DJANGO_DEBUG", "true")


def card(path, title, summary="", kind="article"):
    return (f'<a data-link-type="{kind}" href="{path}">'
            f'<span data-editable="headline">{title}</span>'
            f'<div data-editable="description">{summary}</div></a>')


def listing(cards):
    return f'<html><body><div role="main">{cards}</div></body></html>'.encode()


class CNNSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "news.db"
        database.initialize(self.path)
        self.cnn = next(source for source in SOURCES if source.id == "cnn")
        self.space = next(source for source in SOURCES if source.id == "cnn-space")

    def tearDown(self):
        self.directory.cleanup()

    def test_current_official_pages_are_enabled_and_separate(self):
        for source in (self.cnn, self.space):
            self.assertTrue(source.enabled)
            self.assertEqual(source.discovery, "html")
            self.assertEqual(source.feed_url, "")
        self.assertEqual(self.cnn.website, "https://www.cnn.com")
        self.assertEqual(self.space.website, "https://www.cnn.com/science/space")

    def test_representative_nested_listing_fixture(self):
        content = (Path(__file__).parent / "fixtures" / "publisher-listing.html").read_bytes()
        articles = parse_html_headlines(content, self.cnn.website)
        self.assertEqual([article["title"] for article in articles],
                         ["A fictional mission reaches its destination", "Fictional live coverage"])
        self.assertEqual(articles[0]["summary"], "Scientists discuss the next steps.")

    def test_page_order_ignores_navigation_images_videos_and_duplicates(self):
        story = "/2026/10/23/science/first-story"
        content = ('<nav>' + card(story, "Navigation teaser") + '</nav>'
                   '<div role="main"><a data-link-type="article" href="' + story + '">Photo credit</a>'
                   + card(story, "Mars’ two moons &amp; their <b>secrets</b>", "A <b>new</b> mission.")
                   + card("https://edition.cnn.com" + story + "?utm_source=share", "Duplicate")
                   + card("/2026/10/23/science/video/clip", "Video", kind="video")
                   + card("/2026/10/23/politics/live-news/latest", "Live coverage", kind="live-story")
                   + '</div><footer>' + card("/2026/10/23/science/footer", "Footer") + '</footer>')
        articles = parse_html_headlines(content.encode(), self.cnn.website)
        self.assertEqual([article["title"] for article in articles],
                         ["Mars’ two moons & their secrets", "Live coverage"])
        self.assertEqual(articles[0]["summary"], "A new mission.")
        self.assertEqual(articles[0]["url"], "https://www.cnn.com" + story)
        self.assertIsNone(articles[0]["published_at"])

    def test_only_real_cnn_story_links_count_toward_ten(self):
        bad_paths = ["/science/space", "https://cnn.com.evil.test/2026/10/23/science/fake",
                     "javascript:alert(1)", "/2026/02/30/science/impossible-day", "#section"]
        content = "".join(card(path, "Ignore this") for path in bad_paths)
        content += "".join(card(f"/2026/10/23/science/story-{n}", f"Story {n}") for n in range(15))
        articles = parse_html_headlines(listing(content), self.space.website)
        self.assertEqual(len(articles), 10)
        self.assertEqual(articles[0]["title"], "Story 0")
        self.assertEqual(articles[-1]["title"], "Story 9")

    def test_space_collection_fetches_space_page_and_saves_its_order(self):
        content = listing(card("/2026/10/19/science/mars-moons", "Mars’ two moons"))
        with patch.object(ArticleExtractor, "fetch", return_value=content) as fetch:
            result = collect_source(self.space, self.path)
        self.assertEqual(fetch.call_args.args[0], self.space.website)
        self.assertEqual(result["count"], 1)
        self.assertEqual(database.get_headlines("cnn-space", self.path)[0]["title"], "Mars’ two moons")
        self.assertEqual(database.get_headlines("cnn", self.path), [])

    def test_changed_or_blocked_listing_preserves_last_success(self):
        article = {"url": "https://www.cnn.com/2026/10/19/science/mars-moons",
                   "title": "Saved headline", "summary": "", "published_at": None, "body": ""}
        database.save_collection(self.space.id, [article], self.path)
        before = database.get_statuses(self.path)[self.space.id]["succeeded_at"]
        for result in (b"<html>Access denied</html>", b"<html>New layout</html>"):
            with patch.object(ArticleExtractor, "fetch", return_value=result):
                outcome = collect_source(self.space, self.path)
            self.assertEqual(outcome["count"], 0)
            self.assertEqual(database.get_headlines(self.space.id, self.path)[0]["title"], "Saved headline")
            self.assertEqual(database.get_statuses(self.path)[self.space.id]["succeeded_at"], before)

    def test_robots_denial_prevents_listing_request(self):
        with patch("news.collector.download", return_value=b"User-agent: *\nDisallow: /science/\n") as fetch:
            result = collect_source(self.space, self.path)
        self.assertEqual(result["count"], 0)
        self.assertIn("robots.txt", result["error"])
        fetch.assert_called_once_with("https://www.cnn.com/robots.txt")

    def test_missing_robots_does_not_download_listing(self):
        with patch("news.collector.download", side_effect=TimeoutError("Robots unavailable")) as fetch:
            result = collect_source(self.space, self.path)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["error"], "Robots unavailable")
        fetch.assert_called_once_with("https://www.cnn.com/robots.txt")

    def test_robots_rules_and_spacing_are_shared_between_listing_requests(self):
        extractor = ArticleExtractor()
        with patch("news.collector.download", side_effect=[b"User-agent: *\nAllow: /\n", b"Homepage", b"Space"] ) as fetch, \
                patch("news.collector.time.sleep") as sleep:
            self.assertEqual(extractor.fetch(self.cnn.website), b"Homepage")
            self.assertEqual(extractor.fetch(self.space.website), b"Space")
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_redirected_html_checks_destination_robots_before_page_request(self):
        requests = []

        def download(url, max_bytes=None, redirect_guard=None):
            requests.append(url)
            if url == "https://publisher.example/robots.txt":
                return b"User-agent: *\nAllow: /\n"
            if url == "https://other.example/robots.txt":
                return b"User-agent: *\nDisallow: /blocked\n"
            if url == "https://publisher.example/story":
                redirect_guard("https://other.example/blocked")
            self.fail("A blocked redirect destination was requested")

        with patch("news.collector.download", side_effect=download), patch("news.collector.time.sleep"):
            with self.assertRaisesRegex(ValueError, "disallowed by robots"):
                ArticleExtractor().fetch("https://publisher.example/story")
        self.assertEqual(requests, ["https://publisher.example/robots.txt", "https://publisher.example/story",
                                    "https://other.example/robots.txt"])


if __name__ == "__main__":
    unittest.main()
