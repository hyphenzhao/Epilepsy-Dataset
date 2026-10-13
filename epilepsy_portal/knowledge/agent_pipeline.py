"""
Agent 多步推理流水线

五步推理链：
  Step 1: 分析 → 提取关键特征（qwen2.5:7b，非流式）
  Step 2: 检索 → 根据特征检索知识库（向量库）
  Step 3: 草稿 → 生成报告初稿（qwen2.5:14b，流式）
  Step 4: 自查 → 审核草稿找问题（qwen2.5:7b，非流式）
  Step 5: 修订 → 根据审核意见修改（qwen2.5:14b，流式）
"""

import json
import logging
import re
import urllib.request
import urllib.error

from epilepsy.llm import LLMError, _safe_metadata
from .models import KnowledgeSettings
from .ollama_client import _get_ollama_base_url
from .agent_prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    AGENT_DRAFT_SYSTEM_PROMPT,
    CRITIQUE_SYSTEM_PROMPT,
    REVISE_SYSTEM_PROMPT,
)

log = logging.getLogger(__name__)
RETRYABLE_CODES = frozenset({
    "empty_response", "incomplete_stream", "timeout", "transient_http", "connection_error",
})
ERROR_REASONS = {
    "empty_response": "服务未返回正文。",
    "incomplete_stream": "输出流提前结束。",
    "timeout": "请求超时。",
    "transient_http": "服务暂时不可用。",
    "connection_error": "无法连接模型服务。",
    "authentication": "认证失败，请检查 API Key 和权限。",
    "rate_limit": "限额或余额受限。",
    "output_limit": "达到输出上限，报告未完成。",
    "nontext_response": "模型未返回完整正文。",
}


