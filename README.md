# Products

Minimal FastAPI application for household cards.

## Requirements

- Python 3.12+
- `uv`
- MongoDB for local runs
- Docker for the default integration-test strategy

## Configuration

| Variable | Default |
| --- | --- |
| `MONGODB_URI` | `mongodb://127.0.0.1:27017` |
| `MONGODB_DATABASE` | `products` |
| `MCP_TOKEN` | empty; MCP requests are rejected |
| `UI_PASSWORD` | empty; the UI answers 503 (sign-in is impossible, nothing is served) |
| `TEST_MONGODB_URI` | unset; starts MongoDB with Testcontainers |

## Development

```sh
uv sync
uv run uvicorn main:app --reload
uv run pytest
```

Set `TEST_MONGODB_URI` to an existing disposable MongoDB when Docker is unavailable:

```sh
TEST_MONGODB_URI=mongodb://127.0.0.1:27017 uv run pytest
```

`/health` and `/health/ready` return `{"status":"ok"}` only when MongoDB initialization succeeds and responds to `ping`; otherwise they return HTTP 503. Readiness retries initialization, so a MongoDB that becomes available after startup is picked up without restarting the app. The application owns its production-created `MongoClient` and closes it during shutdown. Tests pass a separate database through `Settings`, while `TEST_MONGODB_URI` selects a disposable server.

## Authentication

The UI is gated by a password form, not by a reverse-proxy challenge:

- `UI_PASSWORD` is the only credential. Unset, the app serves nothing: `/login` answers `503` and every other UI route redirects into it.
- Signing in sets one cookie, `products_session`, that carries its own expiry signed with an HMAC keyed by `UI_PASSWORD`. No session store exists, and rotating the password invalidates every session.
- A session lasts 30 days. The cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` when the request reached the app over HTTPS.
- `GET /logout` drops the cookie. The "continue here" link after login only accepts local paths.
- Exempt from the session: `/login`, `/logout`, `/static/*`, `/health`, `/health/ready`, and `/mcp` (which keeps its own Bearer token). Everything else needs the cookie: unauthenticated `POST`s get `401`, while `GET`s are redirected to the login form with the original path and query preserved.

## Deployment

Compose runs the app and MongoDB on an internal network. MongoDB has no host port; the app is bound to `127.0.0.1:8000` by default for a host-installed Caddy reverse proxy.

```sh
cp .env.example .env
openssl rand -hex 32    # put output in MCP_TOKEN in .env
openssl rand -base64 18 # put output in UI_PASSWORD in .env
docker compose up -d --build
```

Point Caddy at `127.0.0.1:8000`; terminating TLS is all it does — no `basic_auth`, because the app gates its own UI (see *Authentication*). Route the MCP endpoint through the same HTTPS path; it is exempt from the UI session and authenticates its own requests.

Rotate the MCP token by replacing `MCP_TOKEN` in `.env` with a new `openssl rand -hex 32` value, then recreate the app:

```sh
docker compose up -d --force-recreate app
```

MCP clients use `/mcp/` with `Authorization: Bearer <MCP_TOKEN>`. Never commit `.env` or put the token in a URL or log.
