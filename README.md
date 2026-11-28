# MedGuard AI

Operations and compliance copilot for clinics on ClinicOS: hybrid RAG + multi-agent
(LangGraph) over each clinic's live records and Indian healthcare regulations, with
per-clinic isolation, citations and LLM-native observability. Built on Google Cloud.

> What it is and isn't: [docs/POSITIONING.md](docs/POSITIONING.md).
>
> Status: **M1: database schemas, roles and Row-Level Security.** See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
> design and [docs/PROPOSAL.md](docs/PROPOSAL.md) for the problem it solves.

## Run locally

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up -d --build        # Postgres + pgvector → migrations → API on :8080
curl localhost:8080/health/ready
```

Or run the API on your machine against the Docker database:

```bash
docker compose up -d db
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8080
```

## Tests

```bash
uv run pytest                       # unit tests, no Docker needed
uv run pytest -m integration        # needs `docker compose up -d db`; builds throwaway DBs
uv run ruff check .
```
