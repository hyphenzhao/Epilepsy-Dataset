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

from .models import KnowledgeSettings
from .ollama_client import _get_ollama_base_url
from .agent_prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    AGENT_DRAFT_SYSTEM_PROMPT,
    CRITIQUE_SYSTEM_PROMPT,
    REVISE_SYSTEM_PROMPT,
)

log = logging.getLogger(__name__)


class AgentPipeline:
    """Agent 多步推理流水线，作为 SSE 事件生成器"""

    def __init__(self, patient, server):
        self.patient = patient
        self.server = server
        settings = KnowledgeSettings.load()
        self.think_model = settings.extraction_model or "qwen2.5:7b"
        self.generate_model = server.model or "qwen2.5:14b"
        self.draft_text = ""  # 累积草稿文本，供 Step 4 使用

    # ============================================================
    #  入口
    # ============================================================

    def execute(self):
        """执行五步推理，yield SSE 事件字符串"""
        try:
            # 获取患者数据
            from epilepsy.views_helper import build_patient_export_json
            self.export_json = build_patient_export_json(self.patient)
        except Exception as exc:
            yield self._error(f"获取患者数据失败: {exc}")
            yield self._done()
            return

        try:
            # Step 1: 分析
            yield self._progress(1, 5, "分析患者关键特征...")
            analysis = self._analyze()
            features_str = "、".join(analysis.get("key_features", ["未识别到突出特征"])[:5])
            yield self._progress(1, 5, f"识别到：{features_str}",
                                detail=self._format_analysis(analysis))

            # Step 2: 检索
            yield self._progress(2, 5, "检索知识库...")
            retrieval = self._retrieve(analysis)
            yield self._progress(2, 5,
                f"找到 {retrieval['total_found']} 条参考（病例 {len(retrieval['cases'])} + 文献 {len(retrieval['literature'])}）")

            # Step 3: 草稿（流式）
            yield self._progress(3, 5, "撰写报告初稿...")
            self.draft_text = ""
            for event in self._draft(analysis, retrieval):
                yield event
            yield self._progress(3, 5,
                f"初稿完成（{len(self.draft_text)} 字符）")

            # Step 4: 自查
            yield self._progress(4, 5, "自查审核中...")
            critique = self._critique(analysis)
            issues = critique.get("issues", [])
            yield self._progress(4, 5,
                f"发现 {len(issues)} 个问题",
                detail=self._format_critique(critique))

            # Step 5: 修订（流式）
            if critique.get("needs_revision", False) or issues:
                yield self._progress(5, 5, "修订定稿...")
                # 保存草稿用于 prompt，清空 draft_text 让修订版独立累积
                draft_for_revision = self.draft_text
                self.draft_text = ""
                for event in self._revise(critique, draft_for_revision):
                    yield event
                yield self._progress(5, 5, "修订完成")
            else:
                yield self._progress(5, 5, "无需修订，初稿即终稿 ✓")

            yield self._done()

        except Exception as exc:
            log.exception("Agent pipeline 异常")
            yield self._error(f"Agent 流水线异常: {exc}")
            yield self._done()

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
        return ollama_chat_json(messages, model=self.think_model, temperature=0.1,
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

        yield from self._stream_chat(messages, self.generate_model)

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
        return ollama_chat_json(messages, model=self.think_model, temperature=0.1,
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
        yield from self._stream_chat(messages, self.generate_model)

    # ============================================================
    #  Ollama 流式调用（复用 stream_ollama_report 的核心逻辑）
    # ============================================================

    def _stream_chat(self, messages, model):
        """调用 Ollama /api/chat 流式接口，yield SSE 事件"""
        base_url = _get_ollama_base_url()
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if getattr(self.server, "enable_thinking", False):
            payload["think"] = True

        think_state = {"in_think": False}
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=600) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    message = obj.get("message") or {}
                    direct_thinking = obj.get("thinking") or obj.get("reasoning")
                    if not direct_thinking and isinstance(message, dict):
                        direct_thinking = message.get("thinking") or message.get("reasoning")

                    content_text = obj.get("response")
                    if not content_text and isinstance(message, dict):
                        content_text = message.get("content") or message.get("response")

                    thinking_text = ""
                    output_text = ""

                    if direct_thinking:
                        thinking_text += direct_thinking

                    if content_text:
                        tagged_thinking, answer = self._split_thinking(content_text, think_state)
                        if tagged_thinking:
                            thinking_text += tagged_thinking
                        if answer:
                            output_text = answer
                        elif not direct_thinking:
                            output_text = content_text

                    if thinking_text:
                        yield self._thinking(thinking_text)
                    if output_text:
                        self.draft_text += output_text
                        yield self._output(output_text)
                    if obj.get("done"):
                        break

        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"Ollama HTTP {exc.code}: {error_body}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接 Ollama: {exc}")

    def _split_thinking(self, text, state):
        """处理 <think>...</think> 标签，返回 (thinking_text, answer_text)"""
        thinking = ""
        answer = ""
        buf = text

        while "<think>" in buf or "</think>" in buf:
            if state["in_think"]:
                end = buf.find("</think>")
                if end >= 0:
                    thinking += buf[:end]
                    buf = buf[end + len("</think>"):]
                    state["in_think"] = False
                else:
                    thinking += buf
                    buf = ""
            else:
                start = buf.find("<think>")
                if start >= 0:
                    answer += buf[:start]
                    buf = buf[start + len("<think>"):]
                    state["in_think"] = True
                else:
                    # </think> without <think> — treat as answer
                    end = buf.find("</think>")
                    answer += buf[:end] if end >= 0 else buf
                    if end >= 0:
                        buf = buf[end + len("</think>"):]
                    else:
                        buf = ""
        if buf:
            if state["in_think"]:
                thinking += buf
            else:
                answer += buf
        return thinking, answer

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
