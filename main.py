import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from products.domain import BLANK_TEMPLATE_NAME
from products.mcp import create_mcp_server
from products.persistence import MongoPersistence
from products.services import ApplicationService, InvalidRequestError, ServiceError
from products.web import ctx, message, templates


@dataclass(frozen=True, slots=True)
class Settings:
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_database: str = "products"
    mcp_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mongodb_uri=os.getenv("MONGODB_URI", cls.mongodb_uri),
            mongodb_database=os.getenv("MONGODB_DATABASE", cls.mongodb_database),
            mcp_token=os.getenv("MCP_TOKEN", cls.mcp_token),
        )


def _form_string(form: Any, name: str, default: str = "") -> str:
    value = form.get(name, default)
    return value if isinstance(value, str) else default


def _parse_number(value: str) -> int | float | str:
    try:
        return int(value) if "." not in value else float(value)
    except ValueError:
        return value


def _parse_form_value(field_type: str, value: str) -> Any:
    if field_type == "number":
        return _parse_number(value)
    if field_type == "boolean":
        if value.casefold() == "true":
            return True
        if value.casefold() == "false":
            return False
    if field_type == "text[]":
        return [part.strip() for part in value.split(",")]
    return value


def parse_fields(form: Any, *, include_values: bool) -> list[dict[str, Any]]:
    names = ["field_key", "field_label", "field_type", "field_options"]
    if include_values:
        names += ["field_value", "field_present"]
    values = {name: form.getlist(name) for name in names}
    row_count = len(values["field_label"])
    if any(len(items) != row_count for items in values.values()):
        raise InvalidRequestError("field form rows are inconsistent")

    fields = []
    for index in range(row_count):
        field: dict[str, Any] = {
            "label": values["field_label"][index],
            "type": values["field_type"][index],
        }
        key = values["field_key"][index].strip()
        if key:
            field["key"] = key
        if field["type"] == "select":
            field["options"] = [
                option.strip()
                for option in values["field_options"][index].split(",")
                if option.strip()
            ]
        elif values["field_options"][index].strip():
            raise InvalidRequestError("only select fields may have options")

        if include_values:
            present = values["field_present"][index]
            if present not in {"0", "1"}:
                raise InvalidRequestError("field value presence must be 0 or 1")
            if present == "1":
                field["value"] = _parse_form_value(
                    field["type"], values["field_value"][index]
                )
        fields.append(field)
    return fields


def _http_error(exc: ServiceError) -> HTTPException:
    if exc.code == "not_found":
        status_code = 404
    elif exc.code == "conflict":
        status_code = 403 if "blank template" in exc.message else 409
    else:
        status_code = 400
    return HTTPException(status_code, detail=exc.message)


