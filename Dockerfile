# syntax=docker/dockerfile:1.7
#
# Two stages:
#   builder — has uv, resolves and installs dependencies into /app/.venv
#   runtime — slim Python + the venv + our code. No uv, no compilers, no cache.
# The runtime image is smaller (faster Cloud Run cold starts) and has less attack surface.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, code second: a code-only change reuses the cached dependency layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY app ./app

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 medguard
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080
# Never run as root inside the container.
USER medguard
EXPOSE 8080
# Cloud Run injects $PORT; `exec` makes uvicorn PID 1 so it receives SIGTERM and shuts
# down gracefully (closing the DB pool) instead of being killed mid-request.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
