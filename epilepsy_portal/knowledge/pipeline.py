"""
向量化入库流水线

串联: 上传文件 → 文本提取 → 分块 → 向量化 → ChromaDB + DB 入库
"""

import logging
from typing import List, Optional

from django.core.files.uploadedfile import UploadedFile
from django.contrib.auth.models import User

from .models import (
    KnowledgeDocument,
    KnowledgeChunk,
    KnowledgeSettings,
)
from .document_processor import extract_text
from .chunker import chunk_text, estimate_tokens
from .vector_store import add_chunks, delete_chunks

log = logging.getLogger(__name__)


def ingest_document(
    title: str,
    doc_type: str,
    file: UploadedFile | None = None,
    text_content: str | None = None,
    source_patient=None,
    uploaded_by: User | None = None,
    metadata: dict | None = None,
    is_approved: bool = False,
    strategy: str | None = None,
) -> KnowledgeDocument:
    """
    上传并入库一篇文档。

    流程:
      1. 提取文本 (file → text, 或直接用 text_content)
      2. 创建 KnowledgeDocument 记录
      3. 分块
      4. 向量化写入 ChromaDB
      5. 创建 KnowledgeChunk 记录

    参数:
      title: 文档标题
      doc_type: "case_report" | "literature" | "guideline" | "edited_report"
      file: 上传的文件（与 text_content 二选一）
      text_content: 直接传入的文本（与 file 二选一）
      source_patient: 关联 Patient 实例（病例报告类）
      uploaded_by: 上传用户
      metadata: 额外的结构化元数据
      is_approved: 是否已审核
      strategy: 分块策略（None=使用 KnowledgeSettings 中的配置）

    返回:
      KnowledgeDocument 实例
    """
    # --- 1. 获取文本 ---
    if file:
        extracted_text = extract_text(file)
    elif text_content:
        extracted_text = text_content
    else:
        raise ValueError("必须提供 file 或 text_content")

    if not extracted_text.strip():
        raise ValueError("提取的文本为空，无法入库")

    # --- 2. 读取分块策略 ---
    if strategy is None:
        try:
            strategy = KnowledgeSettings.load().chunk_strategy
        except Exception:
            strategy = "section"

    # --- 3. 创建文档记录 ---
    doc = KnowledgeDocument.objects.create(
        title=title,
        doc_type=doc_type,
        source_patient=source_patient,
        original_file=file,
        text_content=extracted_text,
        metadata=metadata or {},
        is_approved=is_approved,
        created_by=uploaded_by,
    )

    # --- 4. 分块 ---
    raw_chunks = chunk_text(extracted_text, strategy=strategy)

    if not raw_chunks:
        raise ValueError("分块结果为空，请检查文本内容或分块策略")

    log.info(
        "文档 '%s' (%s) → %d 个分块 (策略=%s)",
        title, doc_type, len(raw_chunks), strategy,
    )

    # --- 5. 向量化 + ChromaDB ---
    chunk_ids = [f"doc_{doc.pk}_chunk_{i}" for i in range(len(raw_chunks))]
    texts = [c["text"] for c in raw_chunks]
    metadatas = [
        {
            "document_id": doc.pk,
            "doc_type": doc_type,
            "section_label": c.get("section_label", ""),
        }
        for c in raw_chunks
    ]

    try:
        add_chunks(
            doc_type=doc_type,
            chunk_ids=chunk_ids,
            texts=texts,
            metadatas=metadatas,
        )
    except Exception:
        # ChromaDB 写入失败 → 回滚文档记录
        doc.delete()
        raise

    # --- 6. 创建 KnowledgeChunk 记录 ---
    chunk_objs = []
    for i, raw in enumerate(raw_chunks):
        chunk_objs.append(KnowledgeChunk(
            document=doc,
            chunk_index=i,
            text=raw["text"],
            embedding_id=chunk_ids[i],
            section_label=raw.get("section_label", ""),
            token_count=estimate_tokens(raw["text"]),
        ))
    KnowledgeChunk.objects.bulk_create(chunk_objs)

    log.info("文档 pk=%d 入库完成: %d 个 chunks", doc.pk, len(chunk_objs))
    return doc


