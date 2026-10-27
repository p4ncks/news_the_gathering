"""Exercise the Django dashboard with an isolated database and no network."""
import hashlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_DEBUG", "true")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gathering.settings")

import django

django.setup()

from django.test import Client, override_settings
from django.test.utils import setup_test_environment, teardown_test_environment
from django.urls import reverse
from lxml import html

from news import database
from news.sources import SOURCES


def setUpModule():
    setup_test_environment()


def tearDownModule():
    teardown_test_environment()


class AppTests(unittest.TestCase):
    def setUp(self):
        settings = override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
        settings.enable()
        self.addCleanup(settings.disable)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "news.db"
        environment = patch.dict(os.environ, {"NEWS_DB_PATH": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)
        database.initialize()
        self.now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        database.save_collection("bbc", [
            self.article("climate", "Climate summit opens", "Global climate priorities"),
            self.article("markets", "Markets rebound", "Brazil growth expectations"),
        ])
        database.save_collection("g1", [
            self.article("weather", "São Paulo tem chuva", "Tempestade chega à cidade"),
        ])
        self.client = Client()
        self.index = reverse("dashboard:index")

    def article(self, slug, title, summary, **extra):
        return {"url": "https://example.com/" + slug, "title": title,
                "summary": summary, "published_at": self.now, **extra}

    def get(self, **parameters):
        response = self.client.get(self.index, parameters)
        self.assertEqual(response.status_code, 200)
        return response

    def test_default_view_lists_enabled_outlets_and_saved_headline_metrics(self):
        response = self.get()
        enabled = [source for source in SOURCES if source.enabled]
        self.assertEqual(response.context["outlet_count"], len(enabled))
        self.assertEqual(response.context["continent_count"],
                         len({source.continent for source in enabled}))
        self.assertEqual(response.context["headline_count"], 3)
        self.assertEqual([card["source"].id for card in response.context["cards"]],
                         [source.id for source in enabled])
        self.assertIn("Climate summit opens", response.content.decode())
        self.assertFalse(response.context["no_sources"])
        self.assertFalse(response.context["no_headlines"])

    def test_continent_and_outlet_filters_only_include_enabled_matching_outlets(self):
        response = self.get(filtered="1", continent=["Europe"],
                            outlet=["bbc", "g1", "cnn", "unknown"])
        self.assertEqual(response.context["outlet_count"], 1)
        self.assertEqual(response.context["continent_count"], 1)
        self.assertEqual(response.context["headline_count"], 2)
        self.assertEqual([card["source"].id for card in response.context["cards"]], ["bbc"])
        options = response.context["outlet_options"]
        self.assertEqual({option["id"] for option in options},
                         {source.id for source in SOURCES if source.enabled})
        self.assertEqual({option["id"] for option in options if option["selected"]}, {"bbc"})
        self.assertEqual({option["name"] for option in response.context["continent_options"]
                          if option["selected"]}, {"Europe"})
        europe = next(option for option in response.context["continent_options"] if option["name"] == "Europe")
        self.assertTrue(europe["partial"])

    def test_search_matches_summary_case_insensitively_and_keeps_feed_rank(self):
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"], q="BRAZIL")
        card = response.context["cards"][0]
        self.assertEqual(response.context["headline_count"], 2)
        self.assertEqual([article["title"] for article in card["articles"]], ["Markets rebound"])
        self.assertEqual(card["articles"][0]["rank"], 2)
        self.assertNotIn("Climate summit opens", response.content.decode())
        response = self.get(q="TEMPESTADE")
        matches = [article["title"] for card in response.context["cards"]
                   for article in card["articles"]]
        self.assertEqual(matches, ["São Paulo tem chuva"])

    def test_search_without_matches_keeps_outlets_and_saved_headline_count(self):
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"], q="no-such-topic")
        self.assertEqual(response.context["outlet_count"], 1)
        self.assertEqual(response.context["headline_count"], 2)
        self.assertEqual(response.context["cards"], [])
        self.assertTrue(response.context["no_matches"])
        self.assertIn("No headlines match", response.content.decode())
        self.assertIn("0 matches", response.content.decode())
        self.assertIn("0 outlets", response.content.decode())
        shown = self.get(filtered="1", continent=["Europe"], outlet=["bbc"], q="no-such-topic", show_empty="1")
        self.assertEqual(len(shown.context["cards"]), 1)
        self.assertTrue(shown.context["cards"][0]["has_headlines"])

    def test_explicit_empty_or_unknown_filters_never_restore_default_selection(self):
        for parameters in ({"filtered": "1"},
                           {"filtered": "1", "continent": ["Europe"]},
                           {"filtered": "1", "outlet": ["bbc"]},
                           {"filtered": "1", "continent": ["Atlantis"], "outlet": ["unknown"]}):
            with self.subTest(parameters=parameters):
                response = self.get(**parameters)
                self.assertEqual(response.context["outlet_count"], 0)
                self.assertEqual(response.context["headline_count"], 0)
                self.assertEqual(response.context["cards"], [])
                self.assertTrue(response.context["no_sources"])
                self.assertIn("Choose at least one", response.content.decode())

    def test_uncollected_selection_shows_friendly_empty_state(self):
        response = self.get(filtered="1", continent=["Europe"], outlet=["guardian"])
        self.assertTrue(response.context["no_headlines"])
        self.assertEqual(response.context["headline_count"], 0)
        self.assertIn("No headlines collected yet", response.content.decode())
        self.assertIn("Waiting for first update", response.content.decode())

    def test_collection_failure_stale_feed_and_article_details_are_visible(self):
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        database.save_collection("bbc", [self.article(
            "old", "Older saved story", "A saved description", published_at=old,
            body="Previously extracted article text.")])
        with database.connect() as db:
            db.execute("UPDATE source_status SET succeeded_at=? WHERE source_id=?", (old, "bbc"))
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        self.assertTrue(response.context["cards"][0]["collection_stale"])
        self.assertTrue(response.context["cards"][0]["feed_stale"])
        database.record_failure("bbc", "Publisher timed out")
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        html = response.content.decode()
        self.assertIn("Saved edition · update delayed", html)
        self.assertIn("Latest update failed", html)
        self.assertNotIn("Publisher timed out", html)
        self.assertIn("Older saved story", html)
        self.assertNotIn("Previously extracted article text.", html)
        self.assertIn("Read saved article text", html)
        self.assertIn("Published:", html)
        self.assertNotEqual(response.context["cards"][0]["articles"][0]["published_label"],
                            "Not available")

    def test_source_directory_and_selection_include_both_cnn_sections(self):
        response = self.get()
        self.assertEqual(response.context["sources"], sorted(SOURCES, key=lambda source: source.name.casefold()))
        self.assertIn("CNN", response.content.decode())
        self.assertIn("CNN Space", response.content.decode())
        options = {option["id"] for option in response.context["outlet_options"]}
        self.assertTrue({"cnn", "cnn-space"}.issubset(options))

    def test_dashboard_uses_the_configured_freshness_window(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        with database.connect() as db:
            db.execute("UPDATE source_status SET succeeded_at=? WHERE source_id=?", (old, "bbc"))
        with override_settings(NEWS_FRESHNESS_HOURS=24):
            response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        self.assertFalse(response.context["cards"][0]["collection_stale"])
        self.assertEqual(response.context["freshness_hours"], 24)

    def test_source_directory_rows_are_alphabetical_and_omit_internal_notes(self):
        response = self.get()
        document = html.fromstring(response.content)
        directory = document.get_element_by_id("source-directory")
        names = [" ".join(node.itertext()).strip() for node in directory.xpath('.//tbody/tr/th/a')]
        self.assertEqual(names, sorted(names, key=str.casefold))
        text = directory.text_content()
        self.assertNotIn("This is a starter selection", text)
        self.assertNotIn("Collectors run separately", text)
        self.assertNotIn("news/sources.py", text)

    def test_deselecting_last_outlet_clears_its_continent(self):
        response = self.get(filtered="1", continent=["Europe", "South America"], outlet=["g1"], enhanced="1")
        self.assertEqual([card["source"].id for card in response.context["cards"]], ["g1"])
        active = {option["name"] for option in response.context["continent_options"] if option["selected"]}
        self.assertEqual(active, {"South America"})

    def test_all_member_outlets_select_a_full_continent(self):
        europe_ids = [source.id for source in SOURCES if source.enabled and source.continent == "Europe"]
        response = self.get(filtered="1", continent=["Europe"], outlet=europe_ids)
        europe = next(option for option in response.context["continent_options"] if option["name"] == "Europe")
        self.assertTrue(europe["selected"])
        self.assertFalse(europe["partial"])

    def test_disabled_outlet_cannot_be_selected_by_editing_the_url(self):
        sources = [replace(source, enabled=False) if source.id == "cnn" else source for source in SOURCES]
        with patch("dashboard.views.SOURCES", sources):
            response = self.get(filtered="1", continent=["North America"], outlet=["cnn"])
        self.assertEqual(response.context["outlet_count"], 0)
        self.assertNotIn("cnn", {option["id"] for option in response.context["outlet_options"]})

    def test_outlet_controls_hide_groups_outside_selected_continents(self):
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        options = response.context["outlet_options"]
        visible_ids = {option["id"] for option in options if option["visible"]}
        expected_ids = {source.id for source in SOURCES if source.enabled and source.continent == "Europe"}
        self.assertEqual(visible_ids, expected_ids)
        document = html.fromstring(response.content)
        for label in document.xpath('//label[contains(@class, "outlet-option")]'):
            source_id = label.xpath('.//input/@value')[0]
            self.assertEqual("hidden" not in label.attrib, source_id in expected_ids)
        self.assertFalse(response.context["no_visible_outlets"])

    def test_bulk_controls_reflect_all_on_and_all_off_after_navigation(self):
        for parameters, active in (({}, "on"), ({"filtered": "1"}, "off")):
            with self.subTest(active=active):
                response = self.get(**parameters)
                document = html.fromstring(response.content)
                buttons = document.xpath('//button[@data-select-all]')
                self.assertEqual(len(buttons), 2)
                for button in buttons:
                    self.assertEqual(button.get("aria-pressed"),
                                     "true" if button.get("data-select-all") == active else "false")
                if active == "off":
                    self.assertTrue(response.context["no_visible_outlets"])
                    labels = document.xpath('//label[contains(@class, "outlet-option")]')
                    self.assertTrue(all("hidden" in label.attrib for label in labels))
                    hint = document.xpath('//*[@data-empty-outlets]')[0]
                    self.assertNotIn("hidden", hint.attrib)

    def test_compact_edition_keeps_all_ten_headlines_and_extracted_text(self):
        database.save_collection("bbc", [self.article(
            str(rank), f"Saved story number {rank}", "A saved summary",
            body="Last story article text" if rank == 10 else "",
        ) for rank in range(1, 11)])
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        markup = response.content.decode()
        self.assertEqual(response.context["headline_count"], 10)
        self.assertIn("Saved story number 10", markup)
        self.assertNotIn("Last story article text", markup)
        document = html.fromstring(response.content)
        saved_link = document.xpath('//a[contains(text(), "Read saved article text")]/@href')[0]
        reader = self.client.get(saved_link)
        self.assertEqual(reader.status_code, 200)
        self.assertIn("Last story article text", reader.content.decode())

    def test_publisher_text_is_escaped_and_unsafe_article_links_are_not_rendered(self):
        database.save_collection("bbc", [
            self.article("safe", '<script>alert("title")</script>',
                         "<img src=x onerror=alert(1)>", body="<script>body()</script>"),
            self.article("unsafe", "Unsafe link", "Untrusted publisher URL",
                         url="javascript:alert(1)"),
        ])
        html = self.get(filtered="1", continent=["Europe"], outlet=["bbc"]).content.decode()
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn('<script>alert("title")</script>', html)
        self.assertNotIn("<script>body()</script>", html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn('href="https://example.com/safe"', html)

    def test_requests_read_saved_data_without_collecting_or_writing(self):
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        with patch("news.database.initialize", side_effect=AssertionError("Unexpected write")), \
                patch("news.collector.download", side_effect=AssertionError("Unexpected network")):
            self.get()
            health = self.client.get(reverse("dashboard:health"))
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(self.client.post(self.index).status_code, 405)
        self.assertEqual(self.client.post(reverse("dashboard:health")).status_code, 405)

    def test_missing_database_is_an_empty_view_and_is_not_created(self):
        missing = Path(self.directory.name) / "not-created" / "news.db"
        with patch.dict(os.environ, {"NEWS_DB_PATH": str(missing)}):
            response = self.get()
            self.assertEqual(response.context["headline_count"], 0)
            self.assertTrue(response.context["no_headlines"])
            health = self.client.get(reverse("dashboard:health"))
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertFalse(missing.parent.exists())

    def test_unreadable_database_shows_friendly_empty_state(self):
        invalid = Path(self.directory.name) / "invalid.db"
        invalid.write_text("Not a SQLite database", encoding="utf-8")
        with patch.dict(os.environ, {"NEWS_DB_PATH": str(invalid)}):
            with self.assertLogs("dashboard.views", level="ERROR"):
                response = self.client.get(self.index)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.context["database_unavailable"])
        self.assertEqual(response.context["headline_count"], 0)
        self.assertEqual(invalid.read_text(encoding="utf-8"), "Not a SQLite database")

    def test_no_javascript_can_recover_from_an_empty_selection(self):
        response = self.get(filtered="1", continent=["Europe"])
        self.assertEqual(response.context["outlet_count"], 0)
        self.assertFalse(response.context["no_visible_outlets"])
        document = html.fromstring(response.content)
        visible = document.xpath('//label[contains(@class,"outlet-option")][not(@hidden)]//input/@value')
        self.assertIn("bbc", visible)
        self.assertNotIn("g1", visible)
        europe = document.xpath('//input[@name="continent"][@value="Europe"]')[0]
        self.assertIn("checked", europe.attrib)
        recovered = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        self.assertEqual(recovered.context["outlet_count"], 1)
        self.assertIn("Climate summit opens", recovered.content.decode())
        self.assertTrue(all("hidden" in node.attrib for node in document.xpath('//*[@data-enhanced-only]')))

    def test_search_hides_empty_outlets_and_preserves_options_in_links(self):
        response = self.get(q="climate", view="compact")
        self.assertEqual(response.context["match_count"], 1)
        self.assertEqual(response.context["matching_outlet_count"], 1)
        self.assertEqual([card["source"].id for card in response.context["cards"]], ["bbc"])
        self.assertIn("q=climate", response.context["empty_toggle_url"])
        self.assertIn("view=compact", response.context["empty_toggle_url"])
        self.assertNotIn("q=", response.context["clear_search_url"])

    def test_headline_languages_and_edition_scope_are_explicit(self):
        response = self.get()
        document = html.fromstring(response.content)
        bbc = document.get_element_by_id("edition-bbc")
        g1 = document.get_element_by_id("edition-g1")
        self.assertEqual(bbc.xpath('.//h4/@lang'), ["en", "en"])
        self.assertEqual(g1.xpath('.//h4/@lang'), ["pt-BR"])
        self.assertEqual(g1.xpath('.//p[contains(@class,"headline-summary")]/@lang'), ["pt-BR"])
        self.assertIn("Edition: General news", bbc.text_content())
        self.assertIn("Edition: Latest news", g1.text_content())
        for source in SOURCES:
            self.assertTrue(source.language_code)
            self.assertTrue(source.edition_scope)

    def test_dashboard_loads_one_snapshot_and_does_not_embed_full_text(self):
        body = "Large saved article text. " * 3000
        for source in SOURCES:
            if source.enabled:
                database.save_collection(source.id, [self.article(
                    f"large-{rank}", f"Headline {rank}", "Summary", body=body,
                ) for rank in range(10)])
        with patch("news.database.get_dashboard", wraps=database.get_dashboard) as snapshot, \
                patch("news.database.get_headlines", side_effect=AssertionError("Per-source read")), \
                patch("news.database.get_article", side_effect=AssertionError("Unexpected body read")):
            response = self.get()
        self.assertEqual(snapshot.call_count, 1)
        self.assertNotIn(body, response.content.decode())
        self.assertLess(len(response.content), 600_000)
        self.assertTrue(all("body" not in article for card in response.context["cards"] for article in card["articles"]))

    def test_saved_article_is_lazy_scoped_and_escaped(self):
        database.save_collection("bbc", [self.article(
            "saved", "Saved title", "Summary", body='<script>alert("body")</script>\nSaved paragraph',
        )])
        saved = database.get_headlines("bbc")[0]
        url = reverse("dashboard:article", args=["bbc", saved["id"]])
        reader = self.client.get(url)
        self.assertEqual(reader.status_code, 200)
        self.assertIn("Saved paragraph", reader.content.decode())
        self.assertIn("&lt;script&gt;", reader.content.decode())
        self.assertNotIn('<script>alert("body")</script>', reader.content.decode())
        self.assertEqual(self.client.get(reverse("dashboard:article", args=["g1", saved["id"]])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:article", args=["unknown", saved["id"]])).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_saved_text_age_and_version_are_distinct_from_collection_time(self):
        title, summary = "Saved title", "Saved description"
        fetched = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        fingerprint = hashlib.sha256((title + "\0" + summary).encode("utf-8")).hexdigest()
        database.save_collection("bbc", [self.article(
            "versioned", title, summary, body="Saved body", body_fetched_at=fetched,
            body_metadata_hash=fingerprint,
        )])
        saved = database.get_headlines("bbc")[0]
        url = reverse("dashboard:article", args=["bbc", saved["id"]])
        original = self.client.get(url)
        self.assertIn("Text saved:", original.content.decode())
        self.assertNotEqual(original.context["body_saved_label"], "Not available")
        self.assertFalse(original.context["body_metadata_changed"])
        database.save_collection("bbc", [self.article("versioned", "Updated title", summary)])
        changed = self.client.get(url)
        self.assertEqual(changed.context["body_saved_label"], original.context["body_saved_label"])
        self.assertTrue(changed.context["body_metadata_changed"])
        self.assertIn("may predate the current headline", changed.content.decode())

    def test_legacy_saved_text_does_not_claim_a_verified_extraction_time(self):
        database.save_collection("bbc", [self.article("legacy", "Legacy title", "Description", body="Legacy text")])
        saved = database.get_headlines("bbc")[0]
        reader = self.client.get(reverse("dashboard:article", args=["bbc", saved["id"]]))
        self.assertTrue(reader.context["body_version_unknown"])
        self.assertFalse(reader.context["body_metadata_changed"])
        self.assertEqual(reader.context["body_saved_label"], "Not available")
        self.assertIn("have not been verified", reader.content.decode())

    def test_compact_mode_shows_all_ranks_without_embedding_summaries(self):
        database.save_collection("bbc", [self.article(str(rank), f"Title {rank}", "Description") for rank in range(10)])
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"], view="compact")
        document = html.fromstring(response.content)
        card = document.get_element_by_id("edition-bbc")
        self.assertEqual(len(card.xpath('.//li[contains(@class,"headline-compact")]')), 10)
        self.assertFalse(card.xpath('.//details[contains(@class,"more-headlines")]'))
        self.assertEqual(len(card.xpath('.//details[@class="story-details"]')), 10)
        self.assertTrue(response.context["compact"])

    def test_only_the_persistent_apply_action_is_shown(self):
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        document = html.fromstring(response.content)
        form = document.xpath('//form[contains(@class,"filter-form")]')[0]
        self.assertEqual(len(form.xpath('.//button[@type="submit"]')), 1)
        self.assertTrue(form.xpath('.//div[contains(@class,"filter-top-action")]/button'))
        self.assertFalse(form.xpath('.//*[contains(text(),"Reload this view")]'))
        self.assertNotIn("Search titles and summaries in their original languages.", form.text_content())
        self.assertIn("Discover what is the world talking about", form.text_content())
        self.assertFalse(document.xpath('//*[contains(@class,"masthead-caption")]'))

    def test_latest_update_metric_and_discreet_status_stay_in_the_header(self):
        database.record_failure("bbc", "Private failure details")
        response = self.get(filtered="1", continent=["Europe"], outlet=["bbc"])
        document = html.fromstring(response.content)
        card = document.get_element_by_id("edition-bbc")
        self.assertIn("Latest update", document.xpath('//section[@class="metrics"]')[0].text_content())
        self.assertEqual(response.context["latest_collection_label"], response.context["cards"][0]["collected_label"])
        self.assertTrue(card.xpath('.//header//*[contains(@class,"collection-meta")]'))
        self.assertTrue(card.xpath('.//header//*[contains(@class,"collection-state")]'))
        self.assertFalse(card.xpath('.//div[@class="outlet-content"]//*[contains(@class,"notice-warning")]'))
        self.assertNotIn("Private failure details", response.content.decode())

    def test_readiness_distinguishes_process_from_usable_news(self):
        ready_url = reverse("dashboard:ready")
        response = self.client.get(ready_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["fresh"], 2)
        self.assertEqual(response.json()["missing"], len([source for source in SOURCES if source.enabled]) - 2)
        with override_settings(NEWS_MIN_FRESH_SOURCES=3):
            self.assertEqual(self.client.get(ready_url).status_code, 503)
        healthy_sources = [source for source in SOURCES if source.id in {"bbc", "g1"}]
        with patch("dashboard.views.SOURCES", healthy_sources):
            healthy = self.client.get(ready_url)
        self.assertEqual(healthy.status_code, 200)
        self.assertEqual(healthy.json()["status"], "ok")
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        with database.connect() as db:
            db.execute("UPDATE source_status SET succeeded_at=?", (old,))
        stale = self.client.get(ready_url)
        self.assertEqual(stale.status_code, 503)
        self.assertEqual(stale.json()["fresh"], 0)
        self.assertEqual(stale.json()["stale"], 2)
        self.assertEqual(self.client.get(reverse("dashboard:health")).status_code, 200)
        self.assertEqual(self.client.post(ready_url).status_code, 405)

    def test_health_and_public_errors_do_not_leak_collection_details(self):
        secret = "https://admin:private-token@home.internal/private/database/path"
        database.record_failure("bbc", secret)
        response = self.get()
        self.assertNotIn(secret, response.content.decode())
        readiness = self.client.get(reverse("dashboard:ready"))
        self.assertEqual(readiness.json()["failed"], 1)
        self.assertNotIn("private-token", readiness.content.decode())
        self.assertNotIn("python -m news.collect", response.content.decode())


if __name__ == "__main__":
    unittest.main()