class AgentPipeline:
    """Agent 多步推理流水线，作为 SSE 事件生成器"""

    def __init__(self, patient, server):
        self.patient = patient
        self.server = server
        settings = KnowledgeSettings.load()
        from .provider_config import get_role_config
        thinking = get_role_config("thinking", settings=settings)
        self.think_provider = thinking.provider
        self.think_model = thinking.model
        self.generate_model = server.model or "qwen2.5:14b"
        self.draft_text = ""  # 累积草稿文本，供 Step 4 使用

    # ============================================================
    #  入口
    # ============================================================

    def execute(self):
        """执行五步推理，yield SSE 事件字符串"""
        stage = "export"
        completed_draft = ""
        self._report_attempt = 1
        try:
            # 获取患者数据
            from epilepsy.views_helper import build_patient_export_json
            self.export_json = build_patient_export_json(self.patient)
        except Exception:
            self._log_diagnostic(stage, 1, "program_error", level=logging.ERROR)
            yield self._error("无法读取报告数据，请稍后重试。")
            return

        try:
            # Step 1: 分析
            stage = "analysis"
            yield self._progress(1, 5, "分析患者关键特征...")
            analysis = self._analyze()
            features_str = "、".join(analysis.get("key_features", ["未识别到突出特征"])[:5])
            yield self._progress(1, 5, f"识别到：{features_str}",
                                detail=self._format_analysis(analysis))

            # Step 2: 检索
            stage = "retrieval"
            yield self._progress(2, 5, "检索知识库...")
            retrieval = self._retrieve(analysis)
            yield self._progress(2, 5,
                f"找到 {retrieval['total_found']} 条参考（病例 {len(retrieval['cases'])} + 文献 {len(retrieval['literature'])}）")

            # Step 3: 草稿（流式）
            stage = "draft"
            yield self._progress(3, 5, "撰写报告初稿...")
            self.draft_text = ""
            for event in self._draft(analysis, retrieval):
                yield event
            if not self.draft_text.strip():
                raise LLMError("初稿没有返回正文。", code="empty_response")
            completed_draft = self.draft_text
            yield self._progress(3, 5,
                f"初稿完成（{len(self.draft_text)} 字符）")

            # Step 4: 自查
            stage = "critique"
            self._report_attempt = 1
            yield self._progress(4, 5, "自查审核中...")
            critique = self._critique(analysis)
            issues = critique.get("issues", [])
            yield self._progress(4, 5,
                f"发现 {len(issues)} 个问题",
                detail=self._format_critique(critique))

            # Step 5: 修订（流式）
            if critique.get("needs_revision", False) or issues:
                stage = "revision"
                yield self._progress(5, 5, "修订定稿...")
                # The completed draft remains visible until revised text arrives.
                for event in self._revise(critique, completed_draft):
                    yield event
                yield self._progress(5, 5, "修订完成")
            else:
                yield self._progress(5, 5, "无需修订，初稿即终稿 ✓")

            yield self._done()

        except Exception as exc:
            code = getattr(exc, "code", "service_error") if isinstance(exc, LLMError) else "program_error"
            self._log_diagnostic(stage, self._report_attempt, code, level=logging.ERROR)
            if completed_draft:
                self.draft_text = completed_draft
                yield self._event("reset_output", {})
                yield self._output(completed_draft)
                text = ("修订失败，已保留初稿（未经修订），请人工核对后重试。"
                        if stage == "revision" else
                        "报告审核失败，已保留初稿（未经完整审核），请人工核对后重试。")
            elif stage == "draft":
                text = "初稿生成失败，报告未完成，请人工核对后重试。"
            else:
                text = "Agent 处理失败，报告未完成，请检查服务配置后重试。"
            reason = ERROR_REASONS.get(code, "服务或处理异常。")
            yield self._error(text + " 原因：" + reason)

    def _log_diagnostic(self, stage, attempt, code, *, level=logging.WARNING):
        provider = getattr(self.server, "provider", "ollama")
        model = getattr(self, "generate_model", self.server.model)
        if stage in {"analysis", "critique"}:
            provider = getattr(self, "think_provider", provider)
            model = getattr(self, "think_model", model)
        log.log(level, "Agent provider=%s model=%s stage=%s attempt=%s code=%s",
                _safe_metadata(provider), _safe_metadata(model),
                _safe_metadata(stage), attempt, _safe_metadata(code))

    def _stream_report_stage(self, messages, model, *, stage):
        """Retry the same stage input once and keep failed attempts separate."""
        preserved = self.draft_text if stage == "revision" else ""
        step, label = (5, "修订") if stage == "revision" else (3, "初稿生成")
        for attempt in (1, 2):
            self._report_attempt = attempt
            self.draft_text = ""
            if attempt == 2:
                yield self._progress(step, 5, f"{label}未完成，正在进行第 2 次尝试...")
                yield self._event("reset_output", {})
                if preserved:
                    yield self._output(preserved)
            output_started = False
            pending_output = []
            try:
                for event in self._stream_chat(messages, model):
                    payload = json.loads(event.removeprefix("data: "))
                    if payload.get("type") == "output" and not output_started:
                        pending_output.append(event)
                        if not self.draft_text.strip():
                            continue
                        if preserved:
                            yield self._event("reset_output", {})
                        yield from pending_output
                        pending_output = []
                        output_started = True
                    else:
                        yield event
                if not self.draft_text.strip():
                    raise LLMError("LLM 服务没有返回报告正文。", code="empty_response")
                self._log_diagnostic(stage, attempt, "success", level=logging.INFO)
                return
            except LLMError as exc:
                self._log_diagnostic(stage, attempt, exc.code)
                if attempt == 2 or exc.code not in RETRYABLE_CODES:
                    raise

    # ============================================================
    #  Step 1: 分析
    # ============================================================

    def _analyze(self) -> dict:
        user_text = json.dumps(self.export_json, ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": f"请分析以下患者数据，提取关键临床特征：\n\n{user_text[:5000]}"},
        ]
        from .ollama_client import ollama_chat_json
        return ollama_chat_json(messages, model=self.think_model, temperature=0.1, role="thinking", strict=True,
                                fallback={"key_features": [], "complexity": "moderate",
                                          "red_flags": [], "missing_data": [], "suggested_focus": ""})

    def _format_analysis(self, analysis: dict) -> str:
        lines = []
        if analysis.get("suggested_focus"):
            lines.append(f"关注方向：{analysis['suggested_focus']}")
        if analysis.get("complexity"):
            complexity_map = {"simple": "简单", "moderate": "中等", "complex": "复杂"}
            lines.append(f"复杂度：{complexity_map.get(analysis['complexity'], analysis['complexity'])}")
        if analysis.get("red_flags"):
            lines.append(f"⚠ 风险信号：{'、'.join(analysis['red_flags'])}")
        if analysis.get("missing_data"):
            lines.append(f"⚠ 缺失数据：{'、'.join(analysis['missing_data'])}")
        return "\n".join(lines)

    # ============================================================
    #  Step 2: 检索
    # ============================================================

    def _retrieve(self, analysis: dict) -> dict:
        # 用 key_features 构建更精准的查询
        features = analysis.get("key_features", [])
        suggested = analysis.get("suggested_focus", "")

        if features or suggested:
            query = "。".join(features + ([suggested] if suggested else []))
            # 构造一个伪 export_json 给 retrieve_context
            pseudo_export = {"sections": [{"title": "关键特征", "items": [
                {"label": "", "value": query}
            ]}]}
        else:
            pseudo_export = self.export_json

        from .retriever import retrieve_context
        return retrieve_context(pseudo_export)

    # ============================================================
    #  Step 3: 草稿（流式）
    # ============================================================

    def _draft(self, analysis: dict, retrieval: dict):
        from .retriever import format_context_for_prompt

        system_prompt = AGENT_DRAFT_SYSTEM_PROMPT
        custom = (getattr(self.server, "prompt", "") or "").strip()
        if custom:
            system_prompt += f"\n\n## 额外要求\n{custom}"

        messages = [{"role": "system", "content": system_prompt}]

        # 注入分析结果
        analysis_text = f"## 病例分析要点\n关注方向：{analysis.get('suggested_focus', '未指定')}\n关键特征：{'、'.join(analysis.get('key_features', []))}"
        if analysis.get("missing_data"):
            analysis_text += f"\n注意以下数据缺失：{'、'.join(analysis['missing_data'])}"
        if analysis.get("red_flags"):
            analysis_text += f"\n⚠ 风险提示：{'、'.join(analysis['red_flags'])}"
        messages.append({"role": "user", "content": f"【分析指导】\n{analysis_text}"})

        # 注入检索结果
        context_text = format_context_for_prompt(retrieval)
        if context_text:
            messages.append({"role": "user", "content": f"【参考以下相似病例和文献作为写作参考，请勿直接复制】\n\n{context_text}"})

        # 患者数据
        user_text = json.dumps(self.export_json, ensure_ascii=False, indent=2)
        messages.append({"role": "user", "content": f"请基于以下患者数据生成癫痫术前评估报告初稿：\n\n{user_text}"})

        yield from self._stream_report_stage(messages, self.generate_model, stage="draft")

    # ============================================================
    #  Step 4: 自查
    # ============================================================

    def _critique(self, analysis: dict) -> dict:
        user_text = json.dumps(self.export_json, ensure_ascii=False, indent=2)
        draft_summary = self.draft_text[:4000] if self.draft_text else "（草稿为空）"

        messages = [
            {"role": "system", "content": CRITIQUE_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"## 患者原始数据\n{user_text[:3000]}\n\n"
                f"## 报告初稿\n{draft_summary}\n\n"
                f"请对照患者数据审核以上报告初稿，找出问题。"
            )},
        ]
        from .ollama_client import ollama_chat_json
        return ollama_chat_json(messages, model=self.think_model, temperature=0.1, role="thinking", strict=True,
                                fallback={"issues": [], "overall_score": 8, "needs_revision": False, "summary": ""})

    def _format_critique(self, critique: dict) -> str:
        lines = []
        score = critique.get("overall_score", "?")
        lines.append(f"总体评分：{score}/10")
        if critique.get("summary"):
            lines.append(critique["summary"])
        issues = critique.get("issues", [])
        for iss in issues:
            sev_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(iss.get("severity", ""), "•")
            lines.append(f"  {sev_icon} [{iss.get('severity', '?')}] {iss.get('section', '')}：{iss.get('problem', '')}")
        return "\n".join(lines)

    # ============================================================
    #  Step 5: 修订（流式）
    # ============================================================

    def _revise(self, critique: dict, draft_text: str):
        issues = critique.get("issues", [])
        issues_text = "\n".join(
            f"- [{iss.get('severity', '?')}] {iss.get('section', '')}：{iss.get('problem', '')}\n  建议：{iss.get('suggestion', '')}"
            for iss in issues
        )

        messages = [
            {"role": "system", "content": REVISE_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"## 报告初稿\n{draft_text[:5000]}\n\n"
                f"## 审阅意见\n{issues_text}\n\n"
                f"## 患者原始数据（供核实参考）\n"
                f"{json.dumps(self.export_json, ensure_ascii=False, indent=2)[:3000]}\n\n"
                f"请根据审阅意见修订以上报告。"
            )},
        ]
        yield from self._stream_report_stage(messages, self.generate_model, stage="revision")

    # ============================================================
    #  Ollama 流式调用（复用 stream_ollama_report 的核心逻辑）
    # ============================================================

    def _stream_chat(self, messages, model):
        """Stream the selected report provider for both draft and revision."""
        from epilepsy.llm import config_from_server, stream_chat
        think_state = {"in_think": False}
        has_output = False
        completed = False
        for obj in stream_chat(
            config_from_server(self.server, model=model), messages,
            think=getattr(self.server, "enable_thinking", False), timeout=600,
        ):
            message = obj.get("message") or {}
            thinking_text = obj.get("thinking") or obj.get("reasoning") or message.get("thinking") or message.get("reasoning") or ""
            content = obj.get("response") or message.get("content") or message.get("response") or ""
            output_text = ""
            if content:
                tagged_thinking, output_text = self._split_thinking(content, think_state)
                thinking_text += tagged_thinking
            if obj.get("done"):
                remaining_thinking, remaining_output = self._split_thinking("", think_state, final=True)
                thinking_text += remaining_thinking
                output_text += remaining_output
            if thinking_text:
                yield self._thinking(thinking_text)
            if output_text:
                has_output = has_output or bool(output_text.strip())
                self.draft_text += output_text
                yield self._output(output_text)
            if obj.get("done"):
                if not has_output:
                    raise LLMError("LLM 服务返回了空回答，报告未完成。", code="empty_response")
                completed = True
                break
        if not completed:
            raise LLMError("LLM 输出流提前结束，报告未完成。", code="incomplete_stream")

    def _split_thinking(self, text, state, *, final=False):
        """处理 <think>...</think> 标签，返回 (thinking_text, answer_text)"""
        from .streaming import split_thinking_delta
        return split_thinking_delta(text, state, final=final)

    # ============================================================
    #  SSE 事件格式化
    # ============================================================

    def _progress(self, step, total, text, detail=""):
        return self._event("progress", {"step": step, "total": total, "text": text, "detail": detail})

    def _thinking(self, text):
        return self._event("thinking", {"text": text})

    def _output(self, text):
        return self._event("output", {"text": text})

    def _error(self, text):
        return self._event("error", {"text": text})

    def _done(self):
        return self._event("done", {})

    def _event(self, evt_type, payload):
        payload["type"] = evt_type
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
