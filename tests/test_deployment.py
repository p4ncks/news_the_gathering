"""Production settings are checked in fresh processes, without starting a server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured

from gathering.database_config import database_settings


class DeploymentTests(unittest.TestCase):
    def settings_process(self, code, **overrides):
        environment = dict(os.environ)
        for name in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "DJANGO_SETTINGS_MODULE",
                     "NEWS_DB_ENGINE", "NEWS_DB_PATH", "DATABASE_URL"):
            environment.pop(name, None)
        environment.update(overrides)
        return subprocess.run([sys.executable, "-c", code], env=environment,
                              capture_output=True, text=True, timeout=15)

    def test_production_requires_a_secret_by_default(self):
        result = self.settings_process("import gathering.settings")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set DJANGO_SECRET_KEY", result.stderr)

    def test_development_requires_an_explicit_opt_in(self):
        result = self.settings_process(
            "from gathering.settings import DEBUG; assert DEBUG", DJANGO_DEBUG="true")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_health_probe_does_not_redirect(self):
        code = """
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'gathering.settings'
import django
django.setup()
from django.test import Client
client = Client()
assert client.get('/health/', HTTP_HOST='localhost:8000').status_code == 200
assert client.get('/', HTTP_HOST='localhost:8000').status_code == 301
"""
        result = self.settings_process(code, DJANGO_SECRET_KEY="test-only-not-a-production-key",
                                       DJANGO_SECURE_SSL_REDIRECT="true")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsupported_database_engine_fails_clearly(self):
        for engine in ("mariadb", "mysql", "oracle"):
            with self.subTest(engine=engine):
                result = self.settings_process("import gathering.settings", DJANGO_DEBUG="true",
                                               NEWS_DB_ENGINE=engine)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsupported NEWS_DB_ENGINE", result.stderr)

    def test_postgresql_engine_requires_a_database_url(self):
        result = self.settings_process("import gathering.settings", DJANGO_DEBUG="true",
                                       NEWS_DB_ENGINE="postgresql")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires DATABASE_URL", result.stderr)

    def test_web_requests_never_import_collector_dependencies(self):
        code = """
import os, sys
from gathering.wsgi import application
from django.test import Client
client = Client()
for url in ('/', '/ready/', '/health/'):
    client.get(url, HTTP_HOST='localhost:8000')
loaded = {'trafilatura', 'feedparser', 'news.collector', 'news.network'} & set(sys.modules)
assert not loaded, loaded
"""
        with tempfile.TemporaryDirectory() as directory:
            result = self.settings_process(code, DJANGO_DEBUG="true", NEWS_DB_PATH=str(Path(directory) / "news.db"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_vercel_function_is_the_wsgi_application(self):
        root = Path(__file__).resolve().parent.parent
        configuration = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(configuration["regions"], ["cle1"])
        self.assertEqual(configuration["functions"], {"gathering/wsgi.py": {"maxDuration": 30}})
        self.assertNotIn("crons", configuration)

    def test_sqlite_configuration_uses_the_requested_path(self):
        code = """
from gathering.settings import DATABASES
db = DATABASES['default']
assert db['ENGINE'] == 'django.db.backends.sqlite3'
assert db['NAME'] == '/tmp/gathering-config-test.db'
"""
        result = self.settings_process(code, DJANGO_DEBUG="true",
                                       NEWS_DB_PATH="/tmp/gathering-config-test.db")
        self.assertEqual(result.returncode, 0, result.stderr)


class DatabaseUrlTests(unittest.TestCase):
    def settings(self, url, **environment):
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            os.environ.pop("NEWS_DB_ENGINE", None)
            os.environ.update(environment)
            return database_settings()

    def test_url_is_parsed_and_credentials_are_percent_decoded(self):
        settings = self.settings("postgresql://user.ref:p%40ss%2Fw%3Ard@pooler.example.com:6543/postgres")
        self.assertEqual(settings["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual((settings["USER"], settings["PASSWORD"]), ("user.ref", "p@ss/w:rd"))
        self.assertEqual((settings["HOST"], settings["PORT"], settings["NAME"]),
                         ("pooler.example.com", "6543", "postgres"))
        self.assertEqual(settings["CONN_MAX_AGE"], 0)
        self.assertTrue(settings["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertEqual(settings["OPTIONS"], {"connect_timeout": 10, "sslmode": "require"})

    def test_postgres_scheme_alias_is_accepted(self):
        self.assertEqual(self.settings("postgres://u:p@db.example.com/news")["NAME"], "news")

    def test_ssl_is_not_required_for_localhost(self):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            with self.subTest(host=host):
                options = self.settings(f"postgresql://u@{host}:5432/news")["OPTIONS"]
                self.assertNotIn("sslmode", options)

    def test_query_string_wins_over_defaults(self):
        options = self.settings("postgresql://u:p@db.example.com/news?sslmode=verify-full&connect_timeout=3")["OPTIONS"]
        self.assertEqual(options, {"connect_timeout": "3", "sslmode": "verify-full"})
        local = self.settings("postgresql://u@localhost/news?sslmode=require")["OPTIONS"]
        self.assertEqual(local["sslmode"], "require")

    def test_database_url_wins_over_sqlite_path(self):
        settings = self.settings("postgresql://u@localhost/news", NEWS_DB_PATH="/tmp/ignored.db")
        self.assertEqual(settings["NAME"], "news")

    def test_conflicting_or_invalid_configuration_fails_without_leaking_the_password(self):
        cases = (
            ("mysql://u:hunter2@db.example.com/news", {}, "postgres or postgresql"),
            ("sqlite:///hunter2.db", {}, "postgres or postgresql"),
            ("postgresql://u:hunter2@db.example.com:port/news", {}, "malformed"),
            ("postgresql://u:hunter2@db.example.com", {}, "must name a database"),
            ("postgresql://u:hunter2@db.example.com/news", {"NEWS_DB_ENGINE": "sqlite"}, "conflicts"),
            ("postgresql://u:hunter2@db.example.com/news", {"NEWS_DB_ENGINE": "mysql"}, "Unsupported"),
        )
        for url, environment, message in cases:
            with self.subTest(url=url, environment=environment):
                with self.assertRaisesRegex(ImproperlyConfigured, message) as raised:
                    self.settings(url, **environment)
                self.assertNotIn("hunter2", str(raised.exception))

    def test_blank_url_keeps_the_sqlite_default(self):
        with patch.dict(os.environ, {"DATABASE_URL": " "}):
            os.environ.pop("NEWS_DB_ENGINE", None)
            self.assertEqual(database_settings()["ENGINE"], "django.db.backends.sqlite3")
