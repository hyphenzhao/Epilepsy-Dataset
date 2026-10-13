"""Legacy knowledge chat API backed by the common multi-provider adapter."""

import json
import logging
from typing import List

from epilepsy.llm import chat_completion, LLMError
from .provider_config import get_role_config, get_active_server_config

log = logging.getLogger(__name__)
DEFAULT_CHAT_MODEL = "qwen2.5:7b"
DEFAULT_TIMEOUT = 120


def _get_extraction_model() -> str:
    return get_role_config("extraction").model


def _get_ollama_base_url() -> str:
    return get_active_server_config().base_url


def ollama_chat(
    messages: List[dict], model: str | None = None, temperature: float = 0.1,
    stream: bool = False, format_json: bool = False, timeout: int = DEFAULT_TIMEOUT,
    role: str = "extraction",
) -> dict:
    """Return the historical message.content shape for every chat provider."""
    if stream:
        raise ValueError("知识库同步调用不支持流式输出。")
    config = get_role_config(role, model=model)
    content = chat_completion(
        config, messages, temperature=temperature, format_json=format_json, timeout=timeout,
    )
    return {"message": {"role": "assistant", "content": content}}


def ollama_chat_json(
    messages: List[dict], model: str | None = None, temperature: float = 0.1,
    timeout: int = DEFAULT_TIMEOUT, fallback: dict | None = None,
    role: str = "extraction", strict: bool = False,
) -> dict:
    if fallback is None:
        fallback = {}
    try:
        response = ollama_chat(
            messages, model=model, temperature=temperature, format_json=True,
            timeout=timeout, role=role,
        )
        content = response["message"]["content"].strip()
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("Expected a JSON object")
        return parsed
    except (ValueError, TypeError, KeyError) as exc:
        if strict:
            raise LLMError("模型未返回有效的 JSON 对象，请检查模型或指令后重试。") from None
        log.warning("模型未返回有效的 JSON 对象，使用回退值 (%s)", type(exc).__name__)
    except Exception as exc:
        if strict:
            if isinstance(exc, LLMError):
                raise
            raise LLMError("模型调用失败，请检查服务、模型、密钥和配额后重试。", getattr(exc, "status_code", 502)) from None
        log.warning("知识库模型调用失败，使用回退值 (%s)", type(exc).__name__)
    return fallback


def summarize_edit(original_markdown: str, edited_markdown: str) -> str:
    prompt = (
        "请用一句话（50字以内）总结以下两份报告之间的主要实质性修改（中文）：\n\n"
        f"原始报告：\n{original_markdown[:1500]}\n\n修改后报告：\n{edited_markdown[:1500]}"
    )
    messages = [
        {"role": "system", "content": "你是一个报告编辑助手，请简洁总结报告修改要点。只输出一句话摘要。"},
        {"role": "user", "content": prompt},
    ]
    try:
        response = ollama_chat(messages, temperature=0.1, role="extraction")
        return response["message"]["content"].strip()[:200]
    except Exception as exc:
        log.warning("编辑摘要生成失败 (%s)", type(exc).__name__)
        return ""
