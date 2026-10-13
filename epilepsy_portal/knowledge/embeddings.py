"""Text embeddings through the configured Ollama/OpenAI provider."""

from typing import List

from epilepsy.llm import embed_text as provider_embed_text
from .provider_config import get_role_config, get_active_server_config

DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
_KNOWN_DIMENSIONS = {"nomic-embed-text": 768, "bge-m3": 1024, "mxbai-embed-large": 1024}
_DIMENSION_CACHE = {}


def _get_ollama_base_url() -> str:
    """Legacy helper retained for existing callers."""
    return get_active_server_config().base_url


def get_embedding_model() -> str:
    return get_role_config("embedding").model


def embed_text(text: str, model: str | None = None) -> List[float]:
    config = get_role_config("embedding", model=model)
    return provider_embed_text(config, text, timeout=120)


def embed_texts(texts: List[str], model: str | None = None) -> List[List[float]]:
    if not texts:
        return []
    config = get_role_config("embedding", model=model)
    return [provider_embed_text(config, text, timeout=120) for text in texts]


def embedding_dimension(model: str | None = None) -> int:
    config = get_role_config("embedding", model=model)
    cache_key = (config.provider, config.base_url, config.model)
    if cache_key in _DIMENSION_CACHE:
        return _DIMENSION_CACHE[cache_key]
    # Known dimensions apply only to exact Ollama model families.
    family = config.model.split(":", 1)[0]
    if config.provider == "ollama" and family in _KNOWN_DIMENSIONS:
        dimension = _KNOWN_DIMENSIONS[family]
    else:
        dimension = len(provider_embed_text(config, "test dimension", timeout=120))
    _DIMENSION_CACHE[cache_key] = dimension
    return dimension
