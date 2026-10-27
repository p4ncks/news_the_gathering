"""Outlet suggestions save privately and work without JavaScript or network."""
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("DJANGO_DEBUG", "true")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gathering.settings")

import django

django.setup()

from django.db import DatabaseError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import translation
from lxml import html

from dashboard.forms import OutletSuggestionForm
from dashboard.models import OutletSuggestion
from news import database


class SuggestionTests(unittest.TestCase):
    def setUp(self):
        settings = override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
        settings.enable()
        self.addCleanup(settings.disable)
        locale = translation.override("en")
        locale.__enter__()
        self.addCleanup(locale.__exit__, None, None, None)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "news.db"
        environment = patch.dict(os.environ, {"NEWS_DB_PATH": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)
        database.initialize()
        self.client = Client()
        self.endpoint = reverse("dashboard:suggest_outlet")
        self.payload = {
            "name": "An Example Newspaper",
            "website": "https://news.example.com/",
            "country": "São Tomé e Príncipe",
            "reason": "Local reporting adds another perspective.",
            "return_query": "filtered=1&continent=Europe&outlet=bbc&q=climate&view=compact",
        }

    def rows(self):
        with database._using() as alias:
            return list(OutletSuggestion.objects.using(alias).values())

    def test_dashboard_has_an_accessible_post_form_without_listing_submissions(self):
        response = self.client.get(reverse("dashboard:index"))
        page = response.content.decode()
        self.assertIn('id="suggest-outlet"', page)
        self.assertIn('action="/suggest-outlet/"', page)
        self.assertIn('name="csrfmiddlewaretoken"', page)
        self.assertIn('for="id_name"', page)
        self.client.post(self.endpoint, self.payload)
        page = self.client.get(reverse("dashboard:index")).content.decode()
        self.assertNotIn(self.payload["name"], page)
        self.assertNotIn(self.payload["reason"], page)

    def test_valid_suggestion_saves_once_and_redirects_to_the_applied_selection(self):
        response = self.client.post(self.endpoint, self.payload)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.url, "/?" + self.payload["return_query"] + "&suggested=1#suggest-outlet")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        for field in ("name", "website", "country", "reason"):
            self.assertEqual(rows[0][field], self.payload[field])
        self.assertIsNotNone(rows[0]["submitted_at"])
        thank_you = self.client.get(response.url).content.decode()
        self.assertIn("Thank you. Your suggestion has been saved for review.", thank_you)

    def test_only_outlet_name_and_website_are_required(self):
        response = self.client.post(self.endpoint, {"name": "Another newspaper", "website": "https://news.example.org/"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.rows()[0]["country"], "")
        self.assertEqual(self.rows()[0]["reason"], "")
        for field in ("name", "website"):
            with self.subTest(field=field):
                form = OutletSuggestionForm({**self.payload, field: ""})
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_required_markers_and_help_are_accessible_in_both_languages(self):
        for language, required, optional, kicker in (("en", "(required)", "(optional)", "Your source recommendations"),
                                                     ("pt-br", "(obrigatório)", "(opcional)", "Suas recomendações de fontes")):
            with self.subTest(language=language):
                self.client.cookies["django_language"] = language
                response = self.client.get(reverse("dashboard:index"))
                doc = html.fromstring(response.content)
                panel = doc.get_element_by_id("suggest-outlet")
                self.assertIn(kicker, panel.text_content())
                required_inputs = panel.xpath('.//input[@required]/@name')
                self.assertEqual(required_inputs, ["name", "website"])
                for field in ("name", "website"):
                    label = panel.xpath(f'.//label[@for="id_{field}"]')[0]
                    self.assertTrue(label.xpath('.//span[@class="required-marker"][@aria-hidden="true"]'))
                    self.assertIn(required, label.text_content())
                country = panel.xpath('.//label[@for="id_country"]')[0]
                self.assertIn(optional, country.text_content())
                help_text = panel.get_element_by_id("id_reason_helptext")
                self.assertIn("suggestion-help", help_text.get("class"))
                self.assertEqual(panel.get_element_by_id("id_reason").get("aria-describedby"), "id_reason_helptext")

    def test_invalid_values_are_shown_with_errors_and_do_not_save(self):
        payload = {**self.payload, "name": "<script>alert('unsafe')</script>", "website": "not-a-url", "country": ""}
        response = self.client.post(self.endpoint, payload)
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("Please review the highlighted fields.", page)
        self.assertIn('aria-invalid="true"', page)
        self.assertIn('id="suggest-website-errors"', page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert", page)
        self.assertEqual(self.rows(), [])

    def test_url_and_length_limits_are_enforced_server_side(self):
        for updates in ({"website": "ftp://news.example.com/"},
                        {"website": "https://name:secret@news.example.com/"},
                        {"reason": "x" * 1001}, {"name": "x" * 141}):
            with self.subTest(updates=updates):
                form = OutletSuggestionForm({**self.payload, **updates})
                self.assertFalse(form.is_valid())
        form = OutletSuggestionForm({**self.payload, "website": "news.example.com", "reason": ""})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["website"], "https://news.example.com")

    def test_honeypot_prevents_a_write_and_does_not_claim_success(self):
        response = self.client.post(self.endpoint, {**self.payload, "website_confirmation": "spam"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Unable to send this suggestion", response.content.decode())
        self.assertNotIn("Your suggestion has been saved", response.content.decode())
        self.assertEqual(self.rows(), [])

    def test_write_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(self.endpoint).status_code, 405)
        protected = Client(enforce_csrf_checks=True)
        self.assertEqual(protected.post(self.endpoint, self.payload).status_code, 403)
        self.assertEqual(self.rows(), [])

    def test_database_failure_shows_generic_error_and_keeps_values(self):
        with patch.object(OutletSuggestion.objects.__class__, "using", side_effect=DatabaseError("private-password-secret")):
            with self.assertLogs("dashboard.suggestions", level="ERROR"):
                response = self.client.post(self.endpoint, self.payload)
        self.assertEqual(response.status_code, 503)
        page = response.content.decode()
        self.assertIn("Suggestions are temporarily unavailable", page)
        self.assertIn(self.payload["name"], page)
        self.assertNotIn("private-password-secret", page)
        self.assertEqual(self.rows(), [])

    def test_return_query_cannot_redirect_offsite_or_leak_form_values(self):
        response = self.client.post(self.endpoint, {**self.payload,
            "return_query": "next=https%3A%2F%2Fevil.example%2F&name=private&filtered=1&outlet=bbc&continent=Europe"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.url, "/?filtered=1&outlet=bbc&continent=Europe&suggested=1#suggest-outlet")
        self.assertNotIn("private", response.url)
        self.assertNotIn("evil.example", response.url)

    def test_full_backup_and_import_preserve_private_suggestions_and_dates(self):
        self.client.post(self.endpoint, self.payload)
        submitted = datetime(2025, 4, 12, 9, 30, tzinfo=timezone.utc)
        with database._using() as alias:
            OutletSuggestion.objects.using(alias).update(submitted_at=submitted)
        backup = self.path.parent / "full-backup.db"
        database.backup(backup)
        target = self.path.parent / "imported.db"
        result = database.import_sqlite(backup, path=target)
        self.assertEqual(result["dashboard_outletsuggestion"], 1)
        with database._using(target) as alias:
            imported = OutletSuggestion.objects.using(alias).get()
            self.assertEqual(imported.name, self.payload["name"])
            self.assertEqual(imported.reason, self.payload["reason"])
            self.assertEqual(imported.submitted_at, submitted)
        repeated = database.import_sqlite(backup, path=target)
        self.assertTrue(repeated["already_imported"])

    def test_website_snapshot_omits_private_suggestions(self):
        self.client.post(self.endpoint, self.payload)
        exported = self.path.parent / "public-snapshot.db"
        database.export_snapshot(exported)
        with database._using(exported) as alias:
            self.assertFalse(OutletSuggestion.objects.using(alias).exists())
        self.assertEqual(len(self.rows()), 1)