def ingest_markdown_report(
    title: str,
    doc_type: str,
    markdown_text: str,
    source_patient=None,
    uploaded_by: User | None = None,
    metadata: dict | None = None,
    is_approved: bool = False,
) -> KnowledgeDocument:
    """
    直接入库 Markdown 格式的报告文本（无需文件上传）。

    用于：AI 生成报告保存、用户编辑报告入库等场景。
    """
    # Markdown → 纯文本（简单去除常用标记，保留结构）
    import re
    clean = markdown_text
    # 去除 Markdown 标记但保留内容
    clean = re.sub(r"^#{1,6}\s+", "", clean, flags=re.MULTILINE)  # 标题
    clean = re.sub(r"\*\*(.+?)\*\*", r"\1", clean)               # 加粗
    clean = re.sub(r"\*(.+?)\*", r"\1", clean)                     # 斜体
    clean = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", clean)              # 链接
    clean = re.sub(r"`{1,3}.+?`{1,3}", "", clean)                  # 代码块

    return ingest_document(
        title=title,
        doc_type=doc_type,
        text_content=clean,
        source_patient=source_patient,
        uploaded_by=uploaded_by,
        metadata=metadata,
        is_approved=is_approved,
    )


def delete_document(doc: KnowledgeDocument) -> None:
    """删除文档及其所有分块（DB + ChromaDB）"""
    # 收集 ChromaDB ID
    chunk_ids = list(
        doc.chunks.values_list("embedding_id", flat=True)
    )
    # 从 ChromaDB 删除
    if chunk_ids:
        try:
            delete_chunks(doc_type=doc.doc_type, chunk_ids=chunk_ids)
        except Exception as exc:
            log.warning("ChromaDB 删除异常（继续删除 DB 记录）: %s", exc)
    # Django CASCADE 会自动删除 chunk 记录
    doc.delete()
    log.info("文档 pk=%d (%s) 已删除", doc.pk, doc.title)


def rebuild_index(
    doc_type: str | None = None,
    strategy: str | None = None,
) -> dict:
    """
    重建向量索引（切换向量后端或分块策略后使用）。

    流程: 清空所有 chunks → 重新分块 → 重新向量化
    """
    from .vector_store import delete_collection, collection_stats

    qs = KnowledgeDocument.objects.filter(is_approved=True)
    if doc_type:
        qs = qs.filter(doc_type=doc_type)

    # 清空 ChromaDB
    for dt in set(qs.values_list("doc_type", flat=True)):
        delete_collection(dt)

    # 删除旧 chunk 记录
    KnowledgeChunk.objects.filter(document__in=qs).delete()

    # 重新入库
    rebuilt = 0
    failed = 0
    for doc in qs:
        try:
            raw_chunks = chunk_text(doc.text_content, strategy=strategy)
            chunk_ids = [f"doc_{doc.pk}_chunk_{i}" for i in range(len(raw_chunks))]
            texts = [c["text"] for c in raw_chunks]
            metadatas = [
                {
                    "document_id": doc.pk,
                    "doc_type": doc.doc_type,
                    "section_label": c.get("section_label", ""),
                }
                for c in raw_chunks
            ]
            add_chunks(doc.doc_type, chunk_ids, texts, metadatas)

            KnowledgeChunk.objects.bulk_create([
                KnowledgeChunk(
                    document=doc,
                    chunk_index=i,
                    text=raw["text"],
                    embedding_id=chunk_ids[i],
                    section_label=raw.get("section_label", ""),
                    token_count=estimate_tokens(raw["text"]),
                )
                for i, raw in enumerate(raw_chunks)
            ])
            rebuilt += 1
        except Exception as exc:
            log.error("重建文档 pk=%d 失败: %s", doc.pk, exc)
            failed += 1

    stats = collection_stats()
    return {
        "rebuilt": rebuilt,
        "failed": failed,
        "total": qs.count(),
        "stats": stats,
    }
