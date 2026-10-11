from uuid import uuid4

from fastapi.testclient import TestClient

from main import Settings, create_app

MCP_TEST_TOKEN = "test" + "-token"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {MCP_TEST_TOKEN}",
}


def mcp_request(
    client: TestClient, request_id: int, method: str, **params: object
) -> dict:
    response = client.post(
        "/mcp/",
        headers=MCP_HEADERS,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    )
    assert response.status_code == 200
    return response.json()


def call_tool(
    client: TestClient, request_id: int, tool_name: str, **arguments: object
) -> dict:
    result = mcp_request(
        client,
        request_id,
        "tools/call",
        name=tool_name,
        arguments=arguments,
    )
    return result["result"]


def test_mcp_requires_bearer_token(mongodb_uri: str) -> None:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_mcp_auth_{uuid4().hex}",
        mcp_token=MCP_TEST_TOKEN,
    )

    with TestClient(create_app(settings)) as client:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post("/mcp/", json=payload).status_code == 401
        assert client.post("/mcp/?token=test-token", json=payload).status_code == 401
        assert (
            client.post(
                "/mcp/",
                headers={**MCP_HEADERS, "Authorization": "Bearer wrong"},
                json=payload,
            ).status_code
            == 401
        )


def test_mcp_tools_share_service_and_require_delete_confirmation(
    mongodb_uri: str,
) -> None:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_mcp_tools_{uuid4().hex}",
        mcp_token=MCP_TEST_TOKEN,
    )

    with TestClient(create_app(settings)) as client:
        initialized = mcp_request(
            client,
            1,
            "initialize",
            protocolVersion="2025-06-18",
            capabilities={},
            clientInfo={"name": "test", "version": "1"},
        )
        assert initialized["result"]["serverInfo"]["name"] == "Products"

        listed = mcp_request(client, 2, "tools/list")
        assert {tool["name"] for tool in listed["result"]["tools"]} == {
            "create_card",
            "get_card",
            "update_card",
            "delete_card",
            "search_cards",
            "list_templates",
            "get_template",
            "create_template",
            "update_template",
            "delete_template",
        }

        template_result = call_tool(
            client,
            3,
            "create_template",
            name="Food",
            fields=[{"label": "Brand", "type": "text"}],
        )
        template = template_result["structuredContent"]
        template_id = template["id"]
        assert template["name"] == "Food"

        card_result = call_tool(
            client,
            4,
            "create_card",
            template_id=template_id,
            title="Milk",
            values={"Brand": "Acme"},
        )
        card = card_result["structuredContent"]
        card_id = card["id"]
        assert card["fields"][0]["value"] == "Acme"

        updated = call_tool(
            client, 5, "update_card", card_id=card_id, title="Whole milk"
        )
        assert updated["structuredContent"]["title"] == "Whole milk"
        assert updated["structuredContent"]["fields"][0]["value"] == "Acme"

        second = call_tool(
            client,
            6,
            "create_card",
            template_id=template_id,
            title="Bread",
            values={"Brand": "Baker"},
        )["structuredContent"]
        page = call_tool(client, 7, "search_cards", template_id=template_id, limit=1)
        assert len(page["structuredContent"]["items"]) == 1
        assert page["structuredContent"]["next_cursor"]
        next_page = call_tool(
            client,
            8,
            "search_cards",
            template_id=template_id,
            limit=1,
            cursor=page["structuredContent"]["next_cursor"],
        )
        assert {item["id"] for item in next_page["structuredContent"]["items"]} == {
            card_id,
            second["id"],
        } - {page["structuredContent"]["items"][0]["id"]}

        rejected = call_tool(client, 9, "delete_card", card_id=card_id)
        assert rejected["isError"] is True
        assert "confirm=true" in rejected["content"][0]["text"]
        assert (
            call_tool(client, 10, "get_card", card_id=card_id)["structuredContent"][
                "id"
            ]
            == card_id
        )

        deleted = call_tool(client, 11, "delete_card", card_id=card_id, confirm=True)
        assert deleted["structuredContent"] == {"id": card_id, "deleted": True}
        missing = call_tool(client, 12, "get_card", card_id=card_id)
        assert missing["isError"] is True
        assert "not_found" in missing["content"][0]["text"]
        remaining = call_tool(client, 13, "search_cards", query="Whole milk")
        assert all(item["id"] != card_id for item in remaining["structuredContent"]["items"])

        conversion_card = call_tool(
            client,
            14,
            "create_card",
            template_id=template_id,
            title="Conversion",
            values={"Brand": "not a number"},
        )["structuredContent"]
        for request_id, confirm in ((15, "true"), (16, 1)):
            rejected_conversion = call_tool(
                client,
                request_id,
                "update_card",
                card_id=conversion_card["id"],
                convert_fields={"Brand": {"type": "number", "confirm": confirm}},
            )
            assert rejected_conversion["isError"] is True
            assert "confirmation_required" in rejected_conversion["content"][0]["text"]
        assert call_tool(client, 17, "get_card", card_id=conversion_card["id"])["structuredContent"]["fields"][0]["value"] == "not a number"


