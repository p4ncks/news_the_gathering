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


SOURCES = [
    Source("bbc", "BBC News", "Europe", "United Kingdom", "English",
           "https://feeds.bbci.co.uk/news/rss.xml", "https://www.bbc.com/news"),
    Source("guardian", "The Guardian", "Europe", "United Kingdom", "English",
           "https://www.theguardian.com/world/rss", "https://www.theguardian.com", note="World section"),
    Source("npr", "NPR", "North America", "United States", "English",
           "https://feeds.npr.org/1001/rss.xml", "https://www.npr.org"),
    Source("cnn", "CNN", "North America", "United States", "English",
           "https://rss.cnn.com/rss/edition.rss", "https://www.cnn.com", enabled=False,
           note="Legacy RSS may be frozen. Verify a current feed before enabling."),
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
]
CONTINENTS = ["Africa", "Asia", "Europe", "North America", "South America", "Oceania"]
