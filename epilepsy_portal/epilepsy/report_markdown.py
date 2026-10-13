"""Shared Markdown rendering for report previews and document exports."""

import re
from html import escape
from urllib.parse import urlsplit
from xml.etree import ElementTree

from lxml import etree
from lxml import html as lxml_html
import markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor


_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div",
    "dl", "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i",
    "img", "li", "ol", "p", "pre", "s", "span", "strong", "sub", "sup",
    "table", "tbody", "td", "th", "thead", "tr", "ul",
}
_DROP_CONTENT = {
    "audio", "base", "button", "embed", "form", "iframe", "input", "link",
    "math", "meta", "noscript", "object", "option", "script", "select",
    "source", "style", "svg", "template", "textarea", "video",
}


class _StrikethroughProcessor(InlineProcessor):
    def handleMatch(self, match, data):
        element = ElementTree.Element("del")
        element.text = match.group(1)
        return element, match.start(0), match.end(0)


class _ReportInlineExtension(Extension):
    def extendMarkdown(self, md):
        md.inlinePatterns.register(
            _StrikethroughProcessor(r"(?<!~)~~(?=\S)(.+?)(?<=\S)~~(?!~)", md),
            "report_strikethrough", 175,
        )


def _safe_url(value, *, image=False):
    value = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return None
    allowed_schemes = {"", "http", "https"} if image else {"", "http", "https", "mailto"}
    return value if scheme in allowed_schemes else None


def _clean_attributes(node, tag):
    kept = {}
    for name, value in node.attrib.items():
        name = name.lower()
        if name == "title":
            kept[name] = value
        elif tag == "a" and name == "href":
            safe = _safe_url(value)
            if safe is not None:
                kept[name] = safe
        elif tag == "img" and name == "src":
            safe = _safe_url(value, image=True)
            if safe is not None:
                kept[name] = safe
        elif tag == "img" and name == "alt":
            kept[name] = value
        elif tag in {"th", "td"} and name == "style":
            match = re.fullmatch(r"\s*text-align\s*:\s*(left|center|right)\s*;?\s*", value, re.I)
            if match:
                kept[name] = f"text-align: {match.group(1).lower()};"
        elif tag in {"th", "td"} and name == "align" and value.lower() in {"left", "center", "right"}:
            kept[name] = value.lower()
        elif tag in {"th", "td"} and name in {"colspan", "rowspan"}:
            if value.isdigit() and 1 <= int(value) <= 100:
                kept[name] = value
        elif tag == "ol" and name == "start" and re.fullmatch(r"-?\d{1,7}", value):
            kept[name] = value
        elif name == "id" and re.fullmatch(r"fn(?:ref)?[\w:.-]*", value):
            kept[name] = value
        elif name == "class":
            classes = [
                part for part in value.split()
                if part in {"footnote", "footnote-ref", "footnote-backref"}
                or (tag == "code" and re.fullmatch(r"language-[\w+-]+", part))
            ]
            if classes:
                kept[name] = " ".join(classes)
    node.attrib.clear()
    node.attrib.update(kept)


def render_report_markdown(markdown_text):
    """Keep Markdown document structure while removing executable HTML."""
    generated = markdown.markdown(
        markdown_text or "",
        extensions=["extra", "sane_lists", "tables", "fenced_code", _ReportInlineExtension()],
    )
    root = lxml_html.fragment_fromstring(generated or "", create_parent=True)
    for node in list(root.iterdescendants()):
        if node.getparent() is None:
            continue
        if not isinstance(node.tag, str):
            node.drop_tree()
            continue
        tag = node.tag.lower()
        if tag in _DROP_CONTENT:
            node.drop_tree()
        elif tag not in _ALLOWED_TAGS:
            node.drop_tag()
        else:
            _clean_attributes(node, tag)
    return escape(root.text or "", quote=False) + "".join(
        etree.tostring(child, encoding="unicode", method="html") for child in root
    )
