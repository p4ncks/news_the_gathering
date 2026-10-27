"""English/Portuguese interface behavior, without translating publisher text."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault('DJANGO_DEBUG', 'true')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gathering.settings')

import django

django.setup()

from django.test import Client, override_settings
from django.urls import reverse
from django.utils import translation
from lxml import html


class LanguageTests(unittest.TestCase):
    def setUp(self):
        settings = override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
        settings.enable()
        self.addCleanup(settings.disable)
        language = translation.override('en')
        language.__enter__()
        self.addCleanup(language.__exit__, None, None, None)
        self.client = Client()
        self.article = {'id': 1, 'rank': 1, 'title': 'An original English headline',
                        'summary': 'Publisher words stay unchanged.', 'url': 'https://example.com/news',
                        'published_at': '2026-10-25T12:00:00+00:00', 'has_body': True}
        statuses = {'bbc': {'succeeded_at': '2026-10-25T12:30:00+00:00'}}
        read = patch('dashboard.views.database.get_dashboard', return_value=(statuses, {'bbc': [self.article]}))
        read.start()
        self.addCleanup(read.stop)
        self.selection = '?filtered=1&continent=Europe&outlet=bbc&q=original&view=compact'

    def portuguese(self):
        return self.client.post(reverse('set_language'), {'language': 'pt-br', 'next': '/' + self.selection})

    def test_default_english_and_two_available_languages(self):
        response = self.client.get('/' + self.selection)
        doc = html.fromstring(response.content)
        self.assertEqual(doc.get('lang'), 'en')
        self.assertIn('Your global news desk', doc.text_content())
        self.assertIn("Let's hear it.", doc.text_content())
        self.assertEqual(doc.xpath('//select[@name="language"]/option/@value'), ['en', 'pt-br'])
        self.assertEqual(doc.xpath('//select[@name="language"]/option/text()'), ['English', 'Português'])
        self.assertEqual(doc.xpath('//form[contains(@class,"language-form")]/input[@name="next"]/@value'), ['/' + self.selection])

    def test_language_post_persists_and_preserves_selection_url(self):
        response = self.portuguese()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/' + self.selection)
        self.assertEqual(response.cookies['django_language'].value, 'pt-br')
        page = self.client.get(response.url)
        self.assertEqual(page.headers['Content-Language'], 'pt-br')
        doc = html.fromstring(page.content)
        self.assertEqual(doc.get('lang'), 'pt-br')
        self.assertEqual(doc.xpath('//select[@name="language"]/option[@selected]/@value'), ['pt-br'])
        parsed = parse_qs(urlsplit(response.url).query)
        self.assertEqual(parsed['outlet'], ['bbc'])
        self.assertEqual(parsed['view'], ['compact'])
        english = self.client.post(reverse('set_language'), {'language': 'en', 'next': response.url})
        self.assertEqual(self.client.get(english.url).headers['Content-Language'], 'en')

    def test_portuguese_translates_metadata_accessibility_counts_and_dates(self):
        self.portuguese()
        page = self.client.get('/' + self.selection).content.decode()
        doc = html.fromstring(page)
        self.assertIn('Seu panorama global de notícias', page)
        self.assertIn('Vamos ouvir.', page)
        self.assertIn('Voltar ao topo ↑', page)
        self.assertIn('Aplicar minha seleção', page)
        self.assertIn('Última atualização', page)
        self.assertIn('Reino Unido', page)
        self.assertIn('Notícias gerais', page)
        self.assertIn('Inglês', page)
        self.assertIn('out', page.lower())
        self.assertIn('UTC', page)
        self.assertIn('Manchetes de BBC News', page)
        form = doc.xpath('//form[@class="filter-form"]')[0]
        self.assertEqual(form.get('data-selection-singular'), '{count} veículo selecionado')
        self.assertEqual(form.get('data-selection-plural'), '{count} veículos selecionados')
        # Display labels translate, while stable filter values still round-trip.
        self.assertIn('Europa', page)
        self.assertIn('value="Europe"', page)
        self.assertEqual(doc.xpath('//h3[@id="outlet-bbc"]/text()'), ['BBC News'])
        self.assertEqual(doc.xpath('//h4/a/text()'), [self.article['title']])
        self.assertIn(self.article['summary'], page)

    def test_portuguese_empty_search_and_suggestion_form_are_localized(self):
        self.portuguese()
        page = self.client.get('/?filtered=1&continent=Europe&outlet=bbc&q=no-match').content.decode()
        self.assertIn('Nenhuma manchete encontrada', page)
        self.assertIn('Limpar busca', page)
        self.assertIn('Sugira um veículo de notícias', page)
        self.assertIn('Nome do veículo', page)
        self.assertIn('Enviar sugestão', page)
        self.assertNotIn('No headlines match', page)

    def test_reader_uses_same_language_and_keeps_original_body(self):
        self.portuguese()
        saved = {**self.article, 'body': 'The original saved English article body.', 'body_metadata_hash': ''}
        with patch('dashboard.views.database.get_article', return_value=saved):
            page = self.client.get(reverse('dashboard:article', args=['bbc', 1])).content.decode()
        self.assertIn('<html lang="pt-br">', page)
        self.assertIn('Voltar às notícias', page)
        self.assertIn('Leia o original em BBC News', page)
        self.assertIn('Texto salvo', page)
        self.assertIn('lang="en"', page)
        self.assertIn(saved['body'], page)
        self.assertIn(self.article['title'], page)

    def test_language_redirect_does_not_allow_external_destinations(self):
        response = self.client.post(reverse('set_language'), {'language': 'pt-br', 'next': 'https://untrusted.example/'})
        self.assertEqual(response.url, '/')

    def test_compiled_catalog_supports_singular_and_plural(self):
        with translation.override('pt-br'):
            self.assertEqual(translation.ngettext('%(count)s outlet selected', '%(count)s outlets selected', 1), '%(count)s veículo selecionado')
            self.assertEqual(translation.ngettext('%(count)s outlet selected', '%(count)s outlets selected', 2), '%(count)s veículos selecionados')
            self.assertEqual(translation.gettext('Not available'), 'Não disponível')
        self.assertTrue((Path(__file__).resolve().parents[1] / 'locale/pt_BR/LC_MESSAGES/django.mo').is_file())
