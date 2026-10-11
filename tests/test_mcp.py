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
