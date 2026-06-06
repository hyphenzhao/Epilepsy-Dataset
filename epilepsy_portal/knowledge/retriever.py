"""
知识库检索编排

用于 RAG 增强报告生成：
  1. 从患者导出 JSON 构建查询文本
  2. 并行检索病例库 + 文献库
  3. 结果去重、格式化
"""

import logging
from typing import List, Dict, Any

from .vector_store import query_similar
from .models import KnowledgeSettings

log = logging.getLogger(__name__)


def build_query_text(export_json: dict) -> str:
    """
    将患者导出 JSON 拼接为自然语言查询文本。

    输入格式: build_patient_export_json() 的返回值
    输出: "基本信息：姓名张三，性别男。发作症状学：发作类型局灶性发作..."
    """
    sections = export_json.get("sections", [])
    parts = []

    for section in sections:
        title = section.get("title", "")
        items = section.get("items", [])
        if not items:
            continue
        item_texts = []
        for item in items:
            label = item.get("label", "")
            value = item.get("value", "")
            if value and str(value).strip():
                item_texts.append(f"{label}{value}")
        if item_texts:
            parts.append(f"{title}：{'，'.join(item_texts)}")

    query = "。".join(parts) + "。"
    log.debug("构建查询文本: %d 字符", len(query))
    return query


def retrieve_context(export_json: dict) -> dict:
    """
    检索与当前患者相关的知识库内容。

    参数:
      export_json: build_patient_export_json() 的返回值

    返回:
      {
        "cases": [{"text": "...", "score": 0.85, "section": "发作症状学", "doc_id": 1, "doc_title": "..."}],
        "literature": [...],
        "total_found": N,
      }
    """
    settings = KnowledgeSettings.load()
    query = build_query_text(export_json)

    if not query.strip():
        return {"cases": [], "literature": [], "total_found": 0}

    top_k_cases = settings.top_k_cases
    top_k_literature = settings.top_k_literature

    cases_results = _retrieve("case_report", query, top_k_cases)
    lit_results = _retrieve("literature", query, top_k_literature)

    total = len(cases_results) + len(lit_results)
    log.info("检索完成: 病例 %d 条, 文献 %d 条", len(cases_results), len(lit_results))

    return {
        "cases": cases_results,
        "literature": lit_results,
        "total_found": total,
    }


def _retrieve(doc_type: str, query: str, top_k: int) -> List[dict]:
    """检索并格式化结果"""
    try:
        raw = query_similar(doc_type, query, top_k=top_k)
    except Exception as exc:
        log.warning("检索 '%s' 失败: %s", doc_type, exc)
        return []

    # ChromaDB 返回格式: {ids: [[...]], distances: [[...]], documents: [[...]], metadatas: [[...]]}
    ids = raw.get("ids", [[]])[0] if raw.get("ids") else []
    distances = raw.get("distances", [[]])[0] if raw.get("distances") else []
    documents = raw.get("documents", [[]])[0] if raw.get("documents") else []
    metadatas = raw.get("metadatas", [[]])[0] if raw.get("metadatas") else []

    results = []
    seen_doc_ids = set()

    for i in range(len(ids)):
        meta = metadatas[i] if i < len(metadatas) else {}
        doc_id = meta.get("document_id")
        text = documents[i] if i < len(documents) else ""
        distance = distances[i] if i < len(distances) else 1.0

        # 去重：同一文档只保留最匹配的 chunk
        if doc_id and doc_id in seen_doc_ids:
            continue
        if doc_id:
            seen_doc_ids.add(doc_id)

        # 截断过长文本
        if len(text) > 1500:
            text = text[:1500] + "..."

        # 获取文档标题
        doc_title = _get_doc_title(doc_id) if doc_id else "未知文档"

        results.append({
            "text": text,
            "score": round(1.0 - distance, 4) if distance else 0.0,  # cosine distance → similarity
            "section": meta.get("section_label", ""),
            "doc_id": doc_id,
            "doc_title": doc_title,
        })

    return results


def _get_doc_title(doc_id: int) -> str:
    """从数据库获取文档标题"""
    try:
        from .models import KnowledgeDocument
        doc = KnowledgeDocument.objects.only("title").get(pk=doc_id)
        return doc.title
    except Exception:
        return ""


def format_context_for_prompt(retrieval_result: dict) -> str:
    """
    将检索结果格式化为 prompt 上下文文本。

    返回可直接注入 prompt 的中文字符串。
    """
    parts = []

    cases = retrieval_result.get("cases", [])
    if cases:
        parts.append("## 相似病例参考\n")
        for i, c in enumerate(cases, 1):
            title = c.get("doc_title", "病例")
            section = c.get("section", "")
            text = c.get("text", "")
            header = f"【参考{i}】{title}"
            if section:
                header += f" — {section}"
            parts.append(f"{header}\n{text}\n")

    literature = retrieval_result.get("literature", [])
    if literature:
        parts.append("## 相关文献/指南摘录\n")
        for i, lit in enumerate(literature, 1):
            title = lit.get("doc_title", "文献")
            text = lit.get("text", "")
            parts.append(f"【文献{i}】{title}\n{text}\n")

    return "\n".join(parts) if parts else ""
