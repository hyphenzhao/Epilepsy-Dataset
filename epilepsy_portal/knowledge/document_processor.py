"""
文档处理 — 上传 → 文本提取

支持：PDF（pdfplumber）、Word（python-docx）、TXT / Markdown（直接读取）
"""

import logging
import os
from typing import Optional

from django.core.files.uploadedfile import UploadedFile

log = logging.getLogger(__name__)

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


def extract_text(file: UploadedFile) -> str:
    """
    从上传文件中提取纯文本。

    根据扩展名自动选择解析器，返回清洗后的文本。
    """
    ext = os.path.splitext(file.name)[1].lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件格式 '{ext}'。支持: {', '.join(SUPPORTED_EXTENSIONS.keys())}"
        )

    if ext == ".pdf":
        return _extract_pdf(file)
    elif ext == ".docx":
        return _extract_docx(file)
    else:
        # .txt / .md / .markdown
        return _extract_text(file)

    return ""


def _extract_pdf(file: UploadedFile) -> str:
    """从 PDF 提取文本（使用 pdfplumber）"""
    import pdfplumber

    text_parts = []
    with pdfplumber.open(file) as pdf:
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
            else:
                log.debug("PDF 第 %d 页无文本或提取为空", i + 1)

    full_text = "\n\n".join(text_parts)
    if not full_text.strip():
        raise ValueError("PDF 文件未提取到文本内容，可能是扫描版图片 PDF")

    return _clean_text(full_text)


def _extract_docx(file: UploadedFile) -> str:
    """从 Word (.docx) 提取文本"""
    from docx import Document

    doc = Document(file)
    paragraphs = []
    for para in doc.paragraphs:
        if para.text.strip():
            paragraphs.append(para.text.strip())

    # 也提取表格中的文本
    for table in doc.tables:
        for row in table.rows:
            row_text = []
            for cell in row.cells:
                if cell.text.strip():
                    row_text.append(cell.text.strip())
            if row_text:
                paragraphs.append(" | ".join(row_text))

    full_text = "\n\n".join(paragraphs)
    if not full_text.strip():
        raise ValueError("Word 文档未提取到文本内容")

    return _clean_text(full_text)


def _extract_text(file: UploadedFile) -> str:
    """从纯文本文件直接读取"""
    content = file.read().decode("utf-8", errors="replace")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return _clean_text(content)


def _clean_text(text: str) -> str:
    """清洗文本：去除多余空白、统一换行"""
    # 将连续 3 个以上换行压缩为 2 个
    import re

    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # 去除行尾空白
    text = "\n".join(line.rstrip() for line in text.splitlines())
    # 去除首尾空白
    text = text.strip()
    return text
