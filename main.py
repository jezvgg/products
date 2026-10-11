import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from products.domain import BLANK_TEMPLATE_NAME
from products.mcp import create_mcp_server
from products.persistence import MongoPersistence
from products.services import ApplicationService, ServiceError
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
        except PyMongoError as exc:
            app.state.persistence_error = exc
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if mongo_client is None:
                client.close()

    app = FastAPI(title="Products", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="products/static"), name="static")

    @app.get("/health")
    @app.get("/health/ready")
    def readiness(request: Request) -> dict[str, str]:
        try:
            request.app.state.mongo_client.admin.command("ping")
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB unavailable") from exc
        return {"status": "ok"}

    def rows(form):
        keys, labels, types, opts = [form.getlist(x) for x in ("field_key", "field_label", "field_type", "field_options")]
        result=[]
        for i,label in enumerate(labels):
            if not label.strip(): continue
            typ=types[i]; f={"key":keys[i],"label":label,"type":typ}
            if typ=="select": f["options"]=[x.strip() for x in opts[i].split(",") if x.strip()]
            if "field_value" in form:
                raw=form.getlist("field_value")[i]
                if typ=="number":
                    try: f["value"]=float(raw) if "." in raw else int(raw)
                    except ValueError: f["value"]=raw
                elif typ=="boolean": f["value"]=raw=="true"
                elif typ=="text[]": f["value"]=[x.strip() for x in raw.split(",") if x.strip()]
                else: f["value"]=raw
            result.append(f)
        return result
    def service(request): return request.app.state.service
    @app.get("/")
    def home(): return RedirectResponse("/cards", status_code=303)
    @app.get("/cards")
    def card_list(request: Request, q: str="", template_id: str="", cursor: str=""):
        try: result=service(request).search_cards(title=q or None, template_id=template_id or None, cursor=cursor or None)
        except ServiceError as exc: raise HTTPException(400,message(exc)) from exc
        return templates.TemplateResponse(request=request,name="cards.html",context=ctx(request,cards=result["items"],next_cursor=result["next_cursor"],query=q,selected=template_id,templates=service(request).list_templates()))
    @app.get("/cards/new")
    def card_new(request: Request, template_id: str=""):
        chosen=service(request).get_template(template_id) if template_id else None
        return templates.TemplateResponse(request=request,name="card_form.html",context=ctx(request,card=None,selected=chosen,templates=service(request).list_templates(),error=None))
    @app.post("/cards/new")
    async def card_create(request: Request):
        form=await request.form(); svc=service(request)
        try: card=svc.create_card(form.get("template_id"),form.get("title"),values={f["key"]:f["value"] for f in rows(form) if "value" in f})
        except ServiceError as exc: return templates.TemplateResponse(request=request,name="card_form.html",context=ctx(request,card=None,selected=svc.get_template(form.get("template_id")),templates=svc.list_templates(),error=message(exc)),status_code=400)
        return RedirectResponse(f"/cards/{card['id']}/edit",status_code=303)
    @app.get("/cards/{card_id}/edit")
    def card_edit(request: Request,card_id: str):
        try: card=service(request).get_card(card_id)
        except ServiceError as exc: raise HTTPException(404,message(exc)) from exc
        return templates.TemplateResponse(request=request,name="card_form.html",context=ctx(request,card=card,selected=None,templates=service(request).list_templates(),error=None))
    @app.post("/cards/{card_id}/edit")
    async def card_save(request: Request,card_id: str):
        form=await request.form(); svc=service(request)
        try:
            submitted=rows(form); current=svc.get_card(card_id)
            changes={f["key"]:{"type": f["type"], "options": f.get("options"), "confirm": bool(form.get("confirm_conversion"))} for f in submitted for old in current["fields"] if f["key"]==old["key"] and f["type"]!=old["type"]}
            if changes: svc.update_card(card_id, convert_fields=changes)
            card=svc.update_card(card_id,title=form.get("title"),fields=submitted)
        except ServiceError as exc: return templates.TemplateResponse(request=request,name="card_form.html",context=ctx(request,card=svc.get_card(card_id),selected=None,templates=svc.list_templates(),error=message(exc)),status_code=400)
        return RedirectResponse(f"/cards/{card['id']}/edit",status_code=303)
    @app.post("/cards/{card_id}/delete")
    def card_delete(request: Request,card_id: str): service(request).delete_card(card_id); return RedirectResponse("/cards",status_code=303)
    @app.get("/templates")
    def template_list(request: Request): return templates.TemplateResponse(request=request,name="templates.html",context=ctx(request,templates=service(request).list_templates()))
    @app.get("/templates/new")
    def template_new(request: Request): return templates.TemplateResponse(request=request,name="template_form.html",context=ctx(request,item=None,error=None))
    @app.post("/templates/new")
    async def template_create(request: Request):
        form=await request.form(); svc=service(request)
        try: item=svc.create_template(form.get("name"),rows(form))
        except ServiceError as exc: return templates.TemplateResponse(request=request,name="template_form.html",context=ctx(request,item=None,error=message(exc)),status_code=400)
        return RedirectResponse(f"/templates/{item['id']}/edit",status_code=303)
    @app.get("/templates/{template_id}/edit")
    def template_edit(request: Request,template_id: str):
        try: item=service(request).get_template(template_id)
        except ServiceError as exc: raise HTTPException(404,message(exc)) from exc
        if item["name"]==BLANK_TEMPLATE_NAME: raise HTTPException(403,"blank template is immutable")
        return templates.TemplateResponse(request=request,name="template_form.html",context=ctx(request,item=item,error=None))
    @app.post("/templates/{template_id}/edit")
    async def template_save(request: Request,template_id: str):
        form=await request.form(); svc=service(request)
        try: item=svc.update_template(template_id,name=form.get("name"),fields=rows(form))
        except ServiceError as exc: return templates.TemplateResponse(request=request,name="template_form.html",context=ctx(request,item=svc.get_template(template_id),error=message(exc)),status_code=400)
        return RedirectResponse(f"/templates/{item['id']}/edit",status_code=303)
    @app.post("/templates/{template_id}/delete")
    def template_delete(request: Request,template_id: str): service(request).delete_template(template_id); return RedirectResponse("/templates",status_code=303)

    app.mount("/mcp", mcp.streamable_http_app())
    return app


app = create_app()
