"""Suggestion review uses fixtures, the collector's safety policy, and no writes."""
import ast
import io
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("DJANGO_DEBUG", "true")

from news import collector, database, maintenance, network, suggestion_review as review
from news.sources import Source
from support import initialize_default_database

FIXTURES = Path(__file__).parent / "fixtures"
WEBSITE = "https://reader.example/news/"


def suggestion(**changes):
    return {"id": 7, "name": "Reader News", "website": WEBSITE, "country": "brazil",
            "reason": "Local reporting", "submitted_at": "2026-11-05T12:00:00+00:00", **changes}


class SuggestionReviewTests(unittest.TestCase):
    def setUp(self):
        self.responses = {WEBSITE: (FIXTURES / "suggestion-page.html").read_bytes(),
                          "https://reader.example/feed": (FIXTURES / "suggestion-feed.xml").read_bytes(),
                          "https://feeds.reader.example/atom.xml": (FIXTURES / "suggestion-atom.xml").read_bytes()}
        self.robots = {}
        self.requests = []
        patches = [
            patch("news.network.run_with_deadline", side_effect=lambda function, args, deadline: function(*args)),
            patch("news.collector.time.sleep"),
            patch("news.network.socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]),
            patch("news.network.urllib3.HTTPSConnectionPool", side_effect=self.make_pool),
            patch("news.network.urllib3.HTTPConnectionPool", side_effect=self.make_pool),
        ]
        for mock_patch in patches:
            mock_patch.start()
            self.addCleanup(mock_patch.stop)

    def make_pool(self, *args, **kwargs):
        def request(method, target, headers, **options):
            url = "https://" + headers["Host"] + target
            self.requests.append(url)
            if target == "/robots.txt":
                content = self.robots.get(headers["Host"], b"User-agent: *\nAllow: /\n")
            else:
                content = self.responses.get(url, (404, "", b""))
            status, location, body = (200, "", content) if isinstance(content, bytes) else content
            response = Mock(status=status, headers={"Location": location})
            response.read.side_effect = [body, b""]
            return response
        pool = Mock()
        pool.urlopen.side_effect = request
        return pool

    def test_discovers_relative_rss_and_atom_and_filters_other_links(self):
        self.assertEqual(review.advertised_feeds(self.responses[WEBSITE], WEBSITE),
                         ["https://reader.example/feed", "https://feeds.reader.example/atom.xml"])
        self.assertEqual(review.common_feeds(WEBSITE + "?query=ignored"),
                         ["https://reader.example" + path for path in review.COMMON_FEED_PATHS])

    def test_reports_all_usable_items_and_latest_date_beyond_the_collectors_tenth_item(self):
        content = self.responses["https://reader.example/feed"]
        self.assertEqual(len(collector.parse_feed(content)), 10)
        report = review.inspect_feed(content, "https://reader.example/feed")
        self.assertEqual(report.item_count, 12)
        self.assertEqual(report.newest_item_date, "2026-11-05T14:00:00+00:00")
        self.assertEqual(report.sample_titles, ["Primeira notícia", "Segunda notícia", "Terceira notícia"])
        self.assertEqual(report.language_code, "pt-br")

    def test_atom_uses_updated_dates_and_feed_language(self):
        report = review.inspect_feed(self.responses["https://feeds.reader.example/atom.xml"], "https://feeds.reader.example/atom.xml")
        self.assertEqual(report.item_count, 3)
        self.assertEqual(report.newest_item_date, "2026-11-05T14:00:00+00:00")
        self.assertEqual(report.language_code, "fr")

    def test_report_selects_more_items_when_dates_tie_and_prints_a_valid_draft(self):
        original_open = open

        def read_only_open(path, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("Review must not write project files")
            return original_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=read_only_open):
            output = review.review_suggestion(suggestion())
        self.assertIn("Items: 12", output)
        self.assertIn("Items: 3", output)
        self.assertIn("Sample: 'Terceira notícia'", output)
        self.assertIn("Unavailable: Publisher returned HTTP 404", output)
        draft = output[output.index("Source(\n"):]
        fields = {keyword.arg: ast.literal_eval(keyword.value)
                  for keyword in ast.parse(draft).body[0].value.elts[0].keywords}
        self.assertEqual(fields["feed_url"], "https://reader.example/feed")
        self.assertEqual((fields["continent"], fields["country"]), ("South America", "Brazil"))
        self.assertEqual((fields["language"], fields["language_code"]), ("Português", "pt-BR"))
        self.assertIn("TODO", fields["edition_scope"])
        self.assertEqual(self.requests.count("https://reader.example/feed"), 1)
        self.assertEqual(self.requests.count("https://reader.example/robots.txt"), 1)
        self.assertIn("https://reader.example/arc/outboundfeeds/rss/", self.requests)

    def test_newer_feed_wins_even_with_fewer_items(self):
        self.responses["https://feeds.reader.example/atom.xml"] = self.responses["https://feeds.reader.example/atom.xml"].replace(
            b"2026-11-05T14:00:00Z", b"2026-11-06T14:00:00Z")
        output = review.review_suggestion(suggestion())
        draft = output[output.index("Source(\n"):]
        self.assertIn("feed_url='https://feeds.reader.example/atom.xml'", draft)
        self.assertIn("language='Français'", draft)

    def test_common_paths_work_when_homepage_fails_and_bad_feeds_do_not_stop_review(self):
        self.responses[WEBSITE] = (503, "", b"")
        self.responses["https://reader.example/feed"] = (FIXTURES / "truncated-feed.xml").read_bytes()
        self.responses["https://reader.example/rss.xml"] = (FIXTURES / "publisher-feed.xml").read_bytes()
        output = review.review_suggestion(suggestion())
        self.assertIn("Page discovery failed", output)
        self.assertIn("Feed XML is malformed or incomplete", output)
        self.assertIn("feed_url='https://reader.example/rss.xml'", output)
        self.assertIn("language='TODO: language'", output)
        self.assertIn("language_code='TODO: language_code'", output)

    def test_no_working_feeds_suggests_html_discovery(self):
        self.responses.clear()
        output = review.review_suggestion(suggestion())
        self.assertIn('No usable feed found. Consider discovery="html"', output)
        self.assertNotIn("Source(\n", output)

    def test_robots_disallowed_page_and_feeds_are_never_requested(self):
        self.robots["reader.example"] = b"User-agent: *\nDisallow: /\n"
        output = review.review_suggestion(suggestion())
        self.assertIn("disallowed by robots.txt", output)
        self.assertEqual(self.requests, ["https://reader.example/robots.txt"])

    def test_redirect_checks_destination_robots_before_fetching_it(self):
        self.responses[WEBSITE] = (302, "https://blocked.example/page", b"")
        self.robots["blocked.example"] = b"User-agent: *\nDisallow: /\n"
        output = review.review_suggestion(suggestion())
        self.assertIn("Page discovery failed: Page collection disallowed by robots.txt", output)
        self.assertIn("https://blocked.example/robots.txt", self.requests)
        self.assertNotIn("https://blocked.example/page", self.requests)
        self.assertIn("Items: 12", output)

    def test_public_page_cannot_redirect_or_advertise_a_private_feed(self):
        original = network.socket.getaddrinfo

        def resolve(host, port, **kwargs):
            if host == "127.0.0.1":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
            return original(host, port, **kwargs)

        self.responses[WEBSITE] = b'<link rel="alternate" type="application/rss+xml" href="http://127.0.0.1/internal">'
        self.responses["https://reader.example/feed"] = (302, "http://127.0.0.1/internal", b"")
        with patch("news.network.socket.getaddrinfo", side_effect=resolve):
            output = review.review_suggestion(suggestion())
        self.assertIn("private or reserved", output)
        self.assertFalse(any("127.0.0.1" in url for url in self.requests))

    def test_size_limits_and_deadlines_are_kept_for_review_requests(self):
        self.responses[WEBSITE] = b"x" * (collector.MAX_BYTES + 1)
        output = review.review_suggestion(suggestion())
        self.assertIn("Response exceeds 5 MB limit", output)
        original = collector.SOURCE_DEADLINE.get()
        with patch.object(collector.ArticleExtractor, "fetch", side_effect=network.DeadlineExceeded("expired")):
            output = review.review_suggestion(suggestion())
        self.assertIn("expired", output)
        self.assertIn('discovery="html"', output)
        self.assertEqual(collector.SOURCE_DEADLINE.get(), original)

    def test_unknown_geography_and_language_are_explicit_and_names_cannot_inject_code(self):
        report = review.FeedReport("https://reader.example/rss", 1, None, ["Undated"], "zz")
        draft = review.source_draft(suggestion(name="Name\n'); print('bad') #", country="Atlantis"), report)
        self.assertEqual(len(ast.parse(draft).body), 1)
        self.assertIn("TODO: continent", draft)
        self.assertIn("TODO: country (suggested: Atlantis)", draft)
        self.assertIn("language='TODO: language'", draft)
        self.assertIn("language_code='zz'", draft)
        self.assertIsNone(review.inspect_feed(b'<rss><channel><item><title>Undated</title><link>https://reader.example/a</link></item></channel></rss>', "https://reader.example/rss").newest_item_date)

    def test_terminal_control_characters_from_the_public_and_remote_pages_are_escaped(self):
        self.responses[WEBSITE] = b'<link rel="alternate" type="application/rss+xml" href="/feed\x1b]0;title\x07">'
        output = review.review_suggestion(suggestion(country="Bra\x1b[2Jzil\nDraft for review: fake"))
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\x07", output)
        self.assertIn("Country: Bra\\x1b[2Jzil\\nDraft for review: fake", output)
        self.assertIn("Feed: https://reader.example/feed\\x1b]0;title\\x07", output)
        self.assertIn("Source(\n    id='reader-news',", output)

    def test_catalog_matches_both_host_fields_ignores_www_and_all_includes_matches(self):
        sources = [Source("existing", "Existing", "Europe", "France", "Français",
                          "https://feeds.existing.example/rss", "https://www.existing.example")]
        rows = [suggestion(website="http://EXISTING.example./path"),
                suggestion(id=8, website="https://www.feeds.existing.example/anything")]
        with patch.object(review, "SOURCES", sources), patch.object(database, "get_outlet_suggestions", return_value=rows):
            with patch.object(review, "review_suggestion", return_value="Reviewed") as inspect:
                output = review.review_suggestions()
                inspect.assert_not_called()
                self.assertIn("Skipped 2", output)
                self.assertIn("--all", output)
            with patch.object(review, "review_suggestion", return_value="Reviewed") as inspect:
                review.review_suggestions(include_catalogued=True)
                self.assertEqual(inspect.call_count, 2)
                self.assertTrue(all(call.args[1] for call in inspect.call_args_list))
        self.assertIn("[already in the catalog]", review.review_suggestion(suggestion(), already_catalogued=True))

    def test_command_wires_all_and_prints_plain_review_text(self):
        with patch.object(database, "get_outlet_suggestions", return_value=[suggestion()]), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(maintenance.main(["suggestions", "--all"]), 0)
        self.assertIn("Suggestion #7: 'Reader News'", output.getvalue())
        self.assertIn("Source(\n", output.getvalue())

    def test_root_and_catalog_subdomains_match_without_matching_unrelated_domains(self):
        source = Source("r7", "R7", "South America", "Brazil", "Português",
                        "https://noticias.r7.com/arc/outboundfeeds/rss/", "https://noticias.r7.com")
        with patch.object(review, "SOURCES", [source]):
            for website in ("https://www.r7.com/", "https://noticias.r7.com/", "https://www.noticias.r7.com/news"):
                with self.subTest(website=website):
                    self.assertEqual(review.matching_sources(website), [source])
            for website in ("https://notr7.com/", "https://r7.com.evil.example/", "https://example.com/"):
                with self.subTest(website=website):
                    self.assertEqual(review.matching_sources(website), [])
            with patch.object(database, "get_outlet_suggestions", return_value=[suggestion(website="https://www.r7.com/")]), \
                    patch.object(review, "review_suggestion", return_value="Reviewed") as inspect:
                self.assertIn("Skipped 1", review.review_suggestions())
                inspect.assert_not_called()
                review.review_suggestions(include_catalogued=True)
                self.assertTrue(inspect.call_args.args[1])
        self.assertTrue(review.same_site("noticias.r7.com", "r7.com"))
        self.assertFalse(review.same_site("bbc.co.uk", "unrelated.co.uk"))

    def test_matching_catalog_feed_is_also_reviewed_and_portuguese_country_is_recognized(self):
        source = Source("reader", "Reader", "South America", "Brazil", "Português",
                        "https://news.reader.example/catalog/rss.xml", "https://news.reader.example")
        self.responses.clear()
        self.responses[source.feed_url] = (FIXTURES / "suggestion-feed.xml").read_bytes()
        with patch.object(review, "SOURCES", [source]):
            output = review.review_suggestion(suggestion(country="Brasil"), already_catalogued=True)
        self.assertIn("[already in the catalog]", output)
        self.assertIn(f"feed_url={source.feed_url!r}", output)
        self.assertIn("country='Brazil'", output)
        self.assertIn("continent='South America'", output)


class SuggestionDatabaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        environment = patch.dict(os.environ, {"NEWS_DB_PATH": str(self.directory / "news.db")})
        environment.start()
        self.addCleanup(environment.stop)
        initialize_default_database()

    def test_configured_database_reads_suggestions_in_review_order_without_writes(self):
        from dashboard.models import OutletSuggestion
        from django.db import connections
        from django.test.utils import CaptureQueriesContext

        with database._using() as alias:
            OutletSuggestion.objects.using(alias).create(name="Older", website=WEBSITE, country="Brazil")
            OutletSuggestion.objects.using(alias).create(name="Newer", website=WEBSITE, country="France", reason="Review me")
        with database._using(read_only=True) as alias:
            with CaptureQueriesContext(connections[alias]) as queries, \
                    patch.object(database, "initialize", side_effect=AssertionError("Must not initialize")):
                rows = database.get_outlet_suggestions()
        self.assertEqual([row["name"] for row in rows], ["Newer", "Older"])
        self.assertEqual(rows[0]["reason"], "Review me")
        self.assertTrue(all(query["sql"].lstrip().upper().startswith(("SELECT", "PRAGMA")) for query in queries))
        self.assertEqual(database.get_outlet_suggestions(), rows)

    def test_missing_sqlite_database_is_not_created(self):
        missing = self.directory / "missing" / "news.db"
        self.assertEqual(database.get_outlet_suggestions(missing), [])
        self.assertFalse(missing.parent.exists())


if __name__ == "__main__":
    unittest.main()
