#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
自动从 patient_form_partial.html 生成只读详情模板
templates/epilepsy/patient_detail_partial.html

规则：
- 用 <!-- 一、基本信息（默认展开） --> 这类注释做分组标题
- 忽略所有被注释掉的代码（任何在 <!-- ... --> 里面的 {{ form.xxx.label_tag }} 不会被采集）
- 生成的模板只负责纯展示（label + value），不复用任何样式结构
"""

from pathlib import Path
from collections import OrderedDict
import re

# === 路径设置：根据你的项目调整 ===
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates" / "epilepsy"

FORM_TEMPLATE = TEMPLATES_DIR / "patient_form_partial.html"
DETAIL_TEMPLATE = TEMPLATES_DIR / "patient_detail_partial.html"


def load_source() -> str:
    if not FORM_TEMPLATE.exists():
        raise SystemExit(f"找不到 patient_form_partial.html: {FORM_TEMPLATE}")
    return FORM_TEMPLATE.read_text(encoding="utf-8")


def parse_sections_and_fields(src: str) -> "OrderedDict[str, list[str]]":
    """
    从 patient_form_partial.html 中解析：
    - 注释里的大标题（用来做 section）
    - 每个 {{ form.xxx.label_tag }} 字段属于哪个 section

    关键点：任何在 <!-- ... --> 里的 label_tag 都会被忽略。
    """

    # 1) 收集所有注释（用来做：a) heading 候选，b) comment range）
    comment_iter = list(re.finditer(r"<!--(.*?)-->", src, re.DOTALL))
    # (start, end, text)
    comment_ranges = [
        (m.start(), m.end(), m.group(1).strip())
        for m in comment_iter
    ]

    def in_comment(pos: int) -> bool:
        """判断某个位置是否处于 <!-- ... --> 注释块内部。"""
        for start, end, _ in comment_ranges:
            if start <= pos <= end:
                return True
        return False

    # 2) 提取“纯文字的注释”作为 section 标题
    headings = []
    for start, end, text in comment_ranges:
        # 过滤掉真正被注释掉的 HTML/模板代码（里面通常有 < 或 {{ 之类）
        if "<" in text or "{{" in text or "}}" in text or "script" in text.lower():
            continue
        # 剩下的就认为是类似 “一、基本信息（默认展开）” 这种标题
        headings.append((start, text.strip()))
    headings.sort(key=lambda x: x[0])

    # 3) 找出所有不在注释里的 {{ form.xxx.label_tag }}
    fields = []
    field_re = re.compile(r"{{\s*form\.(\w+)\.label_tag\s*}}")
    for m in field_re.finditer(src):
        pos = m.start()
        if in_comment(pos):
            # 在 <!-- ... --> 里的 label_tag，一律忽略
            continue
        fname = m.group(1)
        fields.append((pos, fname))

    # 4) 按“就近之前的 heading 注释”把字段分组
    sections: "OrderedDict[str, list[str]]" = OrderedDict()
    for pos, fname in fields:
        section_title = None
        for h_pos, h_text in headings:
            if h_pos < pos:
                section_title = h_text
            else:
                break
        if section_title is None:
            section_title = "其他"

        sections.setdefault(section_title, [])
        if fname not in sections[section_title]:
            sections[section_title].append(fname)

    return sections


def generate_detail_template(sections: "OrderedDict[str, list[str]]") -> str:
    """
    根据 {section_title: [field_name, ...]} 生成 patient_detail_partial.html 内容。
    使用 form.<field>.label & form.<field>.value 做只读展示，不引入原来的样式结构。
    """
    lines: list[str] = []

    lines.append('<!-- AUTO-GENERATED from patient_form_partial.html')
    lines.append('   请不要手动修改本文件。修改表单后请重新运行 generate_patient_detail_partial.py -->')
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

        lines.append(f'    <h5 class="mb-3">{section_title}</h5>')
        lines.append('    <div class="row">')

        for fname in field_names:
            lines.append('      <div class="col-md-6 mb-3">')
            lines.append('        <div class="border rounded p-2 h-100">')
            lines.append('          <div class="text-muted small mb-1">')
            # 优先用 form.<field>.label；如果表单里没这个字段，就直接显示字段名
            lines.append(
                f'            {{% if form.{fname} %}}{{{{ form.{fname}.label }}}}{{% else %}}{fname}{{% endif %}}'
            )
            lines.append('          </div>')
            lines.append('          <div>')
            lines.append(f'            {{% if form.{fname} %}}')
            lines.append(
                f'              {{{{ form.{fname}.value|default:"-"|linebreaksbr }}}}'
            )
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
    print("注意：patient_detail 视图需要传入 patient 和 form=PatientForm(instance=patient)。")


if __name__ == "__main__":
    main()
