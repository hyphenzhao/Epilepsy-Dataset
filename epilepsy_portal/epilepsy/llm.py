"""Small HTTP adapter for Ollama, OpenAI compatible APIs and Anthropic."""

from dataclasses import dataclass, field
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

PROVIDER_CHOICES = [("ollama", "Ollama"), ("openai", "OpenAI / 兼容 API"), ("anthropic", "Anthropic")]
DEFAULT_URLS = {"ollama": "http://localhost:11434", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1"}


class LLMError(RuntimeError):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def normalize_base_url(provider, value):
    if provider not in DEFAULT_URLS:
        raise ValueError("请选择有效的 LLM 服务类型。")
    value = (value or DEFAULT_URLS[provider]).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API 地址的端口无效。") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)):
        raise ValueError("API 地址应为完整的 HTTP(S) 地址，不能包含登录信息、查询参数或片段。")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("API 地址的端口无效。")
    if provider != "ollama" and not parsed.path.rstrip("/"):
        value += "/v1"
    if provider == "ollama":
        from .ollama_models import resolve_ollama_url
        value = resolve_ollama_url(value)
    return value


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "ollama"
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""

    def __post_init__(self):
        object.__setattr__(self, "base_url", normalize_base_url(self.provider, self.base_url))
        key = (self.api_key or "").strip()
        if any(ord(char) < 32 or ord(char) > 126 for char in key):
            raise ValueError("API Key 含有无效字符，请重新填写。")
        object.__setattr__(self, "api_key", key)


def config_from_server(server, model=None):
    if server is None:
        return LLMConfig(model=model or "")
    return LLMConfig(
        provider=getattr(server, "provider", "ollama") or "ollama",
        base_url=server.base_url,
        api_key=getattr(server, "api_key", "") or "",
        model=model if model is not None else server.model,
    )


def _headers(config):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.provider == "anthropic":
        headers.update({"x-api-key": config.api_key, "anthropic-version": "2023-06-01"})
    elif config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key
    return headers


def _request(config, path, payload=None):
    return urllib.request.Request(
        config.base_url + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
        headers=_headers(config), method="POST" if payload is not None else "GET",
    )


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API credentials must never be forwarded to a redirected address.
        return None


def urlopen(request, timeout):
    return urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=timeout)


def _open(config, request, timeout):
    try:
        return urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            message = "API 地址发生重定向，请填写服务商提供的最终 API 地址后重试。"
        elif exc.code in (401, 403):
            message = "API 认证失败，请检查 API Key 和账号权限。"
        elif exc.code == 429:
            message = "API 请求受限，请检查余额或限额后重试。"
        else:
            message = f"LLM 服务返回 HTTP {exc.code}，请检查模型名称和服务配置。"
        raise LLMError(message) from None
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        raise LLMError("LLM 请求超时，请稍后重试。" if timed_out else "无法连接 LLM 服务，请检查 API 地址和服务状态。", 504 if timed_out else 502) from None


def _load_json(response):
    try:
        payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError()
        return payload
    except (ValueError, UnicodeError) as exc:
        raise LLMError("LLM 服务返回了无效的 JSON 数据。") from exc


def _check_error(obj, config):
    error = obj.get("error")
    if error:
        # Ollama local errors are useful; authenticated API errors may echo input.
        message = str(error)[:300] if config.provider == "ollama" and not config.api_key else "服务返回错误，请检查模型、余额和接口配置。"
        if config.api_key:
            message = message.replace(config.api_key, "[已隐藏]")
        raise LLMError("LLM 生成失败: " + message)


def _check_stop(reason):
    if reason in ("length", "max_tokens", "model_context_window_exceeded"):
        raise LLMError("模型输出达到长度上限，报告未完成，请调整模型或输出上限后重试。")
    if reason in ("content_filter", "refusal", "tool_calls", "function_call", "tool_use", "pause_turn"):
        raise LLMError("模型未返回完整的文本回答，请调整输入或模型后重试。")


