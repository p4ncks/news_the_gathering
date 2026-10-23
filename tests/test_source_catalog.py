"""Keep the configured catalog broad enough for comparisons across continents."""
import unittest
from urllib.parse import urlsplit

from news.sources import CONTINENTS, SOURCES


class SourceCatalogTests(unittest.TestCase):
    def test_source_ids_are_unique(self):
        ids = [source.id for source in SOURCES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_enabled_sources_have_valid_geography_and_publisher_urls(self):
        for source in SOURCES:
            if not source.enabled:
                continue
            with self.subTest(source=source.id):
                self.assertIn(source.continent, CONTINENTS)
                self.assertTrue(source.country)
                self.assertTrue(source.language)
                self.assertIn(source.discovery, {"rss", "html"})
                urls = [source.website]
                if source.discovery == "rss":
                    urls.append(source.feed_url)
                for url in urls:
                    parts = urlsplit(url)
                    self.assertIn(parts.scheme, {"http", "https"})
                    self.assertTrue(parts.hostname)

    def test_each_continent_has_three_distinct_news_organizations(self):
        for continent in CONTINENTS:
            organizations = {
                source.organization or source.id
                for source in SOURCES
                if source.enabled and source.continent == continent
            }
            with self.subTest(continent=continent):
                self.assertGreaterEqual(len(organizations), 3)

    def test_cnn_space_remains_available_as_a_section_of_cnn(self):
        sources = {source.id: source for source in SOURCES if source.enabled}
        self.assertEqual(sources["cnn"].organization, "cnn")
        self.assertEqual(sources["cnn-space"].organization, "cnn")
        self.assertEqual(sources["cnn-space"].discovery, "html")
        self.assertEqual(sources["cnn-space"].website, "https://www.cnn.com/science/space")

    def test_spain_france_and_russia_each_have_two_outlets(self):
        for country in ("Spain", "France", "Russia"):
            with self.subTest(country=country):
                organizations = {source.organization or source.id for source in SOURCES
                                 if source.enabled and source.country == country}
                self.assertGreaterEqual(len(organizations), 2)

    def test_spanish_language_south_american_outlets_cover_three_countries(self):
        sources = [source for source in SOURCES if source.enabled
                   and source.continent == "South America" and source.language == "Español"]
        self.assertGreaterEqual(len(sources), 3)
        self.assertTrue({"Argentina", "Chile", "Colombia"}.issubset({source.country for source in sources}))


if __name__ == "__main__":
    unittest.main()
