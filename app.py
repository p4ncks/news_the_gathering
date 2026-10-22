"""The web interface. All news displayed here is read from SQLite."""
from datetime import datetime, timezone

import streamlit as st

from news import database
from news.sources import CONTINENTS, SOURCES

st.set_page_config(page_title="News, The Gathering", page_icon="🌐", layout="wide")
database.initialize()


def timestamp(value):
    if not value:
        return "Not available"
    return datetime.fromisoformat(value).strftime("%d %b %Y · %H:%M UTC")


def older_than(value, hours):
    return value and (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds() > hours * 3600


with st.sidebar:
    st.title("🌐 The Gathering")
    st.caption("A wider view of the world")
    continents = st.multiselect("Continents", CONTINENTS, default=CONTINENTS)
    available = [s for s in SOURCES if s.enabled and s.continent in continents]
    names = {s.id: s.name for s in SOURCES}
    selected = st.multiselect("Compare outlets", [s.id for s in available],
                              default=[s.id for s in available], format_func=names.get)
    query = st.text_input("Find a topic", placeholder="Search headlines and summaries")
    st.caption("Search filters each outlet’s current top ten. Languages are preserved.")
    st.button("Reload saved headlines", width="stretch")
    st.divider()
    st.caption("Collectors run separately. To populate or update the database:")
    st.code("python -m news.collect", language="bash")
    st.caption("Continent = where the outlet is based, not where the story happened.")

st.caption("THE WORLD, THROUGH DIFFERENT LENSES")
st.title("News, The Gathering")
st.write("Ten headlines per outlet. A place to compare what the world is paying attention to.")
st.caption("Feed order, not a popularity score or an exact homepage ranking. All times are UTC.")

statuses = database.get_statuses()
sources = [s for s in available if s.id in selected]
headlines = {s.id: database.get_headlines(s.id) for s in sources}
metrics = st.columns(3)
metrics[0].metric("Outlets in view", len(sources))
metrics[1].metric("Continents represented", len({s.continent for s in sources}))
metrics[2].metric("Saved headlines", sum(len(items) for items in headlines.values()))
st.divider()

if not sources:
    st.info("Choose at least one continent and outlet in the sidebar.")
elif not any(headlines.values()):
    st.info("No headlines collected yet for this selection. Run `python -m news.collect` in the project folder, then reload.")

for start in range(0, len(sources), 3):
    columns = st.columns(3)
    for column, source in zip(columns, sources[start:start + 3]):
        with column:
            with st.container(border=True):
                st.caption(f"{source.continent.upper()} · {source.country} · {source.language}")
                st.subheader(source.name)
                if source.note:
                    st.caption(source.note)
                status = statuses.get(source.id, {})
                st.caption("Collected: " + timestamp(status.get("succeeded_at")))
                if status.get("error"):
                    st.warning("Latest collection failed. Showing the last saved headlines, if available.")
                    with st.expander("Collection details"):
                        st.text(status["error"])
                        st.caption("Attempted: " + timestamp(status.get("attempted_at")))
                elif not status.get("succeeded_at"):
                    st.caption("Waiting for first collection.")
                elif older_than(status["succeeded_at"], 2):
                    st.warning("Collection is over two hours old.")
                articles = headlines[source.id]
                dates = [a["published_at"] for a in articles if a["published_at"]]
                if dates and older_than(max(dates), 48):
                    st.warning("Newest dated story is over 48 hours old. The feed may be stale.")
                matches = [a for a in articles if query.casefold() in (a["title"] + " " + a["summary"]).casefold()]
                if articles and not matches:
                    st.caption("No matching headlines in this outlet’s top ten.")
                for article in matches:
                    st.markdown(f"**{article['rank']:02d}**")
                    st.text(article["title"])
                    st.caption("Published: " + timestamp(article["published_at"]))
                    if article["summary"]:
                        st.text(article["summary"][:300] + ("…" if len(article["summary"]) > 300 else ""))
                    st.link_button("Read at " + source.name, article["url"])
                    if article["body"]:
                        with st.expander("Extracted article text"):
                            st.text(article["body"])
                    st.divider()

with st.expander("Source directory & how collection works"):
    st.write("Official publisher feeds supply headlines, descriptions and links. Optional Trafilatura extraction saves article text where accessible. Failed feeds never erase a previous successful collection.")
    st.dataframe([{"Outlet": s.name, "Continent": s.continent, "Language": s.language,
                   "Enabled": s.enabled, "Feed": s.feed_url, "Note": s.note} for s in SOURCES],
                 hide_index=True, width="stretch")
    st.caption("This is a starter selection, not a ranking of the most important outlets. Add more in news/sources.py. Antarctica has no comparable national news market. CNN Space is not configured: a specific publication/feed needs to be identified.")
