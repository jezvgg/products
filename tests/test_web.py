import re
from collections.abc import Iterator
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from main import Settings, _filter_arguments, create_app


class FormTestClient(TestClient):
    def post(self, url: str, *args, **kwargs):
        data = kwargs.get("data")
        if isinstance(data, list):
            kwargs.pop("data")
            kwargs["content"] = urlencode(data)
            kwargs.setdefault("follow_redirects", False)
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            kwargs["headers"] = headers
        return super().post(url, *args, **kwargs)


@pytest.fixture
def web_client(mongodb_uri: str) -> Iterator[TestClient]:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_web_{uuid4().hex}",
    )
    with FormTestClient(create_app(settings), headers={"Origin": "http://testserver"}) as client:
        yield client


def test_number_filter_query_values_are_parsed_for_service() -> None:
    template = {"fields": [{"key": "weight", "type": "number"}]}
    filters, values = _filter_arguments(
        template,
        QueryParams({"filter_weight_min": "3", "filter_weight_max": "6.5"}),
    )

    assert filters == {"number_filters": {"weight": {"min": 3, "max": 6.5}}}
    assert values == {"weight": {"min": "3", "max": "6.5"}}


def test_form_test_client_encodes_repeated_form_fields() -> None:
    app = FastAPI()

    @app.post("/form")
    async def read_form(request: Request):
        form = await request.form()
        return {"name": [str(value) for value in form.getlist("name")]}

    with FormTestClient(app) as client:
        response = client.post("/form", data=[("name", "one"), ("name", "two")])

    assert response.json() == {"name": ["one", "two"]}


def _field_keys(html: str) -> list[str]:
    return re.findall(r'name="field_key" value="([^"]*)"', html)


def _blank_id(html: str) -> str:
    match = re.search(r'<option value="([a-f0-9]{24})"\s*>Пустая карточка', html)
    assert match is not None
    return match.group(1)


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


def test_ui_posts_require_same_origin(web_client: TestClient) -> None:
    allowed = web_client.post(
        "/templates/new",
        headers={"Origin": "http://testserver"},
        data=[("name", "Allowed")],
        follow_redirects=False,
    )
    assert allowed.status_code == 303

    rejected = web_client.post(
        "/templates/new",
        headers={"Origin": "https://evil.example"},
        data=[("name", "Rejected")],
    )
    assert rejected.status_code == 403

    blank_id = _blank_id(web_client.get("/cards/new").text)
    created = web_client.post(
        "/cards/new", data=[("title", "Protected"), ("template_id", blank_id)], follow_redirects=False
    )
    card_id = created.headers["location"].split("/")[2]
    rejected_delete = web_client.post(
        f"/cards/{card_id}/delete", headers={"Origin": "https://evil.example"}
    )
    assert rejected_delete.status_code == 403
    assert web_client.get(f"/cards/{card_id}/edit").status_code == 200


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

    blank_id = _blank_id(web_client.get("/cards/new").text)
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
    assert "<li>No cards</li>" in web_client.get("/cards", params={"q": "Invalid"}).text


def test_ui_rejects_missing_title_template_and_duplicate_labels(web_client: TestClient) -> None:
    blank_id = _blank_id(web_client.get("/cards/new").text)
    missing_title = web_client.post("/cards/new", data=[("template_id", blank_id)])
    assert missing_title.status_code == 400
    assert "title" in missing_title.text

    missing_template = web_client.post("/cards/new", data=[("title", "No template")])
    assert missing_template.status_code == 400
    assert "invalid id" in missing_template.text

    duplicate_template = web_client.post(
        "/templates/new",
        data=[
            ("name", "Duplicate labels"),
            ("field_key", ""), ("field_label", "Same"), ("field_type", "text"), ("field_options", ""),
            ("field_key", ""), ("field_label", "same"), ("field_type", "text"), ("field_options", ""),
        ],
    )
    assert duplicate_template.status_code == 400
    assert "unique" in duplicate_template.text