def test_mcp_covers_typed_filters_template_crud_and_ui_parity(mongodb_uri: str) -> None:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_mcp_acceptance_{uuid4().hex}",
        mcp_token=MCP_TEST_TOKEN,
    )

    with TestClient(create_app(settings)) as client:
        templates = call_tool(client, 1, "list_templates")["structuredContent"]
        blank = next(item for item in templates if item["name"] == "Пустая карточка")
        blank_card = call_tool(
            client, 2, "create_card", template_id=blank["id"], title="Title only"
        )["structuredContent"]
        assert blank_card["fields"] == []

        template_fields = [
            {"label": "Brand", "type": "text"},
            {"label": "Count", "type": "number"},
            {"label": "Bought", "type": "date"},
            {"label": "Available", "type": "boolean"},
            {"label": "Kind", "type": "select", "options": ["Food", "Tool"]},
            {"label": "Tags", "type": "text[]"},
        ]
        created_template = call_tool(
            client, 3, "create_template", name="MCP inventory", fields=template_fields
        )["structuredContent"]
        template_id = created_template["id"]
        assert call_tool(client, 4, "get_template", template_id=template_id)["structuredContent"] == created_template

        first = call_tool(
            client,
            5,
            "create_card",
            template_id=template_id,
            title="Red toolbox",
            values={
                "Brand": "Acme tools",
                "Count": 5,
                "Bought": "2025-01-10",
                "Available": True,
                "Kind": "Tool",
                "Tags": ["hardware", "home"],
            },
        )["structuredContent"]
        second = call_tool(
            client,
            6,
            "create_card",
            template_id=template_id,
            title="Blue toolbox",
            values={
                "Brand": "Other",
                "Count": 8,
                "Bought": "2025-01-11",
                "Available": True,
                "Kind": "Tool",
                "Tags": ["hardware"],
            },
        )["structuredContent"]

        filtered = call_tool(
            client,
            7,
            "search_cards",
            query="red",
            template_id=template_id,
            text_filters={"Brand": "Acme"},
            number_filters={"Count": {"min": 3, "max": 6}},
            date_filters={"Bought": {"min": "2025-01-01", "max": "2025-12-31"}},
            boolean_filters={"Available": True},
            select_filters={"Kind": "Tool"},
            text_list_filters={"Tags": "hardware"},
        )["structuredContent"]
        assert [item["id"] for item in filtered["items"]] == [first["id"]]

        field_keys = {field["label"]: field["key"] for field in first["fields"]}
        ui_result = client.get(
            "/cards",
            params={
                "q": "red",
                "template_id": template_id,
                f"filter_{field_keys['Brand']}": "Acme",
                f"filter_{field_keys['Count']}_min": "3",
                f"filter_{field_keys['Count']}_max": "6",
                f"filter_{field_keys['Bought']}_min": "2025-01-01",
                f"filter_{field_keys['Bought']}_max": "2025-12-31",
                f"filter_{field_keys['Available']}": "true",
                f"filter_{field_keys['Kind']}": "Tool",
                f"filter_{field_keys['Tags']}": "hardware",
            },
        )
        assert ui_result.status_code == 200
        assert "Red toolbox" in ui_result.text
        assert "Blue toolbox" not in ui_result.text

        page = call_tool(client, 8, "search_cards", template_id=template_id, limit=1)["structuredContent"]
        assert len(page["items"]) == 1
        assert page["next_cursor"]
        next_page = call_tool(
            client,
            9,
            "search_cards",
            template_id=template_id,
            limit=1,
            cursor=page["next_cursor"],
        )["structuredContent"]
        assert {item["id"] for item in next_page["items"]} == {first["id"], second["id"]} - {page["items"][0]["id"]}

        updated_card = call_tool(
            client,
            10,
            "update_card",
            card_id=first["id"],
            title="Red toolbox updated",
            values={"Count": 6},
        )["structuredContent"]
        assert updated_card["title"] == "Red toolbox updated"
        assert next(field for field in updated_card["fields"] if field["label"] == "Count")["value"] == 6

        changed_template = call_tool(
            client,
            11,
            "update_template",
            template_id=template_id,
            name="MCP inventory changed",
            fields=[{"label": "Changed", "type": "text"}],
        )["structuredContent"]
        assert changed_template["name"] == "MCP inventory changed"
        unchanged_card = call_tool(client, 12, "get_card", card_id=first["id"])["structuredContent"]
        assert [field["label"] for field in unchanged_card["fields"]] == [field["label"] for field in template_fields]

        deleted_template = call_tool(client, 13, "delete_template", template_id=template_id)["structuredContent"]
        assert deleted_template == {"id": template_id, "deleted": True}
        assert call_tool(client, 14, "get_card", card_id=first["id"])["structuredContent"]["template_id"] == template_id

        duplicate_one = call_tool(
            client,
            15,
            "create_card",
            template_id=blank["id"],
            title="Duplicate title",
        )["structuredContent"]
        duplicate_two = call_tool(
            client,
            16,
            "create_card",
            template_id=blank["id"],
            title="Duplicate title",
        )["structuredContent"]
        assert duplicate_one["title"] == duplicate_two["title"]
        assert duplicate_one["id"] != duplicate_two["id"]
        invalid_value = call_tool(
            client,
            17,
            "create_card",
            template_id=blank["id"],
            title="Invalid",
            values={"missing": "value"},
        )
        assert invalid_value["isError"] is True
        assert "unknown field" in invalid_value["content"][0]["text"]

        missing_title = mcp_request(
            client,
            18,
            "tools/call",
            name="create_card",
            arguments={"template_id": blank["id"]},
        )
        assert missing_title["result"]["isError"] is True
        invalid_template = call_tool(
            client, 19, "create_card", template_id="not-an-id", title="Invalid"
        )
        assert invalid_template["isError"] is True
        invalid_fields = call_tool(
            client,
            20,
            "create_template",
            name="Invalid fields",
            fields=[{"label": "Same", "type": "text"}, {"label": "same", "type": "text"}],
        )
        assert invalid_fields["isError"] is True
        invalid_type = call_tool(
            client,
            21,
            "create_template",
            name="Invalid type",
            fields=[{"label": "Value", "type": "object"}],
        )
        assert invalid_type["isError"] is True
        duplicate_name = call_tool(client, 22, "create_template", name="MCP inventory changed")
        assert duplicate_name["isError"] is True
        invalid_select = call_tool(
            client,
            23,
            "create_template",
            name="Invalid select",
            fields=[
                {"label": "Kind", "type": "select", "options": ["A"]},
                {"label": "Count", "type": "number"},
            ],
        )["structuredContent"]
        bad_option = call_tool(
            client,
            24,
            "create_card",
            template_id=invalid_select["id"],
            title="Bad option",
            values={"Kind": "B"},
        )
        assert bad_option["isError"] is True
        bad_number = call_tool(
            client,
            25,
            "create_card",
            template_id=invalid_select["id"],
            title="Bad number",
            values={"Count": "not-a-number"},
        )
        assert bad_number["isError"] is True
        blank_update = call_tool(client, 26, "update_template", template_id=blank["id"], name="No")
        assert blank_update["isError"] is True
        assert "immutable" in blank_update["content"][0]["text"]
