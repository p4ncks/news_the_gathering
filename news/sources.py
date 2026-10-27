"""Edit this list to add outlets. Geography refers to the outlet's home base."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    continent: str
    country: str
    language: str
    feed_url: str
    website: str
    enabled: bool = True
    discovery: str = "rss"
    # Sections can share an organization, e.g. CNN and CNN Space.
    # An empty value means the source ID identifies the organization.
    organization: str = ""
    language_code: str = "en"
    edition_scope: str = "Latest headlines"


SOURCES = [
    Source("bbc", "BBC News", "Europe", "United Kingdom", "English",
           "https://feeds.bbci.co.uk/news/rss.xml", "https://www.bbc.com/news",
           language_code="en", edition_scope="General news"),
    Source("guardian", "The Guardian", "Europe", "United Kingdom", "English",
           "https://www.theguardian.com/world/rss", "https://www.theguardian.com",
           language_code="en", edition_scope="World news"),
    Source("npr", "NPR", "North America", "United States", "English",
           "https://feeds.npr.org/1001/rss.xml", "https://www.npr.org",
           language_code="en", edition_scope="Top stories"),
    Source("cnn", "CNN", "North America", "United States", "English",
           "", "https://www.cnn.com", discovery="html", organization="cnn",
           language_code="en", edition_scope="Homepage"),
    Source("cnn-space", "CNN Space", "North America", "United States", "English",
           "", "https://www.cnn.com/science/space", discovery="html", organization="cnn",
           language_code="en", edition_scope="Space section"),
    Source("g1", "g1 · Globo", "South America", "Brazil", "Português",
           "https://g1.globo.com/rss/g1/", "https://g1.globo.com",
           language_code="pt-BR", edition_scope="Latest news"),
    Source("folha", "Folha de S.Paulo", "South America", "Brazil", "Português",
           "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml", "https://www.folha.uol.com.br",
           language_code="pt-BR", edition_scope="Latest news"),
    Source("aljazeera", "Al Jazeera", "Asia", "Qatar", "English",
           "https://www.aljazeera.com/xml/rss/all.xml", "https://www.aljazeera.com",
           language_code="en", edition_scope="All news"),
    Source("cna", "CNA", "Asia", "Singapore", "English",
           "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "https://www.channelnewsasia.com",
           language_code="en", edition_scope="Latest news"),
    Source("daily-maverick", "Daily Maverick", "Africa", "South Africa", "English",
           "https://www.dailymaverick.co.za/dmrss/", "https://www.dailymaverick.co.za",
           language_code="en", edition_scope="Latest news"),
    Source("abc", "ABC News", "Oceania", "Australia", "English",
           "https://www.abc.net.au/news/feed/51120/rss.xml", "https://www.abc.net.au/news",
           language_code="en", edition_scope="Latest news"),
    Source("dw", "DW", "Europe", "Germany", "English",
           "https://rss.dw.com/rdf/rss-en-top", "https://www.dw.com/en",
           language_code="en", edition_scope="Top stories"),
    Source("cbc", "CBC News", "North America", "Canada", "English",
           "https://www.cbc.ca/webfeed/rss/rss-topstories", "https://www.cbc.ca/news",
           language_code="en", edition_scope="Top stories"),
    Source("buenos-aires-herald", "Buenos Aires Herald", "South America", "Argentina", "English",
           "https://buenosairesherald.com/feed", "https://buenosairesherald.com",
           language_code="en", edition_scope="Latest news"),
    Source("the-hindu", "The Hindu", "Asia", "India", "English",
           "https://www.thehindu.com/news/feeder/default.rss", "https://www.thehindu.com",
           language_code="en", edition_scope="News section"),
    Source("premium-times", "Premium Times", "Africa", "Nigeria", "English",
           "https://www.premiumtimesng.com/feed", "https://www.premiumtimesng.com",
           language_code="en", edition_scope="Latest news"),
    Source("nation", "Nation", "Africa", "Kenya", "English",
           "https://nation.africa/kenya/rss.xml", "https://nation.africa/kenya",
           language_code="en", edition_scope="Kenya news"),
    Source("rnz", "RNZ", "Oceania", "New Zealand", "English",
           "https://www.rnz.co.nz/rss/news.xml", "https://www.rnz.co.nz/news",
           language_code="en", edition_scope="Latest news"),
    Source("sbs", "SBS News", "Oceania", "Australia", "English",
           "https://www.sbs.com.au/news/topic/latest/feed", "https://www.sbs.com.au/news",
           language_code="en", edition_scope="Latest news"),
    Source("el-pais", "El País", "Europe", "Spain", "Español",
           "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", "https://elpais.com",
           language_code="es", edition_scope="Front page"),
    Source("el-mundo", "El Mundo", "Europe", "Spain", "Español",
           "https://e00-elmundo.uecdn.es/rss/portada.xml", "https://www.elmundo.es",
           language_code="es", edition_scope="Front page"),
    Source("le-monde", "Le Monde", "Europe", "France", "Français",
           "https://www.lemonde.fr/rss/une.xml", "https://www.lemonde.fr",
           language_code="fr", edition_scope="Front page"),
    Source("france24", "France 24", "Europe", "France", "Français",
           "https://www.france24.com/fr/rss", "https://www.france24.com/fr/",
           language_code="fr", edition_scope="Latest news"),
    Source("tass", "TASS", "Europe", "Russia", "Русский",
           "https://tass.ru/rss/v2.xml", "https://tass.ru",
           language_code="ru", edition_scope="Latest news"),
    Source("kommersant", "Kommersant", "Europe", "Russia", "Русский",
           "https://www.kommersant.ru/RSS/news.xml", "https://www.kommersant.ru",
           language_code="ru", edition_scope="Latest news"),
    Source("la-nacion", "La Nación", "South America", "Argentina", "Español",
           "https://www.lanacion.com.ar/arc/outboundfeeds/rss/?outputType=xml", "https://www.lanacion.com.ar",
           language_code="es-AR", edition_scope="Latest news"),
    Source("cooperativa", "Cooperativa", "South America", "Chile", "Español",
           "https://www.cooperativa.cl/noticias/site/tax/port/all/rss_3___1.xml", "https://www.cooperativa.cl",
           language_code="es-CL", edition_scope="Latest news"),
    Source("el-tiempo", "El Tiempo", "South America", "Colombia", "Español",
           "https://www.eltiempo.com/rss/colombia.xml", "https://www.eltiempo.com",
           language_code="es-CO", edition_scope="Colombia section"),
]
CONTINENTS = ["Africa", "Asia", "Europe", "North America", "South America", "Oceania"]
