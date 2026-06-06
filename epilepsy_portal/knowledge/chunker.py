"""
智能分块器

策略（在 KnowledgeSettings 中配置）：
  - section   : 按报告分区标题分块（默认，匹配 12 个分区）
  - paragraph : 按自然段落分块
  - fixed     : 固定长度（~500 tokens）
  - hybrid    : 分区内细分为段落级
"""

import logging
import re
from typing import List

# 默认分块策略
DEFAULT_STRATEGY = "section"

log = logging.getLogger(__name__)

# 癫痫术前评估报告的 12 个分区标题模式
SECTION_PATTERNS = [
    r"[一二三四五六七八九十]+、\s*基本",
    r"[一二三四五六七八九十]+、\s*病史",
    r"[一二三四五六七八九十]+、\s*发作症状",
    r"[一二三四五六七八九十]+、\s*神经[系统科]检查",
    r"[一二三四五六七八九十]+、\s*认知[和与]",
    r"[一二三四五六七八九十]+、\s*精神",
    r"[一二三四五六七八九十]+、\s*(视频)?.?头皮.?EEG",
    r"[一二三四五六七八九十]+、\s*(脑电|脑电图)",
    r"[一二三四五六七八九十]+、\s*影像[学检查]",
    r"[一二三四五六七八九十]+、\s*MRI",
    r"[一二三四五六七八九十]+、\s*PET",
    r"[一二三四五六七八九十]+、\s*[一第]期.*(无创|评估)",
    r"[一二三四五六七八九十]+、\s*SEEG",
    r"[一二三四五六七八九十]+、\s*[二第]期.*(有创|评估)",
    r"[一二三四五六七八九十]+、\s*(外科)?切除",
    r"[一二三四五六七八九十]+、\s*(手术|外科)",
    r"[一二三四五六七八九十]+、\s*评估信息",
    r"[一二三四五六七八九十]+、\s*综合",
    r"[一二三四五六七八九十]+、\s*小结",
    r"[一二三四五六七八九十]+、\s*建议",
]

# 也支持数字编号：1. 2. 3.
SECTION_PATTERNS_NUMERIC = [
    r"\d+[\.\、\)]\s*\S",
]


def _detect_section_boundaries(text: str) -> List[dict]:
    """
    检测文本中的分区边界。

    返回: [{"start": 0, "label": "一、基本信息"}, ...]
    """
    combined = re.compile(
        "|".join(f"({p})" for p in SECTION_PATTERNS),
        re.UNICODE,
    )

    boundaries = []
    for m in combined.finditer(text):
        line_start = text.rfind("\n", 0, m.start())
        if line_start == -1:
            line_start = 0
        else:
            line_start += 1  # 跳过换行符

        # 提取标题文本（取匹配行）
        line_end = text.find("\n", m.start())
        if line_end == -1:
            line_end = len(text)
        heading = text[m.start():line_end].strip()

        # 只取合理长度的标题（不超过 30 字）
        if len(heading) <= 30:
            boundaries.append({
                "start": line_start,
                "label": heading,
            })

    # 按位置排序
    boundaries.sort(key=lambda b: b["start"])

    # 去重：相同 label 保留第一个
    seen = set()
    unique = []
    for b in boundaries:
        if b["label"] not in seen:
            seen.add(b["label"])
            unique.append(b)

    return unique


def chunk_by_section(text: str) -> List[dict]:
    """
    按报告分区标题分块。

    返回: [{"text": "...", "section_label": "一、基本信息"}, ...]
    """
    boundaries = _detect_section_boundaries(text)

    if not boundaries:
        # 没检测到分区标题 → 退回按段落分块
        log.info("未检测到报告分区标题，降级为按段落分块")
        return chunk_by_paragraph(text)

    chunks = []
    for i, boundary in enumerate(boundaries):
        start = boundary["start"]
        end = boundaries[i + 1]["start"] if i + 1 < len(boundaries) else len(text)
        chunk_text = text[start:end].strip()

        if chunk_text and len(chunk_text) > 20:  # 过滤太短的段
            chunks.append({
                "text": chunk_text,
                "section_label": boundary["label"],
            })

    return chunks


def chunk_by_paragraph(text: str, min_chars: int = 100) -> List[dict]:
    """
    按自然段落分块。

    以空行为段落边界，合并过短的段落。
    """
    raw_paragraphs = re.split(r"\n\s*\n", text)

    chunks = []
    buffer = ""
    buffer_label = ""

    for para in raw_paragraphs:
        para = para.strip()
        if not para:
            continue

        # 尝试检测该段落是否为一个标题
        is_heading = len(para) <= 30 and not para.endswith("。")

        if is_heading:
            # 先输出之前积累的 buffer
            if buffer.strip():
                chunks.append({
                    "text": buffer.strip(),
                    "section_label": buffer_label or "",
                })
            buffer = para + "\n"
            buffer_label = para
        elif len(buffer) + len(para) < min_chars * 3:
            # 合并短段落
            buffer += "\n" + para
        else:
            if buffer.strip():
                chunks.append({
                    "text": buffer.strip(),
                    "section_label": buffer_label or "",
                })
            buffer = para
            buffer_label = ""

    # 最后的 buffer
    if buffer.strip():
        chunks.append({
            "text": buffer.strip(),
            "section_label": buffer_label or "",
        })

    return chunks


def chunk_by_fixed(text: str, max_chars: int = 1800) -> List[dict]:
    """
    固定长度分块（~500 tokens ≈ 1800 中文字符）。

    尽量在段落边界断开。
    """
    paragraphs = text.split("\n")
    chunks = []
    buffer = ""

    for para in paragraphs:
        if len(buffer) + len(para) > max_chars and buffer:
            chunks.append({
                "text": buffer.strip(),
                "section_label": "",
            })
            buffer = para
        else:
            buffer += "\n" + para if buffer else para

    if buffer.strip():
        chunks.append({
            "text": buffer.strip(),
            "section_label": "",
        })

    return chunks


def chunk_by_hybrid(text: str) -> List[dict]:
    """
    混合策略：先按分区，每个分区内如果太长再细分为段落级。
    """
    section_chunks = chunk_by_section(text)
    result = []
    for sc in section_chunks:
        if len(sc["text"]) < 2000:
            result.append(sc)
        else:
            # 细分
            sub_paras = chunk_by_paragraph(sc["text"], min_chars=200)
            for sp in sub_paras:
                sp["section_label"] = sc["section_label"]
                result.append(sp)
    return result


# ============================================================
#  入口
# ============================================================

def chunk_text(text: str, strategy: str | None = None) -> List[dict]:
    """
    对文本进行分块，返回 chunk 字典列表。

    strategy: "section" | "paragraph" | "fixed" | "hybrid"
              为 None 时使用默认策略 "section"
    """
    if strategy is None:
        strategy = DEFAULT_STRATEGY

    log.info("分块策略: %s, 文本长度: %d 字符", strategy, len(text))

    if strategy == "paragraph":
        return chunk_by_paragraph(text)
    elif strategy == "fixed":
        return chunk_by_fixed(text)
    elif strategy == "hybrid":
        return chunk_by_hybrid(text)
    else:
        return chunk_by_section(text)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文 ~1.5 字符/token，英文 ~4 字符/token）"""
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)
