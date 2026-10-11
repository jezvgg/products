"""Jinja environment, template helpers, and the HTML routes of the app."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .domain import BLANK_TEMPLATE_NAME
from .forms import filter_arguments, form_string, parse_fields
from .services import ApplicationService, ServiceError

templates = Jinja2Templates(directory="products/templates")
FIELD_TYPES = ["text", "number", "boolean", "date", "select", "text[]"]

_STATUS_BY_CODE = {
    "invalid_request": 400,
    "not_found": 404,
    "conflict": 409,
    "confirmation_required": 409,
    "storage_error": 503,
}


def context(request: Request, **values: Any) -> dict[str, Any]:
    """Base template context shared by every rendered page."""
    return {"request": request, "field_types": FIELD_TYPES, **values}


def error_message(exc: Exception) -> str:
    """Human readable message for a service error (or any other exception)."""
    return exc.message if isinstance(exc, ServiceError) else str(exc)


def service_status(exc: ServiceError) -> int:
    """Map a service error onto the HTTP status the UI should answer with."""
    if exc.code == "conflict" and "blank template" in exc.message:
        return 403
    return _STATUS_BY_CODE.get(exc.code, 400)


def display_value(field: dict[str, Any]) -> str:
    """Format a stored field value for compact display."""
    value = field.get("value")
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


templates.env.globals["display_value"] = display_value


def error_response(request: Request, exc: ServiceError) -> HTMLResponse:
    """Render the shared error page for an unhandled service error."""
    status_code = service_status(exc)
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context=context(request, status_code=status_code, error=error_message(exc)),
        status_code=status_code,
    )


def build_router() -> APIRouter:
    """Build the HTML router for cards and templates."""
    router = APIRouter()

    def service(request: Request) -> ApplicationService:
        return request.app.state.service

    def template_or_none(svc: ApplicationService, template_id: str) -> dict[str, Any] | None:
        if not template_id:
            return None
        return svc.get_template(template_id)

    def editor_context(
        request: Request,
        svc: ApplicationService,
        *,
        card: dict[str, Any] | None,
        selected: dict[str, Any] | None = None,
        error: str | None = None,
        submitted_fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "card": card,
            "selected": selected,
            "templates": svc.list_templates(),
            "error": error,
        }
        if submitted_fields is not None:
            values["submitted_fields"] = submitted_fields
        return context(request, **values)

    @router.get("/")
    def home() -> RedirectResponse:
        return RedirectResponse("/cards", status_code=303)

    @router.get("/cards")
    def card_list(request: Request, q: str = "", template_id: str = "", cursor: str = ""):
        svc = service(request)
        selected = None
        error = None
        status_code = 200
        result: dict[str, Any] = {"items": [], "next_cursor": None}
        filter_values: dict[str, Any] = {}
        template_list: list[dict[str, Any]] = []
        try:
            selected = template_or_none(svc, template_id)
            filter_kwargs, filter_values = (
                filter_arguments(selected, request.query_params) if selected else ({}, {})
            )
            result = svc.search_cards(
                title=q or None,
                template_id=template_id or None,
                cursor=cursor or None,
                **filter_kwargs,
            )
            template_list = svc.list_templates()
        except ServiceError as exc:
            error = error_message(exc)
            status_code = service_status(exc)

        next_url = None
        if result["next_cursor"]:
            next_url = str(request.url.include_query_params(cursor=result["next_cursor"]))
        return templates.TemplateResponse(
            request=request,
            name="cards.html",
            context=context(
                request,
                cards=result["items"],
                next_url=next_url,
                query=q,
                selected=template_id,
                selected_template=selected,
                templates=template_list,
                error=error,
                filter_values=filter_values,
            ),
            status_code=status_code,
        )

    @router.get("/cards/new")
    def card_new(request: Request, template_id: str = ""):
        svc = service(request)
        selected = template_or_none(svc, template_id)
        return templates.TemplateResponse(
            request=request,
            name="card_form.html",
            context=editor_context(request, svc, card=None, selected=selected),
        )

    @router.post("/cards/new")
    async def card_create(request: Request):
        form = await request.form()
        svc = service(request)
        fields: list[dict[str, Any]] = []
        try:
            fields = parse_fields(form, include_values=True)
            card = svc.create_card(
                form_string(form, "template_id"),
                form_string(form, "title"),
                fields=fields,
            )
        except ServiceError as exc:
            try:
                selected = template_or_none(svc, form_string(form, "template_id"))
            except ServiceError:
                selected = None
            return templates.TemplateResponse(
                request=request,
                name="card_form.html",
                context=editor_context(
                    request,
                    svc,
                    card=None,
                    selected=selected,
                    error=error_message(exc),
                    submitted_fields=fields,
                ),
                status_code=400,
            )
        return RedirectResponse(f"/cards/{card['id']}/edit", status_code=303)

    @router.get("/cards/{card_id}/edit")
    def card_edit(request: Request, card_id: str):
        svc = service(request)
        card = svc.get_card(card_id)
        return templates.TemplateResponse(
            request=request,
            name="card_form.html",
            context=editor_context(request, svc, card=card),
        )

    @router.post("/cards/{card_id}/edit")
    async def card_save(request: Request, card_id: str):
        form = await request.form()
        svc = service(request)
        current = svc.get_card(card_id)
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
                title=form_string(form, "title"),
                fields=submitted,
            )
        except ServiceError as exc:
            return templates.TemplateResponse(
                request=request,
                name="card_form.html",
                context=editor_context(
                    request, svc, card=svc.get_card(card_id), error=error_message(exc)
                ),
                status_code=400,
            )
        return RedirectResponse(f"/cards/{card['id']}/edit", status_code=303)

    @router.post("/cards/{card_id}/delete")
    def card_delete(request: Request, card_id: str):
        service(request).delete_card(card_id)
        return RedirectResponse("/cards", status_code=303)

    @router.get("/templates")
    def template_list(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="templates.html",
            context=context(request, templates=service(request).list_templates()),
        )

    @router.get("/templates/new")
    def template_new(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="template_form.html",
            context=context(request, item=None, error=None),
        )

    @router.post("/templates/new")
    async def template_create(request: Request):
        form = await request.form()
        svc = service(request)
        try:
            item = svc.create_template(form.get("name"), parse_fields(form, include_values=False))
        except ServiceError as exc:
            return templates.TemplateResponse(
                request=request,
                name="template_form.html",
                context=context(request, item=None, error=error_message(exc)),
                status_code=400,
            )
        return RedirectResponse(f"/templates/{item['id']}/edit", status_code=303)

    @router.get("/templates/{template_id}/edit")
    def template_edit(request: Request, template_id: str):
        item = service(request).get_template(template_id)
        if item["name"] == BLANK_TEMPLATE_NAME:
            raise HTTPException(status_code=403, detail="blank template is immutable")
        return templates.TemplateResponse(
            request=request,
            name="template_form.html",
            context=context(request, item=item, error=None),
        )

    @router.post("/templates/{template_id}/edit")
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
            return templates.TemplateResponse(
                request=request,
                name="template_form.html",
                context=context(
                    request,
                    item=svc.get_template(template_id),
                    error=error_message(exc),
                ),
                status_code=400,
            )
        return RedirectResponse(f"/templates/{item['id']}/edit", status_code=303)

    @router.post("/templates/{template_id}/delete")
    def template_delete(request: Request, template_id: str):
        service(request).delete_template(template_id)
        return RedirectResponse("/templates", status_code=303)

    return router
