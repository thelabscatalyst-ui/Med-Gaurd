# MedGuard AI — Architecture v2

> Status: **draft for Meher's review** (2026-11-20). This replaces the Gemini blueprint
> (`Medguard Architecture.md`, kept for history). That blueprint assumed a hospital
> system; this version is designed against the real ClinicOS codebase.

---

## 1. What MedGuard is, and what it is not

MedGuard is an **operations and compliance copilot** for clinics that run on ClinicOS.
A doctor or clinic owner asks a question in plain language. MedGuard answers from two
sources, with citations, and only from data that person is allowed to see:

| Source | Examples | How it's answered |
|---|---|---|
| **The clinic's own live records** (from ClinicOS) | "How many no-shows did I have this month vs last?", "Which bills are still partly unpaid?", "What's my average wait time on Mondays?" | Generated SQL over a curated, tenant-scoped copy |
| **Regulations, policies, SOPs** | "How long must I keep prescription records?", "What does the DPDP Act require when a patient asks to delete their data?" | Hybrid retrieval over documents |
| **Both together** | "Which prescriptions this month included Schedule H1 drugs, and what register must I keep for them?" | Both run in parallel, then one cited answer |

**Out of scope, deliberately:** diagnosis, treatment, dosing or any clinical advice.
Clinical decision support can turn software into a regulated medical device in India.
MedGuard answers *operational and compliance* questions only, and the router refuses
clinical ones. This boundary is a product decision, not a limitation to hide.

---

## 2. The core idea: two independent systems and one thin contract

ClinicOS and MedGuard are **separate projects**, with separate repos, deploys and
databases. Neither imports the other's code. They meet at exactly three points: an
**identity contract**, a **data contract** and a **UI embed**.

```text
┌──────────────────────── ClinicOS (Railway) ────────────────────────┐
│  System of record. Owns users, login, PIN, roles, clinic data.     │
│                                                                    │
│  FastAPI + Jinja ── Postgres (prod) ── R2 vault                    │
│     │                    │                                         │
│     │ (1) IDENTITY       │ (2) DATA                                │
│     │ mints a 5-min      │ read-only role                          │
│     │ MedGuard token     │ `medguard_reader`                       │
│     │ (Ed25519 JWT)      │                                         │
│     │ + JWKS endpoint    │                                         │
│     │                    │                                         │
│     │ (3) UI: "Ask MedGuard" panel in the dashboard                │
└─────┼────────────────────┼─────────────────────────────────────────┘
      │ Bearer token       │ TLS, SELECT only
      ▼                    ▼
┌──────────────────────── MedGuard (Google Cloud) ───────────────────┐
│                                                                    │
│  Cloud Run: medguard-api ◄── verifies token with ClinicOS JWKS     │
│     │   LangGraph agents · guardrails · tracing                    │
│     ▼                                                              │
│  Cloud SQL Postgres + pgvector (MedGuard's OWN database)           │
│     ops (synced) · semantic views · knowledge · identity_vault     │
│     · telemetry · audit          ▲                                 │
│                                  │                                 │
│  Cloud Run Job: clinicos-sync ───┘  (Cloud Scheduler, every 5 min) │
│  Cloud Run Job: doc-ingest          (on upload / on demand)        │
│  Vertex AI: Gemini (LLM) + Gemini embeddings                       │
│  Secret Manager · Cloud Logging · Cloud Trace                      │
└────────────────────────────────────────────────────────────────────┘
```

**Why not merge them?** Merging would mean an LLM workload sharing a process and a
database with the clinic's front desk. A slow agent query would then slow the token
queue, and every ClinicOS deploy would also redeploy the AI stack. Kept separate,
MedGuard can fail, scale or be swapped without touching ClinicOS. This is also how a
Forward Deployed Engineer integrates with a customer's existing system: you don't get
to rewrite their app; you attach to it through narrow, auditable interfaces.

**Why not let MedGuard query ClinicOS production directly?** See §5.1.

---

## 3. The integration contract (what ClinicOS has to add)

These are all the changes on the ClinicOS side. They are done in the ClinicOS repo,
under ClinicOS's own rules (tests green, no push without asking). Expect about one
router file, one template partial and one CSP line.

