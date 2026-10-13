"""Resolve the provider configuration for each knowledge/Agent role."""

from epilepsy.llm import LLMConfig, config_from_server


def get_active_server_config(model=None):
    from epilepsy.models import OllamaServer
    server = OllamaServer.objects.filter(is_enabled=True).first()
    return config_from_server(server, model=model)


def get_role_config(role, settings=None, model=None):
    """Return an independent role config, with legacy model fallbacks."""
    if role not in {"thinking", "extraction", "embedding"}:
        raise ValueError("未知的模型用途。")
    if settings is None:
        from .models import KnowledgeSettings
        settings = KnowledgeSettings.load()

    override = getattr(settings, role + "_provider", "") or ("ollama" if role == "embedding" else "")
    if role == "embedding" and override not in {"ollama", "openai"}:
        raise ValueError("Anthropic 不提供 Embedding API，请为文本向量化选择 Ollama 或 OpenAI / 兼容 API。")
    base_override = (getattr(settings, role + "_api_base_url", "") or "").strip()
    api_key = getattr(settings, role + "_api_key", "") or ""
    if api_key and (not override or not base_override):
        raise ValueError("单独保存的 API Key 必须绑定服务类型和 API 地址，请重新保存该用途的模型配置。")
    if role == "thinking" and not override:
        extraction = get_role_config("extraction", settings=settings)
        thinking_model = model if model is not None else (getattr(settings, "thinking_model", "") or extraction.model)
        return LLMConfig(
            provider=extraction.provider,
            base_url=base_override or extraction.base_url,
            api_key=extraction.api_key if not base_override else "",
            model=thinking_model,
        )
    active = get_active_server_config()
    provider = override or active.provider
    base_url = base_override or (active.base_url if provider == active.provider else "")
    # Explicit role configs never borrow another config's credential. An
    # inherited chat config can use the active server key only at its address.
    if not api_key and not override and not base_override:
        api_key = active.api_key

    if model is None:
        model = getattr(settings, role + "_model", "") or ""
        if role == "thinking" and not model:
            model = getattr(settings, "extraction_model", "") or "qwen2.5:7b"
        elif role == "extraction" and not model:
            model = "qwen2.5:7b"
        elif role == "embedding" and not model:
            model = "nomic-embed-text"
    return LLMConfig(provider=provider, base_url=base_url, api_key=api_key, model=model)
