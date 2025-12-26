from django import template
import json
import ast

register = template.Library()

@register.filter
def display_multi(value, choices):
    """
    通用多选显示：
    - 支持 list / tuple
    - 支持 JSON 字符串: '["A","B"]'
    - 支持 Python list 字符串: "['A','B']"
    - 支持逗号分隔: 'A,B'
    """
    if value in (None, "", [], ()):
        return "-"

    vals = None

    # 已经是 list / tuple
    if isinstance(value, (list, tuple, set)):
        vals = list(value)
    else:
        s = str(value).strip()

        # JSON list
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                vals = parsed
        except Exception:
            pass

        # Python list string
        if vals is None:
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, list):
                    vals = parsed
            except Exception:
                pass

        # 逗号分隔
        if vals is None:
            vals = [v.strip() for v in s.split(",") if v.strip()]

    mapping = dict(choices)
    labels = [mapping.get(v, v) for v in vals]

    return "、".join(labels) if labels else "-"
