from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from math import isfinite
from typing import Any, cast

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from .domain import (
    FIELD_TYPES,
    DomainValidationError,
    ImmutableTemplateError,
    apply_field_values,
    validate_field,
    validate_fields,
    validate_template_name,
    validate_title,
    validate_value,
)
from .persistence import MongoPersistence

_UNSET = object()


class ServiceError(Exception):
    """Stable error contract for UI and MCP adapters."""

    code = "service_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class InvalidRequestError(ServiceError):
    code = "invalid_request"


class ResourceNotFoundError(ServiceError):
    code = "not_found"


class ConflictError(ServiceError):
    code = "conflict"


class ConfirmationRequiredError(ServiceError):
    code = "confirmation_required"


class StorageError(ServiceError):
    code = "storage_error"


NotFoundError = ResourceNotFoundError
ValidationError = InvalidRequestError


def _public(document: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(document)
    result["id"] = str(result.pop("_id"))
    if "template_id" in result:
        result["template_id"] = str(result["template_id"])
    return result


def _call(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except ServiceError:
        raise
    except ImmutableTemplateError as exc:
        raise ConflictError(str(exc)) from exc
    except KeyError as exc:
        raise ResourceNotFoundError(str(exc).strip("'")) from exc
    except DomainValidationError as exc:
        message = str(exc)
        if message == "template not found":
            raise ResourceNotFoundError(message) from exc
        raise InvalidRequestError(message) from exc
    except PyMongoError as exc:
        raise StorageError("storage operation failed") from exc


def _object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise InvalidRequestError("invalid id")


def _field_position(fields: list[dict[str, Any]], reference: Any) -> int:
    if not isinstance(reference, str) or not reference.strip():
        raise InvalidRequestError("field reference must be a non-empty string")
    exact = [i for i, field in enumerate(fields) if field["key"] == reference or field["label"] == reference]
    if not exact:
        folded = reference.casefold()
        exact = [i for i, field in enumerate(fields) if field["label"].casefold() == folded]
    if not exact:
        raise ResourceNotFoundError("field not found")
    return exact[0]


def _assert_unique_labels(fields: list[dict[str, Any]]) -> None:
    labels = [field["label"].casefold() for field in fields]
    if len(labels) != len(set(labels)):
        raise InvalidRequestError("field labels must be unique")


def _date_value(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidRequestError("date filter must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidRequestError("date filter must be YYYY-MM-DD") from exc
    if len(value) != 10:
        raise InvalidRequestError("date filter must be YYYY-MM-DD")
    return value


def _number_value(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise InvalidRequestError("number filter must be finite")
    return value


def _range(spec: Any, validator: Callable[[Any], Any], label: str) -> dict[str, Any]:
    if isinstance(spec, dict):
        lower = spec.get("min", spec.get("from", _UNSET))
        upper = spec.get("max", spec.get("to", _UNSET))
    elif isinstance(spec, (list, tuple)) and len(spec) == 2:
        lower, upper = spec
    else:
        raise InvalidRequestError(f"{label} filter must provide min and max")
    result: dict[str, Any] = {}
    if lower is not _UNSET and lower is not None:
        result["$gte"] = validator(lower)
    if upper is not _UNSET and upper is not None:
        result["$lte"] = validator(upper)
    if not result:
        raise InvalidRequestError(f"{label} filter must provide min or max")
    return result


def _cursor(value: str) -> tuple[datetime, ObjectId]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(value.encode()).decode())
        timestamp = datetime.fromisoformat(raw["updated_at"])
        identifier = _object_id(raw["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidRequestError("invalid cursor") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp, identifier


def _encode_cursor(document: dict[str, Any]) -> str:
    timestamp = document["updated_at"]
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    value = {
        "updated_at": timestamp.astimezone(UTC).isoformat(),
        "id": str(document["_id"]),
    }
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


class ApplicationService:
    def __init__(self, persistence: MongoPersistence) -> None:
        self.persistence = persistence

    def create_template(self, name: Any, fields: Any = None) -> dict[str, Any]:
        return _call(lambda: self.persistence.create_template(name, fields))

    def get_template(self, template_id: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            result = self.persistence.get_template(template_id)
            if result is None:
                raise ResourceNotFoundError("template not found")
            return result

        return _call(operation)

    def list_templates(self) -> list[dict[str, Any]]:
        return _call(self.persistence.list_templates)

    def update_template(
        self,
        template_id: Any,
        *,
        name: Any = _UNSET,
        fields: Any = _UNSET,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if name is not _UNSET:
            validate_template_name(name)
            kwargs["name"] = name
        if fields is not _UNSET:
            validate_fields(fields, template=True)
            kwargs["fields"] = fields
        return _call(lambda: self.persistence.update_template(template_id, **kwargs))

    def delete_template(self, template_id: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            if not self.persistence.delete_template(template_id):
                raise ResourceNotFoundError("template not found")
            return {"id": str(template_id), "deleted": True}

        return _call(operation)

    def create_card(
        self,
        template_id: Any,
        title: Any,
        *,
        values: Any = None,
        fields: Any = _UNSET,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"values": values}
        if fields is not _UNSET:
            if values is not None:
                raise InvalidRequestError("fields and values cannot be provided together")
            kwargs["fields"] = fields
        return _call(lambda: self.persistence.create_card(template_id, title, **kwargs))

    def get_card(self, card_id: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            result = self.persistence.get_card(card_id)
            if result is None:
                raise ResourceNotFoundError("card not found")
            return result

        return _call(operation)

    def delete_card(self, card_id: Any) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            if not self.persistence.delete_card(card_id):
                raise ResourceNotFoundError("card not found")
            return {"id": str(card_id), "deleted": True}

        return _call(operation)

    def update_card(
        self,
        card_id: Any,
        *,
        title: Any = _UNSET,
        values: Any = _UNSET,
        fields: Any = _UNSET,
        add_fields: Any = _UNSET,
        remove_fields: Any = _UNSET,
        rename_fields: Any = _UNSET,
        convert_fields: Any = _UNSET,
        clear_fields: Any = _UNSET,
    ) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            current = self.get_card(card_id)
            current_fields = deepcopy(current["fields"])

            if fields is not _UNSET and any(
                value is not _UNSET
                for value in (add_fields, remove_fields, rename_fields, convert_fields, clear_fields)
            ):
                raise InvalidRequestError("fields cannot be combined with field operations")
            if fields is not _UNSET:
                current_fields = validate_fields(fields)
            else:
                if title is not _UNSET:
                    validate_title(title)
                if values is not _UNSET and values is None:
                    raise InvalidRequestError("values must be an object")
                if add_fields is not _UNSET:
                    additions = add_fields if isinstance(add_fields, list) else [add_fields]
                    for raw in additions:
                        current_fields.append(validate_field(raw))
                    _assert_unique_labels(current_fields)
                if remove_fields is not _UNSET:
                    references = remove_fields if isinstance(remove_fields, list) else [remove_fields]
                    for reference in references:
                        current_fields.pop(_field_position(current_fields, reference))
                if rename_fields is not _UNSET:
                    if not isinstance(rename_fields, dict):
                        raise InvalidRequestError("rename_fields must be an object")
                    for reference, label in rename_fields.items():
                        if not isinstance(label, str) or not label.strip():
                            raise InvalidRequestError("field label must be a non-empty string")
                        current_fields[_field_position(current_fields, reference)]["label"] = label
                    _assert_unique_labels(current_fields)
                if values is not _UNSET:
                    current_fields = apply_field_values(current_fields, values)
                if convert_fields is not _UNSET:
                    self._convert_fields(current_fields, convert_fields)
                if clear_fields is not _UNSET:
                    references = clear_fields if isinstance(clear_fields, list) else [clear_fields]
                    for reference in references:
                        current_fields[_field_position(current_fields, reference)].pop("value", None)
                current_fields = validate_fields(current_fields)

            new_title = current["title"] if title is _UNSET else title
            if title is not _UNSET:
                validate_title(new_title)
            return self.persistence.update_card(card_id, title=new_title, fields=current_fields)

        return _call(operation)

    def add_card_field(
        self,
        card_id: Any,
        label: str,
        field_type: str,
        *,
        options: list[str] | None = None,
        value: Any = _UNSET,
    ) -> dict[str, Any]:
        field: dict[str, Any] = {"label": label, "type": field_type}
        if options is not None:
            field["options"] = options
        if value is not _UNSET:
            field["value"] = value
        return self.update_card(card_id, add_fields=field)

    def remove_card_field(self, card_id: Any, field: str) -> dict[str, Any]:
        return self.update_card(card_id, remove_fields=field)

    def rename_card_field(self, card_id: Any, field: str, label: str) -> dict[str, Any]:
        return self.update_card(card_id, rename_fields={field: label})

    def clear_card_field(self, card_id: Any, field: str) -> dict[str, Any]:
        return self.update_card(card_id, clear_fields=field)

    def change_card_field_type(
        self,
        card_id: Any,
        field: str,
        field_type: str,
        *,
        options: list[str] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        return self.update_card(
            card_id,
            convert_fields={field: {"type": field_type, "options": options, "confirm": confirm}},
        )

    def _convert_fields(self, fields: list[dict[str, Any]], changes: Any) -> None:
        if not isinstance(changes, dict):
            raise InvalidRequestError("convert_fields must be an object")
        for reference, raw_change in changes.items():
            spec = {"type": raw_change} if isinstance(raw_change, str) else raw_change
            if not isinstance(spec, dict) or spec.get("type") not in FIELD_TYPES:
                raise InvalidRequestError("unsupported field type")
            position = _field_position(fields, reference)
            field = fields[position]
            old_type = field["type"]
            new_type = spec["type"]
            options = spec.get("options", field.get("options") if new_type == "select" else None)
            if new_type == "select":
                validate_field({"key": field["key"], "label": field["label"], "type": new_type, "options": options})
            elif options is not None:
                raise InvalidRequestError("only select fields may have options")

            if "value" in field:
                try:
                    converted = self._safe_conversion(
                        old_type,
                        new_type,
                        field["value"],
                        cast(list[str] | None, options),
                    )
                except (DomainValidationError, ValueError) as exc:
                    if not spec.get("confirm", False):
                        raise ConfirmationRequiredError("field conversion requires confirmation") from exc
                    field.pop("value", None)
                else:
                    field["value"] = converted
            field["type"] = new_type
            if new_type == "select":
                field["options"] = options
            else:
                field.pop("options", None)

    @staticmethod
    def _safe_conversion(old_type: str, new_type: str, value: Any, options: list[str] | None) -> Any:
        if old_type == new_type:
            return validate_value(new_type, value, options)
        if new_type == "text":
            if old_type == "text[]":
                raise ValueError("list conversion may lose structure")
            return str(value).lower() if isinstance(value, bool) else str(value)
        if old_type == "select" and new_type == "text":
            return value
        if old_type == "text" and new_type == "number":
            number = float(value)
            if not isfinite(number):
                raise ValueError("number must be finite")
            return int(number) if number.is_integer() else number
        if old_type == "text" and new_type == "boolean":
            if value.casefold() == "true":
                return True
            if value.casefold() == "false":
                return False
            raise ValueError("text is not a boolean")
        if old_type == "text" and new_type == "date":
            return validate_value("date", value)
        if old_type == "text" and new_type == "select":
            return validate_value("select", value, options)
        raise ValueError("conversion may lose data")

    def search_cards(
        self,
        *,
        title: str | None = None,
        query: str | None = None,
        template_id: Any = None,
        filters: dict[str, Any] | None = None,
        text_filters: dict[str, str] | None = None,
        number_filters: dict[str, Any] | None = None,
        date_filters: dict[str, Any] | None = None,
        boolean_filters: dict[str, bool] | None = None,
        select_filters: dict[str, str] | None = None,
        text_list_filters: dict[str, str] | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
                raise InvalidRequestError("limit must be between 1 and 100")
            if title is not None and query is not None:
                raise InvalidRequestError("title and query cannot both be provided")
            search = title if title is not None else query
            if search is not None and not isinstance(search, str):
                raise InvalidRequestError("title search must be a string")
            selected_template = self.get_template(template_id) if template_id is not None else None
            mongo_query: dict[str, Any] = {}
            if template_id is not None:
                mongo_query["template_id"] = _object_id(template_id)
            if search:
                mongo_query["title"] = {"$regex": re.escape(search), "$options": "i"}

            typed_filters: list[tuple[dict[str, Any] | None, str]] = [
                (text_filters, "text"),
                (number_filters, "number"),
                (date_filters, "date"),
                (boolean_filters, "boolean"),
                (select_filters, "select"),
                (text_list_filters, "text[]"),
            ]
            for typed, field_type in typed_filters:
                if typed is None:
                    continue
                if not isinstance(typed, dict):
                    raise InvalidRequestError(f"{field_type} filters must be an object")
                for reference, spec in typed.items():
                    self._add_field_filter(mongo_query, selected_template, reference, spec, field_type)
            if filters is not None:
                if not isinstance(filters, dict):
                    raise InvalidRequestError("filters must be an object")
                for reference, spec in filters.items():
                    field_type = self._template_field_type(selected_template, reference)
                    if field_type is None:
                        raise InvalidRequestError("generic field filters require a selected template")
                    self._add_field_filter(mongo_query, selected_template, reference, spec, field_type)

            if cursor is not None:
                updated_at, identifier = _cursor(cursor)
                mongo_query.setdefault("$and", []).append(
                    {"$or": [{"updated_at": {"$lt": updated_at}}, {"updated_at": updated_at, "_id": {"$lt": identifier}}]}
                )
            documents = list(
                self.persistence.cards.find(mongo_query)
                .sort([("updated_at", DESCENDING), ("_id", DESCENDING)])
                .limit(limit + 1)
            )
            has_more = len(documents) > limit
            documents = documents[:limit]
            result = {"items": [_public(document) for document in documents], "next_cursor": None}
            if has_more and documents:
                result["next_cursor"] = _encode_cursor(documents[-1])
            return result

        return _call(operation)

    @staticmethod
    def _template_field_type(template: dict[str, Any] | None, reference: Any) -> str | None:
        if template is None:
            return None
        if not isinstance(reference, str):
            raise InvalidRequestError("field reference must be a string")
        folded = reference.casefold()
        for field in template["fields"]:
            if field["key"] == reference or field["label"].casefold() == folded:
                return field["type"]
        raise ResourceNotFoundError("field not found")

    def _add_field_filter(
        self,
        query: dict[str, Any],
        template: dict[str, Any] | None,
        reference: Any,
        spec: Any,
        field_type: str,
    ) -> None:
        if template is None:
            raise InvalidRequestError("field filters require a selected template")
        actual_type = self._template_field_type(template, reference)
        if actual_type != field_type:
            raise InvalidRequestError(f"field is not of type {field_type}")
        field = next(
            field for field in template["fields"] if field["key"] == reference or field["label"].casefold() == str(reference).casefold()
        )
        condition: Any
        if field_type == "text":
            if not isinstance(spec, str):
                raise InvalidRequestError("text filter must be a string")
            condition = {"$regex": re.escape(spec), "$options": "i"}
        elif field_type == "number":
            condition = _range(spec, _number_value, "number")
        elif field_type == "date":
            condition = _range(spec, _date_value, "date")
        elif field_type == "boolean":
            if not isinstance(spec, bool):
                raise InvalidRequestError("boolean filter must be a boolean")
            condition = spec
        elif field_type == "select":
            if not isinstance(spec, str) or spec not in field.get("options", []):
                raise InvalidRequestError("select filter must match an option")
            condition = spec
        else:
            if not isinstance(spec, str):
                raise InvalidRequestError("text-list filter must be a string")
            condition = spec
        query.setdefault("$and", []).append({"fields": {"$elemMatch": {"key": field["key"], "value": condition}}})


# Short name for adapters that prefer an explicit application boundary.
ApplicationServices = ApplicationService
