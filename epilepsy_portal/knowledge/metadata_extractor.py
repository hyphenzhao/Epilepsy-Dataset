"""
AI 元数据提取

使用 Ollama 从上传文档中提取结构化信息。

提取策略：
  - 病例报告: 9 个字段（人口学、发作、EEG、MRI、SEEG、手术、预后等）
  - 文献/指南: 4 个字段（论点、场景、证据等级、建议）
  - 文本截断到 6000 字符以保证响应速度
  - 使用 Ollama format=json 确保可解析输出
"""

import logging
from typing import Optional

from .ollama_client import ollama_chat_json

log = logging.getLogger(__name__)

MAX_CHARS = 6000  # 截断长度

# ============================================================
#  病例报告提取 Prompt
# ============================================================

CASE_REPORT_SYSTEM_PROMPT = """你是一个专业的癫痫术前评估文档信息提取助手。请从以下病例报告中提取结构化信息，以JSON格式返回。

需要提取的字段和说明：
- patient_age: 患者年龄（数字），如无法提取则为 ""
- patient_gender: 患者性别（"男"/"女"），如无法提取则为 ""
- seizure_type: 发作类型描述（如"局灶性发作"、"全面性发作"、"局灶性意识障碍性发作"等）
- eeg_findings: EEG/头皮脑电图关键发现（发作间期/发作期放电模式、侧向性、定位等）
- mri_findings: MRI/影像学关键发现（如"左侧海马硬化"、"FCD"等）
- seeg_results: SEEG/立体脑电图结果（如未提及则为 ""）
- surgery_type: 手术类型（如"前颞叶切除术"、"SEEG引导下射频热凝术"等）
- prognosis_engel: Engel分级预后（如"Engel I级"等）
- special_findings: 特殊发现/其他重要信息

规则：
- 如某字段在报告中完全未提及，值设为空字符串 ""
- 请提取具体信息而非原文照搬
- 返回严格有效的JSON对象，不要包含markdown代码块标记或其他说明文字
- 所有字段值使用中文"""


def extract_case_report_metadata(text: str) -> dict:
    """从病例报告文本提取结构化元数据"""
    if not text or not text.strip():
        return _empty_case_metadata()

    truncated = text[:MAX_CHARS]

    messages = [
        {"role": "system", "content": CASE_REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": truncated},
    ]

    try:
        result = ollama_chat_json(messages, temperature=0.1)
        # 校验并补全字段
        return _validate_case_metadata(result)
    except Exception as exc:
        log.warning("病例报告元数据提取失败: %s", exc)
        return _empty_case_metadata()


# ============================================================
#  文献/指南提取 Prompt
# ============================================================

LITERATURE_SYSTEM_PROMPT = """你是一个专业的医学文献信息提取助手。请从以下文献或指南中提取结构化信息，以JSON格式返回。

需要提取的字段和说明：
- core_thesis: 核心论点/研究目的/主要结论
- applicable_scenarios: 适用场景/适用范围/适用人群
- evidence_level: 证据等级（如"A级推荐"、"B级推荐"、"专家共识"、"回顾性研究"等）
- recommendations: 具体建议/推荐意见

规则：
- 如某字段在文档中完全未提及，值设为空字符串 ""
- 请提取关键要点而非原文照搬
- 返回严格有效的JSON对象，不要包含markdown代码块标记或其他说明文字
- 所有字段值使用中文"""


def extract_literature_metadata(text: str) -> dict:
    """从文献/指南文本提取结构化元数据"""
    if not text or not text.strip():
        return _empty_literature_metadata()

    truncated = text[:MAX_CHARS]

    messages = [
        {"role": "system", "content": LITERATURE_SYSTEM_PROMPT},
        {"role": "user", "content": truncated},
    ]

    try:
        result = ollama_chat_json(messages, temperature=0.1)
        return _validate_literature_metadata(result)
    except Exception as exc:
        log.warning("文献元数据提取失败: %s", exc)
        return _empty_literature_metadata()


# ============================================================
#  统一入口
# ============================================================

def extract_metadata(text: str, doc_type: str) -> dict:
    """
    根据文档类型自动选择提取器。

    参数:
      text: 文档纯文本
      doc_type: "case_report" | "literature" | "guideline" | "edited_report"

    返回:
      结构化元数据 dict
    """
    if doc_type == "case_report" or doc_type == "edited_report":
        return extract_case_report_metadata(text)
    elif doc_type in ("literature", "guideline"):
        return extract_literature_metadata(text)
    else:
        log.warning("未知文档类型 '%s'，跳过元数据提取", doc_type)
        return {}


# ============================================================
#  校验与默认值
# ============================================================

CASE_METADATA_FIELDS = [
    "patient_age", "patient_gender", "seizure_type",
    "eeg_findings", "mri_findings", "seeg_results",
    "surgery_type", "prognosis_engel", "special_findings",
]

LITERATURE_METADATA_FIELDS = [
    "core_thesis", "applicable_scenarios",
    "evidence_level", "recommendations",
]


def _empty_case_metadata() -> dict:
    return {f: "" for f in CASE_METADATA_FIELDS}


def _empty_literature_metadata() -> dict:
    return {f: "" for f in LITERATURE_METADATA_FIELDS}


def _validate_case_metadata(raw: dict) -> dict:
    """补全缺失字段，过滤多余字段"""
    result = {}
    for f in CASE_METADATA_FIELDS:
        val = raw.get(f, "")
        result[f] = str(val) if val else ""
    return result


def _validate_literature_metadata(raw: dict) -> dict:
    """补全缺失字段，过滤多余字段"""
    result = {}
    for f in LITERATURE_METADATA_FIELDS:
        val = raw.get(f, "")
        result[f] = str(val) if val else ""
    return result
