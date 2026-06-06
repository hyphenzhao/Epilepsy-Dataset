from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .models import KnowledgeSettings, KnowledgeDocument, KnowledgeChunk, ReportEditHistory
from .forms import KnowledgeSettingsForm
from .vector_store import collection_stats


def _require_staff(request):
    """检查用户是否为 ADMIN 或 STAFF，否则返回 403"""
    from epilepsy.models import UserRole
    profile = getattr(request.user, "profile", None)
    return profile and profile.role in [UserRole.ADMIN, UserRole.STAFF]


# ============================================================
#  知识库设置
# ============================================================

@login_required
def knowledge_settings(request):
    """知识库设置页面"""
    if not _require_staff(request):
        return render(request, "knowledge/forbidden.html", status=403)

    ks = KnowledgeSettings.load()
    stats = collection_stats()

    if request.method == "POST":
        form = KnowledgeSettingsForm(request.POST, instance=ks)
        if form.is_valid():
            form.save()
            messages.success(request, "知识库设置已更新。")
            return redirect("knowledge:settings")
        else:
            messages.error(request, "保存失败，请检查填写内容。")
    else:
        form = KnowledgeSettingsForm(instance=ks)

    return render(request, "knowledge/settings.html", {
        "form": form,
        "settings": ks,
        "stats": stats,
    })


# ============================================================
#  文档列表
# ============================================================

@login_required
def knowledge_list(request):
    """知识库文档列表（已审核文档）"""
    if not _require_staff(request):
        return render(request, "knowledge/forbidden.html", status=403)

    doc_type = request.GET.get("doc_type", "")
    query = request.GET.get("q", "")
    page_num = request.GET.get("page", "1")

    docs = KnowledgeDocument.objects.filter(is_approved=True).select_related("created_by", "source_patient")

    if doc_type:
        docs = docs.filter(doc_type=doc_type)
    if query:
        from django.db.models import Q
        docs = docs.filter(
            Q(title__icontains=query) | Q(text_content__icontains=query)
        )

    docs = docs.order_by("-created_at")

    paginator = Paginator(docs, 20)
    page_obj = paginator.get_page(page_num)

    return render(request, "knowledge/knowledge_list.html", {
        "page_obj": page_obj,
        "doc_type": doc_type,
        "query": query,
        "doc_types": KnowledgeDocument.DOC_TYPE_CHOICES,
    })


# ============================================================
#  文档详情
# ============================================================

@login_required
def knowledge_detail(request, pk):
    """查看文档详情与分块列表"""
    if not _require_staff(request):
        return render(request, "knowledge/forbidden.html", status=403)

    doc = get_object_or_404(
        KnowledgeDocument.objects.select_related("created_by", "source_patient"),
        pk=pk,
    )
    chunks = doc.chunks.all()

    return render(request, "knowledge/knowledge_detail.html", {
        "doc": doc,
        "chunks": chunks,
    })


# ============================================================
#  上传文档（Step 4 会扩展）
# ============================================================

@login_required
def knowledge_upload(request):
    """上传文档页面"""
    if not _require_staff(request):
        return render(request, "knowledge/forbidden.html", status=403)

    from .forms import KnowledgeUploadForm

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "confirm":
            # 确认上传 → 运行完整流水线
            doc_id = request.POST.get("doc_id")
            doc = get_object_or_404(KnowledgeDocument, pk=doc_id)

            # 收集用户编辑后的 metadata
            from .pipeline import ingest_document
            from .metadata_extractor import CASE_METADATA_FIELDS, LITERATURE_METADATA_FIELDS

            fields = CASE_METADATA_FIELDS if doc.doc_type in ("case_report", "edited_report") else LITERATURE_METADATA_FIELDS
            metadata = {}
            for f in fields:
                val = request.POST.get(f"meta_{f}", "").strip()
                if val:
                    metadata[f] = val

            try:
                # 更新 metadata 并运行 pipeline
                doc.metadata = metadata
                doc.is_approved = True
                doc.save(update_fields=["metadata", "is_approved"])

                # 重新运行分块+向量化（之前只是创建了 doc 记录）
                from .chunker import chunk_text, estimate_tokens
                from .vector_store import add_chunks

                strategy = KnowledgeSettings.load().chunk_strategy
                raw_chunks = chunk_text(doc.text_content, strategy=strategy)
                chunk_ids = [f"doc_{doc.pk}_chunk_{i}" for i in range(len(raw_chunks))]
                texts = [c["text"] for c in raw_chunks]
                metadatas = [
                    {"document_id": doc.pk, "doc_type": doc.doc_type, "section_label": c.get("section_label", "")}
                    for c in raw_chunks
                ]
                add_chunks(doc.doc_type, chunk_ids, texts, metadatas)

                KnowledgeChunk.objects.filter(document=doc).delete()
                KnowledgeChunk.objects.bulk_create([
                    KnowledgeChunk(
                        document=doc, chunk_index=i, text=raw["text"],
                        embedding_id=chunk_ids[i],
                        section_label=raw.get("section_label", ""),
                        token_count=estimate_tokens(raw["text"]),
                    ) for i, raw in enumerate(raw_chunks)
                ])

                messages.success(request, f"文档「{doc.title}」已入库（{len(raw_chunks)} 个分块）。")
                return redirect("knowledge:list")

            except Exception as exc:
                messages.error(request, f"入库失败: {exc}")
                return redirect("knowledge:upload")

        else:
            # 文件上传预览
            form = KnowledgeUploadForm(request.POST, request.FILES)
            if form.is_valid():
                from .document_processor import extract_text
                from .metadata_extractor import extract_metadata

                file = request.FILES["file"]
                title = form.cleaned_data["title"]
                doc_type = form.cleaned_data["doc_type"]

                try:
                    text_content = extract_text(file)
                except Exception as exc:
                    messages.error(request, f"文本提取失败: {exc}")
                    return render(request, "knowledge/upload.html", {"form": form})

                metadata = extract_metadata(text_content, doc_type)

                doc = KnowledgeDocument.objects.create(
                    title=title,
                    doc_type=doc_type,
                    original_file=file,
                    text_content=text_content,
                    metadata=metadata,
                    is_approved=False,
                    created_by=request.user,
                )

                return render(request, "knowledge/upload.html", {
                    "form": form,
                    "preview_doc": doc,
                    "text_preview": text_content[:2000],
                    "text_total_chars": len(text_content),
                })
    else:
        form = KnowledgeUploadForm()

    return render(request, "knowledge/upload.html", {"form": form})


