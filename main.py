import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from products.mcp import create_mcp_server
from products.persistence import MongoPersistence
from products.services import ApplicationService


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
        client = (
            mongo_client
            if mongo_client is not None
            else MongoClient(settings.mongodb_uri)
        )
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

    @app.get("/health")
    @app.get("/health/ready")
    def readiness(request: Request) -> dict[str, str]:
        try:
            request.app.state.mongo_client.admin.command("ping")
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB unavailable") from exc
        return {"status": "ok"}

    app.mount("/mcp", mcp.streamable_http_app())
    return app


app = create_app()
