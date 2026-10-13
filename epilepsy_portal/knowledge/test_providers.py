from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import connection
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.forms.models import model_to_dict
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings

from epilepsy.llm import LLMConfig, LLMError
from .models import KnowledgeSettings
from .forms import KnowledgeSettingsForm
from .provider_config import get_role_config
from .ollama_client import ollama_chat_json, summarize_edit
from . import embeddings


@override_settings(OLLAMA_URL_OVERRIDES={})
class RoleConfigTests(SimpleTestCase):
    def settings(self, **overrides):
        fields = dict(
            thinking_provider="", thinking_api_base_url="", thinking_api_key="", thinking_model="",
            extraction_provider="", extraction_api_base_url="", extraction_api_key="", extraction_model="legacy-thinker",
            embedding_provider="ollama", embedding_api_base_url="", embedding_api_key="", embedding_model="nomic-embed-text",
        )
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def test_chat_role_inherits_active_server_and_legacy_model(self):
        active = LLMConfig("openai", "https://active.invalid/v1", "synthetic-active-key", "report-model")
        with patch("knowledge.provider_config.get_active_server_config", return_value=active):
            config = get_role_config("thinking", self.settings())
        self.assertEqual((config.provider, config.base_url, config.model), ("openai", "https://active.invalid/v1", "legacy-thinker"))
        self.assertEqual(config.api_key, "synthetic-active-key")

    def test_roles_can_select_independent_providers_and_models(self):
        settings = self.settings(thinking_provider="anthropic", thinking_model="synthetic-thinking",
                                 thinking_api_base_url="https://thinking.invalid/v1", thinking_api_key="synthetic-think-key",
                                 extraction_provider="openai", extraction_api_base_url="https://extract.invalid/v1",
                                 extraction_model="synthetic-extraction", extraction_api_key="synthetic-extract-key")
        with patch("knowledge.provider_config.get_active_server_config", return_value=LLMConfig(model="report-model")):
            thinking = get_role_config("thinking", settings)
            extraction = get_role_config("extraction", settings)
        self.assertEqual((thinking.provider, thinking.model), ("anthropic", "synthetic-thinking"))
        self.assertEqual((extraction.provider, extraction.model), ("openai", "synthetic-extraction"))
        self.assertEqual(thinking.api_key, "synthetic-think-key")
        self.assertEqual(extraction.api_key, "synthetic-extract-key")

    def test_custom_endpoint_does_not_receive_inherited_server_key(self):
        active = LLMConfig("openai", "https://active.invalid/v1", "synthetic-active-key", "report-model")
        with patch("knowledge.provider_config.get_active_server_config", return_value=active):
            config = get_role_config("extraction", self.settings(extraction_api_base_url="https://other.invalid/v1"))
        self.assertEqual(config.api_key, "")

    def test_blank_thinking_group_inherits_complete_extraction_config(self):
        active = LLMConfig("openai", "https://report.invalid/v1", "synthetic-report-key", "report-model")
        settings = self.settings(extraction_provider="anthropic", extraction_api_base_url="https://extract.invalid/v1",
                                 extraction_api_key="synthetic-extract-key", extraction_model="claude-synthetic")
        with patch("knowledge.provider_config.get_active_server_config", return_value=active):
            config = get_role_config("thinking", settings)
        self.assertEqual((config.provider, config.base_url, config.api_key, config.model),
                         ("anthropic", "https://extract.invalid/v1", "synthetic-extract-key", "claude-synthetic"))
        settings.thinking_model = "custom-model"
        with patch("knowledge.provider_config.get_active_server_config", return_value=active):
            custom_model = get_role_config("thinking", settings)
        self.assertEqual((custom_model.provider, custom_model.model), ("anthropic", "custom-model"))

    def test_anthropic_report_does_not_change_default_embedding_provider(self):
        with patch("knowledge.provider_config.get_active_server_config", return_value=LLMConfig("anthropic", "", "synthetic-key", "report-model")):
            config = get_role_config("embedding", self.settings())
        self.assertEqual((config.provider, config.base_url, config.model), ("ollama", "http://localhost:11434", "nomic-embed-text"))
        self.assertEqual(config.api_key, "")

    def test_anthropic_embedding_is_rejected_before_any_service_lookup(self):
        with patch("knowledge.provider_config.get_active_server_config") as active:
            with self.assertRaisesRegex(ValueError, "Anthropic.*Embedding"):
                get_role_config("embedding", self.settings(embedding_provider="anthropic"))
        active.assert_not_called()

    def test_unbound_role_key_is_rejected_without_any_service_lookup(self):
        for change in ({"thinking_api_key": "synthetic-key"},
                       {"thinking_provider": "openai", "thinking_api_key": "synthetic-key"}):
            with self.subTest(change=change), patch("knowledge.provider_config.get_active_server_config") as active:
                with self.assertRaisesRegex(ValueError, "必须绑定"):
                    get_role_config("thinking", self.settings(**change))
                active.assert_not_called()


