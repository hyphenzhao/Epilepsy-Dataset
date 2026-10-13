import json
import urllib.error
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import OllamaServer, UserProfile, UserRole
from .views import check_ollama_server_available


class OllamaModelListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {}
        for role in (UserRole.ADMIN, UserRole.STAFF, UserRole.GUEST):
            user = get_user_model().objects.create_user(username=f"models_{role.lower()}")
            UserProfile.objects.create(user=user, role=role)
            cls.users[role] = user
        cls.unprofiled_user = get_user_model().objects.create_user(username="models_unprofiled")

    def setUp(self):
        self.url = reverse("epilepsy:ollama_model_list")
        self.client.force_login(self.users[UserRole.ADMIN])
        network_patch = patch("epilepsy.ollama_models.urllib.request.urlopen")
        self.network = network_patch.start()
        self.addCleanup(network_patch.stop)
        self.set_payload({"models": []})

    def set_payload(self, payload):
        self.network.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")

    def test_anonymous_is_redirected_and_get_is_not_allowed(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login") + "?next="))
        self.client.force_login(self.users[UserRole.ADMIN])
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.network.assert_not_called()

    def test_guest_and_users_without_profiles_cannot_query_models(self):
        for user in (self.users[UserRole.GUEST], self.unprofiled_user):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(self.url)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["status"], "error")
        self.network.assert_not_called()

    def test_staff_cannot_query_an_explicit_address(self):
        self.client.force_login(self.users[UserRole.STAFF])
        for data in ({"ip": "localhost", "port": 11434}, {"ip": "localhost"}, {"port": 11434}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url, data).status_code, 403)
        self.network.assert_not_called()

    def test_current_enabled_server_is_used_by_admin_and_staff(self):
        OllamaServer.objects.create(name="Disabled", ip="unused.local", port=11434, model="unused", is_enabled=False)
        OllamaServer.objects.create(name="Active", ip="ollama.local", port=12345, model="qwen:7b", is_enabled=True)
        self.set_payload({"models": [{"name": "qwen:7b"}]})
        for role in (UserRole.ADMIN, UserRole.STAFF):
            with self.subTest(role=role):
                self.client.force_login(self.users[role])
                response = self.client.post(self.url)
                self.assertEqual(response.json(), {"status": "ok", "models": ["qwen:7b"], "server": "http://ollama.local:12345"})
                self.network.assert_called_with("http://ollama.local:12345/api/tags", timeout=5)

    def test_default_server_and_empty_models_are_successful(self):
        self.client.force_login(self.users[UserRole.STAFF])
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "models": [], "server": "http://localhost:11434"})
        self.network.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)

    def test_model_names_are_normalized_and_invalid_entries_are_ignored(self):
        self.set_payload({"models": [
            {"name": " qwen:7b "}, {"model": "embed:latest"}, {"name": "qwen:7b"},
            {"name": 123, "model": "fallback:latest"}, {"name": "", "model": "embed:latest"},
            {"name": []}, {"name": "bad name"}, {"name": "bad\x00name"}, {}, None, "invalid", 7,
        ]})
        response = self.client.post(self.url, {"ip": "127.0.0.1", "port": 11434})
        self.assertEqual(response.json()["models"], ["qwen:7b", "embed:latest", "fallback:latest"])

    def test_valid_hosts_and_ports(self):
        for host, port, server in [
            ("192.168.50.94", 11434, "http://192.168.50.94:11434"),
            ("Ollama-Server.LOCAL", 1, "http://ollama-server.local:1"),
            ("localhost", 65535, "http://localhost:65535"),
            ("::1", 11434, "http://[::1]:11434"),
            ("[2001:db8::1]", 11434, "http://[2001:db8::1]:11434"),
        ]:
            with self.subTest(host=host, port=port):
                response = self.client.post(self.url, {"ip": host, "port": port})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["server"], server)
                self.network.assert_called_with(server + "/api/tags", timeout=5)

    def test_invalid_addresses_do_not_make_network_requests(self):
        for data in [
            {"ip": "localhost"}, {"port": 11434}, {"ip": "", "port": 11434},
            *[{"ip": host, "port": 11434} for host in (
                "http://localhost", "localhost/path", "user@localhost", "localhost:11434",
                "localhost?query=1", "localhost#fragment", "999.999.999.999", "[localhost]",
                "local host", "localhost\\path", "-bad.local",
            )],
            *[{"ip": "localhost", "port": port} for port in (0, 65536, -1, "bad", "1.5", "")],
        ]:
            with self.subTest(data=data):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["status"], "error")
        self.network.assert_not_called()

    def test_network_errors_and_timeouts_have_json_errors(self):
        for error, status in [
            (urllib.error.URLError("Connection refused"), 502),
            (urllib.error.HTTPError("http://localhost/api/tags", 503, "Unavailable", {}, None), 502),
            (TimeoutError("timed out"), 504),
            (urllib.error.URLError(TimeoutError("timed out")), 504),
        ]:
            with self.subTest(error=type(error).__name__, status=status):
                self.network.side_effect = error
                response = self.client.post(self.url)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["status"], "error")
                self.assertTrue(response.json()["error"])

    def test_malformed_responses_are_reported(self):
        for body in (b"not JSON", b"\xff", b"[]", b"{}", b'{"models": {}}'):
            with self.subTest(body=body):
                self.network.return_value.__enter__.return_value.read.return_value = body
                response = self.client.post(self.url)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["status"], "error")

    def test_csrf_protection_is_required(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.users[UserRole.ADMIN])
        self.assertEqual(csrf_client.post(self.url).status_code, 403)
        self.network.assert_not_called()
        token = "a" * 32
        csrf_client.cookies[settings.CSRF_COOKIE_NAME] = token
        self.assertEqual(csrf_client.post(self.url, HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.network.assert_called_once()

    def test_existing_server_availability_check_uses_the_shared_client(self):
        server = OllamaServer(ip="::1", port=11434, model="qwen:7b")
        self.assertEqual(server.base_url, "http://[::1]:11434")
        self.set_payload({"models": [{"model": "qwen:7b"}, {"name": "qwen:7b"}]})
        self.assertEqual(check_ollama_server_available(server, timeout=3), (True, ["qwen:7b"]))
        self.network.assert_called_once_with("http://[::1]:11434/api/tags", timeout=3)
