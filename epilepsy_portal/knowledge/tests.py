import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from epilepsy.llm import LLMError
from epilepsy.views import stream_ollama_report
from .agent_pipeline import AgentPipeline


class MockOllamaResponse:
    def __init__(self, objects):
        self.lines = [(json.dumps(obj) + "\n").encode("utf-8") for obj in objects]

    def __enter__(self):
        return iter(self.lines)

    def __exit__(self, *args):
        pass


class ReportStreamTests(SimpleTestCase):
    """All report data and Ollama responses are synthetic; no network or DB."""

    def setUp(self):
        self.server = SimpleNamespace(
            base_url="http://mock-ollama.invalid", model="mock-model", enable_thinking=False,
        )

    def make_agent(self):
        agent = AgentPipeline.__new__(AgentPipeline)
        agent.server = self.server
        agent.patient = object()
        agent.generate_model = self.server.model
        agent.draft_text = ""
        return agent

    def stream_events(self, mode, objects):
        agent = self.make_agent()
        with patch("epilepsy.llm.urlopen", return_value=MockOllamaResponse(objects)), \
                patch("knowledge.agent_pipeline._get_ollama_base_url", return_value=self.server.base_url):
            stream = (stream_ollama_report(None, self.server, messages=[])
                      if mode == "plain" else agent._stream_chat([], self.server.model))
            events = [json.loads(event.removeprefix("data: ")) for event in stream]
        return events, agent

    @staticmethod
    def event_text(events, event_type):
        return "".join(event.get("text", "") for event in events if event["type"] == event_type)

    def test_tagged_reasoning_never_enters_report(self):
        objects = [
            {"message": {"content": "<think>Synthetic reasoning</think>"}},
            {"message": {"content": "Synthetic report"}, "done": True},
        ]
        for mode in ("plain", "agent"):
            with self.subTest(mode=mode):
                events, agent = self.stream_events(mode, objects)
                self.assertEqual(self.event_text(events, "thinking"), "Synthetic reasoning")
                self.assertEqual(self.event_text(events, "output"), "Synthetic report")
                if mode == "agent":
                    self.assertEqual(agent.draft_text, "Synthetic report")

    def test_reasoning_tags_split_across_chunks(self):
        objects = [{"message": {"content": chunk}} for chunk in (
            "<thi", "nk>Synthetic ", "reasoning</th", "ink>Synthetic report",
        )] + [{"done": True}]
        for mode in ("plain", "agent"):
            with self.subTest(mode=mode):
                events, _ = self.stream_events(mode, objects)
                self.assertEqual(self.event_text(events, "thinking"), "Synthetic reasoning")
                self.assertEqual(self.event_text(events, "output"), "Synthetic report")

    def test_direct_reasoning_field_is_separate(self):
        objects = [{"message": {
            "thinking": "Synthetic reasoning", "content": "Synthetic report",
        }, "done": True}]
        for mode in ("plain", "agent"):
            with self.subTest(mode=mode):
                events, _ = self.stream_events(mode, objects)
                self.assertEqual(self.event_text(events, "thinking"), "Synthetic reasoning")
                self.assertEqual(self.event_text(events, "output"), "Synthetic report")

    def test_done_flushes_literal_delimiter_prefix(self):
        for mode in ("plain", "agent"):
            with self.subTest(mode=mode):
                events, _ = self.stream_events(mode, [
                    {"message": {"content": "Synthetic report <"}}, {"done": True},
                ])
                self.assertEqual(self.event_text(events, "output"), "Synthetic report <")

    def test_done_stops_reading_additional_data(self):
        for mode in ("plain", "agent"):
            with self.subTest(mode=mode):
                events, _ = self.stream_events(mode, [
                    {"message": {"content": "Synthetic report"}, "done": True},
                    {"message": {"content": "Unexpected data"}},
                ])
                self.assertEqual(self.event_text(events, "output"), "Synthetic report")

    def test_stream_error_is_reported(self):
        for mode in ("plain", "agent"):
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, "mock model failed"):
                self.stream_events(mode, [{"error": "mock model failed"}])

    def test_eof_without_done_is_failure(self):
        for mode in ("plain", "agent"):
            for objects in ([], [{"message": {"content": "Synthetic partial report"}}]):
                with self.subTest(mode=mode, objects=objects), \
                        self.assertRaisesRegex(RuntimeError, "输出流提前结束"):
                    self.stream_events(mode, objects)

    def test_agent_draft_failure_does_not_continue_to_critique(self):
        for objects in ([{"error": "mock model failed"}],
                        [{"message": {"content": "Synthetic partial report"}}]):
            with self.subTest(objects=objects):
                agent = self.make_agent()
                agent.generate_model = self.server.model
                agent._analyze = Mock(return_value={"key_features": []})
                agent._retrieve = Mock(return_value={"cases": [], "literature": [], "total_found": 0})
                agent._draft = lambda *args: agent._stream_chat([], self.server.model)
                agent._critique = Mock()
                with patch("epilepsy.views_helper.build_patient_export_json", return_value={"sections": []}), \
                        patch("epilepsy.llm.urlopen", return_value=MockOllamaResponse(objects)), \
                        patch("knowledge.agent_pipeline._get_ollama_base_url", return_value=self.server.base_url), \
                        self.assertLogs("knowledge.agent_pipeline", level="ERROR"):
                    events = [json.loads(event.removeprefix("data: ")) for event in agent.execute()]
                self.assertTrue(any(event["type"] == "error" for event in events))
                self.assertFalse(any(event["type"] == "done" for event in events))
                self.assertFalse(any(event["type"] == "progress" and event["step"] == 4 for event in events))
                agent._critique.assert_not_called()

    def test_agent_revision_replaces_draft_before_new_output(self):
        agent = self.make_agent()
        agent._analyze = Mock(return_value={"key_features": []})
        agent._retrieve = Mock(return_value={"cases": [], "literature": [], "total_found": 0})
        agent._critique = Mock(return_value={"needs_revision": True, "issues": []})

        def draft(*args):
            agent.draft_text += "Synthetic draft"
            yield agent._output("Synthetic draft")

        agent._draft = draft
        with patch("epilepsy.views_helper.build_patient_export_json", return_value={"sections": []}), \
                patch("epilepsy.llm.stream_chat", return_value=iter([
                    {"message": {"content": "Synthetic final report"}, "done": True},
                ])) as stream:
            events = [json.loads(event.removeprefix("data: ")) for event in agent.execute()]
        self.assertIn("Synthetic draft", str(stream.call_args.args[1]))

        report_events = [event for event in events if event["type"] in ("output", "reset_output", "done")]
        self.assertEqual([event["type"] for event in report_events], [
            "output", "reset_output", "output", "done",
        ])
        final_report = ""
        for event in report_events:
            if event["type"] == "reset_output":
                final_report = ""
            elif event["type"] == "output":
                final_report += event["text"]
        self.assertEqual(final_report, "Synthetic final report")
        self.assertEqual(agent.draft_text, "Synthetic final report")

    def test_agent_keeps_draft_when_revision_is_unnecessary(self):
        agent = self.make_agent()
        agent._analyze = Mock(return_value={"key_features": []})
        agent._retrieve = Mock(return_value={"cases": [], "literature": [], "total_found": 0})
        agent._critique = Mock(return_value={"needs_revision": False, "issues": []})
        def draft(*args):
            agent.draft_text = "Synthetic final draft"
            yield agent._output(agent.draft_text)

        agent._draft = draft
        agent._revise = Mock()
        with patch("epilepsy.views_helper.build_patient_export_json", return_value={"sections": []}):
            events = [json.loads(event.removeprefix("data: ")) for event in agent.execute()]
        self.assertFalse(any(event["type"] == "reset_output" for event in events))
        self.assertEqual(self.event_text(events, "output"), "Synthetic final draft")
        agent._revise.assert_not_called()


