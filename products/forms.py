"""Request parsing helpers for the HTML adapters.

The web routes receive flat, repeated form fields; these helpers turn them back
into the structured field lists and typed filter arguments the service layer
expects. Keeping them here keeps the routing module focused on HTTP concerns.
"""

from __future__ import annotations

from typing import Any

from .services import InvalidRequestError

FIELD_FIELD_NAMES = ("field_key", "field_label", "field_type", "field_options")
FIELD_VALUE_NAMES = ("field_value", "field_present")


def form_string(form: Any, name: str, default: str = "") -> str:
    """Return a form entry as a string, falling back to ``default``."""
    value = form.get(name, default)
    return value if isinstance(value, str) else default


def parse_number(value: str) -> int | float | str:
    """Parse a numeric string, leaving anything else untouched."""
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def parse_form_value(field_type: str, value: str) -> Any:
    """Convert a raw form string into the value shape of ``field_type``."""
    if field_type == "number":
        return parse_number(value)
    if field_type == "boolean":
        if value.casefold() == "true":
            return True
        if value.casefold() == "false":
            return False
    if field_type == "text[]":
        return [part.strip() for part in value.split(",")]
    return value


def parse_fields(form: Any, *, include_values: bool) -> list[dict[str, Any]]:
    """Rebuild a list of fields from the repeated editor form fields.

    ``include_values`` selects the card editor shape (with values) over the
    template editor shape (metadata only).
    """
    names = list(FIELD_FIELD_NAMES)
    if include_values:
        names += list(FIELD_VALUE_NAMES)
    values = {name: form.getlist(name) for name in names}
    row_count = len(values["field_label"])
    if any(len(items) != row_count for items in values.values()):
        raise InvalidRequestError("field form rows are inconsistent")

    fields: list[dict[str, Any]] = []
    for index in range(row_count):
        field: dict[str, Any] = {
            "label": values["field_label"][index],
            "type": values["field_type"][index],
        }
        key = values["field_key"][index].strip()
        if key:
            field["key"] = key
        if field["type"] == "select":
            field["options"] = [
                option.strip()
                for option in values["field_options"][index].split(",")
                if option.strip()
            ]
        elif values["field_options"][index].strip():
            raise InvalidRequestError("only select fields may have options")

        if include_values:
            present = values["field_present"][index]
            if present not in {"0", "1"}:
                raise InvalidRequestError("field value presence must be 0 or 1")
            if present == "1":
                field["value"] = parse_form_value(field["type"], values["field_value"][index])
        fields.append(field)
    return fields


def filter_arguments(
    template: dict[str, Any], params: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Translate query parameters into typed service filters and display values.

    Returns the keyword arguments for :meth:`ApplicationService.search_cards`
    together with the raw values used to re-render the filter form.
    """
    typed: dict[str, dict[str, Any]] = {}
    values: dict[str, Any] = {}
    for field in template["fields"]:
        key = field["key"]
        name = f"filter_{key}"
        if field["type"] in {"number", "date"}:
            minimum = params.get(f"{name}_min", "").strip()
            maximum = params.get(f"{name}_max", "").strip()
            if not minimum and not maximum:
                continue
            spec = {"min": minimum} if minimum else {}
            if maximum:
                spec["max"] = maximum
            if field["type"] == "number":
                spec = {bound: parse_number(value) for bound, value in spec.items()}
            typed.setdefault(f"{field['type']}_filters", {})[key] = spec
            values[key] = {"min": minimum, "max": maximum}
        else:
            value = params.get(name, "")
            if not value:
                continue
            if field["type"] == "boolean":
                folded = value.casefold()
                parsed: Any = folded == "true" if folded in {"true", "false"} else value
            else:
                parsed = value
            typed.setdefault(
                {
                    "text": "text_filters",
                    "boolean": "boolean_filters",
                    "select": "select_filters",
                    "text[]": "text_list_filters",
                }[field["type"]],
                {},
            )[key] = parsed
            values[key] = value
    return typed, values
