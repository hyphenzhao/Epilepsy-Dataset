from django import forms
from types import SimpleNamespace

from .models import KnowledgeSettings
from epilepsy.llm import normalize_base_url


class KnowledgeSettingsForm(forms.ModelForm):
    clear_thinking_api_key = forms.BooleanField(required=False, label="清除单独保存的密钥")
    clear_extraction_api_key = forms.BooleanField(required=False, label="清除单独保存的密钥")
    clear_embedding_api_key = forms.BooleanField(required=False, label="清除单独保存的密钥")
    thinking_api_key = forms.CharField(required=False, label="API Key", widget=forms.PasswordInput(render_value=False, attrs={"class": "form-control", "autocomplete": "new-password"}))
    extraction_api_key = forms.CharField(required=False, label="API Key", widget=forms.PasswordInput(render_value=False, attrs={"class": "form-control", "autocomplete": "new-password"}))
    embedding_api_key = forms.CharField(required=False, label="API Key", widget=forms.PasswordInput(render_value=False, attrs={"class": "form-control", "autocomplete": "new-password"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original = {}
        self.embedding_config_changed = False
        self.credentials_cleared = False
        self.fields["embedding_provider"].required = False
        for role in ("thinking", "extraction", "embedding"):
            for suffix in ("provider", "api_base_url", "api_key", "model"):
                name = role + "_" + suffix
                self._original[name] = getattr(self.instance, name, "") or ""
            key_name = role + "_api_key"
            self.initial.pop(key_name, None)
            self.fields[key_name].initial = None
            self.fields["clear_" + key_name].widget.attrs["class"] = "form-check-input"
            self.fields[key_name].help_text = (
                "已保存密钥，留空保留；更换服务或 API 地址时需重新填写。单独保存密钥会固定当前服务地址。"
                if self._original[key_name] else "密钥不会回显。沿用现有服务无需填写；单独保存密钥会固定当前服务类型和 API 地址。"
            )

    @staticmethod
    def _endpoint_scope(provider, value):
        # Provider-less roles inherit the active provider. Comparing explicit
        # addresses conservatively still prevents reuse at a new endpoint.
        return normalize_base_url(provider or "openai", value) if value else ""

    def clean(self):
        cleaned = super().clean()
        # Resolve extraction first, since unsaved thinking defaults depend on
        # that form's new provider/address/credential rather than old settings.
        for role in ("extraction", "embedding", "thinking"):
            provider_name = role + "_provider"
            url_name = role + "_api_base_url"
            key_name = role + "_api_key"
            if provider_name not in self.data:
                cleaned[provider_name] = self._original[provider_name] or ("ollama" if role == "embedding" else "")
            if url_name not in self.data:
                cleaned[url_name] = self._original[url_name]
            provider = cleaned.get(provider_name, "") or ("ollama" if role == "embedding" else "")
            cleaned[provider_name] = provider
            url = (cleaned.get(url_name, "") or "").strip()
            cleaned[url_name] = url
            if role == "thinking" and not provider and any(
                name in self.errors for name in ("extraction_provider", "extraction_api_base_url", "extraction_api_key")
            ):
                self.add_error(key_name, "请先修正所沿用的信息提取服务配置，再保存思考与自查配置。")
                continue
            try:
                old_url = self._endpoint_scope(self._original[provider_name], self._original[url_name])
                new_url = self._endpoint_scope(provider, url)
            except ValueError as exc:
                self.add_error(url_name, str(exc))
                continue
            same_scope = provider == self._original[provider_name] and old_url == new_url
            if self._original[key_name]:
                from .provider_config import get_role_config
                before_values = dict(self._original)
                before_values[key_name] = ""
                after_values = dict(self._original)
                after_values.update({name: value for name, value in cleaned.items() if name in self._original})
                after_values.update({provider_name: provider, url_name: url, key_name: ""})
                try:
                    before = get_role_config(role, settings=SimpleNamespace(**before_values))
                    after = get_role_config(role, settings=SimpleNamespace(**after_values))
                except ValueError:
                    self.add_error(url_name, "服务配置无效，请检查该用途及其所沿用服务的类型和 API 地址。")
                    continue
                same_scope = (before.provider, before.base_url) == (after.provider, after.base_url)
            supplied_key = cleaned.get(key_name, "") or ""
            if cleaned.get("clear_" + key_name):
                cleaned[key_name] = ""
            elif supplied_key:
                cleaned[key_name] = supplied_key
            elif same_scope:
                cleaned[key_name] = self._original[key_name]
            else:
                cleaned[key_name] = ""
                self.credentials_cleared |= bool(self._original[key_name])
            if cleaned[key_name] and any(ord(char) < 33 or ord(char) > 126 for char in cleaned[key_name]):
                self.add_error(key_name, "API Key 只能使用可见 ASCII 字符，不能包含空格、换行或其他控制字符。")
                continue
            if cleaned[key_name] and (not provider or not url):
                # A role-specific credential must remain bound to its current
                # destination when the active report server later changes.
                from .provider_config import get_role_config
                values = dict(self._original)
                values.update({name: value for name, value in cleaned.items() if name in self._original})
                values.update({provider_name: provider, url_name: url, key_name: ""})
                try:
                    resolved = get_role_config(role, settings=SimpleNamespace(**values))
                except ValueError:
                    self.add_error(key_name, "无法确定密钥对应的服务，请先修正该用途及其所沿用服务的配置。")
                    continue
                cleaned[provider_name] = resolved.provider
                cleaned[url_name] = resolved.base_url
            if role == "embedding":
                self.embedding_config_changed = (
                    not same_scope or cleaned.get("embedding_model", "") != self._original["embedding_model"]
                )
        return cleaned

    class Meta:
        model = KnowledgeSettings
        fields = [
            "vector_backend",
            "chunk_strategy",
            "knowledge_scope",
            "learning_mode",
            "enable_rag",
            "enable_agent",
            "top_k_cases",
            "top_k_literature",
            "top_k_edits",
            "hybrid_alpha",
            "metadata_extraction_prompt",
            "embedding_model",
            "extraction_model",
            "embedding_provider", "embedding_api_base_url", "embedding_api_key",
            "extraction_provider", "extraction_api_base_url", "extraction_api_key",
            "thinking_provider", "thinking_api_base_url", "thinking_api_key", "thinking_model",
        ]
        widgets = {
            "vector_backend": forms.Select(attrs={"class": "form-control"}),
            "chunk_strategy": forms.Select(attrs={"class": "form-control"}),
            "knowledge_scope": forms.Select(attrs={"class": "form-control"}),
            "learning_mode": forms.Select(attrs={"class": "form-control"}),
            "top_k_cases": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 20}),
            "top_k_literature": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 30}),
            "top_k_edits": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 10}),
            "enable_rag": forms.CheckboxInput(attrs={"class": "custom-control-input"}),
            "enable_agent": forms.CheckboxInput(attrs={"class": "custom-control-input"}),
            "hybrid_alpha": forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 1, "step": 0.1}),
            "metadata_extraction_prompt": forms.Textarea(attrs={"class": "form-control", "rows": 5, "placeholder": "留空使用默认提取模板..."}),
            "embedding_model": forms.TextInput(attrs={"class": "form-control", "placeholder": "nomic-embed-text"}),
            "extraction_model": forms.TextInput(attrs={"class": "form-control", "placeholder": "qwen2.5:7b"}),
            "thinking_model": forms.TextInput(attrs={"class": "form-control", "placeholder": "留空沿用信息提取模型"}),
            "embedding_provider": forms.Select(attrs={"class": "form-control"}),
            "extraction_provider": forms.Select(attrs={"class": "form-control"}),
            "thinking_provider": forms.Select(attrs={"class": "form-control"}),
            "embedding_api_base_url": forms.TextInput(attrs={"class": "form-control", "placeholder": "留空使用当前服务器或所选服务的默认地址"}),
            "extraction_api_base_url": forms.TextInput(attrs={"class": "form-control", "placeholder": "留空使用当前服务器或所选服务的默认地址"}),
            "thinking_api_base_url": forms.TextInput(attrs={"class": "form-control", "placeholder": "留空使用当前服务器或所选服务的默认地址"}),
        }
        labels = {
            "vector_backend": "向量数据库后端",
            "chunk_strategy": "文档分块策略",
            "knowledge_scope": "知识库共享范围",
            "learning_mode": "编辑反馈学习策略",
            "enable_rag": "启用 RAG 增强",
            "enable_agent": "启用 Agent 模式",
            "top_k_cases": "相似病例检索数 (top-K)",
            "top_k_literature": "文献片段检索数 (top-M)",
            "top_k_edits": "历史编辑检索数",
            "hybrid_alpha": "混合检索权重 (α)",
            "metadata_extraction_prompt": "元数据提取 Prompt",
            "embedding_model": "文本向量化模型",
            "extraction_model": "信息提取模型",
        }
        help_texts = {
            "vector_backend": "切换后需重建索引。ChromaDB 功能完善，SQLite-vec 零额外依赖。",
            "chunk_strategy": "新上传的文档将使用此策略分块。",
            "enable_rag": "开启后，生成报告时可选择检索知识库中的相似病例和文献作为参考。",
            "enable_agent": "开启后，可使用多步推理（分析→检索→草稿→自查→修订）生成报告。",
            "hybrid_alpha": "0 = 纯关键词检索，1 = 纯向量语义检索。默认 0.7。",
            "metadata_extraction_prompt": "自定义 AI 提取元数据的指令。留空使用默认模板。",
            "embedding_model": "用于知识库检索，请选择支持文本向量化的模型。更换服务、地址或模型后需重建已有知识库索引。Anthropic 不提供 Embedding API。",
            "extraction_model": "用于文档信息提取和报告编辑摘要，请选择支持对话的模型。",
            "thinking_model": "用于 Agent 患者分析与报告自查；留空沿用信息提取模型。单独选择服务时，请选用该服务支持的模型。",
        }


class KnowledgeUploadForm(forms.Form):
    """上传知识库文档的表单"""
    doc_type = forms.ChoiceField(
        choices=[
            ("case_report", "病例报告"),
            ("literature", "文献/论文"),
            ("guideline", "指南/规范"),
        ],
        label="文档类型",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    title = forms.CharField(
        max_length=500,
        label="文档标题",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "输入文档标题...",
        }),
    )
    file = forms.FileField(
        label="选择文件",
        help_text="支持 PDF / Word (.docx) / 纯文本 (.txt / .md)，最大 10MB",
        widget=forms.ClearableFileInput(attrs={"class": "form-control-file"}),
    )
