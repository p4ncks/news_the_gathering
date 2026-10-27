"""Production settings are checked in fresh processes, without starting a server."""
import os
import subprocess
import sys
import unittest


class DeploymentTests(unittest.TestCase):
    def settings_process(self, code, **overrides):
        environment = dict(os.environ)
        for name in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "DJANGO_SETTINGS_MODULE",
                     "NEWS_DB_ENGINE", "NEWS_DB_PATH"):
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
        for engine in ("mariadb", "mysql", "postgresql"):
            with self.subTest(engine=engine):
                result = self.settings_process("import gathering.settings", DJANGO_DEBUG="true",
                                               NEWS_DB_ENGINE=engine)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("This release supports SQLite only", result.stderr)

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
