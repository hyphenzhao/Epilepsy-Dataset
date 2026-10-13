import io
import json
from email.message import Message
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import urllib.request
from urllib.response import addinfourl
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.forms.models import model_to_dict
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from epilepsy.llm import (
    LLMConfig, LLMError, chat_completion, embed_text, fetch_models,
    normalize_base_url, stream_chat,
)


FAKE_KEY = "synthetic-key-for-mocked-tests-only"
FAKE_PROMPT = "synthetic-private-prompt-never-send"
MESSAGES = [{"role": "user", "content": FAKE_PROMPT}]


class HTTPFixture(io.BytesIO):
    status = 200

    def getcode(self):
        return self.status


def json_response(payload):
    return HTTPFixture(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def sse_response(events, *, done=False):
    frames = []
    for event in events:
        data = json.dumps(event, ensure_ascii=False)
        kind = event.get("type")
        prefix = f"event: {kind}\r\n" if kind else ""
        frames.append(prefix + "data: " + data + "\r\n\r\n")
    if done:
        frames.append("data: [DONE]\r\n\r\n")
    return HTTPFixture((': keepalive\r\n\r\n' + ''.join(frames)).encode("utf-8"))


def openai_events(*, finish_reason="stop"):
    return [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": "合成思考"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "合成正文"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]},
        {"choices": [], "usage": {"completion_tokens": 12}},
    ]


def anthropic_events(*, stop_reason="end_turn", include_stop=True):
    events = [
        {"type": "message_start", "message": {"role": "assistant", "content": [], "stop_reason": None}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "合成思考"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "synthetic-signature"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "合成正文"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": 12}},
    ]
    if include_stop:
        events.append({"type": "message_stop"})
    return events


class LLMProviderProtocolTests(SimpleTestCase):
    def config(self, provider, *, model="synthetic-model"):
        base = "http://ollama.test:11434" if provider == "ollama" else "https://provider.test/v1"
        return LLMConfig(provider, base, "" if provider == "ollama" else FAKE_KEY, model)

    def request_payload(self, urlopen):
        request = urlopen.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        payload = json.loads(request.data.decode("utf-8")) if request.data else None
        return request, headers, payload

    def assert_no_key_in_payload(self, payload):
        self.assertNotIn(FAKE_KEY, json.dumps(payload, ensure_ascii=False))

    def assert_no_sensitive_error(self, error):
        self.assertNotIn(FAKE_KEY, str(error))
        self.assertNotIn(FAKE_PROMPT, str(error))

    @patch("epilepsy.llm.urlopen")
    def test_ollama_chat_keeps_local_protocol_without_auth(self, urlopen):
        urlopen.return_value = json_response({"message": {"content": "合成回答"}, "done": True})
        self.assertEqual(chat_completion(self.config("ollama"), MESSAGES), "合成回答")
        request, headers, payload = self.request_payload(urlopen)
        self.assertEqual(request.full_url, "http://ollama.test:11434/api/chat")
        self.assertNotIn("authorization", headers)
        self.assertNotIn("x-api-key", headers)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"], MESSAGES)

    @patch("epilepsy.llm.urlopen")
    def test_openai_chat_uses_bearer_header_and_json_output(self, urlopen):
        urlopen.return_value = json_response({"choices": [{
            "message": {"content": '{"synthetic":true}'}, "finish_reason": "stop",
        }]})
        self.assertEqual(chat_completion(self.config("openai"), MESSAGES, format_json=True), '{"synthetic":true}')
        request, headers, payload = self.request_payload(urlopen)
        self.assertEqual(request.full_url, "https://provider.test/v1/chat/completions")
        self.assertEqual(headers["authorization"], "Bearer " + FAKE_KEY)
        self.assertNotIn("response_format", payload)
        self.assertIn("JSON", payload["messages"][-1]["content"])
        self.assertEqual(payload["messages"][0], MESSAGES[0])
        self.assert_no_key_in_payload(payload)

    @patch("epilepsy.llm.urlopen")
    def test_anthropic_chat_separates_system_and_text_blocks(self, urlopen):
        urlopen.return_value = json_response({
            "content": [{"type": "thinking", "thinking": "隐藏合成推理"},
                        {"type": "text", "text": "合成回答"}],
            "stop_reason": "end_turn",
        })
        messages = [{"role": "system", "content": "合成系统指令一"},
                    {"role": "system", "content": "合成系统指令二"}] + MESSAGES
        self.assertEqual(chat_completion(self.config("anthropic"), messages), "合成回答")
        request, headers, payload = self.request_payload(urlopen)
        self.assertEqual(request.full_url, "https://provider.test/v1/messages")
        self.assertEqual(headers["x-api-key"], FAKE_KEY)
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertNotIn("authorization", headers)
        self.assertIn("合成系统指令一", json.dumps(payload["system"], ensure_ascii=False))
        self.assertIn("合成系统指令二", json.dumps(payload["system"], ensure_ascii=False))
        self.assertTrue(all(item["role"] != "system" for item in payload["messages"]))
        self.assertEqual(payload["max_tokens"], 8192)
        self.assert_no_key_in_payload(payload)

    @patch("epilepsy.llm.urlopen")
    def test_openai_stream_normalizes_reasoning_text_and_done(self, urlopen):
        urlopen.return_value = sse_response(openai_events(), done=True)
        chunks = list(stream_chat(self.config("openai"), MESSAGES))
        self.assertEqual(''.join(chunk.get("message", {}).get("thinking", "") for chunk in chunks), "合成思考")
        self.assertEqual(''.join(chunk.get("message", {}).get("content", "") for chunk in chunks), "合成正文")
        self.assertTrue(chunks[-1].get("done"))
        self.assertEqual(sum(bool(chunk.get("done")) for chunk in chunks), 1)
        self.assertNotIn(FAKE_KEY, json.dumps(chunks))
        self.assertNotIn(FAKE_PROMPT, json.dumps(chunks))

    @patch("epilepsy.llm.urlopen")
    def test_anthropic_stream_keeps_thinking_out_of_report_text(self, urlopen):
        urlopen.return_value = sse_response(anthropic_events())
        chunks = list(stream_chat(self.config("anthropic"), MESSAGES, think=True))
        self.assertEqual(''.join(chunk.get("message", {}).get("thinking", "") for chunk in chunks), "合成思考")
        self.assertEqual(''.join(chunk.get("message", {}).get("content", "") for chunk in chunks), "合成正文")
        self.assertTrue(chunks[-1].get("done"))
        self.assertNotIn("synthetic-signature", json.dumps(chunks))
        self.assertNotIn(FAKE_KEY, json.dumps(chunks))

    @patch("epilepsy.llm.urlopen")
    def test_ollama_stream_requires_explicit_completion(self, urlopen):
        body = '\n'.join(json.dumps(chunk) for chunk in [
            {"message": {"content": "合成正文"}, "done": False},
            {"message": {"content": ""}, "done": True},
        ]) + '\n'
        urlopen.return_value = HTTPFixture(body.encode())
        chunks = list(stream_chat(self.config("ollama"), MESSAGES))
        self.assertEqual(''.join(chunk.get("message", {}).get("content", "") for chunk in chunks), "合成正文")
        self.assertTrue(chunks[-1].get("done"))

    @patch("epilepsy.llm.urlopen")
    def test_provider_streams_reject_eof_without_terminal_marker(self, urlopen):
        cases = {
            "ollama": HTTPFixture(b'{"message":{"content":"partial"},"done":false}\n'),
            "openai": sse_response(openai_events()),
            "anthropic": sse_response(anthropic_events(include_stop=False)),
        }
        for provider, response in cases.items():
            with self.subTest(provider=provider):
                urlopen.return_value = response
                chunks = []
                with self.assertRaises(LLMError) as caught:
                    for chunk in stream_chat(self.config(provider), MESSAGES):
                        chunks.append(chunk)
                self.assertFalse(any(chunk.get("done") for chunk in chunks))
                self.assert_no_sensitive_error(caught.exception)

    @patch("epilepsy.llm.urlopen")
    def test_streams_reject_truncation_and_nonfinal_tool_or_refusal_stops(self, urlopen):
        for provider, reasons in (
            ("openai", ("length", "content_filter", "tool_calls")),
            ("anthropic", ("max_tokens", "model_context_window_exceeded", "tool_use", "pause_turn", "refusal")),
        ):
            for reason in reasons:
                with self.subTest(provider=provider, stop_reason=reason):
                    urlopen.return_value = (sse_response(openai_events(finish_reason=reason), done=True)
                        if provider == "openai" else sse_response(anthropic_events(stop_reason=reason)))
                    chunks = []
                    with self.assertRaises(LLMError):
                        for chunk in stream_chat(self.config(provider), MESSAGES):
                            chunks.append(chunk)
                    self.assertFalse(any(chunk.get("done") for chunk in chunks))

    @patch("epilepsy.llm.urlopen")
    def test_http_401_errors_do_not_disclose_key_or_prompt(self, urlopen):
        for provider in ("ollama", "openai", "anthropic"):
            with self.subTest(provider=provider):
                body = json.dumps({"error": {"message": FAKE_KEY + ' ' + FAKE_PROMPT}}).encode()
                urlopen.side_effect = HTTPError("https://provider.test/v1/messages", 401, "Unauthorized", {}, io.BytesIO(body))
                with self.assertRaises(LLMError) as caught:
                    chat_completion(self.config(provider), MESSAGES)
                self.assert_no_sensitive_error(caught.exception)

    @patch("epilepsy.llm.urlopen")
    def test_authenticated_ollama_error_never_exposes_truncated_key_prefix(self, urlopen):
        urlopen.return_value = json_response({"error": "X" * 290 + FAKE_KEY + FAKE_PROMPT})
        config = LLMConfig("ollama", "http://ollama.test:11434", FAKE_KEY, "synthetic-model")
        with self.assertRaises(LLMError) as caught:
            chat_completion(config, MESSAGES)
        self.assert_no_sensitive_error(caught.exception)
        self.assertNotIn(FAKE_KEY[:10], str(caught.exception))

    @patch("epilepsy.llm.urlopen")
    def test_stream_error_event_does_not_disclose_sensitive_details(self, urlopen):
        urlopen.return_value = sse_response([{
            "type": "error", "error": {"type": "authentication_error", "message": FAKE_KEY + ' ' + FAKE_PROMPT},
        }])
        with self.assertRaises(LLMError) as caught:
            list(stream_chat(self.config("anthropic"), MESSAGES))
        self.assert_no_sensitive_error(caught.exception)

    @patch("epilepsy.llm.urlopen")
    def test_anthropic_model_pagination_returns_all_unique_models(self, urlopen):
        urlopen.side_effect = [
            json_response({"data": [{"id": "claude-a"}], "has_more": True, "last_id": "claude-a"}),
            json_response({"data": [{"id": "claude-a"}, {"id": "claude-b"}], "has_more": False, "last_id": "claude-b"}),
        ]
        self.assertEqual(set(fetch_models(self.config("anthropic"))), {"claude-a", "claude-b"})
        self.assertEqual(urlopen.call_count, 2)
        first, second = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(urlsplit(first.full_url).path, "/v1/models")
        self.assertEqual(parse_qs(urlsplit(second.full_url).query)["after_id"], ["claude-a"])
        self.assertEqual(dict((key.lower(), value) for key, value in second.header_items())["x-api-key"], FAKE_KEY)

    @patch("epilepsy.llm.urlopen")
    def test_openai_models_use_bearer_and_model_ids(self, urlopen):
        urlopen.return_value = json_response({"data": [{"id": "model-a"}, {"id": "model-a"}, {"id": "model-b"}]})
        models = fetch_models(self.config("openai"))
        self.assertEqual(set(models), {"model-a", "model-b"})
        self.assertEqual(len(models), 2)
        request, headers, payload = self.request_payload(urlopen)
        self.assertEqual(request.full_url, "https://provider.test/v1/models")
        self.assertEqual(headers["authorization"], "Bearer " + FAKE_KEY)
        self.assertIsNone(payload)

    @patch("epilepsy.llm.urlopen")
    def test_authenticated_ollama_models_use_safe_bearer_request(self, urlopen):
        urlopen.return_value = json_response({
            "models": [{"name": "synthetic-a"}, {"name": "synthetic-a"}, {"model": "synthetic-b"}],
            "api_key": FAKE_KEY, "upstream_prompt": FAKE_PROMPT,
        })
        config = LLMConfig("ollama", "http://ollama.test:11434", FAKE_KEY, "synthetic-model")
        models = fetch_models(config)
        self.assertEqual(set(models), {"synthetic-a", "synthetic-b"})
        self.assertEqual(len(models), 2)
        request, headers, payload = self.request_payload(urlopen)
        self.assertEqual(request.full_url, "http://ollama.test:11434/api/tags")
        self.assertEqual(headers["authorization"], "Bearer " + FAKE_KEY)
        self.assertIsNone(payload)
        self.assertNotIn(FAKE_KEY, json.dumps(models))
        self.assertNotIn(FAKE_PROMPT, json.dumps(models))

    @patch("epilepsy.llm.urlopen")
    def test_embeddings_keep_protocol_specific_payloads(self, urlopen):
        for provider, response in (
            ("ollama", {"embedding": [1, 0.25, -0.5]}),
            ("openai", {"data": [{"index": 0, "embedding": [1, 0.25, -0.5]}]}),
        ):
            with self.subTest(provider=provider):
                urlopen.return_value = json_response(response)
                vector = embed_text(self.config(provider, model="synthetic-embedding"), "合成文本")
                self.assertEqual(vector, [1.0, 0.25, -0.5])
                self.assertTrue(all(isinstance(number, float) for number in vector))
                request, headers, payload = self.request_payload(urlopen)
                self.assertEqual(payload["model"], "synthetic-embedding")
                self.assertEqual(payload["prompt" if provider == "ollama" else "input"], "合成文本")
                self.assertEqual(urlsplit(request.full_url).path, "/api/embeddings" if provider == "ollama" else "/v1/embeddings")
                self.assert_no_key_in_payload(payload)

    @patch("epilepsy.llm.urlopen")
    def test_anthropic_embeddings_are_rejected_before_network_access(self, urlopen):
        with self.assertRaises(LLMError):
            embed_text(self.config("anthropic"), "合成文本")
        urlopen.assert_not_called()

    @patch("epilepsy.llm.urlopen")
    def test_empty_or_nonfinite_embedding_vectors_are_rejected(self, urlopen):
        for vector in ([], [float("nan")], [float("inf")], ["not-a-number"]):
            with self.subTest(vector=vector):
                urlopen.return_value = json_response({"data": [{"embedding": vector}]})
                with self.assertRaises(LLMError):
                    embed_text(self.config("openai"), "合成文本")

    def test_url_normalization_preserves_custom_v1_paths(self):
        self.assertEqual(normalize_base_url("openai", "https://provider.test/gateway/v1/"), "https://provider.test/gateway/v1")
        self.assertEqual(normalize_base_url("anthropic", "https://provider.test/v1/"), "https://provider.test/v1")

    def test_config_representation_does_not_expose_key(self):
        self.assertNotIn(FAKE_KEY, repr(self.config("openai")))

    @patch("epilepsy.llm.urlopen")
    def test_terminal_markers_without_answer_do_not_finish_a_report(self, urlopen):
        for provider, response in (
            ("ollama", HTTPFixture(b'{"message":{"content":""},"done":true}\n')),
            ("openai", sse_response([], done=True)),
            ("anthropic", sse_response([
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"},
            ])),
        ):
            with self.subTest(provider=provider):
                urlopen.return_value = response
                chunks = []
                with self.assertRaises(LLMError):
                    for chunk in stream_chat(self.config(provider), MESSAGES):
                        chunks.append(chunk)
                self.assertFalse(any(chunk.get("done") for chunk in chunks))

    @patch("epilepsy.llm.urlopen")
    def test_control_characters_in_key_are_rejected_without_echo_or_network(self, urlopen):
        bad_key = FAKE_KEY + "\r\nsynthetic-extra-header"
        with self.assertRaises((LLMError, ValueError)) as caught:
            config = LLMConfig("openai", "https://provider.test/v1", bad_key, "synthetic-model")
            chat_completion(config, MESSAGES)
        self.assertNotIn(FAKE_KEY, str(caught.exception))
        self.assertNotIn("synthetic-extra-header", str(caught.exception))
        urlopen.assert_not_called()

    def test_authenticated_redirects_do_not_forward_credentials_to_another_host(self):
        from epilepsy.llm import NoRedirectHandler
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider):
                attempted = []

                class SyntheticRedirect(urllib.request.HTTPHandler):
                    def http_open(self, request):
                        attempted.append(request.full_url)
                        if request.host != "source.test":
                            raise AssertionError("An authenticated redirect was followed")
                        headers = Message()
                        headers["Location"] = "http://other-host.test/capture-key"
                        response = addinfourl(io.BytesIO(b''), headers, request.full_url, code=302)
                        response.msg = "Found"
                        return response

                opener = urllib.request.build_opener(NoRedirectHandler(), SyntheticRedirect())
                with patch("epilepsy.llm.urllib.request.build_opener", return_value=opener):
                    config = LLMConfig(provider, "http://source.test/v1", FAKE_KEY, "synthetic-model")
                    with self.assertRaises(LLMError) as caught:
                        fetch_models(config)
                self.assertEqual(attempted, ["http://source.test/v1/models"])
                self.assert_no_sensitive_error(caught.exception)


def report_events(source):
    events = []
    for chunk in source:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        for frame in chunk.split('\n\n'):
            if frame.startswith('data: '):
                events.append(json.loads(frame[6:]))
    return events


class ReportProviderIntegrationTests(SimpleTestCase):
    def server(self, provider):
        from epilepsy.models import OllamaServer
        return OllamaServer(
            provider=provider, api_base_url="https://report-provider.test/v1",
            api_key=FAKE_KEY, model="synthetic-report-model", prompt="",
            enable_thinking=True,
        )

    def response(self, provider):
        return (sse_response(openai_events(), done=True) if provider == "openai"
                else sse_response(anthropic_events()))

    def assert_report_protocol(self, urlopen, provider, events):
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode())
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(request.full_url, "https://report-provider.test/v1/" + (
            "chat/completions" if provider == "openai" else "messages"))
        self.assertEqual(payload["model"], "synthetic-report-model")
        self.assertEqual(headers["authorization"] if provider == "openai" else headers["x-api-key"],
                         "Bearer " + FAKE_KEY if provider == "openai" else FAKE_KEY)
        self.assertEqual(''.join(event.get("text", "") for event in events if event["type"] == "output"), "合成正文")
        self.assertEqual(''.join(event.get("text", "") for event in events if event["type"] == "thinking"), "合成思考")
        self.assertNotIn(FAKE_KEY, json.dumps(events))
        self.assertNotIn(FAKE_PROMPT, json.dumps(events))

    @patch("epilepsy.llm.urlopen")
    def test_direct_and_rag_reports_use_selected_provider(self, urlopen):
        from epilepsy.views import stream_ollama_report
        for provider in ("openai", "anthropic"):
            for rag in (False, True):
                with self.subTest(provider=provider, rag=rag):
                    urlopen.return_value = self.response(provider)
                    pre_events = ['data: {"type":"retrieval","total":0}\n\n'] if rag else None
                    events = report_events(stream_ollama_report(None, self.server(provider),
                                           messages=MESSAGES, pre_events=pre_events))
                    self.assert_report_protocol(urlopen, provider, events)
                    self.assertEqual(events[-1]["type"], "done")
                    if rag:
                        self.assertEqual(events[0]["type"], "retrieval")

    @patch("epilepsy.llm.urlopen")
    def test_agent_draft_and_revision_use_report_provider_not_thinking_provider(self, urlopen):
        from knowledge.agent_pipeline import AgentPipeline
        from knowledge.models import KnowledgeSettings
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider):
                settings = KnowledgeSettings(
                    thinking_provider="ollama", thinking_model="different-thinking-model",
                    thinking_api_base_url="http://thinking-provider.test:11434",
                )
                with patch("knowledge.models.KnowledgeSettings.load", return_value=settings), \
                        patch("epilepsy.models.OllamaServer.objects.filter") as servers:
                    servers.return_value.first.return_value = self.server(provider)
                    pipeline = AgentPipeline(SimpleNamespace(), self.server(provider))
                pipeline.export_json = {"synthetic": True}
                for stage in ("draft", "revision"):
                    with self.subTest(stage=stage):
                        pipeline.draft_text = ""
                        urlopen.return_value = self.response(provider)
                        source = (pipeline._draft({}, {"cases": [], "literature": [], "total_found": 0})
                                  if stage == "draft" else pipeline._revise({"issues": []}, "合成草稿"))
                        events = report_events(source)
                        self.assert_report_protocol(urlopen, provider, events)
                        self.assertEqual(pipeline.draft_text, "合成正文")

    @patch("epilepsy.llm.urlopen")
    def test_agent_analysis_and_critique_use_independent_thinking_provider(self, urlopen):
        from knowledge.agent_pipeline import AgentPipeline
        from knowledge.models import KnowledgeSettings
        for thinking_provider in ("openai", "anthropic"):
            with self.subTest(thinking_provider=thinking_provider):
                report_provider = "anthropic" if thinking_provider == "openai" else "openai"
                settings = KnowledgeSettings(
                    thinking_provider=thinking_provider, thinking_model="synthetic-thinking-model",
                    thinking_api_base_url="https://thinking-provider.test/v1",
                    thinking_api_key="synthetic-thinking-key",
                )
                with patch("knowledge.models.KnowledgeSettings.load", return_value=settings), \
                        patch("epilepsy.models.OllamaServer.objects.filter") as servers:
                    servers.return_value.first.return_value = self.server(report_provider)
                    pipeline = AgentPipeline(SimpleNamespace(), self.server(report_provider))
                    pipeline.export_json = {"synthetic": True}
                    for action in (pipeline._analyze, lambda: pipeline._critique({})):
                        urlopen.return_value = json_response(
                            {"choices": [{"message": {"content": '{"synthetic":true}'}, "finish_reason": "stop"}]}
                            if thinking_provider == "openai" else
                            {"content": [{"type": "text", "text": '{"synthetic":true}'}], "stop_reason": "end_turn"}
                        )
                        self.assertEqual(action(), {"synthetic": True})
                        request = urlopen.call_args.args[0]
                        self.assertTrue(request.full_url.startswith("https://thinking-provider.test/v1/"))
                        self.assertEqual(json.loads(request.data.decode())["model"], "synthetic-thinking-model")
                        headers = {name.lower(): value for name, value in request.header_items()}
                        self.assertEqual(headers.get("authorization") if thinking_provider == "openai" else headers.get("x-api-key"),
                                         "Bearer synthetic-thinking-key" if thinking_provider == "openai" else "synthetic-thinking-key")

    @patch("epilepsy.llm.urlopen")
    def test_report_endpoint_error_does_not_expose_upstream_reason(self, urlopen):
        from epilepsy import views
        from epilepsy.models import UserRole
        request = RequestFactory().get('/epilepsy/patients/1/generate-report/')
        request.user = SimpleNamespace(is_authenticated=True, profile=SimpleNamespace(role=UserRole.ADMIN))
        for endpoint in (views.patient_generate_report_stream, views.patient_generate_report_stream_rag):
            with self.subTest(endpoint=endpoint.__name__):
                urlopen.side_effect = HTTPError("https://report-provider.test/v1/chat/completions", 401,
                                               FAKE_KEY + ' ' + FAKE_PROMPT, {}, io.BytesIO(b'{}'))
                with patch("epilepsy.views.get_object_or_404", return_value=SimpleNamespace()), \
                        patch("epilepsy.views.OllamaServer.objects.filter") as servers, \
                        patch("epilepsy.views._build_patient_report_messages", return_value=MESSAGES), \
                        patch("epilepsy.views._build_rag_patient_report_messages", return_value=(MESSAGES, {})):
                    servers.return_value.first.return_value = self.server("openai")
                    response = endpoint(request, 1)
                    events = report_events(response.streaming_content)
                self.assertEqual(events[-1]["type"], "error")
                serialized = json.dumps(events)
                self.assertNotIn(FAKE_KEY, serialized)
                self.assertNotIn(FAKE_PROMPT, serialized)
                self.assertFalse(any(event["type"] == "done" for event in events))

    @patch("epilepsy.llm.urlopen")
    def test_thinking_tags_without_report_text_do_not_complete(self, urlopen):
        from epilepsy.views import stream_ollama_report
        from knowledge.agent_pipeline import AgentPipeline
        from knowledge.models import KnowledgeSettings
        tagged = '<think>合成思考，没有报告正文</think>'
        for consumer in ("direct", "agent"):
            with self.subTest(consumer=consumer):
                urlopen.return_value = sse_response([
                    {"choices": [{"delta": {"content": tagged}, "index": 0, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]},
                ], done=True)
                if consumer == "direct":
                    source = stream_ollama_report(None, self.server("openai"), messages=MESSAGES)
                else:
                    with patch("knowledge.models.KnowledgeSettings.load", return_value=KnowledgeSettings()), \
                            patch("epilepsy.models.OllamaServer.objects.filter") as servers:
                        servers.return_value.first.return_value = self.server("openai")
                        pipeline = AgentPipeline(SimpleNamespace(), self.server("openai"))
                    source = pipeline._stream_chat(MESSAGES, pipeline.generate_model)
                events = []
                with self.assertRaises(RuntimeError):
                    for event in source:
                        events.extend(report_events([event]))
                self.assertFalse(any(event["type"] == "done" for event in events))


@override_settings(SECRET_KEY="synthetic-encryption-key-for-isolated-tests")
class StoredLLMKeyCompatibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from epilepsy.models import OllamaServer, UserProfile, UserRole
        cls.server = OllamaServer.objects.create(
            name="Synthetic encrypted service", provider="openai",
            api_base_url="https://provider.test/v1", api_key=FAKE_KEY,
            model="synthetic-model", is_enabled=True,
        )
        cls.admin = get_user_model().objects.create_user(username="synthetic-provider-admin")
        UserProfile.objects.create(user=cls.admin, role=UserRole.ADMIN)

    def setUp(self):
        self.client.force_login(self.admin)

    def form_data(self):
        from epilepsy.forms import OllamaServerForm
        data = model_to_dict(self.server, fields=OllamaServerForm._meta.fields)
        data["api_key"] = ""
        return data

    def test_stored_key_is_encrypted_and_round_trips(self):
        from epilepsy.models import OllamaServer
        with connection.cursor() as cursor:
            cursor.execute('SELECT api_key FROM epilepsy_ollamaserver WHERE id = %s', [self.server.pk])
            stored = cursor.fetchone()[0]
        self.assertNotEqual(stored, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, stored)
        self.assertTrue(stored.startswith("llm-key-v1:"))
        self.assertEqual(OllamaServer.objects.get(pk=self.server.pk).api_key, FAKE_KEY)

    def test_edit_page_does_not_echo_stored_key(self):
        response = self.client.get(reverse("epilepsy:ollama_server_edit", args=[self.server.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FAKE_KEY)
        self.assertContains(response, 'type="password"')

    def test_management_page_does_not_echo_stored_key(self):
        response = self.client.get(reverse("epilepsy:user_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, FAKE_KEY)

    def test_invalid_edit_never_echoes_entered_or_stored_key(self):
        data = self.form_data()
        data.update(model="", api_key="synthetic-new-key-never-echo")
        response = self.client.post(reverse("epilepsy:ollama_server_edit", args=[self.server.pk]), data)
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, FAKE_KEY, status_code=400)
        self.assertNotContains(response, "synthetic-new-key-never-echo", status_code=400)
        self.server.refresh_from_db()
        self.assertEqual(self.server.api_key, FAKE_KEY)

    def test_blank_key_preserves_existing_credentials_for_same_address(self):
        from epilepsy.forms import OllamaServerForm
        form = OllamaServerForm(self.form_data(), instance=self.server)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.server.refresh_from_db()
        self.assertEqual(self.server.api_key, FAKE_KEY)

    def test_address_change_does_not_reuse_existing_credentials(self):
        from epilepsy.forms import OllamaServerForm
        data = self.form_data()
        data["api_base_url"] = "https://another-provider.test/v1"
        form = OllamaServerForm(data, instance=self.server)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.server.refresh_from_db()
        self.assertEqual(self.server.api_key, "")

    def test_explicit_clear_removes_saved_credentials(self):
        from epilepsy.forms import OllamaServerForm
        data = self.form_data()
        data["clear_api_key"] = True
        form = OllamaServerForm(data, instance=self.server)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.server.refresh_from_db()
        self.assertEqual(self.server.api_key, "")

    @patch("epilepsy.llm.urlopen")
    def test_report_routes_use_the_active_provider_without_exposing_key(self, urlopen):
        from datetime import date
        from epilepsy.models import Patient
        patient = Patient.objects.create(
            name="Synthetic provider patient", gender="M", birthday=date(2000, 1, 1),
            handedness="R", admission_date=date(2026, 1, 1),
        )
        for provider in ("openai", "anthropic"):
            self.server.provider = provider
            self.server.save()
            for name, rag in (("patient_generate_report_stream", False),
                              ("patient_generate_report_stream_rag", True)):
                with self.subTest(provider=provider, route=name):
                    urlopen.return_value = (sse_response(openai_events(), done=True)
                                           if provider == "openai" else sse_response(anthropic_events()))
                    with patch("epilepsy.views._build_patient_report_messages", return_value=MESSAGES), \
                            patch("epilepsy.views._build_rag_patient_report_messages", return_value=(MESSAGES, {})):
                        response = self.client.get(reverse("epilepsy:" + name, args=[patient.pk]))
                        events = report_events(response.streaming_content)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(events[-1]["type"], "done")
                    self.assertNotIn(FAKE_KEY, json.dumps(events))
                    self.assertNotIn(FAKE_PROMPT, json.dumps(events))
                    request = urlopen.call_args.args[0]
                    self.assertEqual(request.full_url, "https://provider.test/v1/" + (
                        "chat/completions" if provider == "openai" else "messages"))
                    self.assertEqual(json.loads(request.data.decode())["model"], "synthetic-model")
                    headers = {name.lower(): value for name, value in request.header_items()}
                    self.assertEqual(headers["authorization"] if provider == "openai" else headers["x-api-key"],
                                     "Bearer " + FAKE_KEY if provider == "openai" else FAKE_KEY)
                    if rag:
                        self.assertEqual(events[0]["type"], "retrieval")

    @patch("epilepsy.llm.urlopen")
    def test_model_discovery_reuses_saved_key_only_at_unchanged_address(self, urlopen):
        for base_url, expected in (("https://provider.test/v1", "Bearer " + FAKE_KEY),
                                   ("https://another-provider.test/v1", None)):
            with self.subTest(base_url=base_url):
                urlopen.return_value = json_response({"data": [{"id": "synthetic-model"}]})
                response = self.client.post(reverse("epilepsy:ollama_model_list"), {
                    "server_id": self.server.pk, "provider": "openai",
                    "api_base_url": base_url, "api_key": "",
                })
                self.assertEqual(response.status_code, 200)
                headers = {name.lower(): value for name, value in urlopen.call_args.args[0].header_items()}
                self.assertEqual(headers.get("authorization"), expected)
                self.assertNotIn(FAKE_KEY, response.content.decode())

    @patch("epilepsy.llm.urlopen")
    def test_thinking_model_discovery_uses_pending_extraction_settings(self, urlopen):
        from knowledge.models import KnowledgeSettings
        settings = KnowledgeSettings.objects.create(
            extraction_provider="openai", extraction_api_base_url="https://old-extraction.test/v1",
            extraction_api_key="synthetic-old-extraction-key", extraction_model="old-extraction-model",
        )
        for pending_key in ("synthetic-pending-key", ""):
            with self.subTest(key_supplied=bool(pending_key)):
                urlopen.return_value = json_response({"data": [{"id": "synthetic-claude"}], "has_more": False})
                response = self.client.post(reverse("epilepsy:ollama_model_list"), {
                    "config_role": "thinking", "provider": "", "api_base_url": "", "api_key": "",
                    "extraction_provider": "anthropic", "extraction_api_base_url": "https://pending-extraction.test/v1",
                    "extraction_api_key": pending_key, "clear_extraction_api_key": "0",
                })
                self.assertEqual(response.status_code, 200)
                request = urlopen.call_args.args[0]
                headers = {name.lower(): value for name, value in request.header_items()}
                self.assertEqual(request.full_url, "https://pending-extraction.test/v1/models")
                self.assertEqual(headers["x-api-key"], pending_key)
                self.assertNotIn("authorization", headers)
                self.assertEqual(response.json()["models"], ["synthetic-claude"])
                self.assertNotIn("synthetic-pending-key", response.content.decode())
        settings.refresh_from_db()
        self.assertEqual(settings.extraction_provider, "openai")
        self.assertEqual(settings.extraction_api_key, "synthetic-old-extraction-key")
