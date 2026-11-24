# CLAUDE.md — MedGuard AI

Read this first in every session. It records the current state of the project, the
decisions made so far, and the rules. **Update it in the same change as any code
change**: tick the build log, record new decisions, and add failures to the log.

## What this is
MedGuard AI is an operations and compliance copilot for clinics on ClinicOS. It
answers plain-language questions from the clinic's own live records (synced from
ClinicOS), from regulatory documents, or from both, with citations and strict per-
clinic isolation. It never gives clinical advice.

**Positioning (decided 2026-11-23):** independent and multi-doctor clinics in India, the
segment ClinicOS is built for. Not hospitals: ClinicOS has no admissions, departments or
insurance claims, so a hospital pitch would promise answers with no data behind them.
ClinicOS has no multi-branch chains either. The enterprise customer is the **ClinicOS
platform** (many clinic tenants), and the clinics are its tenants. Hospitals are a stated
future extension, not a claim.

- **Owner:** Meher. Claude works as the junior engineer: one module at a time, each
  explained (what it does, why, what alternatives were considered), and Meher signs
  off before the next module starts. Never generate the whole codebase in one go.
- **Goal:** enterprise quality. **Target: core v1 (M0–M11) deployed with real eval numbers by
  ~2026-12-03.** M12–M16 (20-clinic simulator, full ~120–230-doc corpus, load/chaos, scorecard) follow.
  Resume claims cover only what is actually done and measured.
- **GitHub:** `origin` = github.com/thelabscatalyst-ui/Med-Gaurd. The folder and repo are
  spelled "Gaurd"; the product is "MedGuard". Leave the folder name alone.

## Source-of-truth docs (read in this order)
1. `docs/ARCHITECTURE.md`: **the current design (v2)**, built against the real ClinicOS
   code. If code and this doc disagree, fix one of them in the same change.
2. `docs/POSITIONING.md`: one page on what MedGuard is and isn't. Keep every claim
   consistent with it.
3. `docs/PROPOSAL.md`: the discovery brief and solution proposal (FDE-style).
4. `docs/THREAT_MODEL.md`: threats, defences, tests and status. Update it in every module.
5. `docs/MIGRATIONS.md`: migration rules, techniques and the review checklist.
6. `docs/INTEGRATION_CONTRACT.md`: *(to be written in M2)* the exact ClinicOS ↔
   MedGuard contract.
7. `Medguard Architecture.md` and `Project Structure.txt`: the original Gemini
   blueprint. **Superseded and kept for history only.** It assumed a hospital system
   (departments, pre-auth, admissions) that ClinicOS doesn't have.

## ClinicOS facts that drive the design (verified 2026-11-20)
- Lives at `/Users/apple/Desktop/ClinicOS`. The brand is now "Med Track" (formerly
  Nivora). It is a separate repo with its own CLAUDE.md and rules. Never edit it from a
  MedGuard session without Meher asking.
- **Its production data is tester-entered, not real patients** (Meher, 2026-11-20).
  MedGuard syncs live ClinicOS production data, but treats it as real PHI anyway.
- FastAPI + Jinja2 + SQLAlchemy 2.0. Postgres on Railway (pg8000 driver); SQLite
  locally. It mostly serves HTML pages, with about 50 JSON responses.
- **Auth:** HS256 JWT in the `access_token` HTTP-only cookie (60 min, `tv` claim for
  revocation), `get_current_doctor` in `services/auth_service.py`. PIN gate
  (`pin_session` cookie, `require_pin`) protects billing, income and reports.
- **Tenancy:** each row has `doctor_id` and a nullable `clinic_id` (null = solo doctor).
  Roles are on `clinic_doctors.role`: `owner` | `associate`. Scoping is done by
  `scope_to_active_clinic` in `services/clinic_context.py`. Clinic-wide admin is
  owner-only (`require_clinic_owner_context`).
- **CSP** is set in `main.py`. Any new origin (the MedGuard API) must be added to
  `connect-src`, or the browser silently blocks it.
