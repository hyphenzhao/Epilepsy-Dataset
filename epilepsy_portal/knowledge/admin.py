from django.contrib import admin
from django.utils.html import format_html

from .models import (
    KnowledgeSettings,
    KnowledgeDocument,
    KnowledgeChunk,
    ReportEditHistory,
)
from .vector_store import collection_stats


@admin.register(KnowledgeSettings)
class KnowledgeSettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("向量与分块", {
            "fields": ("vector_backend", "chunk_strategy"),
        }),
        ("知识库策略", {
            "fields": ("knowledge_scope", "learning_mode"),
        }),
        ("检索参数", {
            "fields": ("top_k_cases", "top_k_literature", "top_k_edits", "hybrid_alpha"),
        }),
    )

    def has_add_permission(self, request):
        # 单例模式：只允许存在一条记录
        return not KnowledgeSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class KnowledgeChunkInline(admin.TabularInline):
    model = KnowledgeChunk
    extra = 0
    fields = ("chunk_index", "section_label", "token_count", "embedding_id")
    readonly_fields = ("chunk_index", "section_label", "token_count", "embedding_id")
    can_delete = False
    max_num = 0
    show_change_link = False


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title", "doc_type_badge", "source_patient_link",
        "chunk_count", "is_approved", "created_by", "created_at",
    )
    list_filter = ("doc_type", "is_approved", "created_at")
    search_fields = ("title", "text_content", "source_patient__name")
    readonly_fields = ("text_preview", "metadata_preview", "created_at", "updated_at")
    inlines = [KnowledgeChunkInline]
    actions = ["approve_documents", "unapprove_documents"]

    fieldsets = (
        (None, {
            "fields": ("title", "doc_type", "is_approved"),
        }),
        ("关联", {
            "fields": ("source_patient", "original_file", "created_by"),
        }),
        ("内容", {
            "fields": ("text_preview", "metadata_preview"),
        }),
        ("时间", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def doc_type_badge(self, obj):
        return obj.get_doc_type_display()
    doc_type_badge.short_description = "类型"
    doc_type_badge.admin_order_field = "doc_type"

    def source_patient_link(self, obj):
        if obj.source_patient:
            url = f"/epilepsy/patients/{obj.source_patient.pk}/detail/"
            return format_html(
                '<a href="{}" target="_blank">{}</a>',
                url, obj.source_patient.name,
            )
        return "-"
    source_patient_link.short_description = "患者"

    def chunk_count(self, obj):
        return obj.chunks.count()
    chunk_count.short_description = "分块数"

    def text_preview(self, obj):
        preview = obj.text_content[:500]
        if len(obj.text_content) > 500:
            preview += "…"
        return format_html("<pre style='max-height:300px;overflow:auto'>{}</pre>", preview)
    text_preview.short_description = "文本预览"

    def metadata_preview(self, obj):
        import json
        return format_html(
            "<pre>{}</pre>",
            json.dumps(obj.metadata, ensure_ascii=False, indent=2),
        )
    metadata_preview.short_description = "结构化元数据"

    @admin.action(description="✅ 审核通过选中的文档")
    def approve_documents(self, request, queryset):
        updated = queryset.update(is_approved=True)
        self.message_user(request, f"已审核通过 {updated} 篇文档。")

    @admin.action(description="❌ 取消审核选中的文档")
    def unapprove_documents(self, request, queryset):
        updated = queryset.update(is_approved=False)
        self.message_user(request, f"已取消 {updated} 篇文档的审核状态。")


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("__str__", "section_label", "token_count")
    list_filter = ("section_label",)
    search_fields = ("text", "document__title")
    readonly_fields = ("text_preview",)

    def text_preview(self, obj):
        return format_html("<pre style='max-height:200px;overflow:auto'>{}</pre>", obj.text)
    text_preview.short_description = "文本"


@admin.register(ReportEditHistory)
class ReportEditHistoryAdmin(admin.ModelAdmin):
    list_display = ("patient", "created_by", "diff_summary", "is_accepted", "created_at")
    list_filter = ("is_accepted", "created_at")
    search_fields = ("patient__name", "original_markdown", "edited_markdown")
    readonly_fields = (
        "patient", "original_preview", "edited_preview",
        "diff_preview", "created_by", "created_at",
    )
    actions = ["accept_edits"]

    fieldsets = (
        (None, {
            "fields": ("patient", "created_by", "is_accepted", "created_at"),
        }),
        ("原始报告", {"fields": ("original_preview",)}),
        ("修改后报告", {"fields": ("edited_preview",)}),
        ("差异", {"fields": ("diff_preview",)}),
    )

    def diff_summary(self, obj):
        if obj.edit_summary:
            preview = obj.edit_summary[:80]
            if len(obj.edit_summary) > 80:
                preview += "…"
            return preview
        diff = obj.diff_json
        added = len(diff.get("added", []))
        removed = len(diff.get("removed", []))
        changed = len(diff.get("changed", []))
        return f"+{added} -{removed} ~{changed}"
    diff_summary.short_description = "修改摘要"

    def original_preview(self, obj):
        return format_html(
            "<pre style='max-height:300px;overflow:auto'>{}</pre>",
            obj.original_markdown[:1000],
        )
    original_preview.short_description = "原始报告"

    def edited_preview(self, obj):
        return format_html(
            "<pre style='max-height:300px;overflow:auto'>{}</pre>",
            obj.edited_markdown[:1000],
        )
    edited_preview.short_description = "修改后报告"

    def diff_preview(self, obj):
        import json
        return format_html(
            "<pre>{}</pre>",
            json.dumps(obj.diff_json, ensure_ascii=False, indent=2),
        )
    diff_preview.short_description = "结构化差异"

    @admin.action(description="✅ 采纳选中的编辑（纳入知识库）")
    def accept_edits(self, request, queryset):
        for edit in queryset.filter(is_accepted=False):
            try:
                from .pipeline import ingest_markdown_report
                ingest_markdown_report(
                    title=f"{edit.patient.name} — 编辑报告 ({edit.created_at.strftime('%Y-%m-%d')})",
                    doc_type="edited_report",
                    markdown_text=edit.edited_markdown,
                    source_patient=edit.patient,
                    uploaded_by=edit.created_by,
                    metadata={
                        "edit_summary": edit.edit_summary,
                        "original_diff": edit.diff_json,
                    },
                    is_approved=True,
                )
                edit.is_accepted = True
                edit.save(update_fields=["is_accepted"])
            except Exception as exc:
                self.message_user(
                    request,
                    f"❌ 编辑 pk={edit.pk} 入库失败: {exc}",
                    level="error",
                )
        self.message_user(
            request,
            f"已采纳 {queryset.filter(is_accepted=True).count()} 条编辑。",
        )
