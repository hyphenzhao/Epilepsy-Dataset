import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from knowledge.ollama_client import DEFAULT_CHAT_MODEL, summarize_edit


class EditSummaryModelConfigurationTests(SimpleTestCase):
    @patch("knowledge.ollama_client.urllib.request.urlopen")
    @patch("knowledge.ollama_client._get_ollama_base_url", return_value="http://ollama.test:11434")
    @patch("knowledge.models.KnowledgeSettings.load")
    def test_summary_request_uses_configured_extraction_model(self, load, base_url, urlopen):
        load.return_value = SimpleNamespace(extraction_model="custom-think-model:latest")
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
            "message": {"content": "更新了报告结论。"},
        }).encode("utf-8")

        result = summarize_edit("测试原始内容", "测试修改内容")

        self.assertEqual(result, "更新了报告结论。")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://ollama.test:11434/api/chat")
        self.assertEqual(payload["model"], "custom-think-model:latest")
        self.assertFalse(payload["stream"])

    @patch("knowledge.ollama_client.urllib.request.urlopen")
    @patch("knowledge.ollama_client._get_ollama_base_url", return_value="http://ollama.test:11434")
    @patch("knowledge.models.KnowledgeSettings.load")
    def test_summary_keeps_default_when_configuration_is_empty(self, load, base_url, urlopen):
        load.return_value = SimpleNamespace(extraction_model="")
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"message":{"content":"summary"}}'

        self.assertEqual(summarize_edit("original", "edited"), "summary")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["model"], DEFAULT_CHAT_MODEL)
