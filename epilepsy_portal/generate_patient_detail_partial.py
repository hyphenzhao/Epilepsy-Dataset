#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
自动从 patient_form_partial.html 生成只读详情模板
templates/epilepsy/patient_detail_partial.html

用法：
    python generate_patient_detail_partial.py
"""

import re
from pathlib import Path
from collections import OrderedDict

# === 路径设置，根据实际项目调整 ===
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates" / "epilepsy"

FORM_TEMPLATE = TEMPLATES_DIR / "patient_form_partial.html"
DETAIL_TEMPLATE = TEMPLATES_DIR / "patient_detail_partial.html"


def load_source():
    if not FORM_TEMPLATE.exists():
        raise SystemExit(f"找不到 patient_form_partial.html: {FORM_TEMPLATE}")
    return FORM_TEMPLATE.read_text(encoding="utf-8")


def parse_sections_and_fields(src: str):
    """
    从 patient_form_partial.html 里解析：
    - 注释中的大标题（用来做 section）
    - 每个 {{ form.xxx.label_tag }} 属于哪个 section
    返回 OrderedDict[section_title] = [field_name, ...]
    """
    # 找出所有注释
    comment_re = re.compile(r"<!--(.*?)-->", re.DOTALL)
    comments = []
    for m in comment_re.finditer(src):
        comments.append((m.start(), m.group(1).strip()))

    # 找出所有 label_tag
    field_re = re.compile(r"{{\s*form\.(\w+)\.label_tag\s*}}")
    fields = []
    for m in field_re.finditer(src):
        fields.append((m.start(), m.group(1)))

    def is_heading(text: str) -> bool:
        # 过滤掉被注释掉的 HTML 代码和 JS 标记
        if "<" in text or "jQuery" in text or "Popper" in text or "Bootstrap" in text:
            return False
        # 这里简单认为剩下的都是「大标题」，例如：一、基本信息 / 二、病史 ...
        return True

    sections = OrderedDict()
    for pos, fname in fields:
        section_title = None
        for cpos, ctext in comments:
            if cpos < pos and is_heading(ctext):
                section_title = ctext
            elif cpos >= pos:
                break
        if section_title is None:
            section_title = "其他"

        # 按顺序记录，避免重复字段
        sections.setdefault(section_title, [])
        if fname not in sections[section_title]:
            sections[section_title].append(fname)

    return sections


def generate_detail_template(sections: OrderedDict[str, list[str]]) -> str:
    """
    根据 {section_title: [field_name, ...]} 生成 patient_detail_partial.html 内容
    使用 form.<field>.label 和 form.<field>.value 做只读展示
    """
    lines = []
    lines.append('{# AUTO-GENERATED from patient_form_partial.html')
    lines.append('   请不要手动修改本文件。修改表单后请重新运行 generate_patient_detail_partial.py #}')
    lines.append("")
    lines.append('<div class="card">')
    lines.append('  <div class="card-body">')
    lines.append('    <h4 class="mb-3">{{ patient.name }}</h4>')
    lines.append("")

    first_section = True
    for section_title, field_names in sections.items():
        if not field_names:
            continue

        if not first_section:
            lines.append('    <hr class="my-4">')
        first_section = False

        # Section title
        lines.append(f'    <h5 class="mb-3">{section_title}</h5>')
        lines.append('    <div class="row">')

        for fname in field_names:
            lines.append('      <div class="col-md-6 mb-3">')
            lines.append('        <div class="border rounded p-2 h-100">')
            lines.append('          <div class="text-muted small mb-1">')
            lines.append(f'            {{% if form.{fname} %}}{{{{ form.{fname}.label }}}}{{% else %}}{fname}{{% endif %}}')
            lines.append('          </div>')
            lines.append('          <div>')
            # 这里使用 value + linebreaksbr，简单处理多行文本
            lines.append(f'            {{% if form.{fname} %}}')
            lines.append(f'              {{{{ form.{fname}.value|default:"-"|linebreaksbr }}}}')
            lines.append('            {% else %}')
            lines.append('              -')
            lines.append('            {% endif %}')
            lines.append('          </div>')
            lines.append('        </div>')
            lines.append('      </div>')

        lines.append('    </div>')
        lines.append("")

    lines.append("  </div>")
    lines.append("</div>")
    lines.append("")

    return "\n".join(lines)


def main():
    src = load_source()
    sections = parse_sections_and_fields(src)
    content = generate_detail_template(sections)
    DETAIL_TEMPLATE.write_text(content, encoding="utf-8")
    print(f"已生成: {DETAIL_TEMPLATE}")
    print("请确保在 patient_detail 视图中传入 patient 和 form=PatientForm(instance=patient)。")


if __name__ == "__main__":
    main()
