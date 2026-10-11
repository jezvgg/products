import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from testcontainers.mongodb import MongoDbContainer

from main import Settings, create_app


@pytest.fixture(scope="session")
def mongodb_uri() -> Iterator[str]:
    configured_uri = os.getenv("TEST_MONGODB_URI")
    if configured_uri:
        yield configured_uri
        return

    mongo = MongoDbContainer("mongo:7")
    try:
        mongo.start()
    except Exception as exc:
        pytest.skip(f"MongoDB test strategy unavailable: {exc}")

    try:
        yield mongo.get_connection_url()
    finally:
        mongo.stop()


def test_readiness_reports_application_and_mongodb_health(mongodb_uri: str) -> None:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_test_{uuid4().hex}",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
