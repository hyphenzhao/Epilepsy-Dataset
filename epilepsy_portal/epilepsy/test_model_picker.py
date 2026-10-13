from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.test import TestCase
from django.urls import reverse

from epilepsy.forms import OllamaServerForm
from epilepsy.models import OllamaServer, UserProfile, UserRole
from knowledge.agent_pipeline import AgentPipeline
from knowledge.forms import KnowledgeSettingsForm
from knowledge.models import KnowledgeSettings


class ModelPickerMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class ModelPickerCompatibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="model-picker-admin")
        UserProfile.objects.create(user=cls.admin, role=UserRole.ADMIN)
        cls.staff = get_user_model().objects.create_user(username="model-picker-staff")
        UserProfile.objects.create(user=cls.staff, role=UserRole.STAFF)
        cls.server = OllamaServer.objects.create(
            name="Model picker test", ip="ollama.test", port=11434,
            model="old-custom-report:qa", is_enabled=True,
        )
        cls.settings = KnowledgeSettings.objects.create(
            pk=1, embedding_model="old-custom-embedding:qa",
            extraction_model="old-custom-thinking:qa",
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.stats = patch("knowledge.views.collection_stats", return_value={})
        self.stats.start()
        self.addCleanup(self.stats.stop)
        self.network = patch("epilepsy.ollama_models.urllib.request.urlopen")
        self.urlopen = self.network.start()
        self.addCleanup(self.network.stop)

    def assert_picker_page(self, response, fields, server_fields):
        self.assertEqual(response.status_code, 200)
        markup = ModelPickerMarkup(response.content.decode("utf-8"))
        pickers = [attrs for tag, attrs in markup.elements if "data-ollama-model-picker" in attrs]
        self.assertEqual({attrs["data-field-name"] for attrs in pickers}, set(fields))
        for picker in pickers:
            self.assertEqual(picker["data-models-url"], reverse("epilepsy:ollama_model_list"))
            self.assertEqual(picker["data-server-fields"], str(server_fields).lower())
        for field, expected_value in fields.items():
            inputs = [attrs for tag, attrs in markup.elements
                      if tag == "input" and attrs.get("name") == field]
            self.assertEqual(len(inputs), 1)
            self.assertEqual(inputs[0].get("value", ""), expected_value)
        selects = [attrs for tag, attrs in markup.elements if "data-model-select" in attrs]
        buttons = [attrs for tag, attrs in markup.elements if "data-model-fetch" in attrs]
        self.assertEqual(len(selects), len(fields))
        self.assertEqual(len(buttons), len(fields))
        self.assertTrue(all("name" not in attrs for attrs in selects))
        self.assertTrue(all(attrs.get("type") == "button" for attrs in buttons))
        self.assertContains(response, "js/ollama-model-picker.js")
        self.urlopen.assert_not_called()

    def test_management_add_form_has_model_picker(self):
        response = self.client.get(reverse("epilepsy:user_list"))
        self.assert_picker_page(response, {"model": ""}, True)

    def test_edit_model_picker_retains_existing_custom_value(self):
        response = self.client.get(reverse("epilepsy:ollama_server_edit", args=[self.server.pk]))
        self.assert_picker_page(response, {"model": "old-custom-report:qa"}, True)

    def test_knowledge_models_have_pickers_and_keep_existing_values(self):
        response = self.client.get(reverse("knowledge:settings"))
        self.assert_picker_page(response, {
            "thinking_model": "",
            "embedding_model": "old-custom-embedding:qa",
            "extraction_model": "old-custom-thinking:qa",
        }, False)

    def test_staff_can_use_knowledge_model_pickers(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("knowledge:settings"))
        self.assert_picker_page(response, {
            "thinking_model": "",
            "embedding_model": "old-custom-embedding:qa",
            "extraction_model": "old-custom-thinking:qa",
        }, False)

    def test_manually_entered_server_model_can_be_created(self):
        response = self.client.post(reverse("epilepsy:ollama_server_create"), {
            "name": "Manual model", "ip": "manual-ollama.test", "port": 11434,
            "model": "organization/custom-report:manual", "prompt": "",
            "enable_thinking": True,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OllamaServer.objects.filter(
            name="Manual model", model="organization/custom-report:manual",
        ).exists())
        self.urlopen.assert_not_called()

    def test_edit_preserves_old_model_and_accepts_custom_model(self):
        for model in ("old-custom-report:qa", "organization/new-report:manual"):
            with self.subTest(model=model):
                data = model_to_dict(self.server, fields=OllamaServerForm._meta.fields)
                data["model"] = model
                response = self.client.post(
                    reverse("epilepsy:ollama_server_edit", args=[self.server.pk]), data,
                )
                self.assertEqual(response.status_code, 302)
                self.server.refresh_from_db()
                self.assertEqual(self.server.model, model)
        self.urlopen.assert_not_called()

    def test_knowledge_models_preserve_old_values_and_accept_custom_values(self):
        for embedding, extraction in (
            ("old-custom-embedding:qa", "old-custom-thinking:qa"),
            ("organization/custom-embedding:manual", "organization/custom-thinking:manual"),
        ):
            with self.subTest(embedding=embedding, extraction=extraction):
                data = model_to_dict(self.settings, fields=KnowledgeSettingsForm._meta.fields)
                data.update(embedding_model=embedding, extraction_model=extraction)
                response = self.client.post(reverse("knowledge:settings"), data)
                self.assertEqual(response.status_code, 302)
                self.settings.refresh_from_db()
                self.assertEqual(self.settings.embedding_model, embedding)
                self.assertEqual(self.settings.extraction_model, extraction)
        self.urlopen.assert_not_called()

    def test_agent_uses_configured_thinking_and_report_models(self):
        pipeline = AgentPipeline(SimpleNamespace(), self.server)
        self.assertEqual(pipeline.think_model, self.settings.extraction_model)
        self.assertEqual(pipeline.generate_model, self.server.model)
        self.urlopen.assert_not_called()
