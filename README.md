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

## Deployment

Compose runs the app and MongoDB on an internal network. MongoDB has no host port; the app is bound to `127.0.0.1:8000` by default for a host-installed Caddy reverse proxy.

```sh
cp .env.example .env
openssl rand -hex 32  # put output in MCP_TOKEN in .env
docker compose up -d --build
```

Point Caddy at `127.0.0.1:8000`. Keep UI authentication in Caddy; the app does not embed Caddy authentication. Route the MCP endpoint through the same HTTPS app path without a browser-auth challenge so its `Authorization: Bearer` token reaches the app.

Rotate the MCP token by replacing `MCP_TOKEN` in `.env` with a new `openssl rand -hex 32` value, then recreate the app:

```sh
docker compose up -d --force-recreate app
```

MCP clients use `/mcp/` with `Authorization: Bearer <MCP_TOKEN>`. Never commit `.env` or put the token in a URL or log.
