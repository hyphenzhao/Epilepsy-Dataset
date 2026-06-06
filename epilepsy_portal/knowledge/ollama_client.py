"""
Ollama Chat 客户端

封装 Ollama /api/chat 同步调用，用于：
  - 元数据提取（文档 → 结构化 JSON）
  - 编辑修改摘要
  - 其他非流式 LLM 调用

遵循与 embeddings.py 相同的 urllib.request 模式。
"""

import json
import logging
import urllib.request
import urllib.error
from typing import List, Optional

log = logging.getLogger(__name__)

DEFAULT_CHAT_MODEL = "qwen2.5:7b"
DEFAULT_TIMEOUT = 120  # 秒


def _get_extraction_model() -> str:
    """获取配置的提取模型（从 KnowledgeSettings 读取）"""
    try:
        from .models import KnowledgeSettings
        model = KnowledgeSettings.load().extraction_model
        return model or DEFAULT_CHAT_MODEL
    except Exception:
        return DEFAULT_CHAT_MODEL


def _get_ollama_base_url() -> str:
    """从 epilepsy 模块获取启用的 Ollama 服务器地址，fallback 到默认值"""
    try:
        from epilepsy.models import OllamaServer
        server = OllamaServer.objects.filter(is_enabled=True).first()
        if server:
            return server.base_url
    except Exception:
        pass
    return "http://localhost:11434"


def ollama_chat(
    messages: List[dict],
    model: str | None = None,
    temperature: float = 0.1,
    stream: bool = False,
    format_json: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """
    调用 Ollama /api/chat，返回完整响应 JSON。

    参数:
      messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
      model: 模型名，默认 qwen2.5:7b
      temperature: 温度参数，默认 0.1（确定性输出）
      stream: 是否流式（元数据提取不需要）
      format_json: 是否强制 JSON 格式输出
      timeout: 超时秒数

    返回:
      Ollama API 完整响应 JSON
    """
    if model is None:
        model = _get_extraction_model()

    base_url = _get_ollama_base_url()
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": {"temperature": temperature},
    }
    if format_json:
        payload["format"] = "json"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"Ollama chat 请求失败 (HTTP {exc.code}, model={model}): {error_body}"
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 Ollama 服务 ({base_url}): {exc}")


def ollama_chat_json(
    messages: List[dict],
    model: str | None = None,
    temperature: float = 0.1,
    timeout: int = DEFAULT_TIMEOUT,
    fallback: dict | None = None,
) -> dict:
    """
    调用 Ollama /api/chat 并以 JSON 格式返回 message.content。

    当 Ollama 的 format=json 生效时，response["message"]["content"]
    本身就是合法 JSON 字符串。

    参数:
      messages: 消息列表
      model: 模型名
      temperature: 温度
      timeout: 超时
      fallback: JSON 解析失败时的回退值（默认返回空 dict）

    返回:
      解析后的 Python dict
    """
    if fallback is None:
        fallback = {}

    try:
        response = ollama_chat(
            messages=messages,
            model=model,
            temperature=temperature,
            format_json=True,
            timeout=timeout,
        )
        content = (response.get("message") or {}).get("content", "")
        if not content:
            log.warning("Ollama chat 返回空 content，使用 fallback")
            return fallback

        # 尝试解析 JSON
        # 某些模型即使在 format=json 下也可能包裹 markdown 代码块
        content = content.strip()
        if content.startswith("```"):
            # 去除 ```json ... ``` 包裹
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        return json.loads(content)
    except json.JSONDecodeError as exc:
        log.warning("Ollama chat 返回了非 JSON 内容: %s", exc)
        log.debug("原始 content: %s", content[:500] if 'content' in dir() else "N/A")
        return fallback
    except Exception as exc:
        log.error("ollama_chat_json 异常: %s", exc)
        return fallback


def summarize_edit(original_markdown: str, edited_markdown: str) -> str:
    """
    用 LLM 总结报告修改要点（简短中文摘要）。

    用于 ReportEditHistory.edit_summary 字段。
    """
    prompt = (
        "请用一句话（50字以内）总结以下两份报告之间的主要实质性修改（中文）：\n\n"
        f"原始报告：\n{original_markdown[:1500]}\n\n"
        f"修改后报告：\n{edited_markdown[:1500]}"
    )
    messages = [
        {
            "role": "system",
            "content": "你是一个报告编辑助手，请简洁总结报告修改要点。只输出一句话摘要，不要额外说明。",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = ollama_chat(
            messages=messages,
            model=DEFAULT_CHAT_MODEL,
            temperature=0.1,
            format_json=False,
        )
        content = (response.get("message") or {}).get("content", "")
        return content.strip()[:200]  # 限制长度
    except Exception as exc:
        log.warning("编辑摘要生成失败: %s", exc)
        return ""
