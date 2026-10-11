from uuid import uuid4

from fastapi.testclient import TestClient

from main import Settings, create_app
from products.persistence import MongoPersistence


def test_readiness_reports_application_and_mongodb_health(mongodb_uri: str) -> None:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_test_{uuid4().hex}",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_initialization_failure(mongodb_uri: str, monkeypatch) -> None:
    def fail_initialize(self) -> None:
        raise RuntimeError("schema setup failed")

    monkeypatch.setattr(MongoPersistence, "initialize", fail_initialize)
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_failed_init_{uuid4().hex}",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "MongoDB initialization failed"}