| # | ClinicOS change | Purpose |
|---|---|---|
| C1 | Postgres role `medguard_reader`: `SELECT` on the listed tables only, `CONNECTION LIMIT 2` | The data pipe. Can't write, can't see auth tables |
| C2 | Ed25519 signing key (env var) + `GET /.well-known/jwks.json` | MedGuard verifies tokens with the **public** key only |
| C3 | `POST /medguard/token` (cookie-authenticated, CSRF-protected) | Mints a 5-minute token for the logged-in doctor |
| C4 | "Ask MedGuard" panel (template partial + JS) | The copilot UI inside the dashboard |
| C5 | Add the MedGuard API origin to the CSP `connect-src` in `main.py` | Without it the browser silently blocks the calls (ClinicOS footgun #3) |
| C6 *(later)* | `updated_at` columns on synced tables | Lets sync go incremental instead of snapshot (§5.2) |
| C7 *(later)* | `/medguard/authorize` redirect flow | "Sign in with ClinicOS" for a standalone MedGuard console |

**What ClinicOS never does:** share its `SECRET_KEY` (HS256). If MedGuard held that
key, it could forge ClinicOS login sessions. With a separate asymmetric key, MedGuard
can verify tokens but never mint them.

The contract is written down in `docs/INTEGRATION_CONTRACT.md`, versioned. MedGuard
has a **contract test** that runs before every sync: if a column MedGuard depends on is
missing or changed type, the sync stops and alerts instead of quietly loading
garbage.

---

## 4. Identity and authorization

### 4.1 Request flow

```text
Doctor's browser          ClinicOS                         MedGuard API
      │  clicks "Ask"         │                                  │
      │──POST /medguard/token▶│ checks access_token cookie,      │
      │   (cookie + CSRF)     │ active clinic, role, PIN state   │
      │◀──── 5-min JWT ───────│                                  │
      │                                                          │
      │──── POST /v1/query  (Authorization: Bearer <JWT>) ──────▶│
      │                                  verify signature (JWKS, │
      │                                  cached), aud, exp, iss  │
      │                                  → TenantContext         │
      │◀──────────── streamed, cited answer (SSE) ───────────────│
```

### 4.2 Token claims

```json
{
  "iss": "https://www.clinicos.store",
  "aud": "medguard",
  "sub": "doctor:42",
  "clinic_id": 7,            // null for a solo doctor with no clinic
  "role": "owner",           // owner | associate | solo
  "scopes": ["ops:read", "finance:read", "docs:write"],
  "plan": "clinic",          // drives MedGuard quota
  "tv": 3,                   // ClinicOS token_version, for audit
  "jti": "uuid", "iat": 0, "exp": 0
}
```

### 4.3 MedGuard never grants more than ClinicOS would

The scopes copy ClinicOS's own rules (`services/clinic_context.py`, `auth_service.py`):

| ClinicOS rule | MedGuard equivalent |
|---|---|
| Every query is filtered on `doctor_id`, then on the active `clinic_id` if there is one (`scope_to_active_clinic`) | The same predicate is enforced by **Row-Level Security** (§6.2), not by the prompt |
| Billing, income and reports sit behind the PIN gate (`require_pin`) | `finance:read` is issued only if the PIN session is valid at mint time; bill and expense views are invisible without it |
| Clinic-wide admin views are owner-only (`require_clinic_owner_context`) | `clinic:admin` scope (owner in their own clinic) unlocks clinic-wide views across doctors |
| Associates work inside someone else's clinic | Associates see only their own rows in that clinic |

**Revocation:** tokens live 5 minutes, so a ClinicOS logout or password reset takes
effect within 5 minutes. `jti` is logged so a bad token can be traced in the audit log.

---

## 5. Data plane: getting ClinicOS data into MedGuard

### 5.1 Options considered

| Option | Fresh | Risk to ClinicOS | Coupling | Verdict |
|---|---|---|---|---|
| A. Agent queries ClinicOS prod directly | Live | **High.** LLM-written SQL on the clinic's primary DB; a bad query slows the front desk | Tight: any ClinicOS migration breaks the agent instantly | ✗ |
| B. **Scheduled sync into MedGuard's own DB** | ~5 min | Low: one bounded read every 5 min | Loose: mapping layer + contract test | ✅ **v1** |
| C. Change-data-capture (Postgres logical replication or GCP Datastream) | Seconds | Low | Loose | ⏭ v2 scale path |
| D. ClinicOS pushes events (outbox) | Seconds | Low | Needs changes all over ClinicOS | ✗ for now |

### 5.2 The sync job (`clinicos-sync`, a Cloud Run Job every 5 minutes)

```text
contract test ─► extract (medguard_reader, per table) ─► transform ─► load ─► watermark
                                                          │
                     pseudonymize names/phones ◄──────────┤
                     scrub free text (phones, emails) ◄───┤
                     row hash for change detection ◄──────┘
```

- **v1: snapshot + hash diff.** Most ClinicOS tables have no `updated_at`, so an
  "only new rows" sync would miss edits (a bill's `paid_amount` changing) and deletes.
  At ClinicOS's volume (thousands of rows), reading each table and upserting only the
  rows whose hash changed is cheap and correct. **v2:** once C6 lands, or with CDC,
  switch to true incremental.
- **Freshness SLO:** data is at most 5 minutes old. Every answer that uses records
  shows "data as of 10:42". Being honest about staleness beats pretending to be live.
- **Idempotent:** a run that crashes halfway can simply run again. Each run is logged
  to `ops.sync_runs` (rows read/changed/deleted, duration, contract version).
- **Consistent snapshot:** all ClinicOS tables are read inside **one** `REPEATABLE READ
  READ ONLY` transaction, so every table reflects the same instant. A bill can never
  arrive without its visit, even if the receptionist is billing while the sync runs.
  Loads go parents first (patients → visits → bills → bill_items) in one MedGuard
  transaction, so the AI never sees half a sync.
- **New patients:** first seen → pseudonym `PAT-<HMAC>` created + name/phone stored in
  `identity_vault`. Edits to a patient's name update the vault, not the pseudonym.

### 5.3 PII: the LLM sees tokens, the doctor sees names

ClinicOS today holds tester-entered data, but MedGuard is built **as if it held real
patient data**, so going live never requires a redesign.

1. During sync, patient name and phone go into `identity_vault` (encrypted, tenant-
   scoped). The `ops` tables keep only a pseudonym, `PAT-<HMAC(patient_id)>`.
2. The LLM only ever sees pseudonyms, ages, dates, amounts, drug names and statuses.
3. After the answer is final, the **re-identification step** swaps `PAT-…` back to the
   patient's name, and only for a requester whose token allows that patient's rows.
4. Free-text fields (`notes`, `doctor_notes`, `review`) are **not synced** in v1.
   `diagnosis` is synced after phone and email scrubbing. v2: Cloud Sensitive Data
   Protection (DLP) for names inside free text.
5. LLM calls go through **Vertex AI** in an India region, under Google Cloud's
   enterprise data terms, not the consumer Gemini API free tier.

---

## 6. MedGuard's database (Cloud SQL Postgres 16 + pgvector)

### 6.1 Schemas

| Schema | Contents | Who reads it |
|---|---|---|
| `ops` | Synced, pseudonymized ClinicOS tables: patients, appointments, visits, bills, bill_items, prescriptions, prescription_items, expenses, feedback (rating only), clinics, doctors (id, specialization), `sync_runs` | Sync job writes; nobody else reads raw |
| `semantic` | Curated, documented **views** for the SQL agent: `v_appointments`, `v_visits` (with wait-time minutes), `v_bills` (with outstanding = total − paid), `v_prescription_drugs`, `v_expenses`, `v_daily_summary` | SQL agent (role `medguard_agent`) |
| `knowledge` | `documents`, `global_chunks` + `tenant_chunks` (halfvec + tsvector, see §7.4), corpus versions | Retriever |
| `identity_vault` | pseudonym → encrypted name/phone | Re-identification step only |
| `telemetry` | `llm_traces`, `node_spans` | Metrics endpoint, dashboards |
| `audit` | Append-only: who asked what, which views, row counts, answer hash | Compliance, debugging |
| `agent_state` | LangGraph checkpointer (conversation threads) | Graph runtime |

**Why views?** The LLM writes SQL against a small, stable, documented surface with
clean names (`wait_minutes`, `outstanding_inr`) instead of ClinicOS's raw columns
(`check_in_time`, `call_time`, a legacy `staff_id`). Fewer made-up columns, and a
ClinicOS refactor changes one view, not every prompt.

### 6.2 Tenant isolation is enforced by the database

```sql
-- As built in M1 (migrations/versions/0003_ops_tables_with_rls.py)
ALTER TABLE ops.visits ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.visits FORCE ROW LEVEL SECURITY;          -- applies to the owner too
CREATE POLICY tenant_read ON ops.visits FOR SELECT TO medguard_agent, medguard_api USING (
      (clinic_id = (SELECT medguard.ctx_clinic_id())
       OR (clinic_id IS NULL AND (SELECT medguard.ctx_clinic_id()) IS NULL))   -- 'solo'
  AND (doctor_id = (SELECT medguard.ctx_doctor_id())
       OR (SELECT medguard.ctx_is_clinic_admin()))         -- owner sees the whole clinic
);                                     -- finance tables: AND (SELECT ctx_can_read_finance())
CREATE POLICY sync_all ON ops.visits FOR ALL TO medguard_sync USING (true) WITH CHECK (true);
```
The `medguard.ctx_*()` functions read the transaction-local settings and **raise** if the
doctor/clinic context is missing (fail closed). `(SELECT …)` makes Postgres evaluate each
once per query, not once per row. No write policy exists for the agent or API, so they
can't write even if a grant slipped in.

Every request opens a transaction (`app/db/tenant.py`), sets the context with
`set_config(name, value, true)` (= SET LOCAL, so it dies with the transaction and can't
leak to the next pooled request), then runs the agent's SQL as `medguard_agent`.
**Honest limit:** inside a single transaction, any role can call `set_config` itself.
The database can't forbid that for custom settings, so the M7 SQL guard bans
`set_config`/`SET`/`RESET` in AI-written SQL (THREAT_MODEL B3). Even a prompt-injected `SELECT * FROM v_bills` returns only the
requester's rows. **An automated eval case proves this** (§10).

---

## 7. Knowledge plane (unstructured documents)

### 7.1 Three corpora, three trust levels

| Corpus | Examples | Scope | Phase |
|---|---|---|---|
| **Regulatory (global)** | DPDP Act 2023 and Rules; Drugs and Cosmetics Rules (Schedule H, H1, X); Telemedicine Practice Guidelines; NMC professional-conduct regulations; IRDAI health-insurance master circular; ABDM health data management policy; GST on healthcare services | Every tenant, `clinic_id NULL`, admin-curated | v1 |
| **Clinic-private** | The clinic's own SOPs, TPA and insurer agreements, rate cards, staff policies | One clinic, uploaded by the owner (`docs:write`) | v1.5 |
| **Patient vault** | Lab reports, discharge summaries already in ClinicOS's R2 vault | One patient, strictest access, needs OCR (Document AI) | v3 |

Every regulatory document is checked against the official source before ingestion
and stored with `source_url`, `effective_date` and `version`. When a newer version
arrives, the old chunks are marked `superseded` but kept for audit.

### 7.2 Ingestion pipeline (`doc-ingest` job)

```text
PDF ─► parse (layout-aware: headings, tables, page numbers)
    ─► structure-aware chunks (section-bounded, ~400–600 tokens, small overlap)
    ─► contextual header per chunk: "DPDP Rules 2025 › Rule 8 › Erasure"
    ─► embed (Gemini embedding, 768-d) + tsvector
    ─► upsert with content hash (re-ingesting the same file is a no-op)
```

### 7.3 Retrieval

Dense (vector, top 20) and sparse (tsvector, top 20) search, fused with Reciprocal
Rank Fusion (k = 60), optionally reranked. The top 4–6 chunks go to the synthesizer.
**Every tuning knob is justified by an eval number, not by taste.**

### 7.4 Storage layout for scale (target ~25k–50k chunks, designed for 10× that)

**The filtering trap:** HNSW fetches its ~40 nearest candidates first and applies
`WHERE` filters afterwards. A filter that matches 1% of the corpus (one clinic's
private documents) can therefore return **zero rows** even though matches exist.

| Table | Contents | Vector index | Why |
|---|---|---|---|
| `knowledge.global_chunks` | Regulatory, pricing and insurance corpus (~95% of chunks) | HNSW with **iterative scan** (pgvector ≥ 0.8) for state and version filters | Large; filters are broad (state, `superseded`) |
| `knowledge.tenant_chunks` | Clinic-private documents, a few thousand per clinic | **None**: exact search behind a B-tree index on `clinic_id` + RLS | Small per tenant; exact search takes milliseconds and never misses |

- Vectors are stored as `halfvec(768)` (16-bit), which halves memory with negligible
  quality loss. The HNSW index must fit in RAM; size the Cloud SQL instance for that
  and scale it up only for benchmark windows.
- Each chunk stores `embedding_model`, so a model change means re-embedding into a
  new column, never mixing two embedding spaces.
- Hard metadata filters: the clinic's **state** (near-identical state rules are the
  #1 way to retrieve the wrong clause), `superseded = false` and corpus type.

---

## 8. Agent plane (LangGraph)

### 8.1 The graph

```text
                       ┌──────────────┐
  query + thread ─────►│  Guard-in    │  PII scrub, injection heuristics,
                       └──────┬───────┘  clinical-advice refusal
                              ▼
                       ┌──────────────┐
                       │  Supervisor  │  route: RECORDS | POLICY | HYBRID
                       │   (router)   │  | CLARIFY | REFUSE
                       └──┬────────┬──┘
              ┌───────────┘        └────────────┐   (HYBRID = both, in parallel)
              ▼                                 ▼
   ┌─────────────────────┐          ┌──────────────────────┐
   │  Records agent      │          │  Policy agent        │
   │  (ReAct loop)       │          │  hybrid retrieve     │
   │  tools:             │          │  → RRF → rerank      │
   │   list_views        │          │  → evidence set      │
   │   describe_view     │          └──────────┬───────────┘
   │   run_sql (guarded) │                     │
   └─────────┬───────────┘                     │
             └────────────────┬────────────────┘
                              ▼
                       ┌──────────────┐
                       │ Synthesizer  │  cited answer from evidence only
                       └──────┬───────┘
                              ▼
                       ┌──────────────┐  faithful? complete? cites real?
                       │   Critic     │──fail──► retry the branch that failed (max 1)
                       └──────┬───────┘          or refuse safely
                              ▼ pass
                       ┌──────────────┐
                       │ Re-identify  │  PAT-… → names (authorized only)
                       └──────┬───────┘
                              ▼
                     stream answer + citations + telemetry
```

### 8.2 How this maps to the patterns Google lists

| Pattern | Where |
|---|---|
| **Hierarchical delegation** | The supervisor delegates to specialist agents and merges their results |
| **ReAct** | The records agent reasons, calls `describe_view`, writes SQL, reads the error or result, and tries again (bounded to 4 steps) |
| **Self-reflection** | The critic grades the draft against the evidence; failure triggers a targeted retry or a refusal |
| **State management** | A typed `AgentState` + the Postgres checkpointer, so follow-ups ("and last month?") keep context per thread |

### 8.3 SQL guardrail (defence in depth)

1. **Parse** with `sqlglot`: exactly one `SELECT`, only `semantic.*` views, no
   functions on a deny list (`pg_sleep`, `dblink`…), `LIMIT 200` injected.
2. **Execute** inside a `READ ONLY` transaction with `statement_timeout = 3s`.
3. **Run** as role `medguard_agent`: SELECT on `ops` (RLS-filtered) and `semantic` views; no `identity_vault`, no writes. The guard (step 1) limits the AI to `semantic.*`.
4. **RLS** filters to the tenant (§6.2).

Any one layer may fail; all four failing together is what would have to happen to leak data.

---

## 9. Observability: LLM-native metrics and granular tracing

- **One trace per request**, with a span per graph node (OpenTelemetry → Cloud Trace),
  so you can see where the 2.1 s went.
- **Per request in `telemetry.llm_traces`:** route, tokens in/out, **tokens/sec**,
  **time to first token**, **cost per request** (from a model-price table in config),
  retries, critic verdict, SQL text, retrieved chunk IDs, data watermark.
- **`GET /v1/metrics`** (admin only): p50/p95 latency, average cost, grounding pass
  rate, refusal rate, route mix, cost per clinic per day.
- **Per-tenant budget:** daily token quota by ClinicOS plan. Over budget returns a
  clean 429, not a surprise bill.

---

## 10. Evaluation (from day one, in CI)

`eval/golden_set.jsonl` grows with every module:

| Category | What it checks |
|---|---|
| Records | Is the SQL answer numerically correct? Compared with a hand-written reference query on the same snapshot |
| Policy | Is the right clause retrieved (recall@k), and is the answer faithful to it? |
| Hybrid | Both halves correct and cited |
| **Tenant isolation** | Clinic A's token asks for clinic B's data and gets nothing, every time |
| Adversarial | Prompt injection inside a document, SQL injection in the question, "ignore previous instructions" |
| Out of scope | Clinical-advice questions are refused |
| Unanswerable | Says "not in your records or documents" instead of guessing |

GitHub Actions runs the eval on each PR and fails if a metric drops below its
threshold. The README publishes the numbers.

---

## 11. Deployment (Google Cloud, `asia-south1` Mumbai)

| Component | Service |
|---|---|
| API | Cloud Run service `medguard-api` (min instances 0 in dev, 1 in prod for latency) |
| Sync, ingestion | Cloud Run Jobs, triggered by Cloud Scheduler |
| Database | Cloud SQL for PostgreSQL 16 + pgvector (Neon is the documented fallback if cost blocks it) |
| LLM, embeddings | Vertex AI Gemini models (IDs in config, checked against Google's model list) |
| Secrets | Secret Manager: DB URLs, the `medguard_reader` password, the HMAC pepper, the vault key |
| Images | Artifact Registry, multi-stage Docker build |
| CI/CD | GitHub Actions → tests + eval → build → deploy, using Workload Identity Federation (no JSON keys) |
| Infra as code | Terraform for the above, once the shape is stable |

Environments: `local` (docker-compose Postgres + pgvector), `staging`, `prod`.

---

## 12. Build order

Each module is small, explained, reviewed and merged before the next. The eval
grows alongside.

| # | Module | Done when |
|---|---|---|
| M0 | Repo skeleton, config, Docker, local Postgres + pgvector, `/health/live` + `/health/ready` | `docker compose up` → both 200; ready turns 503 when the DB is down |
| M1 | MedGuard schemas + RLS + roles | RLS test: two tenants, zero cross-reads |
| M2 | Integration contract doc + ClinicOS C1 (read-only role) | `medguard_reader` can SELECT, cannot INSERT |
| M3 | `clinicos-sync`: contract test, extract, pseudonymize, hash-diff load | Synced row counts match ClinicOS; edit in ClinicOS shows up within 5 min |
| M4 | Semantic views + schema descriptions | Hand-written questions answerable by plain SQL on views |
| M5 | Doc ingestion (regulatory corpus, 5–8 verified docs) | Chunks with section headers and page numbers |
| M6 | Hybrid retriever + first retrieval eval | recall@5 measured |
| M7 | Records agent (ReAct) + 4-layer SQL guard | Guard test suite green; records eval measured |
| M8 | LangGraph: supervisor, parallel hybrid, synthesizer, critic, re-identify | End-to-end eval measured |
| M9 | FastAPI: auth (JWKS), `/v1/query` (SSE), tracing, metrics, audit | Token from a fake issuer → 200; tampered → 401 |
| M10 | ClinicOS C2–C5: token endpoint, JWKS, panel, CSP | Ask from inside ClinicOS, get an answer |
| M11 | GCP deploy + CI eval gate | Live URL, eval badge in README |
| M12 | Clinic simulator: 20 clinic profiles driving ClinicOS's real HTTP flows | 20 tenants with realistic, varied history |
| M13 | Corpus scale-up to ~120–230 docs (OCR, tables, amendments, 3–5 states) | Recall@5 curve as the corpus grows (v1 → full) |
| M14 | Load, soak, spike and chaos tests | SLOs met at 20 concurrent clinics |
| M15 | Enterprise readiness scorecard in README (§14) | All gates reported, including misses |
| M16+ | CDC, DLP, standalone console, inspection-readiness agent, patient vault | Hardening and product depth |

---

## 13. Open questions and things to verify

- [ ] Exact Vertex AI model IDs, and whether they're available in `asia-south1`
- [ ] Whether Railway Postgres allows logical replication (decides the v2 CDC path)
- [ ] Cloud SQL smallest-tier monthly cost vs remaining GCP credits
- [ ] Official sources and current versions for each regulatory document
- [ ] Whether ClinicOS associates should ever see clinic-wide aggregates (today: no)
- [ ] Which pgvector version Cloud SQL ships (iterative scan needs ≥ 0.8)

---

## 14. Enterprise readiness: how we prove it

"Enterprise" is shown by measured behaviour at realistic scale, not by asserting it.

| Pillar | Target | How |
|---|---|---|
| **Corpus scale** | v1 (Dec 3): 25–40 core docs, ~2.5k–4k pages. Full: ~120–230 docs, ~8k–15k pages, ~25k–50k chunks | Official sources only (India Code, e-Gazette, CDSCO, NPPA, CPCB, MeitY, NMC, IRDAI, clinicalestablishments.gov.in): clinic registration and minimum standards, drug rules and schedules, NPPA/NLEM price tables, DPDP, waste, PCPNDT/MTP, infection control, GST/consumer law, IRDAI + retail policy wordings, 3–5 states. Collected via `corpus/manifest.yaml` + a collector script. PDFs live in a private GCS bucket; the repo holds only the manifest (URL, hash, version) |
| **Tenant scale** | 20 live clinics | Clinic simulator (M12): GP, pediatric, dental and ortho; 1–4 doctors; Tier-2/3; several states; realistic rhythms (Monday rush, seasonal spikes, no-shows, partial payments). It drives ClinicOS's HTTP interface only and avoids email-sending flows (Resend quota) |
| **Load** | SLOs at 20 concurrent clinics | Locust or k6: baseline, ramp, spike, 1-hour soak. A stub LLM for infrastructure tests, the real LLM for quota tests. Expected failures: Vertex 429s, Cloud SQL connection limits, cold starts, noisy neighbours, sync lag |
| **Resilience** | Graceful degradation | Chaos runs: DB down, slow LLM, stale sync. The system answers partially or refuses cleanly, never with a 500 |
| **Question difficulty** | Per-level scores | L1 lookup · L2 time and compare · L3 multi-step records · L4 multi-step and versioned policy · L5 hybrid · L6 vague, adversarial or out of scope. Personas: owner, associate, receptionist, accountant, inspector, multi-clinic owner. Includes Hinglish and relative-time phrasing |
| **Golden set** | ~100 human-verified gold + ~400 silver | Records: ground truth from reference SQL, then paraphrased. Policy: multi-document synthetic Q&A, with Meher verifying the gold subset. Every tester failure is promoted into the set |
| **Safety** | 0 leaks across all 380 clinic pairs | Automated in CI |
| **Operations** | Dashboards, alerts, runbook, cost per clinic per month | Cloud Monitoring + `/v1/metrics` |

Targets are set after the first baseline and published, misses included.

---

## 15. Scaling path: 20 → 100 → 500 → 1,000 clinics

> Knowledge for production and interviews. **Not in the current build plan.** We build for
> today's scale and know exactly what breaks next, at what size, and how to fix it.

**Load is modest:** ~20 questions per clinic per day, mostly in clinic hours, works out to
~0.3 req/s at 100 clinics and ~3 req/s (bursts of 5–10) at 1,000. Request volume isn't
what breaks. These parts are, in this order:

| Order | Part | Strains at | Why | Fix |
|---|---|---|---|---|
| 1 | **Sync job** (v1 snapshot + hash diff) | ~50–100 clinics | One busy clinic makes ~150k–200k rows a year, so 1,000 clinics ≈ 200M rows a year. Re-reading everything every 5 min is impossible, and it loads ClinicOS's own DB | Incremental sync on `updated_at` (ClinicOS change C6), then CDC (Postgres logical replication or Datastream) from a ClinicOS replica |
| 2 | **LLM quota and cost** | ~500 clinics | 3–5 LLM calls and ~5k–7k tokens per question → ~1–2M tokens per minute at peak for 1,000 clinics. Rough cost ~$0.004 per question ≈ ₹200 per clinic per month (to verify against current pricing), against a ₹599 entry plan | Quota increase, then provisioned throughput. Smallest model for routing, answer caching, per-plan daily budgets, MedGuard as a paid add-on |
| 3 | **DB connections** | ~500 clinics | Cloud Run instances × pool size (40 × 5 = 200) is past what small Cloud SQL tiers allow (~25) | Connection pooler (PgBouncer or Cloud SQL managed pooling) |
| 4 | **DB size and query speed** | ~1,000 clinics | ~50–100 GB of ops data a year | Indexes on `(clinic_id, date)`, monthly partitioning of large tables, agent queries on a read replica |
| — | **API servers** | Not a problem | Cloud Run autoscales | Already handled |
| — | **Document search** | Not a problem | The global corpus doesn't grow with clinics; each query searches only its own clinic's ~2k private chunks (§7.4) | Already handled |

**ClinicOS is never at risk.** MedGuard is a separate system with its own database (§2).
If MedGuard struggles at scale, the clinic front desk keeps working.

**The interview answer:**
> "At 20 clinics it runs as designed. The first bottleneck is the sync job at about 100
> clinics: incremental sync, then change data capture. At 500, LLM quota and connection
> pooling. At 1,000, a read replica, partitioning and provisioned LLM throughput. Cost is
> about ₹200 per clinic per month at that scale, which is why there are per-plan budgets."
