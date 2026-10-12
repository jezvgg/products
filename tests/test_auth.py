import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from main import Settings, create_app
from products import auth

PASSWORD = "test-password"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def auth_settings(mongodb_uri: str) -> Settings:
    return Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_auth_{uuid4().hex}",
        ui_password=PASSWORD,
    )


@pytest.fixture
def anonymous(auth_settings: Settings) -> Iterator[TestClient]:
    """A client that has not logged in and does not follow redirects."""
    with TestClient(create_app(auth_settings), headers=ORIGIN, follow_redirects=False) as client:
        yield client


def test_session_token_round_trip() -> None:
    token = auth.issue_token(PASSWORD)

    assert auth.token_is_valid(PASSWORD, token)


def test_expired_token_is_rejected() -> None:
    token = auth.issue_token(PASSWORD, now=time.time() - auth.SESSION_TTL_SECONDS - 1)

    assert not auth.token_is_valid(PASSWORD, token)


def test_token_is_rejected_after_the_password_changes() -> None:
    assert not auth.token_is_valid("another-password", auth.issue_token(PASSWORD))


def test_forged_and_malformed_tokens_are_rejected() -> None:
    expires, signature = auth.issue_token(PASSWORD).split(".")

    assert not auth.token_is_valid(PASSWORD, f"{int(expires) + 3600}.{signature}")
    assert not auth.token_is_valid(PASSWORD, f"{expires}.{'0' * len(signature)}")
    assert not auth.token_is_valid(PASSWORD, expires)
    assert not auth.token_is_valid(PASSWORD, "not-a-token")
    assert not auth.token_is_valid(PASSWORD, None)
    assert not auth.token_is_valid("", auth.issue_token(PASSWORD))


def test_password_check_never_matches_without_a_configured_password() -> None:
    assert not auth.password_matches("", "")
    assert not auth.password_matches(PASSWORD, "wrong")
    assert auth.password_matches(PASSWORD, PASSWORD)


def test_login_continues_only_at_local_paths() -> None:
    assert auth.safe_next("/templates?id=1") == "/templates?id=1"
    assert auth.safe_next("//evil.example/cards") == "/cards"
    assert auth.safe_next("https://evil.example") == "/cards"
    assert auth.safe_next("") == "/cards"
    assert auth.safe_next(None) == "/cards"


def test_exempt_paths_are_the_login_form_static_assets_mcp_and_health() -> None:
    exempt = ("/login", "/logout", "/static/app.js", "/mcp", "/mcp/", "/health", "/health/ready")
    for path in exempt:
        assert auth.is_exempt(path), path
    for path in ("/cards", "/cards/new", "/templates", "/"):
        assert not auth.is_exempt(path), path


def test_anonymous_visitor_is_sent_to_the_login_form(anonymous: TestClient) -> None:
    response = anonymous.get("/cards")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fcards"


def test_anonymous_visitor_keeps_the_requested_page_and_query(anonymous: TestClient) -> None:
    response = anonymous.get("/cards", params={"q": "milk"})

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fcards%3Fq%3Dmilk"


def test_anonymous_posts_are_refused_instead_of_redirected(anonymous: TestClient) -> None:
    response = anonymous.post("/cards/whatever/delete")

    assert response.status_code == 401
    assert response.text == "authentication required"


def test_public_endpoints_stay_reachable_without_a_session(anonymous: TestClient) -> None:
    assert anonymous.get("/login").status_code == 200
    assert anonymous.get("/health/ready").status_code == 200
    assert anonymous.post("/mcp/", json={"jsonrpc": "2.0", "id": 1}).status_code == 401


def test_wrong_password_keeps_the_visitor_out(anonymous: TestClient) -> None:
    response = anonymous.post("/login", data={"password": "wrong", "next": "/cards"})

    assert response.status_code == 401
    assert "Wrong password" in response.text
    assert auth.COOKIE_NAME not in response.cookies


def test_login_sets_the_session_cookie_and_opens_the_ui(anonymous: TestClient) -> None:
    response = anonymous.post("/login", data={"password": PASSWORD, "next": "/templates"})

    assert response.status_code == 303
    assert response.headers["location"] == "/templates"
    assert auth.COOKIE_NAME in response.cookies
    assert anonymous.get("/templates").status_code == 200


def test_login_ignores_an_external_redirect_target(anonymous: TestClient) -> None:
    response = anonymous.post("/login", data={"password": PASSWORD, "next": "//evil.example"})

    assert response.headers["location"] == "/cards"


def test_logout_closes_the_session(anonymous: TestClient) -> None:
    anonymous.post("/login", data={"password": PASSWORD, "next": "/cards"})

    response = anonymous.get("/logout")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert anonymous.get("/cards").status_code == 303


def test_ui_fails_closed_when_no_password_is_configured(mongodb_uri: str) -> None:
    settings = Settings(
        mongodb_uri=mongodb_uri,
        mongodb_database=f"products_auth_unset_{uuid4().hex}",
    )

    with TestClient(create_app(settings), headers=ORIGIN, follow_redirects=False) as client:
        assert client.get("/cards").status_code == 303
        login = client.get("/login")

    assert login.status_code == 503
    assert login.text == "UI_PASSWORD is not configured"
