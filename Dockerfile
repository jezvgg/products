# syntax=docker/dockerfile:1
FROM python:3.12-alpine AS python-base

FROM python-base AS builder
RUN python -m pip install --no-cache-dir uv==0.9.7

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy

# PyMongo has no musllinux wheel in the current lock and builds its optional C extension.
RUN apk add --no-cache --virtual .build-deps build-base

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY main.py ./
COPY products ./products

FROM python-base AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/main.py ./main.py
COPY --from=builder /app/products ./products

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
