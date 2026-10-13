import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

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

        def revise(critique, original):
            self.assertEqual(original, "Synthetic draft")
            self.assertEqual(agent.draft_text, "")
            agent.draft_text += "Synthetic final report"
            yield agent._output("Synthetic final report")

        agent._draft = draft
        agent._revise = revise
        with patch("epilepsy.views_helper.build_patient_export_json", return_value={"sections": []}):
            events = [json.loads(event.removeprefix("data: ")) for event in agent.execute()]

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
        agent._draft = lambda *args: iter([agent._output("Synthetic final draft")])
        agent._revise = Mock()
        with patch("epilepsy.views_helper.build_patient_export_json", return_value={"sections": []}):
            events = [json.loads(event.removeprefix("data: ")) for event in agent.execute()]
        self.assertFalse(any(event["type"] == "reset_output" for event in events))
        self.assertEqual(self.event_text(events, "output"), "Synthetic final draft")
        agent._revise.assert_not_called()


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
