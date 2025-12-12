#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate epilepsy/json.py with PATIENT_GROUP_FIELDS and FIELDS_FOR_EXPORT.

- PATIENT_GROUP_FIELDS is parsed from templates/epilepsy/patient_form_partial.html
- FIELDS_FOR_EXPORT is parsed from epilepsy/models.py (class Patient)

Place this file next to manage.py and run:

    python generate_patient_json.py
"""

import os
import re
import ast
from pprint import pformat


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, "epilepsy")
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "epilepsy", "patient_form_partial.html")
MODELS_PATH = os.path.join(APP_DIR, "models.py")
OUTPUT_PATH = os.path.join(APP_DIR, "json.py")


def camel_to_snake(name: str) -> str:
    """Convert 'FirstStage' -> 'first_stage', 'SEEG' -> 'seeg'."""
    if name.isupper():
        return name.lower()
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def build_patient_group_fields() -> dict:
    """
    Parse patient_form_partial.html and generate PATIENT_GROUP_FIELDS.

    - Each <div class="card-header" id="headingXXX"> ... </button> defines a group.
    - XXX (Basic, FirstStage, etc.) is converted to snake_case key.
    - The button text is used as label.
    - All {{ form.xxx }} usages between this header and the next header are
      collected as the field list.
    """
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    # Match section headers
    header_pattern = re.compile(
        r'<div\s+class="card-header"\s+id="heading(?P<key>[^"]+)">\s*'
        r".*?<button[^>]*>(?P<label>.*?)</button>",
        re.S,
    )
    # Capture {{ form.xxx }} even if filters / attributes follow (e.g. {{ form.name.errors }})
    field_pattern = re.compile(r"\{\{\s*form\.(\w+)[^}]*\}\}")

    matches = list(header_pattern.finditer(html))
    group_data: dict[str, dict] = {}

    for idx, m in enumerate(matches):
        key_camel = m.group("key")          # e.g. "Basic", "FirstStage"
        key = camel_to_snake(key_camel)     # e.g. "basic", "first_stage"

        label_raw = m.group("label")
        label = " ".join(label_raw.split())  # normalize whitespace

        # Section = from end of this header to start of next header (or EOF)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(html)
        section_html = html[start:end]

        # Collect all field names in this section, de-duplicated
        raw_fields = field_pattern.findall(section_html)
        seen = set()
        fields: list[str] = []
        for fname in raw_fields:
            if fname == "non_field_errors":
                continue
            if fname in seen:
                continue
            seen.add(fname)
            fields.append(fname)

        group_data[key] = {
            "label": label,
            "fields": fields,
        }

    return group_data


def build_fields_for_export() -> list[tuple[str, str]]:
    """
    Parse epilepsy/models.py and build FIELDS_FOR_EXPORT from class Patient.

    For each attribute in Patient defined as:

        some_field = models.XxxField(...)

    we extract:

    - field_name: the attribute name.
    - verbose_name: from verbose_name=... if present, otherwise the first
      positional argument if it is a string, otherwise fall back to field_name.

    We skip the implicit 'id' primary key and any attributes that look like
    managers (e.g. objects = models.Manager()).
    """
    with open(MODELS_PATH, encoding="utf-8") as f:
        source = f.read()

    module = ast.parse(source, filename=MODELS_PATH)

    patient_class = None
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "Patient":
            patient_class = node
            break

    if patient_class is None:
        raise RuntimeError("Could not find class 'Patient' in models.py")

    fields: list[tuple[str, str]] = []

    for node in patient_class.body:
        # Handle assignments like: field_name = models.CharField(...)
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue

        field_name = node.targets[0].id
        if field_name == "id":
            continue

        value = node.value
        if not isinstance(value, ast.Call):
            continue

        func = value.func
        if not isinstance(func, ast.Attribute):
            continue

        # Only consider models.XxxField / ForeignKey / OneToOneField / ManyToManyField
        if not (isinstance(func.value, ast.Name) and func.value.id == "models"):
            continue

        field_class_name = func.attr
        if not (
            field_class_name.endswith("Field")
            or field_class_name in {"ForeignKey", "OneToOneField", "ManyToManyField"}
        ):
            # Likely a Manager or something else; skip
            continue

        verbose_name = None

        # Prefer explicit verbose_name= keyword
        for kw in value.keywords:
            if kw.arg == "verbose_name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                verbose_name = kw.value.value
                break

        # If not set, use the first positional arg if it's a string
        if verbose_name is None and value.args:
            first_arg = value.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                verbose_name = first_arg.value

        if verbose_name is None:
            verbose_name = field_name

        fields.append((field_name, verbose_name))

    return fields


def write_json_module(patient_group_fields: dict, fields_for_export: list[tuple[str, str]]) -> None:
    """
    Write epilepsy/json.py containing PATIENT_GROUP_FIELDS and FIELDS_FOR_EXPORT.
    """
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write("# This file is auto-generated by generate_patient_json.py.\n")
        f.write("# Do not edit manually; re-run the generator when templates/models change.\n\n")

        f.write("PATIENT_GROUP_FIELDS = ")
        f.write(pformat(patient_group_fields, width=120, sort_dicts=False))
        f.write("\n\n")

        f.write("FIELDS_FOR_EXPORT = ")
        f.write(pformat(fields_for_export, width=120))
        f.write("\n")


def main():
    print(f"Reading template: {TEMPLATE_PATH}")
    print(f"Reading models:   {MODELS_PATH}")
    patient_group_fields = build_patient_group_fields()
    fields_for_export = build_fields_for_export()
    write_json_module(patient_group_fields, fields_for_export)
    print(f"Generated:        {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
