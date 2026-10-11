import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from main import Settings, create_app


@pytest.fixture
def web_client(mongodb_uri: str) -> Iterator[TestClient]:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_web_{uuid4().hex}",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _field_keys(html: str) -> list[str]:
    return re.findall(r'name="field_key" value="([^"]*)"', html)


def _template_form(name: str, *, include_fields: bool = True) -> list[tuple[str, str]]:
    data = [("name", name)]
    if include_fields:
        data += [
            ("field_key", ""),
            ("field_label", "Brand"),
            ("field_type", "text"),
            ("field_options", ""),
            ("field_key", ""),
            ("field_label", "Count"),
            ("field_type", "number"),
            ("field_options", ""),
            ("field_key", ""),
            ("field_label", "Kind"),
            ("field_type", "select"),
            ("field_options", "Food,Tool"),
        ]
    return data


def test_ui_template_card_crud_and_blank_protection(web_client: TestClient) -> None:
    response = web_client.post("/templates/new", data=_template_form("Inventory"), follow_redirects=False)
    assert response.status_code == 303
    template_edit = web_client.get(response.headers["location"])
    assert template_edit.status_code == 200
    keys = _field_keys(template_edit.text)
    template_id = response.headers["location"].split("/")[2]

    card_data = [
        ("title", "Hammer"),
        ("template_id", template_id),
        ("field_key", keys[0]),
        ("field_label", "Brand"),
        ("field_type", "text"),
        ("field_options", ""),
        ("field_value", ""),
        ("field_present", "1"),
        ("field_key", keys[1]),
        ("field_label", "Count"),
        ("field_type", "number"),
        ("field_options", ""),
        ("field_value", "2.5"),
        ("field_present", "1"),
        ("field_key", keys[2]),
        ("field_label", "Kind"),
        ("field_type", "select"),
        ("field_options", "Food,Tool"),
        ("field_value", "Tool"),
        ("field_present", "1"),
    ]
    created = web_client.post("/cards/new", data=card_data, follow_redirects=False)
    assert created.status_code == 303
    card_id = created.headers["location"].split("/")[2]
    card_page = web_client.get(created.headers["location"])
    assert card_page.status_code == 200
    assert "Hammer" in card_page.text
    assert "2.5" in card_page.text

    edited = web_client.post(
        f"/cards/{card_id}/edit",
        data=[
            ("title", "Big Hammer"),
            ("field_key", keys[0]),
            ("field_label", "Maker"),
            ("field_type", "text"),
            ("field_options", ""),
            ("field_value", ""),
            ("field_present", "1"),
            ("field_key", ""),
            ("field_label", "Weight"),
            ("field_type", "number"),
            ("field_options", ""),
            ("field_value", "1.2"),
            ("field_present", "1"),
            ("confirm_conversion", "on"),
        ],
        follow_redirects=False,
    )
    assert edited.status_code == 303
    assert "Big Hammer" in web_client.get(edited.headers["location"]).text

    duplicate = web_client.post("/templates/new", data=_template_form("Inventory"))
    assert duplicate.status_code == 400
    assert "unique" in duplicate.text

    deleted_template = web_client.post(f"/templates/{template_id}/delete", follow_redirects=False)
    assert deleted_template.status_code == 303
    assert web_client.get(f"/cards/{card_id}/edit").status_code == 200

    blank_id = re.search(r'<option value="([a-f0-9]{24})">Пустая карточка', web_client.get("/cards/new").text).group(1)
    assert web_client.get(f"/templates/{blank_id}/edit").status_code == 403
    assert web_client.post(f"/templates/{blank_id}/delete").status_code == 403

    deleted_card = web_client.post(f"/cards/{card_id}/delete", follow_redirects=False)
    assert deleted_card.status_code == 303
    assert web_client.get(f"/cards/{card_id}/edit").status_code == 404


def test_ui_typed_filters_and_title_search_are_and_combined(web_client: TestClient) -> None:
    created = web_client.post("/templates/new", data=_template_form("Filterable"), follow_redirects=False)
    template_id = created.headers["location"].split("/")[2]
    keys = _field_keys(web_client.get(created.headers["location"]).text)

    def create_card(title: str, brand: str, count: str, kind: str) -> None:
        web_client.post(
            "/cards/new",
            data=[
                ("title", title),
                ("template_id", template_id),
                ("field_key", keys[0]),
                ("field_label", "Brand"),
                ("field_type", "text"),
                ("field_options", ""),
                ("field_value", brand),
                ("field_present", "1"),
                ("field_key", keys[1]),
                ("field_label", "Count"),
                ("field_type", "number"),
                ("field_options", ""),
                ("field_value", count),
                ("field_present", "1"),
                ("field_key", keys[2]),
                ("field_label", "Kind"),
                ("field_type", "select"),
                ("field_options", "Food,Tool"),
                ("field_value", kind),
                ("field_present", "1"),
            ],
        )

    create_card("Red toolbox", "Acme tools", "5", "Tool")
    create_card("Red food box", "Acme kitchen", "2", "Food")
    create_card("Blue toolbox", "Other", "8", "Tool")

    response = web_client.get(
        "/cards",
        params={
            "template_id": template_id,
            f"filter_{keys[0]}": "Acme",
            f"filter_{keys[1]}_min": "3",
            f"filter_{keys[1]}_max": "6",
            f"filter_{keys[2]}": "Tool",
            "q": "red",
        },
    )
    assert response.status_code == 200
    assert "Red toolbox" in response.text
    assert "Red food box" not in response.text
    assert "Blue toolbox" not in response.text

    invalid = web_client.get(
        "/cards",
        params={"template_id": template_id, f"filter_{keys[1]}_min": "not-a-number"},
    )
    assert invalid.status_code == 400
    assert "number filter" in invalid.text


def test_ui_rejects_invalid_typed_values_and_paginates(web_client: TestClient) -> None:
    created = web_client.post("/templates/new", data=_template_form("Simple", include_fields=False), follow_redirects=False)
    template_id = created.headers["location"].split("/")[2]
    for index in range(51):
        response = web_client.post(
            "/cards/new",
            data=[("title", f"Card {index}"), ("template_id", template_id)],
        )
        assert response.status_code == 303

    first_page = web_client.get("/cards", params={"template_id": template_id})
    assert first_page.status_code == 200
    assert 'href="http://testserver/cards?' in first_page.text
    assert "More" in first_page.text

    bad_template = web_client.post(
        "/templates/new",
        data=_template_form("Bad"),
        follow_redirects=False,
    )
    bad_id = bad_template.headers["location"].split("/")[2]
    keys = _field_keys(web_client.get(bad_template.headers["location"]).text)
    invalid_card = web_client.post(
        "/cards/new",
        data=[
            ("title", "Invalid"),
            ("template_id", bad_id),
            ("field_key", keys[0]),
            ("field_label", "Brand"),
            ("field_type", "text"),
            ("field_options", ""),
            ("field_value", "ok"),
            ("field_present", "1"),
            ("field_key", keys[1]),
            ("field_label", "Count"),
            ("field_type", "number"),
            ("field_options", ""),
            ("field_value", "bad"),
            ("field_present", "1"),
            ("field_key", keys[2]),
            ("field_label", "Kind"),
            ("field_type", "select"),
            ("field_options", "Food,Tool"),
            ("field_value", "Food"),
            ("field_present", "1"),
        ],
    )
    assert invalid_card.status_code == 400
    assert "finite" in invalid_card.text