@login_required
@require_POST
def knowledge_upload_preview(request):
    """AJAX: 上传文件 → 返回文本预览 + 元数据 JSON"""
    if not _require_staff(request):
        return JsonResponse({"error": "无权限"}, status=403)

    from .document_processor import extract_text
    from .metadata_extractor import extract_metadata

    file = request.FILES.get("file")
    doc_type = request.POST.get("doc_type", "case_report")
    title = request.POST.get("title", "")

    if not file:
        return JsonResponse({"error": "未选择文件"}, status=400)

    try:
        text_content = extract_text(file)
    except Exception as exc:
        return JsonResponse({"error": f"文本提取失败: {exc}"}, status=400)

    metadata = extract_metadata(text_content, doc_type)

    doc = KnowledgeDocument.objects.create(
        title=title,
        doc_type=doc_type,
        original_file=file,
        text_content=text_content,
        metadata=metadata,
        is_approved=False,
        created_by=request.user,
    )

    return JsonResponse({
        "doc_id": doc.pk,
        "title": title,
        "text_preview": text_content[:2000],
        "text_total_chars": len(text_content),
        "metadata": metadata,
    })


# ============================================================
#  报告编辑保存
# ============================================================

@login_required
@require_POST
def save_report_edit(request):
    """保存报告编辑：diff → 摘要 → ReportEditHistory"""
    import json
    import difflib

    from epilepsy.models import Patient

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except Exception:
        return JsonResponse({"status": "error", "message": "无效的请求数据"}, status=400)

    patient_id = payload.get("patient_id")
    original = payload.get("original_markdown", "")
    edited = payload.get("edited_markdown", "")

    if not patient_id or not edited.strip():
        return JsonResponse({"status": "error", "message": "缺少必要字段"}, status=400)

    patient = get_object_or_404(Patient, pk=patient_id)

    # 计算 diff
    diff = _compute_diff(original, edited)

    # AI 摘要
    edit_summary = ""
    try:
        from .ollama_client import summarize_edit
        edit_summary = summarize_edit(original, edited)
    except Exception:
        pass

    # 创建记录
    edit_record = ReportEditHistory.objects.create(
        patient=patient,
        original_markdown=original,
        edited_markdown=edited,
        diff_json=diff,
        edit_summary=edit_summary,
        created_by=request.user,
    )

    # 自动学习模式
    if KnowledgeSettings.load().learning_mode == "auto":
        try:
            from .pipeline import ingest_markdown_report
            ingest_markdown_report(
                title=f"{patient.name} — 编辑报告 ({edit_record.created_at.strftime('%Y-%m-%d %H:%M')})",
                doc_type="edited_report",
                markdown_text=edited,
                source_patient=patient,
                uploaded_by=request.user,
                metadata={"edit_summary": edit_summary, "original_diff": diff},
                is_approved=True,
            )
            edit_record.is_accepted = True
            edit_record.save(update_fields=["is_accepted"])
        except Exception as exc:
            return JsonResponse({
                "status": "ok",
                "edit_id": edit_record.pk,
                "message": f"编辑已保存，但自动入库失败: {exc}",
            })

    return JsonResponse({
        "status": "ok",
        "edit_id": edit_record.pk,
        "message": "编辑已保存" + ("并自动入库" if edit_record.is_accepted else "，等待管理员审核"),
    })


def _compute_diff(original: str, edited: str) -> dict:
    """计算两份文本的结构化差异"""
    import difflib

    result = {"added": [], "removed": [], "changed": []}

    orig_lines = original.splitlines(keepends=True)
    edit_lines = edited.splitlines(keepends=True)

    matcher = difflib.SequenceMatcher(None, orig_lines, edit_lines)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            result["added"].append("".join(edit_lines[j1:j2]).strip())
        elif tag == "delete":
            result["removed"].append("".join(orig_lines[i1:i2]).strip())
        elif tag == "replace":
            result["changed"].append({
                "from": "".join(orig_lines[i1:i2]).strip(),
                "to": "".join(edit_lines[j1:j2]).strip(),
            })

    return result


# ============================================================
#  文档删除
# ============================================================

@login_required
@require_POST
def knowledge_delete(request, pk):
    """删除知识库文档"""
    if not _require_staff(request):
        return JsonResponse({"error": "无权限"}, status=403)

    doc = get_object_or_404(KnowledgeDocument, pk=pk)
    from .pipeline import delete_document
    delete_document(doc)
    messages.success(request, f"文档「{doc.title}」已删除。")
    return redirect("knowledge:list")
