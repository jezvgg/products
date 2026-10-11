from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from .services import ApplicationService, ServiceError


class EnvironmentTokenVerifier(TokenVerifier):
    def __init__(self, expected_token: str) -> None:
        self.expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self.expected_token or not secrets.compare_digest(
            token, self.expected_token
        ):
            return None
        return AccessToken(token=token, client_id="products-mcp", scopes=[])


def create_mcp_server(service: Callable[[], ApplicationService], token: str) -> FastMCP:
    mcp = FastMCP(
        "Products",
        token_verifier=EnvironmentTokenVerifier(token),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl("https://products.invalid"),
            resource_server_url=None,
            validate_token_resource=False,
        ),
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )

    def call(operation: Callable[[ApplicationService], Any]) -> Any:
        try:
            return operation(service())
        except ServiceError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc

    @mcp.tool(description="Create a card from a template and return the complete card.")
    def create_card(
        template_id: str, title: str, values: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return call(lambda app: app.create_card(template_id, title, values=values))

    @mcp.tool(description="Retrieve a complete card by id.")
    def get_card(card_id: str) -> dict[str, Any]:
        return call(lambda app: app.get_card(card_id))

    @mcp.tool(
        description="Partially update a card and return the complete resulting card."
    )
    def update_card(
        card_id: str,
        title: str | None = None,
        values: dict[str, Any] | None = None,
        fields: list[dict[str, Any]] | None = None,
        add_fields: list[dict[str, Any]] | dict[str, Any] | None = None,
        remove_fields: list[str] | str | None = None,
        rename_fields: dict[str, str] | None = None,
        convert_fields: dict[str, Any] | None = None,
        clear_fields: list[str] | str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        for name, value in (
            ("title", title),
            ("values", values),
            ("fields", fields),
            ("add_fields", add_fields),
            ("remove_fields", remove_fields),
            ("rename_fields", rename_fields),
            ("convert_fields", convert_fields),
            ("clear_fields", clear_fields),
        ):
            if value is not None:
                kwargs[name] = value
        return call(lambda app: app.update_card(card_id, **kwargs))

    @mcp.tool(description="Permanently delete a card; confirm must be true.")
    def delete_card(card_id: str, confirm: bool = False) -> dict[str, Any]:
        if confirm is not True:
            raise ToolError("confirmation_required: delete_card requires confirm=true")
        return call(lambda app: app.delete_card(card_id))

    @mcp.tool(
        description="Search cards with UI-compatible filters and a continuation cursor."
    )
    def search_cards(
        query: str | None = None,
        template_id: str | None = None,
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
        kwargs: dict[str, Any] = {"limit": limit}
        for name, value in (
            ("query", query),
            ("template_id", template_id),
            ("filters", filters),
            ("text_filters", text_filters),
            ("number_filters", number_filters),
            ("date_filters", date_filters),
            ("boolean_filters", boolean_filters),
            ("select_filters", select_filters),
            ("text_list_filters", text_list_filters),
            ("cursor", cursor),
        ):
            if value is not None:
                kwargs[name] = value
        return call(lambda app: app.search_cards(**kwargs))

    @mcp.tool(description="List all templates.")
    def list_templates() -> list[dict[str, Any]]:
        return call(lambda app: app.list_templates())

    @mcp.tool(description="Retrieve a complete template by id.")
    def get_template(template_id: str) -> dict[str, Any]:
        return call(lambda app: app.get_template(template_id))

    @mcp.tool(description="Create a template and return the complete template.")
    def create_template(
        name: str, fields: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        return call(lambda app: app.create_template(name, fields))

    @mcp.tool(
        description="Partially update a template and return the complete resulting template."
    )
    def update_template(
        template_id: str,
        name: str | None = None,
        fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if name is not None:
            kwargs["name"] = name
        if fields is not None:
            kwargs["fields"] = fields
        return call(lambda app: app.update_template(template_id, **kwargs))

    @mcp.tool(description="Permanently delete a template and return deletion status.")
    def delete_template(template_id: str) -> dict[str, Any]:
        return call(lambda app: app.delete_template(template_id))

    return mcp
