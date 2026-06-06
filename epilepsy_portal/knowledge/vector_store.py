"""
ChromaDB 向量存储封装

管理 ChromaDB client 和 collection，提供增删查接口。

Collection 命名：
  - knowledge_cases     : 病例报告 / 编辑反馈
  - knowledge_literature: 文献 / 指南
"""

import logging
import os
from typing import List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from django.conf import settings as django_settings

from .embeddings import embed_text, embed_texts, embedding_dimension

log = logging.getLogger(__name__)

# ChromaDB 持久化目录（放在项目 large_files 下）
CHROMA_DATA_DIR = os.path.join(
    getattr(django_settings, "LARGE_FILE_BASE_DIR", django_settings.BASE_DIR / "large_files"),
    "chromadb",
)

# 两个 collection
COLLECTION_CASES = "knowledge_cases"           # 病例报告 + 编辑反馈
COLLECTION_LITERATURE = "knowledge_literature"  # 文献 + 指南

_client: Optional[chromadb.PersistentClient] = None


def _get_client() -> chromadb.PersistentClient:
    """懒初始化 ChromaDB PersistentClient"""
    global _client
    if _client is None:
        os.makedirs(CHROMA_DATA_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=CHROMA_DATA_DIR,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        log.info("ChromaDB 已初始化，数据目录: %s", CHROMA_DATA_DIR)
    return _client


def _get_or_create_collection(name: str) -> chromadb.Collection:
    """获取或创建 collection"""
    client = _get_client()
    dim = embedding_dimension()
    try:
        collection = client.get_collection(name)
    except Exception:
        collection = client.create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
        log.info("ChromaDB collection '%s' 已创建 (dim=%d)", name, dim)
    return collection


def get_cases_collection() -> chromadb.Collection:
    return _get_or_create_collection(COLLECTION_CASES)


def get_literature_collection() -> chromadb.Collection:
    return _get_or_create_collection(COLLECTION_LITERATURE)


def _collection_for_doc_type(doc_type: str) -> chromadb.Collection:
    """根据文档类型路由到对应 collection"""
    if doc_type in ("literature", "guideline"):
        return get_literature_collection()
    # case_report, edited_report 等
    return get_cases_collection()


# ============================================================
#  增
# ============================================================

def add_chunks(
    doc_type: str,
    chunk_ids: List[str],
    texts: List[str],
    metadatas: List[dict] | None = None,
) -> None:
    """
    将分块文本向量化后写入 ChromaDB。

    参数:
      doc_type: 文档类型，决定写入哪个 collection
      chunk_ids: Chunk 唯一 ID 列表
      texts: 分块文本列表
      metadatas: 可选的元数据列表（如 section_label, document_id 等）
    """
    if not texts:
        return
    collection = _collection_for_doc_type(doc_type)
    embeddings = embed_texts(texts)
    collection.add(
        ids=chunk_ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    log.info("ChromaDB: 已添加 %d 个 chunks → '%s'", len(texts), collection.name)


# ============================================================
#  查
# ============================================================

def query_similar(
    doc_type: str,
    query_text: str,
    top_k: int = 5,
    where: dict | None = None,
) -> dict:
    """
    查询与 query_text 最相似的 chunks。

    返回 ChromaDB query_results 字典，包含 ids, distances, documents, metadatas。
    """
    collection = _collection_for_doc_type(doc_type)
    query_embedding = embed_text(query_text)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return results


def query_by_section(
    doc_type: str,
    query_text: str,
    section_label: str,
    top_k: int = 3,
) -> dict:
    """在指定分区块内检索（利用 metadata filter）"""
    return query_similar(
        doc_type=doc_type,
        query_text=query_text,
        top_k=top_k,
        where={"section_label": section_label},
    )


# ============================================================
#  删
# ============================================================

def delete_chunks(doc_type: str, chunk_ids: List[str]) -> None:
    """从 ChromaDB 删除指定 chunks"""
    if not chunk_ids:
        return
    collection = _collection_for_doc_type(doc_type)
    collection.delete(ids=chunk_ids)
    log.info("ChromaDB: 已删除 %d 个 chunks", len(chunk_ids))


def delete_collection(doc_type: str) -> None:
    """删除整个 collection（切换向量后端时使用）"""
    client = _get_client()
    name = _collection_for_doc_type(doc_type).name
    try:
        client.delete_collection(name)
        log.warning("ChromaDB collection '%s' 已删除", name)
    except Exception:
        pass


# ============================================================
#  统计
# ============================================================

def collection_stats() -> dict:
    """返回两个 collection 的统计信息"""
    try:
        cases = get_cases_collection()
        lit = get_literature_collection()
        return {
            "cases_count": cases.count(),
            "literature_count": lit.count(),
        }
    except Exception:
        return {"cases_count": 0, "literature_count": 0}
