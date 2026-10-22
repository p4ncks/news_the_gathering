"""Exercise the Python UI with an isolated database and no network."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from news import database


class AppTests(unittest.TestCase):
    def test_filters_search_and_empty_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.db"
            with patch.dict(os.environ, {"NEWS_DB_PATH": str(path)}):
                database.initialize()
                database.save_collection("bbc", [{"url": "https://example.com/story", "title": "Climate story", "summary": "Global climate news"}])
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
                self.assertFalse(app.exception)
                self.assertEqual(app.metric[2].value, "1")
                app.sidebar.text_input[0].set_value("no-such-topic").run()
                self.assertFalse(app.exception)
                self.assertTrue(any("No matching headlines" in caption.value for caption in app.caption))
                app.sidebar.multiselect[0].set_value([]).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.metric[0].value, "0")
                self.assertTrue(any("Choose at least one" in info.value for info in app.info))


if __name__ == "__main__":
    unittest.main()