- **Schema:** clinics, doctors, clinic_doctors, patients, appointments, visits,
  bills, bill_items, prescriptions, prescription_items, expenses, recurring_expenses,
  price_catalog, feedback, patient_documents (R2 vault). There is no department, no
  ICD code, no claim or pre-auth status, and most tables have no `updated_at`.
- Don't read rows from ClinicOS's `clinic.db` or its production database from a
  Claude session. Schema only. MedGuard's sync job is the only reader.

## Decisions
**ACCEPTED** = Meher agreed. **PROPOSED** = awaiting Meher's review.
D3–D10 accepted 2026-11-23 (Meher read the v2 docs and said to start building).
- **ACCEPTED D1. Live ClinicOS data, not a toy dataset.** Source = ClinicOS production
  (tester-entered). Built as if it were real PHI. Resume and README describe it
  honestly as pilot accounts with realistic test data.
- **ACCEPTED D2. ClinicOS and MedGuard stay separate projects.** They meet only at a
  thin contract: a read-only DB role, short-lived signed tokens with JWKS, and an
  embedded panel plus a CSP line.
- **ACCEPTED D3. Identity:** ClinicOS mints 5-minute Ed25519 JWTs (`aud=medguard`), and
  MedGuard verifies them with the public key. ClinicOS's HS256 `SECRET_KEY` is never
  shared.
- **ACCEPTED D4. Data:** a scheduled sync (Cloud Run Job every 5 min, snapshot + hash
  diff) into MedGuard's own Postgres. The agent never queries ClinicOS production. CDC
  is the v2 path.
- **ACCEPTED D5. Tenant isolation through Postgres RLS**, which mirrors ClinicOS's
  scoping, plus scopes (`ops:read`, `finance:read` only with a PIN, `clinic:admin` for
  owners).
- **ACCEPTED D6. Pseudonymize at sync, re-identify after the answer.** The LLM never
  sees names or phones. Free-text notes are not synced in v1.
- **ACCEPTED D7. Four-layer SQL guard:** sqlglot AST → read-only transaction with a
  timeout → `agent_ro` role on `semantic` views → RLS.
- **ACCEPTED D8. GCP-native:** Cloud Run, Cloud SQL + pgvector, Vertex AI in
  `asia-south1`, Secret Manager, Cloud Trace. Model IDs come from config and are
  verified at build time. `text-embedding-004` is shut down.
- **ACCEPTED D9. Scope = operational and compliance Q&A only.** Clinical advice is
  refused (avoids medical-device territory).
- **ACCEPTED D10. Eval-driven:** the golden set includes tenant-leakage and
  adversarial cases; CI gates on thresholds.
- **ACCEPTED D11. Split chunk storage for scale:** `global_chunks` (HNSW +
  iterative scan) and `tenant_chunks` (exact search, no vector index), stored as
  `halfvec(768)` with `embedding_model` recorded. See ARCHITECTURE §7.4.
- **ACCEPTED D13. Tenancy = shared tables + RLS** (2026-11-24). Table-, schema- and
  database-per-clinic were compared and rejected for now: per-clinic tables still need RLS
  for owner vs associate, and multiply migrations and onboarding by the clinic count.
  Future: tiered tenancy (a big customer gets a dedicated DB running the same code).
- **ACCEPTED D14. Migrations = Alembic with raw SQL** (2026-11-24). Rules and techniques
  are in `docs/MIGRATIONS.md`. Migrations run as a separate job, never at app startup.
- **ACCEPTED D15. Threat model is a living doc** (`docs/THREAT_MODEL.md`). Every threat
  gets a defence, a module and a test; update its status column as modules land.
- **ACCEPTED D12. Enterprise readiness is measured, not claimed:** ~120–230 official docs
  (~8k–15k pages; 25–40 by Dec 3), 20 simulated clinics, load and chaos tests, a 6-level question ladder, and a published
  scorecard. See ARCHITECTURE §14.