def _filter_arguments(template: dict[str, Any], params: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    typed: dict[str, dict[str, Any]] = {}
    values: dict[str, Any] = {}
    for field in template["fields"]:
        key = field["key"]
        name = f"filter_{key}"
        if field["type"] in {"number", "date"}:
            minimum = params.get(f"{name}_min", "").strip()
            maximum = params.get(f"{name}_max", "").strip()
            if not minimum and not maximum:
                continue
            spec = {"min": minimum} if minimum else {}
            if maximum:
                spec["max"] = maximum
            typed.setdefault(f"{field['type']}_filters", {})[key] = spec
            values[key] = {"min": minimum, "max": maximum}
        else:
            value = params.get(name, "")
            if not value:
                continue
            if field["type"] == "boolean":
                parsed: Any = value.casefold() == "true" if value.casefold() in {"true", "false"} else value
            else:
                parsed = value
            typed.setdefault(
                {
                    "text": "text_filters",
                    "boolean": "boolean_filters",
                    "select": "select_filters",
                    "text[]": "text_list_filters",
                }[field["type"]],
                {},
            )[key] = parsed
            values[key] = value
    return typed, values


def create_app(
    settings: Settings | None = None,
    mongo_client: MongoClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    app: FastAPI
    mcp = create_mcp_server(lambda: app.state.service, settings.mcp_token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = mongo_client if mongo_client is not None else MongoClient(settings.mongodb_uri)
        app.state.mongo_client = client
        app.state.mongo_database = client[settings.mongodb_database]
        app.state.persistence = MongoPersistence(app.state.mongo_database)
        app.state.service = ApplicationService(app.state.persistence)
        app.state.persistence_error = None
        try:
            app.state.persistence.initialize()
        except Exception as exc:  # noqa: BLE001 - readiness must expose init failures
            app.state.persistence_error = exc
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if mongo_client is None:
                client.close()

    app = FastAPI(title="Products", lifespan=lifespan)

    @app.middleware("http")
    async def require_same_origin(request: Request, call_next: Any):
        if request.method == "POST" and not request.url.path.startswith("/mcp"):
            origin = request.headers.get("origin")
            scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
            expected = f"{scheme}://{request.url.netloc}"
            if origin != expected:
                return PlainTextResponse("same-origin request required", status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory="products/static"), name="static")

    @app.get("/health")
    @app.get("/health/ready")
    def readiness(request: Request) -> dict[str, str]:
        if request.app.state.persistence_error is not None:
            raise HTTPException(status_code=503, detail="MongoDB initialization failed")
        try:
            request.app.state.mongo_client.admin.command("ping")
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB unavailable") from exc
        return {"status": "ok"}

    def service(request: Request) -> ApplicationService:
        return request.app.state.service

    def template_or_none(svc: ApplicationService, template_id: str) -> dict[str, Any] | None:
        if not template_id:
            return None
        return svc.get_template(template_id)

    @app.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/cards", status_code=303)

    @app.get("/cards")
    def card_list(request: Request, q: str = "", template_id: str = "", cursor: str = ""):
        svc = service(request)
        selected = None
        error = None
        result = {"items": [], "next_cursor": None}
        filter_values: dict[str, Any] = {}
        try:
            selected = template_or_none(svc, template_id)
            filter_kwargs, filter_values = (
                _filter_arguments(selected, request.query_params) if selected else ({}, {})
            )
            result = svc.search_cards(
                title=q or None,
                template_id=template_id or None,
                cursor=cursor or None,
                **filter_kwargs,
            )
        except ServiceError as exc:
            error = message(exc)
        next_url = None
        if result["next_cursor"]:
            next_url = str(request.url.include_query_params(cursor=result["next_cursor"]))
        context = ctx(
            request,
            cards=result["items"],
            next_url=next_url,
            query=q,
            selected=template_id,
            selected_template=selected,
            templates=svc.list_templates(),
            error=error,
            filter_values=filter_values,
        )
        return templates.TemplateResponse(
            request=request,
            name="cards.html",
            context=context,
            status_code=400 if error else 200,
        )

    @app.get("/cards/new")
    def card_new(request: Request, template_id: str = ""):
        svc = service(request)
        try:
            selected = template_or_none(svc, template_id)
        except ServiceError as exc:
            raise _http_error(exc) from exc
        return templates.TemplateResponse(
            request=request,
            name="card_form.html",
            context=ctx(
                request,
                card=None,
                selected=selected,
                templates=svc.list_templates(),
                error=None,
            ),
        )

    @app.post("/cards/new")
    async def card_create(request: Request):
        form = await request.form()
        svc = service(request)
        fields: list[dict[str, Any]] = []
        try:
            fields = parse_fields(form, include_values=True)
            card = svc.create_card(
                _form_string(form, "template_id"),
                _form_string(form, "title"),
                fields=fields,
            )
        except ServiceError as exc:
            try:
                selected = template_or_none(svc, _form_string(form, "template_id"))
            except ServiceError:
                selected = None
            return templates.TemplateResponse(
                request=request,
                name="card_form.html",
                context=ctx(
                    request,
                    card=None,
                    selected=selected,
                    templates=svc.list_templates(),
                    error=message(exc),
                    submitted_fields=fields if "fields" in locals() else [],
                ),
                status_code=400,
            )
        return RedirectResponse(f"/cards/{card['id']}/edit", status_code=303)

    @app.get("/cards/{card_id}/edit")
    def card_edit(request: Request, card_id: str):
        svc = service(request)
        try:
            card = svc.get_card(card_id)
        except ServiceError as exc:
            raise _http_error(exc) from exc
        return templates.TemplateResponse(
            request=request,
            name="card_form.html",
            context=ctx(request, card=card, selected=None, templates=svc.list_templates(), error=None),
        )

    @app.post("/cards/{card_id}/edit")
    async def card_save(request: Request, card_id: str):
        form = await request.form()
        svc = service(request)
        try:
            current = svc.get_card(card_id)
        except ServiceError as exc:
            raise _http_error(exc) from exc
        try:
            submitted = parse_fields(form, include_values=True)
            current_by_key = {field["key"]: field for field in current["fields"]}
            changes = {
                field["key"]: {
                    "type": field["type"],
                    "options": field.get("options"),
                    "confirm": bool(form.get("confirm_conversion")),
                }
                for field in submitted
                if field.get("key") in current_by_key
                and field["type"] != current_by_key[field["key"]]["type"]
            }
            if changes:
                converted = svc.update_card(card_id, convert_fields=changes)
                converted_by_key = {field["key"]: field for field in converted["fields"]}
                for field in submitted:
                    converted_field = converted_by_key.get(field.get("key"))
                    if converted_field is not None and "value" not in converted_field:
                        field.pop("value", None)
            card = svc.update_card(
                card_id,
                title=_form_string(form, "title"),
                fields=submitted,
            )
        except ServiceError as exc:
            return templates.TemplateResponse(
                request=request,
                name="card_form.html",
                context=ctx(
                    request,
                    card=svc.get_card(card_id),
                    selected=None,
                    templates=svc.list_templates(),
                    error=message(exc),
                ),
                status_code=400,
            )
        return RedirectResponse(f"/cards/{card['id']}/edit", status_code=303)

    @app.post("/cards/{card_id}/delete")
    def card_delete(request: Request, card_id: str):
        try:
            service(request).delete_card(card_id)
        except ServiceError as exc:
            raise _http_error(exc) from exc
        return RedirectResponse("/cards", status_code=303)

    @app.get("/templates")
    def template_list(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="templates.html",
            context=ctx(request, templates=service(request).list_templates()),
        )

    @app.get("/templates/new")
    def template_new(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="template_form.html",
            context=ctx(request, item=None, error=None),
        )

    @app.post("/templates/new")
    async def template_create(request: Request):
        form = await request.form()
        svc = service(request)
        try:
            item = svc.create_template(form.get("name"), parse_fields(form, include_values=False))
        except ServiceError as exc:
            return templates.TemplateResponse(
                request=request,
                name="template_form.html",
                context=ctx(request, item=None, error=message(exc)),
                status_code=400,
            )
        return RedirectResponse(f"/templates/{item['id']}/edit", status_code=303)

    @app.get("/templates/{template_id}/edit")
    def template_edit(request: Request, template_id: str):
        svc = service(request)
        try:
            item = svc.get_template(template_id)
        except ServiceError as exc:
            raise _http_error(exc) from exc
        if item["name"] == BLANK_TEMPLATE_NAME:
            raise HTTPException(status_code=403, detail="blank template is immutable")
        return templates.TemplateResponse(
            request=request,
            name="template_form.html",
            context=ctx(request, item=item, error=None),
        )

    @app.post("/templates/{template_id}/edit")
    async def template_save(request: Request, template_id: str):
        form = await request.form()
        svc = service(request)
        try:
            item = svc.update_template(
                template_id,
                name=form.get("name"),
                fields=parse_fields(form, include_values=False),
            )
        except ServiceError as exc:
            try:
                item = svc.get_template(template_id)
            except ServiceError:
                raise _http_error(exc) from exc
            return templates.TemplateResponse(
                request=request,
                name="template_form.html",
                context=ctx(request, item=item, error=message(exc)),
                status_code=400,
            )
        return RedirectResponse(f"/templates/{item['id']}/edit", status_code=303)

    @app.post("/templates/{template_id}/delete")
    def template_delete(request: Request, template_id: str):
        try:
            service(request).delete_template(template_id)
        except ServiceError as exc:
            raise _http_error(exc) from exc
        return RedirectResponse("/templates", status_code=303)

    app.mount("/mcp", mcp.streamable_http_app())
    return app


app = create_app()
