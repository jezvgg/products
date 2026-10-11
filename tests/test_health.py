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


def test_readiness_recovers_once_mongodb_is_reachable(mongodb_uri: str, monkeypatch) -> None:
    """A failed startup must not poison readiness forever."""
    original = MongoPersistence.initialize
    calls = {"count": 0}

    def flaky(self) -> None:
        calls["count"] += 1
        if calls["count"] <= 2:  # fails during startup and the first readiness probe
            raise RuntimeError("mongo was not up yet")
        original(self)

    monkeypatch.setattr(MongoPersistence, "initialize", flaky)
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_recovery_{uuid4().hex}",
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/ready").status_code == 200

    assert calls["count"] >= 2