@override_settings(OLLAMA_URL_OVERRIDES={})
class KnowledgeCredentialFormTests(TestCase):
    def setUp(self):
        self.settings = KnowledgeSettings.objects.create(
            extraction_provider="openai", extraction_api_base_url="https://proxy.invalid/v1",
            extraction_api_key="synthetic-existing-key",
        )

    def data(self, **overrides):
        data = model_to_dict(self.settings)
        for role in ("thinking", "extraction", "embedding"):
            data[role + "_api_key"] = ""
        data.update(overrides)
        return data

    def test_key_encrypted_in_database_and_not_rendered(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT extraction_api_key FROM knowledge_knowledgesettings WHERE id=%s", [self.settings.pk])
            stored = cursor.fetchone()[0]
        self.assertTrue(stored.startswith("llm-key-v1:"))
        self.assertNotIn("synthetic-existing-key", stored)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.extraction_api_key, "synthetic-existing-key")
        form = KnowledgeSettingsForm(instance=self.settings)
        html = render_to_string("knowledge/settings.html", {"form": form, "settings": self.settings, "stats": {}})
        self.assertNotIn("synthetic-existing-key", html)
        for role in ("thinking", "extraction", "embedding"):
            self.assertIn('data-llm-config-role="' + role + '"', html)

    def test_blank_key_preserved_at_same_normalized_endpoint(self):
        form = KnowledgeSettingsForm(self.data(extraction_api_base_url="https://proxy.invalid/v1/"), instance=self.settings)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().extraction_api_key, "synthetic-existing-key")

    def test_provider_or_endpoint_change_clears_old_key(self):
        for change in ({"extraction_provider": "anthropic"}, {"extraction_api_base_url": "https://new.invalid/v1"}):
            with self.subTest(change=change):
                instance = KnowledgeSettings.objects.get(pk=self.settings.pk)
                form = KnowledgeSettingsForm(self.data(**change), instance=instance)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["extraction_api_key"], "")
                self.assertTrue(form.credentials_cleared)

    def test_explicit_clear_and_replacement_key(self):
        form = KnowledgeSettingsForm(self.data(clear_extraction_api_key="on"), instance=self.settings)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["extraction_api_key"], "")
        form = KnowledgeSettingsForm(self.data(extraction_api_key="synthetic-new-key"), instance=self.settings)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().extraction_api_key, "synthetic-new-key")

    def test_role_key_pins_inherited_destination_before_report_server_changes(self):
        active = LLMConfig("anthropic", "https://active.invalid/v1", "synthetic-report-key", "report-model")
        with patch("knowledge.provider_config.get_active_server_config", return_value=active):
            form = KnowledgeSettingsForm(self.data(thinking_provider="", thinking_api_base_url="", thinking_api_key="synthetic-own-key"), instance=self.settings)
            self.assertTrue(form.is_valid(), form.errors)
            saved = form.save()
        self.assertEqual((saved.thinking_provider, saved.thinking_api_base_url), ("openai", "https://proxy.invalid/v1"))
        changed = LLMConfig("openai", "https://different.invalid/v1", "synthetic-different-key", "other-report")
        with patch("knowledge.provider_config.get_active_server_config", return_value=changed):
            config = get_role_config("thinking", settings=saved)
        self.assertEqual((config.provider, config.base_url, config.api_key), ("openai", "https://proxy.invalid/v1", "synthetic-own-key"))

    def test_same_page_thinking_key_pins_to_new_extraction_destination(self):
        form = KnowledgeSettingsForm(self.data(
            extraction_provider="anthropic", extraction_api_base_url="https://new-extract.invalid/v1",
            extraction_api_key="synthetic-new-extract-key", extraction_model="claude-synthetic",
            thinking_provider="", thinking_api_base_url="", thinking_api_key="synthetic-own-think-key",
        ), instance=self.settings)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual((saved.thinking_provider, saved.thinking_api_base_url, saved.thinking_api_key),
                         ("anthropic", "https://new-extract.invalid/v1", "synthetic-own-think-key"))
        self.assertEqual((saved.extraction_provider, saved.extraction_api_key), ("anthropic", "synthetic-new-extract-key"))

    def test_invalid_extraction_url_with_new_inherited_thinking_key_is_validation_error(self):
        form = KnowledgeSettingsForm(self.data(
            extraction_api_base_url="https://name:password@invalid.test/v1",
            thinking_provider="", thinking_api_base_url="", thinking_api_key="synthetic-new-think-key",
        ), instance=self.settings)
        self.assertFalse(form.is_valid())
        self.assertIn("extraction_api_base_url", form.errors)
        self.assertIn("thinking_api_key", form.errors)

    def test_non_ascii_and_control_characters_in_role_keys_are_rejected(self):
        for key in ("synthetic\nkey", "synthetic key", "synthetic非ASCII"):
            with self.subTest(key=key):
                form = KnowledgeSettingsForm(self.data(extraction_api_key=key), instance=self.settings)
                self.assertFalse(form.is_valid())
                self.assertIn("extraction_api_key", form.errors)

    def test_embedding_change_marks_rebuild_without_touching_vectors(self):
        with patch("knowledge.vector_store.delete_collection") as delete:
            form = KnowledgeSettingsForm(self.data(embedding_provider="openai", embedding_model="text-embedding-synthetic"), instance=self.settings)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.assertTrue(form.embedding_config_changed)
        delete.assert_not_called()

    def test_settings_save_shows_embedding_rebuild_warning(self):
        from epilepsy.models import UserProfile, UserRole
        user = User.objects.create_user("synthetic-knowledge-admin", password="synthetic-test-password")
        UserProfile.objects.update_or_create(user=user, defaults={"role": UserRole.ADMIN})
        self.client.force_login(user)
        with patch("knowledge.views.collection_stats", return_value={"cases_count": 9, "literature_count": 0}), \
                patch("knowledge.vector_store.delete_collection") as delete:
            response = self.client.post("/knowledge/settings/", self.data(embedding_provider="openai", embedding_model="new-embedding"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("重建索引" in str(message) for message in get_messages(response.wsgi_request)))
        delete.assert_not_called()

    def test_invalid_embedding_provider_and_credential_url_are_rejected(self):
        for change in ({"embedding_provider": "anthropic"}, {"extraction_api_base_url": "https://name:password@host.invalid/v1"}):
            with self.subTest(change=change):
                form = KnowledgeSettingsForm(self.data(**change), instance=self.settings)
                self.assertFalse(form.is_valid())


class ProviderCallTests(SimpleTestCase):
    def test_thinking_json_uses_provider_adapter_and_role(self):
        config = LLMConfig("anthropic", "", "synthetic-key", "synthetic-thinking")
        with patch("knowledge.ollama_client.get_role_config", return_value=config) as resolver, \
                patch("knowledge.ollama_client.chat_completion", return_value='```json\n{"issues": []}\n```') as chat:
            result = ollama_chat_json([{"role": "user", "content": "Synthetic task"}], role="thinking")
        self.assertEqual(result, {"issues": []})
        resolver.assert_called_once_with("thinking", model=None)
        self.assertEqual(chat.call_args.args[0], config)
        self.assertTrue(chat.call_args.kwargs["format_json"])

    def test_summary_uses_extraction_role_and_not_hardcoded_model(self):
        with patch("knowledge.ollama_client.get_role_config", return_value=LLMConfig("openai", "", "synthetic-key", "custom-summary")) as resolver, \
                patch("knowledge.ollama_client.chat_completion", return_value="Synthetic summary"):
            self.assertEqual(summarize_edit("Synthetic original", "Synthetic edit"), "Synthetic summary")
        resolver.assert_called_once_with("extraction", model=None)

    def test_invalid_json_array_uses_fallback(self):
        with patch("knowledge.ollama_client.ollama_chat", return_value={"message": {"content": "[]"}}):
            self.assertEqual(ollama_chat_json([], fallback={"issues": []}), {"issues": []})

    def test_strict_thinking_rejects_invalid_json_and_api_failure_safely(self):
        for result in ("[]", "Synthetic non-JSON output"):
            with self.subTest(result=result), patch("knowledge.ollama_client.ollama_chat", return_value={"message": {"content": result}}):
                with self.assertRaisesRegex(LLMError, "有效的 JSON"):
                    ollama_chat_json([], role="thinking", strict=True)
        with patch("knowledge.ollama_client.ollama_chat", side_effect=RuntimeError("synthetic-private-input-echo")):
            with self.assertRaises(LLMError) as error:
                ollama_chat_json([], role="thinking", strict=True)
        self.assertNotIn("synthetic-private-input-echo", str(error.exception))

    def test_strict_json_preserves_provider_error_classification(self):
        provider_error = LLMError("API 认证失败。", code="authentication")
        with patch("knowledge.ollama_client.ollama_chat", side_effect=provider_error):
            with self.assertRaises(LLMError) as caught:
                ollama_chat_json([], role="thinking", strict=True)
        self.assertIs(caught.exception, provider_error)
        self.assertEqual(caught.exception.code, "authentication")

    def test_metadata_types_use_extraction_config(self):
        from .metadata_extractor import extract_metadata
        config = LLMConfig("openai", "https://extraction.invalid/v1", "synthetic-key", "custom-extraction")
        for doc_type in ("case_report", "literature", "guideline"):
            with self.subTest(doc_type=doc_type), \
                    patch("knowledge.ollama_client.get_role_config", return_value=config) as resolver, \
                    patch("knowledge.ollama_client.chat_completion", return_value='{"patient_age":"synthetic","core_thesis":"Synthetic thesis"}') as chat:
                self.assertIsInstance(extract_metadata("Synthetic document", doc_type), dict)
            resolver.assert_called_once_with("extraction", model=None)
            self.assertEqual(chat.call_args.args[0], config)

    def test_embeddings_use_independent_config(self):
        config = LLMConfig("openai", "https://embed.invalid/v1", "synthetic-embed-key", "embedding-model")
        with patch("knowledge.embeddings.get_role_config", return_value=config) as resolver, \
                patch("knowledge.embeddings.provider_embed_text", return_value=[0.1, 0.2]) as embed:
            self.assertEqual(embeddings.embed_text("Synthetic text"), [0.1, 0.2])
        resolver.assert_called_once_with("embedding", model=None)
        embed.assert_called_once_with(config, "Synthetic text", timeout=120)

    def test_dimensions_are_isolated_by_provider_endpoint_and_model(self):
        configs = [LLMConfig("ollama", "http://one.invalid:11434", "", "nomic-embed-text"),
                   LLMConfig("openai", "https://one.invalid/v1", "", "nomic-embed-text"),
                   LLMConfig("openai", "https://two.invalid/v1", "", "nomic-embed-text")]
        embeddings._DIMENSION_CACHE.clear()
        with patch("knowledge.embeddings.provider_embed_text", side_effect=[[0.0]*3, [0.0]*5]) as embed:
            for config, expected in zip(configs, (768, 3, 5)):
                with patch("knowledge.embeddings.get_role_config", return_value=config):
                    self.assertEqual(embeddings.embedding_dimension(), expected)
                    self.assertEqual(embeddings.embedding_dimension(), expected)
        self.assertEqual(embed.call_count, 2)
        embeddings._DIMENSION_CACHE.clear()

    def test_collection_statistics_never_probe_embedding_api(self):
        from .vector_store import collection_stats
        collection = Mock()
        collection.count.return_value = 9
        client = Mock()
        client.get_collection.return_value = collection
        with patch("knowledge.vector_store._get_client", return_value=client), \
                patch("knowledge.embeddings.provider_embed_text") as embed:
            self.assertEqual(collection_stats(), {"cases_count": 9, "literature_count": 9})
        embed.assert_not_called()
