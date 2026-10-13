import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings
from whitenoise.storage import CompressedStaticFilesStorage
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from epilepsy_portal.settings import base


class CloudSettingsTests(SimpleTestCase):
    def cloud_requirements(self):
        path = Path(base.__file__).resolve().parents[3] / "requirements-cloud.txt"
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        return lines, {
            canonicalize_name(requirement.name): requirement
            for line in lines if line and not line.startswith(("#", "-"))
            for requirement in [Requirement(line)]
        }

    def load_cloud(self, **environment):
        values = {"DJANGO_SECRET_KEY": "isolated-test-secret-" + "x" * 64}
        values.update(environment)
        if values.get("DJANGO_SECRET_KEY") is None:
            values.pop("DJANGO_SECRET_KEY", None)
        path = Path(base.__file__).with_name("cloud.py")
        spec = importlib.util.spec_from_file_location("epilepsy_portal.settings._cloud_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, values, clear=True):
            spec.loader.exec_module(module)
        return module

    def test_cloud_defaults_and_base_business_settings_are_preserved(self):
        original_database = base.DATABASES
        cloud = self.load_cloud(DJANGO_DEBUG="true")
        self.assertFalse(cloud.DEBUG)
        self.assertEqual(cloud.ALLOWED_HOSTS, ["8.160.183.37", "localhost", "127.0.0.1"])
        self.assertEqual(cloud.CSRF_TRUSTED_ORIGINS, ["https://8.160.183.37"])
        self.assertEqual(cloud.DATABASES["default"]["NAME"], Path("/var/lib/epilepsy-portal/db.sqlite3"))
        self.assertEqual(cloud.MEDIA_ROOT, Path("/var/lib/epilepsy-portal/media"))
        self.assertEqual(cloud.LARGE_FILE_BASE_DIR, "/Volumes/Workspace/large_files")
        self.assertEqual(cloud.INSTALLED_APPS, base.INSTALLED_APPS)
        self.assertEqual(cloud.AUTHENTICATION_BACKENDS, base.AUTHENTICATION_BACKENDS)
        self.assertEqual(cloud.LOGIN_REDIRECT_URL, base.LOGIN_REDIRECT_URL)
        self.assertIs(base.DATABASES, original_database)
        self.assertEqual(cloud.OLLAMA_URL_OVERRIDES, {})

    def test_secret_is_required_and_never_inherited_from_base(self):
        for value in (None, "", "   "):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ImproperlyConfigured, "DJANGO_SECRET_KEY"):
                    self.load_cloud(DJANGO_SECRET_KEY=value)
        cloud = self.load_cloud(DJANGO_SECRET_KEY="new-secret-from-environment")
        self.assertEqual(cloud.SECRET_KEY, "new-secret-from-environment")
        self.assertNotEqual(cloud.SECRET_KEY, base.SECRET_KEY)

    def test_environment_can_override_paths_hosts_and_csrf_origins(self):
        cloud = self.load_cloud(
            DJANGO_ALLOWED_HOSTS=" report.example.test, localhost, report.example.test ",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://report.example.test, https://other.example.test",
            DJANGO_DB_PATH="/srv/reports/database.sqlite3",
            DJANGO_MEDIA_ROOT="/srv/reports/media",
            DJANGO_LARGE_FILE_BASE_DIR="/srv/reports/large_files",
        )
        self.assertEqual(cloud.ALLOWED_HOSTS, ["report.example.test", "localhost"])
        self.assertEqual(cloud.CSRF_TRUSTED_ORIGINS, ["https://report.example.test", "https://other.example.test"])
        self.assertEqual(cloud.DATABASES["default"]["NAME"], Path("/srv/reports/database.sqlite3"))
        self.assertEqual(cloud.MEDIA_ROOT, Path("/srv/reports/media"))
        self.assertEqual(cloud.LARGE_FILE_BASE_DIR, "/srv/reports/large_files")

    def test_cloud_https_settings_redirect_http_and_trust_proxy_https(self):
        cloud = self.load_cloud()
        self.assertTrue(cloud.SESSION_COOKIE_SECURE)
        self.assertTrue(cloud.CSRF_COOKIE_SECURE)
        self.assertTrue(cloud.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(cloud.SOCIAL_AUTH_REDIRECT_IS_HTTPS)
        with override_settings(
            ALLOWED_HOSTS=cloud.ALLOWED_HOSTS,
            SECURE_SSL_REDIRECT=cloud.SECURE_SSL_REDIRECT,
            SECURE_PROXY_SSL_HEADER=cloud.SECURE_PROXY_SSL_HEADER,
        ):
            middleware = SecurityMiddleware(lambda request: HttpResponse("ok"))
            factory = RequestFactory()
            response = middleware(factory.get("/accounts/login/", HTTP_HOST="8.160.183.37"))
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.url, "https://8.160.183.37/accounts/login/")
            response = middleware(factory.get(
                "/accounts/login/", HTTP_HOST="8.160.183.37", HTTP_X_FORWARDED_PROTO="https",
            ))
            self.assertEqual(response.status_code, 200)

    def test_compressed_static_storage_does_not_require_a_manifest(self):
        cloud = self.load_cloud()
        self.assertEqual(cloud.STORAGES["staticfiles"]["BACKEND"], "whitenoise.storage.CompressedStaticFilesStorage")
        with override_settings(DEBUG=False):
            storage = CompressedStaticFilesStorage(location="/unused-test-static", base_url="/static/")
            self.assertEqual(storage.url("assets/report.js"), "/static/assets/report.js")

    def test_ollama_url_overrides_are_validated_without_database_changes(self):
        cloud = self.load_cloud(DJANGO_OLLAMA_URL_OVERRIDES=json.dumps({
            "http://192.0.2.5:11434/": "http://127.0.0.1:21134/",
            "http://[2001:db8::5]:11434": "https://models.example.test",
        }))
        self.assertEqual(cloud.OLLAMA_URL_OVERRIDES, {
            "http://192.0.2.5:11434": "http://127.0.0.1:21134",
            "http://[2001:db8::5]:11434": "https://models.example.test",
        })
        for value in (
            "invalid JSON", "[]", "null", '{"source": 1}',
            '{"http://models.example.test": ""}',
            '{"http://models.example.test": "ftp://localhost:21"}',
            '{"http://models.example.test": "http://localhost:0"}',
            '{"http://models.example.test": "http://localhost:99999"}',
            '{"http://models.example.test": "http://user:password@localhost"}',
            '{"http://models.example.test": "http://localhost/api/chat"}',
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    self.load_cloud(DJANGO_OLLAMA_URL_OVERRIDES=value)

    def test_cloud_requirements_are_independent_runtime_dependencies(self):
        lines, requirements = self.cloud_requirements()
        self.assertFalse(any(line.startswith("-") for line in lines))
        expected = {
            "django", "django-globus-portal-framework", "djangorestframework",
            "social-auth-app-django", "social-auth-core", "globus-sdk", "python-jose",
            "requests", "requests-oauthlib", "markdown", "lxml", "pillow", "psutil",
            "python-docx", "reportlab", "whitenoise", "gunicorn", "chromadb",
            "pdfplumber", "pypinyin", "python-dateutil",
        }
        self.assertTrue(expected.issubset(requirements))
        legacy = {"screen", "django-admin", "django-excel-response2", "excel-base", "django-six", "timeconvert", "cookiecutter", "xlwt"}
        self.assertFalse(legacy.intersection(requirements))

    def test_cloud_requirements_keep_source_pins_and_the_secure_django_lts_patch(self):
        _, requirements = self.cloud_requirements()
        path = Path(base.__file__).resolve().parents[3] / "requirements.txt"
        source = {
            canonicalize_name(requirement.name): requirement
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "-"))
            for requirement in [Requirement(line)]
        }
        self.assertEqual(str(requirements["django"].specifier), "==5.2.18")
        self.assertEqual(str(source["django"].specifier), "==5.2.8")
        for name, requirement in requirements.items():
            if name != "django" and name in source:
                with self.subTest(package=name):
                    self.assertEqual(requirement.specifier, source[name].specifier)

    def test_cloud_dependency_graph_does_not_pull_the_legacy_screen_chain(self):
        _, requirements = self.cloud_requirements()
        pending = list(requirements.values())
        visited = set()
        names = set()
        while pending:
            requirement = pending.pop()
            name = canonicalize_name(requirement.name)
            key = (name, tuple(sorted(requirement.extras)))
            if key in visited:
                continue
            visited.add(key)
            names.add(name)
            distribution = metadata.distribution(name)
            for text in distribution.requires or ():
                dependency = Requirement(text)
                extras = requirement.extras or {""}
                if dependency.marker is None or any(dependency.marker.evaluate({
                    "extra": extra, "python_version": "3.14", "python_full_version": "3.14.4",
                    "sys_platform": "linux", "os_name": "posix",
                }) for extra in extras):
                    pending.append(dependency)
        self.assertFalse(names.intersection({"screen", "django-admin", "django-excel-response2", "excel-base", "timeconvert", "cookiecutter"}))