## Build log (see ARCHITECTURE.md §12 for "done when")
- [x] M0 Skeleton, config, Docker, local Postgres + pgvector, `/health/live` + `/health/ready`
      (2026-11-23, awaiting Meher's review; see FAILURES.md for the pool-recovery bug)
- [ ] M1 MedGuard schemas + RLS + roles (decide the migration tool here)
- [ ] M2 Integration contract doc + ClinicOS read-only role
- [ ] M3 `clinicos-sync` (contract test, pseudonymize, hash-diff)
- [ ] M4 Semantic views
- [ ] M5 Regulatory doc ingestion
- [ ] M6 Hybrid retriever + retrieval eval
- [ ] M7 Records agent (ReAct) + SQL guard
- [ ] M8 LangGraph: supervisor, parallel hybrid, synthesizer, critic, re-identify
- [ ] M9 FastAPI: JWKS auth, SSE query, tracing, metrics, audit
- [ ] M10 ClinicOS side: token endpoint, JWKS, panel, CSP
- [ ] M11 GCP deploy + CI eval gate
- [ ] M12–M16 Simulator, corpus scale-up, load/chaos, scorecard (ARCHITECTURE §12, §14)

## Code map and commands
- `app/main.py`: `create_app()` + lifespan (opens and closes the DB pool)
- `app/config.py`: the only place env vars are read (pydantic-settings). No `@property`.
- `app/db/pool.py`: psycopg 3 async pool with a custom `check` (purge-all-on-first-dead)
- `app/api/health.py`: `/health/live` (no deps) and `/health/ready` (DB + pgvector, 503 when down)
- `tests/`: unit tests need no Docker; `-m integration` needs `docker compose up -d db`
- `infra/db/init/`: local-only Docker init SQL. Migrations take over in M1.
```bash
uv sync                                  # Python 3.12 venv from uv.lock
uv run pytest && uv run pytest -m integration
uv run ruff check . && uv run ruff format --check .
docker compose up -d --build             # db on :5433, api on :8080
```
- Local DB is on host port **5433**, not 5432. Image pinned to `pgvector/pgvector:0.8.0-pg16`.
- Dependencies are added with `uv add` only, and only well-known packages. Don't add a
  package just because a warning suggests it. (Starlette's TestClient warns "install
  httpx2"; we kept `httpx` because the suggested name couldn't be verified.)

## Rules
- Explain before building. Every module's commit message says what it does and why.
- Never commit secrets. `.env` stays out of git; `.env.example` holds placeholders only.
- Verify, don't assert: claims of "works" come with a command and its output.
- **Meher does all git commits and pushes. Claude never runs `git commit` or `git push`.**
  Read-only git is fine. At the end of a module, suggest a commit message.
- Record every failure in `docs/FAILURES.md` (create it with the first module):
  symptom → root cause → fix → lesson.

## Change log
- 2026-11-20: CLAUDE.md created after reading the Gemini blueprint and the ClinicOS schema.
- 2026-11-20: Architecture v2 (`docs/ARCHITECTURE.md`) and proposal (`docs/PROPOSAL.md`)
  written against ClinicOS's real auth, tenancy and schema. Gemini docs superseded.
  D1 and D2 accepted by Meher. No code yet.
- 2026-11-23: D3–D12 accepted (scale + readiness plan folded into ARCHITECTURE §7.4, §14).
  M0 built: uv/pyproject, FastAPI app factory, settings, psycopg pool, health endpoints,
  multi-stage Dockerfile (non-root), compose with pgvector 0.8.0, 5 tests. Found and fixed
  the DB-restart pool bug (FAILURES.md); verified first request after a restart → 200 in 6 ms.
- 2026-11-23: Added docs/POSITIONING.md and ARCHITECTURE §15 (scaling path to 1,000 clinics).
  The scaling section is knowledge only: no build-plan changes (C6 stays "later").
- 2026-11-24: D13–D15 accepted. Added docs/MIGRATIONS.md and docs/THREAT_MODEL.md (27 threats,
  10 self-evaluation attacks). ARCHITECTURE §5.2: consistent-snapshot sync + new-patient handling.
