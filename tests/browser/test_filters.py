"""Browser regressions against an isolated local Django server and fixture database.

Run separately: python -m unittest discover -s tests/browser -v
Install requirements-test.txt and run python -m playwright install chromium first.
"""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from wsgiref.simple_server import make_server, WSGIRequestHandler

os.environ["DJANGO_DEBUG"] = "true"
os.environ["NEWS_DB_ENGINE"] = "sqlite"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gathering.settings")

from playwright.sync_api import sync_playwright, expect


class QuietRequests(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="gathering-browser-")
        os.environ["NEWS_DB_PATH"] = str(Path(cls.temporary.name) / "news.db")
        import django
        django.setup()
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.wsgi import get_wsgi_application
        from news import database
        from news.sources import SOURCES
        cls.sources = [source for source in SOURCES if source.enabled]
        database.initialize()
        for source in cls.sources:
            database.save_collection(source.id, [{
                "url": f"https://example.com/{source.id}/{rank}",
                "title": f"{source.name} headline {rank}",
                "summary": "A saved climate report",
                "published_at": database.utc_now(),
                "body": "BODY_SENTINEL " + "Saved article text. " * 2000 if source.id == "bbc" and rank == 1 else "",
            } for rank in range(1, 11)])
        cls.server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()),
                                 handler_class=QuietRequests)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        from django.db import connections
        connections.close_all()
        cls.temporary.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.page = self.context.new_page()
        self.page.goto(self.url)

    def tearDown(self):
        self.context.close()

    def test_bulk_and_continent_changes_synchronize_outlets(self):
        self.page.get_by_role("button", name="All off", exact=True).click()
        self.assertEqual(self.page.locator('input[name="outlet"]:checked').count(), 0)
        self.assertEqual(self.page.locator(".outlet-option:visible").count(), 0)
        self.page.get_by_role("button", name="All on", exact=True).click()
        self.assertEqual(self.page.locator('input[name="outlet"]:checked').count(), len(self.sources))
        self.page.locator('input[name="continent"][value="Europe"]').uncheck()
        self.assertEqual(self.page.locator('input[name="outlet"][data-continent="Europe"]:checked').count(), 0)
        self.assertEqual(self.page.locator('.outlet-option:visible input[data-continent="Europe"]').count(), 0)

    def test_partial_continent_and_last_outlet_state(self):
        self.page.locator('input[name="outlet"][value="bbc"]').uncheck()
        europe = self.page.locator('input[name="continent"][value="Europe"]')
        expect(europe).to_have_attribute("aria-checked", "mixed")
        self.page.get_by_role("button", name="All off", exact=True).click()
        europe.check()
        for source in self.sources:
            if source.continent == "Europe":
                self.page.locator(f'input[name="outlet"][value="{source.id}"]').uncheck()
        expect(europe).not_to_be_checked()
        self.assertEqual(self.page.locator(".outlet-option:visible").count(), 0)

    def test_continent_only_bookmark_keeps_choices_available(self):
        self.page.goto(self.url + "?filtered=1&continent=Europe")
        expect(self.page.locator('input[name="outlet"][value="bbc"]')).to_be_visible()
        expect(self.page.locator("[data-empty-outlets]")).not_to_be_visible()

    def test_search_has_global_empty_state_and_preserves_selection(self):
        self.page.get_by_role("searchbox", name="Find a topic").fill("no-match-unique-token")
        self.page.get_by_role("button", name="Apply my selection").first.click()
        expect(self.page.get_by_role("heading", name='No headlines match “no-match-unique-token”.')).to_be_visible()
        self.assertEqual(self.page.locator(".outlet-card").count(), 0)
        self.page.get_by_role("link", name="Clear search", exact=True).first.click()
        expect(self.page.locator(".outlet-card")).to_have_count(len(self.sources))

    def test_single_apply_and_aligned_scrollable_cards(self):
        expect(self.page.locator('.filter-form button[type="submit"]')).to_have_count(1)
        expect(self.page.get_by_role("link", name="Reload this view")).to_have_count(0)
        self.assertTrue(self.page.locator(".outlet-card").evaluate_all(
            "(cards) => new Set(cards.map(card => Math.round(card.getBoundingClientRect().height))).size === 1"))
        expect(self.page.locator("#edition-bbc .outlet-content")).to_have_attribute("tabindex", "0")

    def test_compact_mode_and_lazy_article_text(self):
        self.assertNotIn("BODY_SENTINEL", self.page.content())
        self.page.get_by_role("link", name="Compact headlines", exact=True).click()
        expect(self.page.locator("#edition-bbc .headline")).to_have_count(10)
        expect(self.page.locator("#edition-bbc .headline-lead")).to_have_count(0)
        self.page.locator("#edition-bbc .story-details").first.locator("summary").click()
        self.page.locator("#edition-bbc").get_by_role("link", name="Read saved article text").click()
        expect(self.page.locator(".reader-body")).to_contain_text("BODY_SENTINEL")

    def test_keyboard_focus_and_escape_dismiss_tooltip(self):
        flag = self.page.locator("#edition-bbc .country-flag")
        flag.focus()
        expect(flag.locator(".country-tooltip")).to_be_visible()
        flag.press("Escape")
        expect(flag.locator(".country-tooltip")).not_to_be_visible()
        expect(self.page.locator("#edition-g1 h4").first).to_have_attribute("lang", "pt-BR")

    def test_no_javascript_can_recover_from_empty_selection(self):
        context = self.browser.new_context(java_script_enabled=False)
        try:
            page = context.new_page()
            page.goto(self.url + "?filtered=1")
            page.locator('input[name="continent"][value="Europe"]').check()
            page.get_by_role("button", name="Apply my selection").first.click()
            bbc = page.locator('input[name="outlet"][value="bbc"]')
            expect(bbc).to_be_visible()
            bbc.check()
            page.get_by_role("button", name="Apply my selection").first.click()
            expect(page.locator(".outlet-card")).to_have_count(1)
        finally:
            context.close()

    def test_mobile_filters_do_not_precede_news_with_a_long_form(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.reload()
        expect(self.page.locator(".filter-panel")).not_to_have_attribute("open", "")
        self.assertLess(self.page.locator("#headlines").bounding_box()["y"], 230)
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        self.assertTrue(self.page.locator(".metric-label").evaluate_all(
            "(labels) => labels.every(label => label.scrollWidth <= label.clientWidth)"))
        self.page.locator(".filter-panel > summary").click()
        expect(self.page.get_by_role("searchbox", name="Find a topic")).to_be_visible()

    def test_apply_action_is_available_on_the_first_desktop_screen(self):
        button = self.page.get_by_role("button", name="Apply my selection").first
        expect(button).to_be_in_viewport()
