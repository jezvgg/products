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

`/health` and `/health/ready` return `{"status":"ok"}` only when MongoDB responds to `ping`; otherwise they return HTTP 503. The application owns its production-created `MongoClient` and closes it during shutdown. Tests pass a separate database through `Settings`, while `TEST_MONGODB_URI` selects a disposable server.