def test_blank_card_accepts_first_field(web_client: TestClient) -> None:
    blank_id = _blank_id(web_client.get("/cards/new").text)
    created = web_client.post(
        "/cards/new",
        data=[
            ("title", "First field"),
            ("template_id", blank_id),
            ("field_key", ""),
            ("field_label", "Note"),
            ("field_type", "text"),
            ("field_options", ""),
            ("field_value", ""),
            ("field_present", "0"),
        ],
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert "Note" in web_client.get(created.headers["location"]).text


def test_ui_blank_creation_and_template_copy_are_independent(web_client: TestClient) -> None:
    blank = web_client.get("/cards/new")
    assert blank.status_code == 200
    blank_id = _blank_id(blank.text)
    created_blank = web_client.post(
        "/cards/new",
        data=[("title", "Title only"), ("template_id", blank_id)],
        follow_redirects=False,
    )
    assert created_blank.status_code == 303
    blank_card = web_client.get(created_blank.headers["location"])
    assert "Title only" in blank_card.text
    assert "field-row" not in blank_card.text

    template = web_client.post(
        "/templates/new",
        data=_template_form("Independent"),
        follow_redirects=False,
    )
    template_id = template.headers["location"].split("/")[2]
    template_page = web_client.get(template.headers["location"])
    keys = _field_keys(template_page.text)
    card = web_client.post(
        "/cards/new",
        data=[
            ("title", "Copied fields"),
            ("template_id", template_id),
            ("field_key", keys[0]),
            ("field_label", "Maker"),
            ("field_type", "text"),
            ("field_options", ""),
            ("field_value", "Acme"),
            ("field_present", "1"),
            ("field_key", keys[1]),
            ("field_label", "Count"),
            ("field_type", "number"),
            ("field_options", ""),
            ("field_value", "2"),
            ("field_present", "1"),
            ("field_key", keys[2]),
            ("field_label", "Kind"),
            ("field_type", "select"),
            ("field_options", "Food,Tool"),
            ("field_value", "Tool"),
            ("field_present", "1"),
        ],
        follow_redirects=False,
    )
    assert card.status_code == 303
    card_id = card.headers["location"].split("/")[2]

    edited_template = web_client.post(
        f"/templates/{template_id}/edit",
        data=_template_form("Independent changed", include_fields=False),
        follow_redirects=False,
    )
    assert edited_template.status_code == 303
    card_page = web_client.get(f"/cards/{card_id}/edit")
    assert "Maker" in card_page.text
    assert "Independent changed" not in card_page.text


def test_ui_filters_cover_boolean_date_and_text_list(web_client: TestClient) -> None:
    fields = [
        ("field_key", ""), ("field_label", "Bought"), ("field_type", "date"), ("field_options", ""),
        ("field_key", ""), ("field_label", "Available"), ("field_type", "boolean"), ("field_options", ""),
        ("field_key", ""), ("field_label", "Tags"), ("field_type", "text[]"), ("field_options", ""),
    ]
    created = web_client.post("/templates/new", data=[("name", "Typed filters"), *fields], follow_redirects=False)
    template_id = created.headers["location"].split("/")[2]
    keys = _field_keys(web_client.get(created.headers["location"]).text)

    def create(title: str, bought: str, available: str, tags: str) -> None:
        assert web_client.post(
            "/cards/new",
            data=[
                ("title", title), ("template_id", template_id),
                ("field_key", keys[0]), ("field_label", "Bought"), ("field_type", "date"),
                ("field_options", ""), ("field_value", bought), ("field_present", "1"),
                ("field_key", keys[1]), ("field_label", "Available"), ("field_type", "boolean"),
                ("field_options", ""), ("field_value", available), ("field_present", "1"),
                ("field_key", keys[2]), ("field_label", "Tags"), ("field_type", "text[]"),
                ("field_options", ""), ("field_value", tags), ("field_present", "1"),
            ],
        ).status_code == 303

    create("Fresh", "2025-01-01", "true", "cold,food")
    create("Old", "2024-01-01", "false", "food")
    create("Other", "2025-01-02", "true", "tool")

    response = web_client.get(
        "/cards",
        params={
            "template_id": template_id,
            f"filter_{keys[0]}_min": "2025-01-01",
            f"filter_{keys[0]}_max": "2025-12-31",
            f"filter_{keys[1]}": "true",
            f"filter_{keys[2]}": "cold",
        },
    )
    assert response.status_code == 200
    assert "Fresh" in response.text
    assert "Old" not in response.text
    assert "Other" not in response.text
