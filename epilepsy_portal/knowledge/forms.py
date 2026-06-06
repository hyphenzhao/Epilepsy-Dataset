from django import forms

from .models import KnowledgeSettings


class KnowledgeSettingsForm(forms.ModelForm):
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
            "embedding_model": "Embedding 模型",
            "extraction_model": "信息提取模型",
        }
        help_texts = {
            "vector_backend": "切换后需重建索引。ChromaDB 功能完善，SQLite-vec 零额外依赖。",
            "chunk_strategy": "新上传的文档将使用此策略分块。",
            "enable_rag": "开启后，生成报告时可选择检索知识库中的相似病例和文献作为参考。",
            "enable_agent": "开启后，可使用多步推理（分析→检索→草稿→自查→修订）生成报告。",
            "hybrid_alpha": "0 = 纯关键词检索，1 = 纯向量语义检索。默认 0.7。",
            "metadata_extraction_prompt": "自定义 AI 提取元数据的指令。留空使用默认模板。",
            "embedding_model": "用于文本向量化。需先在 Ollama 中 pull 该模型。",
            "extraction_model": "用于元数据提取和编辑摘要。推荐 qwen2.5:7b。",
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
