import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import WriteError
from testcontainers.community.mongodb import MongoDbContainer

from main import Settings, create_app
from products.domain import DomainValidationError, ImmutableTemplateError


@pytest.fixture(scope="module")
def mongodb_uri() -> Iterator[str]:
    configured_uri = os.getenv("TEST_MONGODB_URI")
    if configured_uri:
        yield configured_uri
        return

    try:
        mongo = MongoDbContainer("mongo:7")
        mongo.start()
    except Exception as exc:  # noqa: BLE001 - test environment may fail through Docker or MongoDB
        pytest.skip(f"MongoDB test strategy unavailable: {exc}")
        return

    try:
        yield mongo.get_connection_url()
    finally:
        mongo.stop()


@pytest.fixture
def client(mongodb_uri: str) -> Iterator[TestClient]:
    app = create_app(
        Settings(mongodb_uri=mongodb_uri, mongodb_database=f"products_test_{uuid4().hex}")
    )
    with TestClient(app) as test_client:
        yield test_client


def _persistence(client: TestClient):
    return client.app.state.persistence  # type: ignore[attr-defined]


def test_persists_typed_template_and_independent_card(client: TestClient) -> None:
    assert client.get("/health/ready").json() == {"status": "ok"}
    persistence = _persistence(client)
    template = persistence.create_template(
        "Продукты",
        [
            {"label": "Бренд", "type": "text"},
            {"label": "Количество", "type": "number"},
            {"label": "Куплено", "type": "date"},
            {"label": "Магазин", "type": "select", "options": ["А", "Б"]},
            {"label": "Теги", "type": "text[]"},
        ],
    )
    assert [field["label"] for field in template["fields"]] == [
        "Бренд",
        "Количество",
        "Куплено",
        "Магазин",
        "Теги",
    ]

    card = persistence.create_card(
        template["id"],
        "Молоко",
        values={
            "Бренд": "Домик",
            "Количество": 2.5,
            "Куплено": "2025-01-02",
            "Магазин": "А",
            "Теги": ["холодильник"],
        },
    )
    assert card["template_id"] == template["id"]
    assert [field["key"] for field in card["fields"]] == [
        field["key"] for field in template["fields"]
    ]
    assert {field["label"]: field["value"] for field in card["fields"]} == {
        "Бренд": "Домик",
        "Количество": 2.5,
        "Куплено": "2025-01-02",
        "Магазин": "А",
        "Теги": ["холодильник"],
    }

    persistence.update_template(template["id"], name="Еда")
    persisted_card = persistence.get_card(card["id"])
    assert persisted_card is not None
    assert persisted_card["template_id"] == template["id"]
    assert persisted_card["fields"][0]["value"] == "Домик"


def test_blank_seed_is_idempotent_and_immutable(client: TestClient) -> None:
    persistence = _persistence(client)
    templates = persistence.list_templates()
    blank = next(template for template in templates if template["name"] == "Пустая карточка")
    assert blank["fields"] == []
    assert persistence.seed_blank_template()["id"] == blank["id"]
    assert (
        len([item for item in persistence.list_templates() if item["name"] == "Пустая карточка"])
        == 1
    )

    with pytest.raises(ImmutableTemplateError):
        persistence.update_template(blank["id"], name="Изменён")
    with pytest.raises(ImmutableTemplateError):
        persistence.delete_template(blank["id"])

    card = persistence.create_card(blank["id"], "Без полей")
    assert card["fields"] == []


def test_rejects_invalid_values_and_duplicate_names(client: TestClient) -> None:
    persistence = _persistence(client)
    persistence.create_template("Т", [])
    with pytest.raises(DomainValidationError):
        persistence.create_template("Т", [])
    blank = next(
        template
        for template in persistence.list_templates()
        if template["name"] == "Пустая карточка"
    )
    with pytest.raises(DomainValidationError):
        persistence.create_card(blank["id"], "x", values={"missing": "x"})


def test_template_deletion_keeps_card_and_template_id(client: TestClient) -> None:
    persistence = _persistence(client)
    template = persistence.create_template("Временный", [])
    card = persistence.create_card(template["id"], "Сохранись")

    assert persistence.delete_template(template["id"])
    assert persistence.get_template(template["id"]) is None
    assert persistence.get_card(card["id"])["template_id"] == template["id"]


def test_configures_indexes_and_structural_validation(client: TestClient) -> None:
    database = client.app.state.mongo_database  # type: ignore[attr-defined]
    assert "templates_name_unique" in database.templates.index_information()
    assert "cards_updated_at_desc" in database.cards.index_information()
    assert "cards_template_updated_at" in database.cards.index_information()

    with pytest.raises(WriteError):
        database.cards.insert_one({"title": "malformed"})
