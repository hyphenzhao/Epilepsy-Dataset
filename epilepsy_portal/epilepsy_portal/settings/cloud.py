"""Cloud deployment settings; local development continues to use base.py."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *


def _csv_environment(name, default):
    values = [value.strip() for value in os.environ.get(name, default).split(",") if value.strip()]
    if not values:
        raise ImproperlyConfigured(f"{name} must contain at least one value.")
    return list(dict.fromkeys(values))


DEBUG = False
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set for cloud deployment.")

ALLOWED_HOSTS = _csv_environment("DJANGO_ALLOWED_HOSTS", "8.160.183.37,localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _csv_environment("DJANGO_CSRF_TRUSTED_ORIGINS", "https://8.160.183.37")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("DJANGO_DB_PATH", "/var/lib/epilepsy-portal/db.sqlite3")),
    }
}
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", "/var/lib/epilepsy-portal/media"))
MEDIA_URL = "/media/"
LARGE_FILE_BASE_DIR = os.environ.get("DJANGO_LARGE_FILE_BASE_DIR", "/Volumes/Workspace/large_files")

# Nginx must replace this header and proxy only to a private Gunicorn listener.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SOCIAL_AUTH_REDIRECT_IS_HTTPS = True

# Compression retains ordinary asset URLs, so a missing manifest cannot break
# template rendering after deployment or when an asset is added.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
STATICFILES_STORAGE = STORAGES["staticfiles"]["BACKEND"]


def _ollama_url_overrides():
    try:
        overrides = json.loads(os.environ.get("DJANGO_OLLAMA_URL_OVERRIDES", "{}"))
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("DJANGO_OLLAMA_URL_OVERRIDES must be a JSON object of server URLs.") from exc
    if not isinstance(overrides, dict):
        raise ImproperlyConfigured("DJANGO_OLLAMA_URL_OVERRIDES must be a JSON object of server URLs.")
    normalized = {}
    for source, target in overrides.items():
        for address in (source, target):
            if (
                not isinstance(address, str) or not address.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in address)
            ):
                raise ImproperlyConfigured("Ollama URL overrides must map nonempty URL strings to URL strings.")
            try:
                parsed = urlsplit(address.strip())
                valid = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and not any(character.isspace() for character in parsed.hostname)
                    and parsed.username is None and parsed.password is None
                    and parsed.path in {"", "/"}
                    and not parsed.query and not parsed.fragment
                    and (parsed.port is None or 1 <= parsed.port <= 65535)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ImproperlyConfigured("Ollama URL overrides require HTTP(S) server URLs without paths or credentials.")
        normalized[source.strip().rstrip("/")] = target.strip().rstrip("/")
    return normalized


OLLAMA_URL_OVERRIDES = _ollama_url_overrides()
