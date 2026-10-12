"""Application entrypoint: configuration, lifecycle, and the ASGI app.

The HTML routes live in :mod:`products.web`; this module owns configuration,
the MongoDB lifecycle, the same-origin guard, health reporting, and mounting.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from products import auth
from products.mcp import create_mcp_server
from products.persistence import MongoPersistence
from products.services import ApplicationService, ServiceError
from products.web import build_router, error_response

DEFAULT_MONGODB_URI = "mongodb://127.0.0.1:27017"
DEFAULT_MONGODB_DATABASE = "products"
DEFAULT_MCP_TOKEN = ""
DEFAULT_UI_PASSWORD = ""


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration, read from the environment in production.

    The defaults live in module constants on purpose: with ``slots=True`` the
    class attributes are slot descriptors, so ``cls.mongodb_uri`` is *not* the
    default value and must never be used as an ``os.getenv`` fallback.
    """

    mongodb_uri: str = DEFAULT_MONGODB_URI
    mongodb_database: str = DEFAULT_MONGODB_DATABASE
    mcp_token: str = DEFAULT_MCP_TOKEN
    ui_password: str = DEFAULT_UI_PASSWORD

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            mongodb_uri=os.getenv("MONGODB_URI", DEFAULT_MONGODB_URI),
            mongodb_database=os.getenv("MONGODB_DATABASE", DEFAULT_MONGODB_DATABASE),
            mcp_token=os.getenv("MCP_TOKEN", DEFAULT_MCP_TOKEN),
            ui_password=os.getenv("UI_PASSWORD", DEFAULT_UI_PASSWORD),
        )


def create_app(
    settings: Settings | None = None,
    mongo_client: MongoClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    app: FastAPI
    mcp = create_mcp_server(lambda: app.state.service, settings.mcp_token)

    def initialize(state: Any) -> None:
        """(Re)apply the MongoDB schema, remembering failures for readiness."""
        try:
            state.persistence.initialize()
        except Exception as exc:  # noqa: BLE001 - readiness must expose init failures
            state.persistence_error = exc
        else:
            state.persistence_error = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = mongo_client if mongo_client is not None else MongoClient(settings.mongodb_uri)
        app.state.mongo_client = client
        app.state.mongo_database = client[settings.mongodb_database]
        app.state.persistence = MongoPersistence(app.state.mongo_database)
        app.state.service = ApplicationService(app.state.persistence)
        app.state.persistence_error = None
        initialize(app.state)
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
            scheme = (
                request.headers.get("x-forwarded-proto", request.url.scheme)
                .split(",", 1)[0]
                .strip()
            )
            expected = f"{scheme}://{request.url.netloc}"
            if origin != expected:
                return PlainTextResponse("same-origin request required", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def require_ui_session(request: Request, call_next: Any):
        """Gate every HTML route behind the login cookie (see :mod:`products.auth`)."""
        if auth.is_exempt(request.url.path) or auth.token_is_valid(
            settings.ui_password, request.cookies.get(auth.COOKIE_NAME)
        ):
            return await call_next(request)
        if request.method in ("GET", "HEAD"):
            target = request.url.path
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(
                f"{auth.LOGIN_PATH}?next={quote(target, safe='')}", status_code=303
            )
        # Browsers only post forms from a page they were allowed to open; anything
        # else (a stale tab, curl) is told to authenticate instead of being shown
        # an error page it cannot act on.
        return PlainTextResponse("authentication required", status_code=401)

    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, exc: ServiceError) -> HTMLResponse:
        # Routes that can render a useful form handle their own errors; anything
        # that escapes (for example a storage failure while listing templates)
        # still gets a readable page instead of a bare 500.
        return error_response(request, exc)

    app.mount("/static", StaticFiles(directory="products/static"), name="static")

    @app.get("/health")
    @app.get("/health/ready")
    def readiness(request: Request) -> dict[str, str]:
        state = request.app.state
        if state.persistence_error is not None:
            # MongoDB may have become reachable since startup: retry the schema
            # setup instead of reporting a permanently failed readiness probe.
            initialize(state)
            if state.persistence_error is not None:
                raise HTTPException(status_code=503, detail="MongoDB initialization failed")
        try:
            state.mongo_client.admin.command("ping")
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB unavailable") from exc
        return {"status": "ok"}

    app.include_router(build_router(settings.ui_password))
    app.mount("/mcp", mcp.streamable_http_app())
    return app


app = create_app()
