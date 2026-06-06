"""
Ollama Embedding 封装

调用 Ollama /api/embeddings 接口生成文本向量。
模型推荐：nomic-embed-text（~137M，中文可接受）
"""

import json
import logging
import urllib.request
import urllib.error
from typing import List

from .models import KnowledgeSettings

log = logging.getLogger(__name__)

# 默认 embedding 模型（KnowledgeSettings 未配置时的 fallback）
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"

# Ollama 服务地址（与 epilepsy 模块共用 OllamaServer 配置）
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

# 缓存已知模型的维度，避免每次调用 API
_KNOWN_DIMENSIONS = {
    "nomic-embed-text": 768,
    "bge-m3": 1024,
    "mxbai-embed-large": 1024,
}


def _get_ollama_base_url() -> str:
    """从 epilepsy 模块获取启用的 Ollama 服务器地址，fallback 到默认值"""
    try:
        from epilepsy.models import OllamaServer
        server = OllamaServer.objects.filter(is_enabled=True).first()
        if server:
            return server.base_url
    except Exception:
        pass
    return DEFAULT_OLLAMA_BASE_URL


def get_embedding_model() -> str:
    """获取当前配置的 embedding 模型名（从 KnowledgeSettings 读取）"""
    try:
        from .models import KnowledgeSettings
        return KnowledgeSettings.load().embedding_model or DEFAULT_EMBEDDING_MODEL
    except Exception:
        return DEFAULT_EMBEDDING_MODEL


def embed_text(text: str, model: str | None = None) -> List[float]:
    """将单段文本转为向量"""
    if model is None:
        model = get_embedding_model()

    base_url = _get_ollama_base_url()
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            embedding = body.get("embedding")
            if embedding is None:
                raise RuntimeError(f"Ollama embeddings 返回格式异常: {body}")
            return embedding
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"Ollama embedding 请求失败 (HTTP {exc.code}): {error_body}"
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 Ollama 服务 ({base_url}): {exc}")


def embed_texts(texts: List[str], model: str | None = None) -> List[List[float]]:
    """批量将文本转为向量（逐个调用，Ollama API 暂不支持批量）"""
    return [embed_text(t, model=model) for t in texts]


def embedding_dimension(model: str | None = None) -> int:
    """获取 embedding 向量的维度（优先使用已知维度表，避免 API 调用）"""
    if model is None:
        model = get_embedding_model()
    # 检查已知维度表
    if model in _KNOWN_DIMENSIONS:
        return _KNOWN_DIMENSIONS[model]
    # 检查模型中是否包含已知名称
    for known, dim in _KNOWN_DIMENSIONS.items():
        if known in model:
            return dim
    # fallback: 试调一次获得维度，并缓存
    vec = embed_text("test dimension", model=model)
    _KNOWN_DIMENSIONS[model] = len(vec)
    return len(vec)
