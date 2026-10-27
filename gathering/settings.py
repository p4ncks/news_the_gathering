"""Small Django configuration. Production values come from the environment."""
import os
import math
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .database_config import database_settings

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("true", "1", "yes")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY when DJANGO_DEBUG=false.")
    SECRET_KEY = "django-insecure-local-development-only-news-the-gathering"

ALLOWED_HOSTS = [host.strip() for host in os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"
).split(",") if host.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get(
    "DJANGO_CSRF_TRUSTED_ORIGINS", ""
).split(",") if origin.strip()]

INSTALLED_APPS = ["django.contrib.staticfiles", "dashboard", "news"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "gathering.urls"
WSGI_APPLICATION = "gathering.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
}]

# Both collector and web use the same models; web requests never run migrations.
DATABASES = {"default": database_settings()}
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("pt-br", "Português")]
USE_I18N = True
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_SECURE = not DEBUG
LANGUAGE_COOKIE_SAMESITE = "Lax"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
# Enable HTTPS redirects after configuring a TLS reverse proxy. Trust forwarded
# protocol headers only when that proxy strips incoming client-supplied headers.
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "false").lower() == "true"
# A local container probe must stay HTTP even when the public site requires TLS.
# This endpoint returns only process liveness, never application data.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
if os.environ.get("DJANGO_TRUST_PROXY", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

NEWS_FRESHNESS_HOURS = float(os.environ.get("NEWS_FRESHNESS_HOURS", "2"))
NEWS_MIN_FRESH_SOURCES = int(os.environ.get("NEWS_MIN_FRESH_SOURCES", "1"))
if not math.isfinite(NEWS_FRESHNESS_HOURS) or NEWS_FRESHNESS_HOURS <= 0 or NEWS_MIN_FRESH_SOURCES < 1:
    raise ImproperlyConfigured("News freshness hours and minimum fresh sources must be positive.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
