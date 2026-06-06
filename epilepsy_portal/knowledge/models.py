from django.conf import settings
from django.db import models


# ============================================================
#  知识库设置（用户可配置项）
# ============================================================

class KnowledgeSettings(models.Model):
    """全局知识库设置，单例模式"""

    VECTOR_BACKEND_CHOICES = [
        ("chromadb", "ChromaDB（推荐）"),
        ("sqlite_vec", "SQLite-vec（零额外依赖）"),
    ]
    CHUNK_STRATEGY_CHOICES = [
        ("section", "按报告分区（推荐）"),
        ("paragraph", "按段落"),
        ("fixed", "固定长度（500 tokens）"),
        ("hybrid", "混合（分区 + 段落细分）"),
    ]
    SCOPE_CHOICES = [
        ("global", "全科室共享"),
        ("role", "按角色隔离"),
        ("user", "按用户隔离"),
    ]
    LEARNING_MODE_CHOICES = [
        ("review", "管理员审核后入库（推荐）"),
        ("auto", "自动学习"),
        ("disabled", "关闭编辑学习"),
    ]

    vector_backend = models.CharField(
        max_length=20, choices=VECTOR_BACKEND_CHOICES, default="chromadb",
        verbose_name="向量数据库后端",
    )
    chunk_strategy = models.CharField(
        max_length=20, choices=CHUNK_STRATEGY_CHOICES, default="section",
        verbose_name="文档分块策略",
    )
    knowledge_scope = models.CharField(
        max_length=20, choices=SCOPE_CHOICES, default="global",
        verbose_name="知识库共享范围",
    )
    learning_mode = models.CharField(
        max_length=20, choices=LEARNING_MODE_CHOICES, default="review",
        verbose_name="编辑反馈学习策略",
    )
    # 检索参数
    top_k_cases = models.PositiveSmallIntegerField(
        default=5, verbose_name="相似病例检索数 (top-K)",
    )
    top_k_literature = models.PositiveSmallIntegerField(
        default=10, verbose_name="文献片段检索数 (top-M)",
    )
    top_k_edits = models.PositiveSmallIntegerField(
        default=3, verbose_name="历史编辑检索数",
    )
    hybrid_alpha = models.FloatField(
        default=0.7, verbose_name="混合检索权重 (α)",
        help_text="0=纯关键词，1=纯向量。默认 0.7 偏向量。",
    )
    metadata_extraction_prompt = models.TextField(
        blank=True, default="", verbose_name="元数据提取自定义 Prompt",
        help_text="留空则使用默认提取模板。可用于定制提取字段和要求。",
    )
    # RAG 功能开关
    enable_rag = models.BooleanField(
        default=True, verbose_name="启用 RAG 增强",
        help_text="开启后，生成报告时可选择检索知识库中的相似病例和文献作为参考。",
    )
    enable_agent = models.BooleanField(
        default=True, verbose_name="启用 Agent 模式",
        help_text="开启后，可使用多步推理（分析→检索→草稿→自查→修订）生成报告。",
    )
    # Ollama 模型配置
    embedding_model = models.CharField(
        max_length=100, default="nomic-embed-text",
        verbose_name="Embedding 模型",
        help_text="用于文本向量化。需先在 Ollama 中 ollama pull 该模型。",
    )
    extraction_model = models.CharField(
        max_length=100, default="qwen2.5:7b",
        verbose_name="信息提取模型",
        help_text="用于元数据提取和编辑摘要。推荐 qwen2.5:7b。",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="最后更新")

    class Meta:
        verbose_name = "知识库设置"
        verbose_name_plural = "知识库设置"

    def __str__(self):
        return "知识库设置"

    @classmethod
    def load(cls):
        """获取全局设置单例，不存在则创建默认值"""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ============================================================
#  知识库文档
# ============================================================

