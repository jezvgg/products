import pytest

from products.services import (
    ApplicationService,
    ConfirmationRequiredError,
    InvalidRequestError,
    ServiceError,
)


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


def test_confirmation_is_required_for_risky_conversion() -> None:
    service = ApplicationService.__new__(ApplicationService)
    fields = [{"key": "weight", "label": "Weight", "type": "text", "value": "unknown"}]

    with pytest.raises(ConfirmationRequiredError):
        service._convert_fields(fields, {"weight": {"type": "number"}})

    service._convert_fields(fields, {"weight": {"type": "number", "confirm": True}})
    assert fields == [{"key": "weight", "label": "Weight", "type": "number"}]