def _chat_payload(config, messages, *, stream, temperature=None, max_tokens=None, think=False, format_json=False):
    if not config.model:
        raise LLMError("请先选择 LLM 模型。", 400)
    payload = {"model": config.model, "messages": messages, "stream": stream}
    if config.provider == "ollama":
        if think:
            payload["think"] = True
        options = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if options:
            payload["options"] = options
        if format_json:
            payload["format"] = "json"
        return "/api/chat", payload
    if format_json:
        messages = list(messages) + [{"role": "user", "content": "请仅返回合法的 JSON 对象，不要添加 Markdown 代码块或其他说明。"}]
        payload["messages"] = messages
    if config.provider == "openai":
        reasoning_model = bool(re.match(r"^(o[134](?:-|$)|gpt-[56](?:[.-]|$))", config.model))
        if temperature is not None and not reasoning_model:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_completion_tokens" if reasoning_model else "max_tokens"] = max_tokens
        return "/chat/completions", payload
    system = [item.get("content", "") for item in messages if item.get("role") in ("system", "developer")]
    payload["messages"] = [item for item in messages if item.get("role") not in ("system", "developer")]
    if system:
        payload["system"] = "\n\n".join(system)
    payload["max_tokens"] = max_tokens or 8192
    if think:
        if re.match(r"^claude-(?:opus|sonnet)-(?:4-6|[5-9])", config.model):
            payload["thinking"] = {"type": "adaptive"}
        else:
            payload["thinking"] = {"type": "enabled", "budget_tokens": 1024}
        payload["max_tokens"] = max(payload["max_tokens"], 2048)
    elif temperature is not None:
        payload["temperature"] = temperature
    return "/messages", payload


def chat_completion(config, messages, *, temperature=None, max_tokens=None, think=False, format_json=False, timeout=120):
    path, payload = _chat_payload(config, messages, stream=False, temperature=temperature, max_tokens=max_tokens, think=think, format_json=format_json)
    with _open(config, _request(config, path, payload), timeout) as response:
        obj = _load_json(response)
    _check_error(obj, config)
    if config.provider == "ollama":
        _check_stop(obj.get("done_reason"))
        text = (obj.get("message") or {}).get("content") or obj.get("response") or ""
    elif config.provider == "openai":
        choices = obj.get("choices") or []
        if not choices:
            raise LLMError("LLM 服务未返回回答。")
        _check_stop(choices[0].get("finish_reason"))
        text = (choices[0].get("message") or {}).get("content") or ""
    else:
        _check_stop(obj.get("stop_reason"))
        text = "".join(block.get("text", "") for block in obj.get("content", []) if block.get("type") == "text")
    if not isinstance(text, str) or not text.strip():
        raise LLMError("LLM 服务返回了空回答。")
    return text


def _sse_data(response):
    lines = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if lines:
                yield "\n".join(lines)
                lines = []
        elif line.startswith("data:"):
            lines.append(line[5:].lstrip(" "))
    if lines:
        yield "\n".join(lines)