class KnowledgeDocument(models.Model):
    """知识库文档：病例报告、文献、指南、编辑反馈"""

    DOC_TYPE_CHOICES = [
        ("case_report", "病例报告"),
        ("literature", "文献/论文"),
        ("guideline", "指南/规范"),
        ("edited_report", "用户修改报告"),
    ]

    title = models.CharField(max_length=500, verbose_name="标题")
    doc_type = models.CharField(
        max_length=20, choices=DOC_TYPE_CHOICES, verbose_name="文档类型",
    )
    source_patient = models.ForeignKey(
        "epilepsy.Patient",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="knowledge_docs",
        verbose_name="关联患者",
        help_text="病例报告类关联的患者",
    )
    original_file = models.FileField(
        upload_to="knowledge/raw/%Y/%m/",
        blank=True,
        verbose_name="原始文件",
        help_text="上传的 PDF / Word / 文本文件",
    )
    text_content = models.TextField(
        verbose_name="纯文本内容",
        help_text="提取/清洗后的完整文本",
    )
    metadata = models.JSONField(
        default=dict,
        verbose_name="结构化元数据",
        help_text="AI 提取的结构化信息，如发作类型、EEG 发现等",
    )
    is_approved = models.BooleanField(
        default=False, verbose_name="已审核",
        help_text="管理员审核后纳入知识库",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="knowledge_docs",
        verbose_name="上传者",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "知识库文档"
        verbose_name_plural = "知识库文档"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["doc_type", "is_approved"]),
            models.Index(fields=["source_patient"]),
        ]

    def __str__(self):
        return f"[{self.get_doc_type_display()}] {self.title}"


# ============================================================
#  文档分块
# ============================================================

class KnowledgeChunk(models.Model):
    """文档分块，向量检索的基本单位"""

    document = models.ForeignKey(
        KnowledgeDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
        verbose_name="所属文档",
    )
    chunk_index = models.PositiveIntegerField(verbose_name="分块序号（从 0 开始）")
    text = models.TextField(verbose_name="分块文本")
    embedding_id = models.CharField(
        max_length=100, verbose_name="向量库 ID",
        help_text="ChromaDB 中的 chunk id",
    )
    section_label = models.CharField(
        max_length=200, blank=True, default="",
        verbose_name="对应分区块",
        help_text="如 '发作症状学'、'影像学检查' 等",
    )
    token_count = models.PositiveIntegerField(default=0, verbose_name="Token 数")

    class Meta:
        verbose_name = "文档分块"
        verbose_name_plural = "文档分块"
        ordering = ["document", "chunk_index"]
        unique_together = [("document", "chunk_index")]

    def __str__(self):
        return f"{self.document.title} — chunk {self.chunk_index}"


# ============================================================
#  报告编辑历史
# ============================================================

class ReportEditHistory(models.Model):
    """用户对 AI 生成报告的编辑记录"""

    patient = models.ForeignKey(
        "epilepsy.Patient",
        on_delete=models.CASCADE,
        related_name="report_edits",
        verbose_name="患者",
    )
    original_markdown = models.TextField(
        verbose_name="AI 原始生成的 Markdown",
    )
    edited_markdown = models.TextField(
        verbose_name="用户修改后的 Markdown",
    )
    diff_json = models.JSONField(
        default=dict,
        verbose_name="结构化修改记录",
        help_text="diff 分析结果：新增/删除/修改的内容",
    )
    edit_summary = models.TextField(
        blank=True, default="",
        verbose_name="AI 修改摘要",
        help_text="LLM 提取的修改要点",
    )
    is_accepted = models.BooleanField(
        default=False,
        verbose_name="已采纳",
        help_text="管理员审核后纳入知识库",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="report_edits",
        verbose_name="编辑者",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="编辑时间")

    class Meta:
        verbose_name = "报告编辑历史"
        verbose_name_plural = "报告编辑历史"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["patient", "-created_at"]),
            models.Index(fields=["is_accepted"]),
        ]

    def __str__(self):
        return f"{self.patient.name} — 编辑于 {self.created_at.strftime('%Y-%m-%d %H:%M')}"
