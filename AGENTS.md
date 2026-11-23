# AGENTS.md — MedGuard AI

Read this first in every session. It records the current state of the project, the
decisions made so far, and the rules. **Update it in the same change as any code
change**: tick the build log, record new decisions, and add failures to the log.

## What this is
MedGuard AI is an operations and compliance copilot for clinics on ClinicOS. It
answers plain-language questions from the clinic's own live records (synced from
ClinicOS), from regulatory documents, or from both, with citations and strict per-
clinic isolation. It never gives clinical advice.

- **Owner:** Meher. Codex works as the junior engineer: one module at a time, each
  explained (what it does, why, what alternatives were considered), and Meher signs
  off before the next module starts. Never generate the whole codebase in one go.
- **Goal:** enterprise quality over speed. There is no fixed deadline.
- **GitHub:** `origin` = github.com/thelabscatalyst-ui/Med-Gaurd. The folder and repo are
  spelled "Gaurd"; the product is "MedGuard". Leave the folder name alone.

## Source-of-truth docs (read in this order)
1. `docs/ARCHITECTURE.md`: **the current design (v2)**, built against the real ClinicOS
   code. If code and this doc disagree, fix one of them in the same change.
2. `docs/PROPOSAL.md`: the discovery brief and solution proposal (FDE-style).
3. `docs/INTEGRATION_CONTRACT.md`: *(to be written in M2)* the exact ClinicOS ↔
   MedGuard contract.
4. `Medguard Architecture.md` and `Project Structure.txt`: the original Gemini
   blueprint. **Superseded and kept for history only.** It assumed a hospital system
   (departments, pre-auth, admissions) that ClinicOS doesn't have.

## ClinicOS facts that drive the design (verified 2026-11-20)
- Lives at `/Users/apple/Desktop/ClinicOS`. The brand is now "Med Track" (formerly
  Nivora). It is a separate repo with its own AGENTS.md and rules. Never edit it from a
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
  Codex session. Schema only. MedGuard's sync job is the only reader.

## Decisions
**ACCEPTED** = Meher agreed. **PROPOSED** = awaiting Meher's review.
- **ACCEPTED D1. Live ClinicOS data, not a toy dataset.** Source = ClinicOS production
  (tester-entered). Built as if it were real PHI. Resume and README describe it
  honestly as pilot accounts with realistic test data.
- **ACCEPTED D2. ClinicOS and MedGuard stay separate projects.** They meet only at a
  thin contract: a read-only DB role, short-lived signed tokens with JWKS, and an
  embedded panel plus a CSP line.
- **PROPOSED D3. Identity:** ClinicOS mints 5-minute Ed25519 JWTs (`aud=medguard`), and
  MedGuard verifies them with the public key. ClinicOS's HS256 `SECRET_KEY` is never
  shared.
- **PROPOSED D4. Data:** a scheduled sync (Cloud Run Job every 5 min, snapshot + hash
  diff) into MedGuard's own Postgres. The agent never queries ClinicOS production. CDC
  is the v2 path.
- **PROPOSED D5. Tenant isolation through Postgres RLS**, which mirrors ClinicOS's
  scoping, plus scopes (`ops:read`, `finance:read` only with a PIN, `clinic:admin` for
  owners).
- **PROPOSED D6. Pseudonymize at sync, re-identify after the answer.** The LLM never
  sees names or phones. Free-text notes are not synced in v1.
- **PROPOSED D7. Four-layer SQL guard:** sqlglot AST → read-only transaction with a
  timeout → `agent_ro` role on `semantic` views → RLS.
- **PROPOSED D8. GCP-native:** Cloud Run, Cloud SQL + pgvector, Vertex AI in
  `asia-south1`, Secret Manager, Cloud Trace. Model IDs come from config and are
  verified at build time. `text-embedding-004` is shut down.
- **PROPOSED D9. Scope = operational and compliance Q&A only.** Clinical advice is
  refused (avoids medical-device territory).
- **PROPOSED D10. Eval-driven:** the golden set includes tenant-leakage and
  adversarial cases; CI gates on thresholds.

## Build log (see ARCHITECTURE.md §12 for "done when")
- [ ] M0 Skeleton, config, Docker, local Postgres + pgvector, `/health`
- [ ] M1 MedGuard schemas + RLS + roles
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

## Rules
- Explain before building. Every module's commit message says what it does and why.
- Never commit secrets. `.env` stays out of git; `.env.example` holds placeholders only.
- Verify, don't assert: claims of "works" come with a command and its output.
- Commit per logical change. Never push unless Meher asks.
- Record every failure in `docs/FAILURES.md` (create it with the first module):
  symptom → root cause → fix → lesson.

## Change log
- 2026-11-20: AGENTS.md created after reading the Gemini blueprint and the ClinicOS schema.
- 2026-11-20: Architecture v2 (`docs/ARCHITECTURE.md`) and proposal (`docs/PROPOSAL.md`)
  written against ClinicOS's real auth, tenancy and schema. Gemini docs superseded.
  D1 and D2 accepted by Meher. No code yet.