def stream_chat(config, messages, *, temperature=None, max_tokens=None, think=False, timeout=600):
    """Yield content/thinking/done events in the existing report stream format."""
    path, payload = _chat_payload(config, messages, stream=True, temperature=temperature, max_tokens=max_tokens, think=think)
    completed = False
    has_output = False
    with _open(config, _request(config, path, payload), timeout) as response:
        if config.provider == "ollama":
            for raw in response:
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise LLMError("LLM 输出流格式无效，报告未完成。") from exc
                _check_error(obj, config)
                _check_stop(obj.get("done_reason"))
                content = (obj.get("message") or {}).get("content") or obj.get("response") or ""
                has_output = has_output or bool(content.strip())
                if obj.get("done") and not has_output:
                    raise LLMError("LLM 服务返回了空回答，报告未完成。")
                yield obj
                if obj.get("done"):
                    completed = True
                    break
        else:
            for data in _sse_data(response):
                if data == "[DONE]" and config.provider == "openai":
                    if not has_output:
                        raise LLMError("LLM 服务返回了空回答，报告未完成。")
                    completed = True
                    yield {"done": True}
                    break
                try:
                    obj = json.loads(data)
                except (ValueError, UnicodeError) as exc:
                    raise LLMError("LLM 输出流格式无效，报告未完成。") from exc
                _check_error(obj, config)
                if config.provider == "openai":
                    for choice in obj.get("choices") or []:
                        if choice.get("index", 0) != 0:
                            continue
                        _check_stop(choice.get("finish_reason"))
                        delta = choice.get("delta") or {}
                        message = {"content": delta.get("content") or "", "thinking": delta.get("reasoning_content") or delta.get("reasoning") or ""}
                        has_output = has_output or bool(message["content"].strip())
                        if message["content"] or message["thinking"]:
                            yield {"message": message, "done": False}
                else:
                    kind = obj.get("type")
                    if kind == "message_delta":
                        _check_stop((obj.get("delta") or {}).get("stop_reason"))
                    elif kind in ("content_block_delta", "content_block_start"):
                        delta = obj.get("delta") or obj.get("content_block") or {}
                        message = {"content": delta.get("text") or "", "thinking": delta.get("thinking") or ""}
                        has_output = has_output or bool(message["content"].strip())
                        if message["content"] or message["thinking"]:
                            yield {"message": message, "done": False}
                    elif kind == "message_stop":
                        if not has_output:
                            raise LLMError("LLM 服务返回了空回答，报告未完成。")
                        completed = True
                        yield {"done": True}
                        break
    if not completed:
        raise LLMError("LLM 输出流提前结束，报告未完成，请重试。")


def fetch_models(config, timeout=None):
    if timeout is None:
        timeout = 5 if config.provider == "ollama" else 15
    if config.provider == "ollama" and not config.api_key:
        from .ollama_models import fetch_ollama_models
        return fetch_ollama_models(config.base_url, timeout=timeout)
    if config.provider == "ollama":
        with _open(config, _request(config, "/api/tags"), timeout) as response:
            obj = _load_json(response)
        _check_error(obj, config)
        if not isinstance(obj.get("models"), list):
            raise LLMError("API 返回的模型列表格式无效。")
        names = []
        for item in obj["models"]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name.strip() and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in name):
                if name not in names:
                    names.append(name)
        return names
    models, seen = [], set()
    path = "/models"
    cursors = set()
    for _ in range(100):
        with _open(config, _request(config, path), timeout) as response:
            obj = _load_json(response)
        _check_error(obj, config)
        if not isinstance(obj.get("data"), list):
            raise LLMError("API 返回的模型列表格式无效。")
        for item in obj["data"]:
            name = item.get("id") if isinstance(item, dict) else None
            if isinstance(name, str) and name.strip() and not any(c.isspace() or ord(c) < 32 for c in name):
                if name not in seen:
                    seen.add(name)
                    models.append(name)
        if config.provider != "anthropic" or not obj.get("has_more"):
            return models
        cursor = obj.get("last_id")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise LLMError("API 模型列表分页异常，请重试。")
        cursors.add(cursor)
        path = "/models?" + urllib.parse.urlencode({"after_id": cursor})
    raise LLMError("API 模型列表分页过多，请手动填写模型名称。")


def embed_text(config, text, timeout=120):
    if config.provider == "anthropic":
        raise LLMError("Anthropic 没有 Embedding API，请为文本向量化选择 Ollama 或 OpenAI 兼容服务。", 400)
    if config.provider == "ollama":
        path, payload = "/api/embeddings", {"model": config.model, "prompt": text}
    else:
        path, payload = "/embeddings", {"model": config.model, "input": text, "encoding_format": "float"}
    with _open(config, _request(config, path, payload), timeout) as response:
        obj = _load_json(response)
    _check_error(obj, config)
    if config.provider == "ollama":
        vector = obj.get("embedding")
    else:
        data = obj.get("data") or []
        vector = data[0].get("embedding") if data and isinstance(data[0], dict) else None
    if not isinstance(vector, list) or not vector or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in vector):
        raise LLMError("Embedding API 返回的向量格式无效。")
    return [float(n) for n in vector]
