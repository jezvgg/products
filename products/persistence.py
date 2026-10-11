from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, DuplicateKeyError

from .domain import (
    BLANK_TEMPLATE_NAME,
    DomainValidationError,
    ImmutableTemplateError,
    apply_field_values,
    copy_template_fields,
    validate_fields,
    validate_template_name,
    validate_title,
)

TEMPLATE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "fields", "created_at", "updated_at"],
        "properties": {
            "_id": {"bsonType": "objectId"},
            "name": {"bsonType": "string", "minLength": 1},
            "fields": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["key", "label", "type"],
                    "properties": {
                        "key": {"bsonType": "string", "minLength": 1},
                        "label": {"bsonType": "string", "minLength": 1},
                        "type": {"enum": ["text", "number", "boolean", "date", "select", "text[]"]},
                        "options": {"bsonType": "array", "items": {"bsonType": "string"}},
                    },
                    "additionalProperties": False,
                },
            },
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
        "additionalProperties": False,
    }
}

CARD_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "template_id", "title", "fields", "created_at", "updated_at"],
        "properties": {
            "_id": {"bsonType": "objectId"},
            "template_id": {"bsonType": "objectId"},
            "title": {"bsonType": "string", "minLength": 1},
            "fields": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["key", "label", "type"],
                    "properties": {
                        "key": {"bsonType": "string", "minLength": 1},
                        "label": {"bsonType": "string", "minLength": 1},
                        "type": {"enum": ["text", "number", "boolean", "date", "select", "text[]"]},
                        "options": {"bsonType": "array", "items": {"bsonType": "string"}},
                        "value": {},
                    },
                    "additionalProperties": False,
                },
            },
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
        "additionalProperties": False,
    }
}


def _now() -> datetime:
    return datetime.now(UTC)


def _object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise DomainValidationError("invalid id")


def _public(document: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(document)
    result["id"] = str(result.pop("_id"))
    if "template_id" in result:
        result["template_id"] = str(result["template_id"])
    return result


def _required(document: Any) -> dict[str, Any]:
    if document is None:
        raise RuntimeError("document disappeared during update")
    return document


class MongoPersistence:
    def __init__(self, database: Database[Any]) -> None:
        self.database = database
        self.templates: Collection[Any] = database["templates"]
        self.cards: Collection[Any] = database["cards"]

    def initialize(self) -> None:
        self._configure_collection("templates", TEMPLATE_VALIDATOR)
        self._configure_collection("cards", CARD_VALIDATOR)
        self.templates.create_index([("name", ASCENDING)], unique=True, name="templates_name_unique")
        self.cards.create_index([("updated_at", DESCENDING)], name="cards_updated_at_desc")
        self.cards.create_index(
            [("template_id", ASCENDING), ("updated_at", DESCENDING)],
            name="cards_template_updated_at",
        )
        self.seed_blank_template()

    def _configure_collection(self, name: str, validator: dict[str, Any]) -> None:
        try:
            self.database.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
        except CollectionInvalid:
            self.database.command(
                "collMod",
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )

    def seed_blank_template(self) -> dict[str, Any]:
        now = _now()
        document = {
            "_id": ObjectId(),
            "name": BLANK_TEMPLATE_NAME,
            "fields": [],
            "created_at": now,
            "updated_at": now,
        }
        with suppress(DuplicateKeyError):
            self.templates.update_one(
                {"name": BLANK_TEMPLATE_NAME},
                {"$setOnInsert": document},
                upsert=True,
            )
        result = self.templates.find_one({"name": BLANK_TEMPLATE_NAME})
        if result is None:
            raise RuntimeError("blank template was not seeded")
        return _public(result)

    def create_template(self, name: Any, fields: Any = None) -> dict[str, Any]:
        name = validate_template_name(name)
        normalized_fields = validate_fields([] if fields is None else fields, template=True)
        now = _now()
        document = {
            "_id": ObjectId(),
            "name": name,
            "fields": normalized_fields,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.templates.insert_one(document)
        except DuplicateKeyError as exc:
            raise DomainValidationError("template name must be unique") from exc
        return _public(document)

    def get_template(self, template_id: Any) -> dict[str, Any] | None:
        document = self.templates.find_one({"_id": _object_id(template_id)})
        return None if document is None else _public(document)

    def list_templates(self) -> list[dict[str, Any]]:
        return [_public(document) for document in self.templates.find().sort("name", ASCENDING)]

    def update_template(
        self,
        template_id: Any,
        *,
        name: Any = None,
        fields: Any = None,
    ) -> dict[str, Any]:
        object_id = _object_id(template_id)
        current = self.templates.find_one({"_id": object_id})
        if current is None:
            raise KeyError("template not found")
        if current["name"] == BLANK_TEMPLATE_NAME:
            raise ImmutableTemplateError("blank template is immutable")
        changes: dict[str, Any] = {"updated_at": _now()}
        if name is not None:
            changes["name"] = validate_template_name(name)
        if fields is not None:
            changes["fields"] = validate_fields(fields, template=True)
        try:
            self.templates.update_one({"_id": object_id}, {"$set": changes})
        except DuplicateKeyError as exc:
            raise DomainValidationError("template name must be unique") from exc
        return _public(_required(self.templates.find_one({"_id": object_id})))

    def delete_template(self, template_id: Any) -> bool:
        object_id = _object_id(template_id)
        current = self.templates.find_one({"_id": object_id})
        if current is None:
            return False
        if current["name"] == BLANK_TEMPLATE_NAME:
            raise ImmutableTemplateError("blank template is immutable")
        return self.templates.delete_one({"_id": object_id}).deleted_count == 1

    def create_card(
        self,
        template_id: Any,
        title: Any,
        *,
        values: Any = None,
    ) -> dict[str, Any]:
        template_object_id = _object_id(template_id)
        template = self.templates.find_one({"_id": template_object_id})
        if template is None:
            raise DomainValidationError("template not found")
        fields = copy_template_fields(template["fields"])
        fields = apply_field_values(fields, values)
        now = _now()
        document = {
            "_id": ObjectId(),
            "template_id": template_object_id,
            "title": validate_title(title),
            "fields": fields,
            "created_at": now,
            "updated_at": now,
        }
        self.cards.insert_one(document)
        return _public(document)

    def get_card(self, card_id: Any) -> dict[str, Any] | None:
        document = self.cards.find_one({"_id": _object_id(card_id)})
        return None if document is None else _public(document)

    def list_cards(self) -> list[dict[str, Any]]:
        return [_public(document) for document in self.cards.find().sort("updated_at", DESCENDING)]

    def update_card(
        self,
        card_id: Any,
        *,
        title: Any = None,
        values: Any = None,
    ) -> dict[str, Any]:
        object_id = _object_id(card_id)
        current = self.cards.find_one({"_id": object_id})
        if current is None:
            raise KeyError("card not found")
        changes: dict[str, Any] = {"updated_at": _now()}
        if title is not None:
            changes["title"] = validate_title(title)
        if values is not None:
            changes["fields"] = apply_field_values(validate_fields(current["fields"]), values)
        self.cards.update_one({"_id": object_id}, {"$set": changes})
        return _public(_required(self.cards.find_one({"_id": object_id})))

    def delete_card(self, card_id: Any) -> bool:
        return self.cards.delete_one({"_id": _object_id(card_id)}).deleted_count == 1
