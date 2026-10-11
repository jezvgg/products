import os
from collections.abc import Iterator

import pytest
from testcontainers.community.mongodb import MongoDbContainer


@pytest.fixture(scope="session")
def mongodb_uri() -> Iterator[str]:
    configured_uri = os.getenv("TEST_MONGODB_URI")
    if configured_uri:
        yield configured_uri
        return

    try:
        mongo = MongoDbContainer("mongo:7")
        mongo.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MongoDB test strategy unavailable: {exc}")
        return

    try:
        yield mongo.get_connection_url()
    finally:
        mongo.stop()
