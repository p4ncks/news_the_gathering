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
    note: str = ""
    discovery: str = "rss"
    # Sections can share an organization, e.g. CNN and CNN Space.
    # An empty value means the source ID identifies the organization.
    organization: str = ""


SOURCES = [
    Source("bbc", "BBC News", "Europe", "United Kingdom", "English",
           "https://feeds.bbci.co.uk/news/rss.xml", "https://www.bbc.com/news"),
    Source("guardian", "The Guardian", "Europe", "United Kingdom", "English",
           "https://www.theguardian.com/world/rss", "https://www.theguardian.com", note="World section"),
    Source("npr", "NPR", "North America", "United States", "English",
           "https://feeds.npr.org/1001/rss.xml", "https://www.npr.org"),
    Source("cnn", "CNN", "North America", "United States", "English",
           "", "https://www.cnn.com", note="Homepage order", discovery="html", organization="cnn"),
    Source("cnn-space", "CNN Space", "North America", "United States", "English",
           "", "https://www.cnn.com/science/space", note="Space section order", discovery="html", organization="cnn"),
    Source("g1", "g1 · Globo", "South America", "Brazil", "Português",
           "https://g1.globo.com/rss/g1/", "https://g1.globo.com"),
    Source("folha", "Folha de S.Paulo", "South America", "Brazil", "Português",
           "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml", "https://www.folha.uol.com.br"),
    Source("aljazeera", "Al Jazeera", "Asia", "Qatar", "English",
           "https://www.aljazeera.com/xml/rss/all.xml", "https://www.aljazeera.com"),
    Source("cna", "CNA", "Asia", "Singapore", "English",
           "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "https://www.channelnewsasia.com"),
    Source("daily-maverick", "Daily Maverick", "Africa", "South Africa", "English",
           "https://www.dailymaverick.co.za/dmrss/", "https://www.dailymaverick.co.za"),
    Source("abc", "ABC News", "Oceania", "Australia", "English",
           "https://www.abc.net.au/news/feed/51120/rss.xml", "https://www.abc.net.au/news"),
    Source("dw", "DW", "Europe", "Germany", "English",
           "https://rss.dw.com/rdf/rss-en-top", "https://www.dw.com/en", note="Top stories"),
    Source("cbc", "CBC News", "North America", "Canada", "English",
           "https://www.cbc.ca/webfeed/rss/rss-topstories", "https://www.cbc.ca/news"),
    Source("buenos-aires-herald", "Buenos Aires Herald", "South America", "Argentina", "English",
           "https://buenosairesherald.com/feed", "https://buenosairesherald.com"),
    Source("the-hindu", "The Hindu", "Asia", "India", "English",
           "https://www.thehindu.com/news/feeder/default.rss", "https://www.thehindu.com", note="News section"),
    Source("premium-times", "Premium Times", "Africa", "Nigeria", "English",
           "https://www.premiumtimesng.com/feed", "https://www.premiumtimesng.com"),
    Source("nation", "Nation", "Africa", "Kenya", "English",
           "https://nation.africa/kenya/rss.xml", "https://nation.africa/kenya"),
    Source("rnz", "RNZ", "Oceania", "New Zealand", "English",
           "https://www.rnz.co.nz/rss/news.xml", "https://www.rnz.co.nz/news"),
    Source("sbs", "SBS News", "Oceania", "Australia", "English",
           "https://www.sbs.com.au/news/topic/latest/feed", "https://www.sbs.com.au/news", note="Latest news"),
    Source("el-pais", "El País", "Europe", "Spain", "Español",
           "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", "https://elpais.com", note="Front page"),
    Source("el-mundo", "El Mundo", "Europe", "Spain", "Español",
           "https://e00-elmundo.uecdn.es/rss/portada.xml", "https://www.elmundo.es", note="Front page"),
    Source("le-monde", "Le Monde", "Europe", "France", "Français",
           "https://www.lemonde.fr/rss/une.xml", "https://www.lemonde.fr", note="Front page"),
    Source("france24", "France 24", "Europe", "France", "Français",
           "https://www.france24.com/fr/rss", "https://www.france24.com/fr/"),
    Source("tass", "TASS", "Europe", "Russia", "Русский",
           "https://tass.ru/rss/v2.xml", "https://tass.ru"),
    Source("kommersant", "Kommersant", "Europe", "Russia", "Русский",
           "https://www.kommersant.ru/RSS/news.xml", "https://www.kommersant.ru"),
    Source("la-nacion", "La Nación", "South America", "Argentina", "Español",
           "https://www.lanacion.com.ar/arc/outboundfeeds/rss/?outputType=xml", "https://www.lanacion.com.ar"),
    Source("cooperativa", "Cooperativa", "South America", "Chile", "Español",
           "https://www.cooperativa.cl/noticias/site/tax/port/all/rss_3___1.xml", "https://www.cooperativa.cl"),
    Source("el-tiempo", "El Tiempo", "South America", "Colombia", "Español",
           "https://www.eltiempo.com/rss/colombia.xml", "https://www.eltiempo.com", note="Colombia section"),
]
CONTINENTS = ["Africa", "Asia", "Europe", "North America", "South America", "Oceania"]