class AgentStageRetryTests(SimpleTestCase):
    """Exercise the real Agent stage generators with synthetic model streams."""

    def setUp(self):
        self.agent = AgentPipeline.__new__(AgentPipeline)
        self.agent.server = SimpleNamespace(
            provider="openai", base_url="https://synthetic-model.invalid/v1",
            model="synthetic-report-model", api_key="SYNTHETIC_PRIVATE_KEY",
            prompt="", enable_thinking=True,
        )
        self.agent.patient = object()
        self.agent.generate_model = self.agent.server.model
        self.agent.draft_text = ""
        self.agent._analyze = Mock(return_value={"key_features": []})
        self.agent._retrieve = Mock(return_value={"cases": [], "literature": [], "total_found": 0})
        self.agent._critique = Mock(return_value={
            "needs_revision": True,
            "issues": [{"severity": "high", "problem": "合成审核意见", "suggestion": "合成修订建议"}],
        })
        self.export = patch("epilepsy.views_helper.build_patient_export_json", return_value={
            "sections": [{"title": "合成数据", "items": [
                {"label": "合成字段", "value": "SYNTHETIC_PRIVATE_CASE"},
            ]}],
        })
        self.export.start()
        self.addCleanup(self.export.stop)
        self.original = "SYNTHETIC_PRIVATE_DRAFT 完整初稿"

    @staticmethod
    def completed(text):
        return iter([{"message": {"content": text}, "done": True}])

    @staticmethod
    def visible_report(events):
        text = ""
        for event in events:
            if event["type"] == "reset_output":
                text = ""
            elif event["type"] == "output":
                text += event["text"]
        return text

    def run_agent(self, responses):
        with patch("epilepsy.llm.stream_chat", side_effect=responses) as stream:
            events = [json.loads(event.removeprefix("data: ")) for event in self.agent.execute()]
        return events, stream

    def assert_no_success(self, events):
        self.assertFalse(any(event["type"] == "done" for event in events))
        self.assertEqual(events[-1]["type"], "error")

    def test_empty_revision_retries_identical_input_and_then_succeeds(self):
        events, stream = self.run_agent([
            self.completed(self.original),
            iter([{"message": {"thinking": "只有合成思考"}}, {"done": True}]),
            self.completed("成功修订后的完整正文"),
        ])
        self.assertEqual(stream.call_count, 3)
        first, second = stream.call_args_list[1:]
        self.assertEqual(first.args, second.args)
        self.assertEqual(first.kwargs, second.kwargs)
        self.assertIn(self.original, str(first.args[1]))
        self.assertEqual(self.visible_report(events), "成功修订后的完整正文")
        self.assertEqual(self.agent.draft_text, "成功修订后的完整正文")
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(any(event["type"] == "progress" and event["step"] == 5
                            and "第 2 次" in event["text"] for event in events))

    def test_two_empty_revisions_restore_draft_and_keep_review_without_done(self):
        with self.assertLogs("knowledge.agent_pipeline", level="WARNING") as logs:
            events, stream = self.run_agent([
                self.completed(self.original), iter([{"done": True}]), iter([{"done": True}]),
            ])
        self.assertEqual(stream.call_count, 3)
        self.assert_no_success(events)
        self.assertEqual(self.visible_report(events), self.original)
        self.assertEqual(self.agent.draft_text, self.original)
        self.assertIn("修订失败，已保留初稿（未经修订）", events[-1]["text"])
        self.assertTrue(any("合成审核意见" in event.get("detail", "") for event in events))
        diagnostic = '\n'.join(logs.output)
        self.assertIn("stage=revision attempt=2 code=empty_response", diagnostic)
        for private in (self.original, "SYNTHETIC_PRIVATE_KEY", "SYNTHETIC_PRIVATE_CASE"):
            self.assertNotIn(private, diagnostic)
        self.assertNotIn("Traceback", diagnostic)

    def test_partial_revision_retry_never_mixes_attempts(self):
        events, stream = self.run_agent([
            self.completed(self.original),
            iter([{"message": {"content": "失败尝试的部分内容"}}]),
            self.completed("第二次完整修订"),
        ])
        self.assertEqual(stream.call_count, 3)
        self.assertEqual(stream.call_args_list[1].args, stream.call_args_list[2].args)
        self.assertEqual(self.visible_report(events), "第二次完整修订")
        self.assertEqual(self.agent.draft_text, "第二次完整修订")
        self.assertTrue(any(event["type"] == "output" and event["text"] == "失败尝试的部分内容"
                            for event in events))
        self.assertEqual(events[-1]["type"], "done")

    def test_authentication_failure_does_not_retry_and_restores_draft(self):
        events, stream = self.run_agent([
            self.completed(self.original),
            LLMError("SYNTHETIC_PRIVATE_KEY SYNTHETIC_PRIVATE_CASE", code="authentication"),
        ])
        self.assertEqual(stream.call_count, 2)
        self.assertEqual(len(stream.call_args_list[1:]), 1)
        self.assert_no_success(events)
        self.assertEqual(self.visible_report(events), self.original)
        self.assertNotIn("SYNTHETIC_PRIVATE_KEY", events[-1]["text"])
        self.assertIn("认证失败", events[-1]["text"])

    def test_nonrecoverable_draft_errors_are_explained_without_retry(self):
        for code, explanation in (("authentication", "认证失败"),
                                  ("rate_limit", "限额或余额受限"),
                                  ("output_limit", "达到输出上限")):
            with self.subTest(code=code):
                events, stream = self.run_agent([
                    LLMError("SYNTHETIC_PRIVATE_KEY SYNTHETIC_PRIVATE_CASE", code=code),
                ])
                self.assertEqual(stream.call_count, 1)
                self.assert_no_success(events)
                self.assertIn(explanation, events[-1]["text"])
                self.assertNotIn("SYNTHETIC_PRIVATE_KEY", events[-1]["text"])
                self.agent._critique.assert_not_called()

    def test_thinking_failures_log_the_thinking_service(self):
        self.agent.think_provider = "anthropic"
        self.agent.think_model = "synthetic-thinking-model"
        for stage in ("analysis", "critique"):
            with self.subTest(stage=stage):
                self.agent._analyze.side_effect = None
                self.agent._critique.side_effect = None
                failing = self.agent._analyze if stage == "analysis" else self.agent._critique
                failing.side_effect = LLMError("SYNTHETIC_PRIVATE_KEY", code="authentication")
                with self.assertLogs("knowledge.agent_pipeline", level="ERROR") as logs:
                    events, _ = self.run_agent([] if stage == "analysis" else [self.completed(self.original)])
                self.assert_no_success(events)
                diagnostic = '\n'.join(logs.output)
                self.assertIn(f"provider=anthropic model=synthetic-thinking-model stage={stage}", diagnostic)
                self.assertNotIn("synthetic-report-model", diagnostic)
                self.assertNotIn("SYNTHETIC_PRIVATE_KEY", diagnostic)

    def test_programming_failure_is_not_retried_and_restores_draft_safely(self):
        with self.assertLogs("knowledge.agent_pipeline", level="ERROR") as logs:
            events, stream = self.run_agent([
                self.completed(self.original),
                ValueError("SYNTHETIC_PRIVATE_KEY SYNTHETIC_PRIVATE_CASE"),
            ])
        self.assertEqual(stream.call_count, 2)
        self.assert_no_success(events)
        self.assertEqual(self.visible_report(events), self.original)
        self.assertIn("已保留初稿（未经修订）", events[-1]["text"])
        self.assertNotIn("SYNTHETIC_PRIVATE_KEY", '\n'.join(logs.output) + events[-1]["text"])
        self.assertNotIn("SYNTHETIC_PRIVATE_CASE", '\n'.join(logs.output) + events[-1]["text"])

    def test_failed_draft_does_not_proceed_to_review(self):
        events, stream = self.run_agent([
            LLMError("合成空响应", code="empty_response"),
            LLMError("合成空响应", code="empty_response"),
        ])
        self.assertEqual(stream.call_count, 2)
        self.assert_no_success(events)
        self.agent._critique.assert_not_called()
        self.assertFalse(any(event["type"] == "progress" and event["step"] == 4 for event in events))
        self.assertTrue(any(event["type"] == "progress" and event["step"] == 3
                            and "第 2 次" in event["text"] for event in events))

    def test_draft_retries_only_recoverable_codes(self):
        for code in ("empty_response", "incomplete_stream", "timeout", "transient_http", "connection_error"):
            with self.subTest(code=code):
                self.agent._critique.return_value = {"needs_revision": False, "issues": []}
                events, stream = self.run_agent([
                    LLMError("合成可恢复错误", code=code), self.completed("恢复后的完整初稿"),
                ])
                self.assertEqual(stream.call_count, 2)
                self.assertEqual(stream.call_args_list[0].args, stream.call_args_list[1].args)
                self.assertEqual(events[-1]["type"], "done")
                self.assertEqual(self.visible_report(events), "恢复后的完整初稿")

    def test_thinking_only_attempts_never_become_report_text(self):
        events, stream = self.run_agent([
            iter([{"message": {"thinking": "思考不等于报告"}}, {"done": True}]),
            iter([{"message": {"content": "<think>思考不等于报告</think>"}}, {"done": True}]),
        ])
        self.assertEqual(stream.call_count, 2)
        self.assert_no_success(events)
        self.assertEqual(self.visible_report(events), "")
        self.agent._critique.assert_not_called()

    def test_revision_keeps_draft_until_first_nonblank_output(self):
        events, _ = self.run_agent([
            self.completed(self.original), iter([
                {"message": {"thinking": "修订阶段的合成思考"}},
                {"message": {"content": "\n"}},
                {"message": {"content": "修订后的完整正文"}, "done": True},
            ]),
        ])
        thinking = next(index for index, event in enumerate(events)
                        if event["type"] == "thinking" and event["text"] == "修订阶段的合成思考")
        first_reset = next(index for index, event in enumerate(events) if event["type"] == "reset_output")
        self.assertGreater(first_reset, thinking)
        self.assertEqual(self.visible_report(events), '\n修订后的完整正文')


class RetrievalScoreTests(SimpleTestCase):
    def test_exact_match_has_full_similarity_score(self):
        from .retriever import _retrieve
        response = {
            "ids": [["synthetic-chunk"]], "distances": [[0.0]],
            "documents": [["Synthetic knowledge"]], "metadatas": [[{}]],
        }
        with patch("knowledge.retriever.query_similar", return_value=response):
            results = _retrieve("case_report", "Synthetic query", 1)
        self.assertEqual(results[0]["score"], 1.0)
