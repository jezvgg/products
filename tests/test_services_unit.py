from copy import deepcopy
from typing import Any, cast

import pytest

from products.domain import DomainValidationError, validate_value
from products.services import (
    ApplicationService,
    ConfirmationRequiredError,
    InvalidRequestError,
    ServiceError,
    _date_value,
)


class CardPersistence:
    def __init__(self) -> None:
        self.card = {
            "id": "507f1f77bcf86cd799439011",
            "title": "Item",
            "fields": [
                {"key": "weight", "label": "Weight", "type": "text", "value": "2.5"},
                {"key": "remove", "label": "Remove", "type": "text", "value": "old"},
            ],
        }

    def get_card(self, card_id: str) -> dict:
        return deepcopy(self.card)

    def update_card(self, card_id: str, *, title: str, fields: list[dict]) -> dict:
        self.card = {**self.card, "title": title, "fields": deepcopy(fields)}
        return deepcopy(self.card)


def test_service_errors_have_stable_wire_shape() -> None:
    error = InvalidRequestError("bad request")

    assert isinstance(error, ServiceError)
    assert error.code == "invalid_request"
    assert error.as_dict() == {"code": "invalid_request", "message": "bad request"}


def test_safe_field_conversions_preserve_supported_values() -> None:
    assert ApplicationService._safe_conversion("text", "number", "2.5", None) == 2.5
    assert ApplicationService._safe_conversion("text", "boolean", "TRUE", None) is True
    assert ApplicationService._safe_conversion("number", "text", 2.5, None) == "2.5"

    with pytest.raises(ValueError):
        ApplicationService._safe_conversion("text[]", "text", ["a", "b"], None)


def test_full_field_replacement_preserves_identity_and_converts_existing_values() -> None:
    persistence = CardPersistence()
    service = ApplicationService(cast(Any, persistence))

    updated = service.update_card(
        persistence.card["id"],
        fields=[
            {"key": "weight", "label": "Mass", "type": "number"},
            {"label": "Added", "type": "text", "value": "new"},
        ],
    )

    assert updated["fields"] == [
        {"key": "weight", "label": "Mass", "type": "number", "value": 2.5},
        {"key": updated["fields"][1]["key"], "label": "Added", "type": "text", "value": "new"},
    ]


def test_full_field_replacement_requires_confirmation_for_risky_existing_value() -> None:
    persistence = CardPersistence()
    service = ApplicationService(cast(Any, persistence))

    with pytest.raises(ConfirmationRequiredError):
        service.update_card(
            persistence.card["id"],
            fields=[{"key": "weight", "label": "Weight", "type": "boolean"}],
        )


def test_confirmation_is_required_for_risky_conversion() -> None:
    service = ApplicationService.__new__(ApplicationService)
    fields = [{"key": "weight", "label": "Weight", "type": "text", "value": "unknown"}]

    with pytest.raises(ConfirmationRequiredError):
        service._convert_fields(fields, {"weight": {"type": "number"}})
    with pytest.raises(ConfirmationRequiredError):
        service._convert_fields(fields, {"weight": {"type": "number", "confirm": "true"}})
    with pytest.raises(ConfirmationRequiredError):
        service._convert_fields(fields, {"weight": {"type": "number", "confirm": 1}})

    service._convert_fields(fields, {"weight": {"type": "number", "confirm": True}})
    assert fields == [{"key": "weight", "label": "Weight", "type": "number"}]


def test_dates_require_exact_shape_for_values_and_filters() -> None:
    for value in ("2025-1-01", "20250101"):
        with pytest.raises(DomainValidationError):
            validate_value("date", value)
        with pytest.raises(InvalidRequestError):
            _date_value(value)
