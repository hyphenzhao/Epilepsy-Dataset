import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .models import OllamaServer
from .ollama_models import fetch_ollama_models, resolve_ollama_url


class PrivateOllamaRoutingTests(SimpleTestCase):
    def test_default_url_is_unchanged(self):
        server = OllamaServer(ip="192.168.50.31", port=11434)
        self.assertEqual(server.base_url, "http://192.168.50.31:11434")

    @override_settings(OLLAMA_URL_OVERRIDES={
        "http://192.168.50.31:11434": "http://127.0.0.1:11435",
    })
    def test_runtime_and_model_discovery_use_the_same_private_route(self):
        server = OllamaServer(ip="192.168.50.31", port=11434)
        self.assertEqual(server.base_url, "http://127.0.0.1:11435")
        with patch("epilepsy.ollama_models.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
                "models": [{"name": "synthetic-model:latest"}],
            }).encode()
            self.assertEqual(fetch_ollama_models("http://192.168.50.31:11434"), ["synthetic-model:latest"])
            urlopen.assert_called_once_with("http://127.0.0.1:11435/api/tags", timeout=5)
        self.assertEqual(server.ip, "192.168.50.31")

    @override_settings(OLLAMA_URL_OVERRIDES={})
    def test_unlisted_server_is_not_redirected(self):
        self.assertEqual(resolve_ollama_url("http://another-server:12345"), "http://another-server:12345")
