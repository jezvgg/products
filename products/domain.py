from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import isfinite
from typing import Any
from uuid import uuid4

FIELD_TYPES = frozenset({"text", "number", "boolean", "date", "select", "text[]"})
BLANK_TEMPLATE_NAME = "Пустая карточка"


class DomainValidationError(ValueError):
    pass


class ImmutableTemplateError(DomainValidationError):
    pass


def validate_title(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError("title must be a non-empty string")
    return value


def validate_template_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError("name must be a non-empty string")
    return value.strip()


def _validate_options(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DomainValidationError("select options must be a non-empty list")
    if any(not isinstance(option, str) or not option.strip() for option in value):
        raise DomainValidationError("select options must contain non-empty strings")
    if len(set(value)) != len(value):
        raise DomainValidationError("select options must be unique")
    return value


def validate_value(field_type: str, value: Any, options: list[str] | None = None) -> Any:
    if field_type == "text":
        if not isinstance(value, str):
            raise DomainValidationError("text field value must be a string")
    elif field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise DomainValidationError("number field value must be finite")
    elif field_type == "boolean":
        if not isinstance(value, bool):
            raise DomainValidationError("boolean field value must be a boolean")
    elif field_type == "date":
        if not isinstance(value, str):
            raise DomainValidationError("date field value must be YYYY-MM-DD")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise DomainValidationError("date field value must be YYYY-MM-DD") from exc
        if len(value) != 10:
            raise DomainValidationError("date field value must be YYYY-MM-DD")
    elif field_type == "select":
        if not isinstance(value, str) or options is None or value not in options:
            raise DomainValidationError("select field value must match an option")
    elif field_type == "text[]":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise DomainValidationError("text[] field value must be a list of strings")
    else:
        raise DomainValidationError(f"unsupported field type: {field_type}")
    return value


def validate_field(raw: Any, *, template: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DomainValidationError("field must be an object")

    allowed = {"key", "label", "type", "options"}
    if not template:
        allowed.add("value")
    unknown = set(raw) - allowed
    if unknown:
        raise DomainValidationError(f"unknown field properties: {', '.join(sorted(unknown))}")

    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise DomainValidationError("field label must be a non-empty string")
    field_type = raw.get("type")
    if field_type not in FIELD_TYPES:
        raise DomainValidationError(f"unsupported field type: {field_type}")

    key = raw.get("key", uuid4().hex)
    if not isinstance(key, str) or not key.strip():
        raise DomainValidationError("field key must be a non-empty string")

    options = raw.get("options")
    if field_type == "select":
        options = _validate_options(options)
    elif options is not None:
        raise DomainValidationError("only select fields may have options")

    field = {"key": key, "label": label, "type": field_type}
    if options is not None:
        field["options"] = options
    if not template and "value" in raw:
        field["value"] = validate_value(field_type, raw["value"], options)
    return field


def validate_fields(raw_fields: Any, *, template: bool = False) -> list[dict[str, Any]]:
    if not isinstance(raw_fields, list):
        raise DomainValidationError("fields must be a list")
    fields = [validate_field(raw, template=template) for raw in raw_fields]
    labels = [field["label"].casefold() for field in fields]
    if len(labels) != len(set(labels)):
        raise DomainValidationError("field labels must be unique")
    keys = [field["key"] for field in fields]
    if len(keys) != len(set(keys)):
        raise DomainValidationError("field keys must be unique")
    return fields


def copy_template_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return deepcopy(validate_fields(fields, template=True))


def apply_field_values(fields: list[dict[str, Any]], values: Any) -> list[dict[str, Any]]:
    if values is None:
        return fields
    if not isinstance(values, dict):
        raise DomainValidationError("values must be an object")
    by_key = {field["key"]: field for field in fields}
    by_label = {field["label"]: field for field in fields}
    for name, value in values.items():
        field = by_key.get(name) or by_label.get(name)
        if field is None:
            raise DomainValidationError(f"unknown field: {name}")
        field["value"] = validate_value(field["type"], value, field.get("options"))
    return validate_fields(fields)
