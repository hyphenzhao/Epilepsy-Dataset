#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
自动从 patient_form_partial.html 生成只读详情模板
templates/epilepsy/patient_detail_partial.html

改进点（相对旧版）：
1) 字段采集不再只依赖 `{{ form.xxx.label_tag }}`：
   - 同时采集 `{{ form.xxx }}`（如单独渲染的 select/checkbox/hidden 等）
   - 同时采集 `{% for cb in form.xxx %}`（常见于 checkbox/radio 手工循环渲染）
   这样可以避免“新增 coded sEEG 字段在详情页缺失”的问题。

2) 详情页值展示对 choices 做了“值 -> 显示文本”映射：
   - 单选：显示 choice label
   - 多选（checkbox）：显示已选项的 choice label（可显示中文）
   这样可以避免详情页显示英文 code/字段值而不是中文选项名的问题。

规则：
- 用类似 `<!-- 一、基本信息（默认展开） -->` 的注释做分组标题
  （仅识别以中文序号或数字序号开头的注释，如：一、/二、/1./1、）
- 忽略所有被注释掉的代码（任何在 `<!-- ... -->` 里面的字段引用不会被采集）
- 生成的模板只负责纯展示（label + value），不复用原来的样式结构
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
    - 模板中出现的 form 字段引用属于哪个 section

    关键点：
    - 任何在 <!-- ... --> 注释块内部的字段引用都会被忽略
    - section 标题仅识别形如 “一、xxx / 1.xxx / 1、xxx”
      避免把诸如 “<!-- 出现方式 -->” 这类注释误当作新的大 section
    """

    # 1) 收集所有 HTML 注释（用来做：a) heading 候选，b) comment range）
    comment_iter = list(re.finditer(r"<!--(.*?)-->", src, re.DOTALL))
    # (start, end, text)
    comment_ranges = [(m.start(), m.end(), m.group(1).strip()) for m in comment_iter]

    def in_comment(pos: int) -> bool:
        """判断某个位置是否处于 <!-- ... --> 注释块内部。"""
        for start, end, _ in comment_ranges:
            # 注意：使用 start <= pos < end，避免 pos == end 被误判为仍在注释中
            if start <= pos < end:
                return True
        return False

    # 2) 提取“纯文字且像章节标题的注释”作为 section 标题
    headings = []
    heading_re = re.compile(r"^\s*([一二三四五六七八九十百]+、|\d+[\.、])")
    for start, end, text in comment_ranges:
        t = text.strip()
        if not t:
            continue
        # 过滤掉真正被注释掉的 HTML/模板代码（里面通常有 < 或 {{ 之类）
        if "<" in t or "{{" in t or "}}" in t or "script" in t.lower():
            continue
        if not heading_re.match(t):
            continue
        headings.append((start, t))
    headings.sort(key=lambda x: x[0])

    # 3) 采集字段引用（不在注释中）
    matches = []  # [(pos, field_name), ...]

    # 3.1) 标准 label_tag
    for m in re.finditer(r"{{\s*form\.(\w+)\.label_tag\s*}}", src):
        if not in_comment(m.start()):
            matches.append((m.start(), m.group(1)))

    # 3.2) 直接渲染字段：{{ form.xxx }}
    blacklist = {"non_field_errors", "instance", "media"}
    for m in re.finditer(r"{{\s*form\.(\w+)\s*}}", src):
        if in_comment(m.start()):
            continue
        fname = m.group(1)
        if fname in blacklist:
            continue
        matches.append((m.start(), fname))

    # 3.3) checkbox/radio 等常见手工循环：{% for cb in form.xxx %}
    for m in re.finditer(r"{%\s*for\s+\w+\s+in\s+form\.(\w+)\s*%}", src):
        if not in_comment(m.start()):
            matches.append((m.start(), m.group(1)))

    matches.sort(key=lambda x: x[0])

    # 4) 按“就近之前的 heading 注释”把字段分组
    sections: "OrderedDict[str, list[str]]" = OrderedDict()
    for pos, fname in matches:
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

    展示策略：
    - label：优先 form.<field>.label
    - value：
      * 若字段存在 choices，则把 value 映射为 choice_label（多选显示多个 badge）
      * 否则直接显示 value（支持换行）
    """
    lines: list[str] = []

    lines.append('<!-- AUTO-GENERATED from patient_form_partial.html')
    lines.append('   请不要手动修改本文件。修改表单后请重新运行 generate_patient_detail_partial.py -->')
    lines.append('')
    lines.append('<div class="card">')
    lines.append('  <div class="card-body">')
    lines.append('    <h4 class="mb-3">{{ patient.name }}</h4>')

    # ===== 文件预览（仅前三张图片） =====
    lines.append('    <hr class="my-3">')
    lines.append('    <h5 class="mb-3">文件预览（仅显示前三张图片）</h5>')
    lines.append('    <div class="row">')

    def add_preview_block(title: str, var: str, ftype: str):
        lines.append('      <div class="col-12 col-lg-6 mb-3">')
        lines.append('        <div class="border rounded p-2 h-100">')
        lines.append(f'          <div class="text-muted small mb-2">{title}</div>')
        lines.append('          <div class="row">')
        lines.append(f'            {{% for f in {var} %}}')
        lines.append('              <div class="col-4 mb-2">')
        lines.append('                <div class="card">')
        lines.append('                  <img class="card-img-top img-fluid" style="max-height:140px; object-fit:cover;" loading="lazy"')
        lines.append(f'                       src="{{% url \'epilepsy:patient_file_preview\' \'{ftype}\' f.id %}}"')
        lines.append(f'                       data-preview-src="{{% url \'epilepsy:patient_file_preview\' \'{ftype}\' f.id %}}"')
        lines.append('                       alt="{{ f.file_name }}" title="点击放大">')
        lines.append('                  <div class="card-body p-2">')
        lines.append('                    <div class="small text-truncate" title="{{ f.file_name }}">{{ f.file_name }}</div>')
        lines.append('                  </div>')
        lines.append('                </div>')
        lines.append('              </div>')
        lines.append(f'            {{% empty %}}<div class="col-12 text-muted small">暂无可预览图片</div>{{% endfor %}}')
        lines.append('          </div>')
        lines.append('        </div>')
        lines.append('      </div>')

    add_preview_block('MRI', 'preview_mri', 'mri')
    add_preview_block('PET', 'preview_pet', 'pet')
    add_preview_block('EEG', 'preview_eeg', 'eeg')
    add_preview_block('sEEG', 'preview_seeg', 'seeg')

    lines.append('    </div>')
    lines.append('')

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
            lines.append(f'            {{% if form.{fname} %}}{{{{ form.{fname}.label }}}}{{% else %}}{fname}{{% endif %}}')
            lines.append('          </div>')
            lines.append('          <div>')
            lines.append(f'            {{% if form.{fname} %}}')
            lines.append(f'              {{% with bf=form.{fname} val=form.{fname}.value %}}')
            lines.append('                {% if bf.field.choices %}')
            lines.append('                  {% if val %}')
            lines.append('                    {% if bf.field.widget.allow_multiple_selected %}')
            lines.append('                      {% for v,l in bf.field.choices %}')
            lines.append('                        {% if v in val %}<span class="badge badge-secondary mr-1">{{ l }}</span>{% endif %}')
            lines.append('                      {% endfor %}')
            lines.append('                    {% else %}')
            lines.append('                      {% for v,l in bf.field.choices %}')
            lines.append('                        {% if v == val %}{{ l }}{% endif %}')
            lines.append('                      {% endfor %}')
            lines.append('                    {% endif %}')
            lines.append('                  {% else %}-{% endif %}')
            lines.append('                {% else %}')
            lines.append('                  {{ val|default:"-"|linebreaksbr }}')
            lines.append('                {% endif %}')
            lines.append('              {% endwith %}')
            lines.append('            {% else %}')
            lines.append('              -')
            lines.append('            {% endif %}')
            lines.append('          </div>')
            lines.append('        </div>')
            lines.append('      </div>')

        lines.append('    </div>')
        lines.append('')

    lines.append('  </div>')
    lines.append('</div>')
    lines.append('')

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
