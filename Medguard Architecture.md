# MedGuard AI — Production Hybrid-RAG & Multi-Agent System Architecture

> **Document Purpose:** Canonical technical specification and architectural blueprint for **MedGuard AI**, an enterprise-grade Hybrid-RAG and Multi-Agent Operations Copilot built for healthcare clinics. Designed to be used as a system reference for human engineers and AI development tools (e.g., Claude Code, Cursor, VS Code).

---

## 1. System Overview & Core Objectives

**MedGuard AI** unifies **structured operational data** (anonymized clinic records replicated from ClinicOS: patients, appointments, prescriptions, billing) with **unstructured policy documents** (public healthcare regulations, ABDM guidelines, insurance pre-authorization rules, and clinical SOPs) behind a single, secure, observable API.

### Engineering Principles
1. **Zero Direct Production DB Access:** LLMs never query the live ClinicOS database. All structured queries run against an anonymized, read-only staging replica.
2. **Hybrid Retrieval Over Naive Vector Search:** Combines dense semantic search (`pgvector`) with sparse keyword search (PostgreSQL `tsvector`/BM25), fused via Reciprocal Rank Fusion (RRF) and refined with a reranker.
3. **Bounded Self-Reflection:** A dedicated Grader/Critic node verifies factual grounding before returning responses, looping at most once on failure and refusing to answer rather than hallucinating.
4. **LLM-Native Observability:** Every request is traced end-to-end with latency, token throughput (`tokens/sec`), cost per request (`USD`), route taken, and grounding status.
5. **Verifiable Evaluation:** System quality is measured quantitatively via an automated golden-dataset evaluation harness rather than manual spot-checks.

---

## 2. High-Level System Architecture

```text
══════════════════════════ OFFLINE PIPELINE (Data Prep) ══════════════════════════

[ ClinicOS Live DB ] ──► Anonymization ETL ──► [ Staging Postgres (Read-Only) ]
(Patients, Rx, Bills)    (Strips PII/Names)     (Structured Tables + Schema Docs)

[ Public Health PDFs ] ──► Structure-Aware ──► Dual Indexing ──► [ Postgres pgvector ]
(ABDM, IRDAI, SOPs)        Chunking + Meta     (Vector + BM25)    (Unstructured Chunks)

══════════════════════════ ONLINE PIPELINE (Runtime API) ═════════════════════════

[ Client / Swagger / Eval Harness ]
                 │
                 ▼  (POST /v1/query | X-API-Key Auth)
┌────────────────────────────────────────────────────────────────────────────┐
│                     FastAPI Service (Google Cloud Run)                     │
│  1. Pydantic Request Validation                                            │
│  2. PII Redaction Middleware (Masks phones, emails, Aadhaar/IDs)           │
│  3. Observability Wrapper (Generates trace_id, starts timer)               │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                   LangGraph Multi-Agent Orchestrator                       │
│                        (Typed Shared State)                                │
│                                                                            │
│   ┌──────────────┐                                                         │
│   │ Router Agent │ ── Classifies intent: SQL | RAG | HYBRID                │
│   └──────┬───────┘                                                         │
│          ├─────────────────────────────────┐                               │
│          ▼                                 ▼                               │
│  ┌───────────────────────┐       ┌───────────────────────────────┐         │
│  │      SQL Agent        │       │       Hybrid RAG Agent        │         │
│  │ • Schema-aware SQL    │       │ • Dense Search (pgvector)     │         │
│  │ • AST/Regex Guardrail │       │ • Sparse Search (tsvector)    │         │
│  │ • Read-only execution │       │ • Reciprocal Rank Fusion(RRF) │         │
│  │ • 1x Syntax self-fix  │       │ • Cross-Encoder / LLM Rerank  │         │
│  └───────────┬───────────┘       └───────────────┬───────────────┘         │
│              └─────────────────┬─────────────────┘                         │
│                                ▼                                           │
│                     ┌────────────────────┐                                 │
│                     │  Synthesizer Node  │ (Drafts cited response)         │
│                     └──────────┬─────────┘                                 │
│                                ▼                                           │
│                     ┌────────────────────┐                                 │
│                     │   Grader / Critic  │ (Checks grounding & relevance)  │
│                     └──────────┬─────────┘                                 │
│                                │                                           │
│          [Pass] ◄──────────────┴──────────────► [Fail: Rewrite & Retry     │
│            │                                     (max 1 loop) or Refuse]   │
└────────────┼───────────────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────┐      ┌────────────────────────────────┐
│       PostgreSQL + pgvector        │      │      Observability Sink        │
│ • Structured: clinics, patients,   │      │ • Table: llm_traces            │
│   appointments, prescriptions,     │      │ • Metrics: latency_ms,         │
│   billing                          │      │   tokens_per_sec, cost_usd,    │
│ • Unstructured: doc_chunks         │      │   retry_count, grounding_pass  │
└────────────────────────────────────┘      └────────────────────────────────┘
```

---

## 3. Tech Stack Specification

| Layer | Technology | Rationale |
|---|---|---|
| **Language** | Python 3.11+ | Native async support, ecosystem standard for AI/ML backends |
| **API Framework** | FastAPI + Pydantic v2 | High-throughput async endpoints, strict schema validation, auto OpenAPI docs |
| **Agent Orchestration** | LangGraph | Explicit typed state graph, deterministic routing, cyclic retry loops |
| **LLM & Embeddings** | Gemini 2.5 Flash & `text-embedding-004` | Low latency, generous free/low-cost tier, native Google ecosystem |
| **Database & Vector Store** | PostgreSQL 16 + `pgvector` (Neon / Supabase) | Single unified engine for relational SQL, vector similarity, and BM25 (`tsvector`) |
| **Document Parsing** | `pymupdf` (fitz) / `unstructured` | Preserves headings, tables, and page numbers during PDF extraction |
| **Containerization** | Docker (Multi-stage build) | Reproducible, minimal-footprint production images |
| **Cloud Hosting** | Google Cloud Run | Serverless containers, scales to zero, native GCP deployment |

---

## 4. Offline Pipeline (Data Ingestion & Preparation)

### 4.1 Structured Data (ClinicOS Replication & Anonymization)
1. **Source:** Export or mirror ClinicOS operational tables (`clinics`, `patients`, `appointments`, `prescriptions`, `billing`).
2. **PII Stripping:** Run an anonymization ETL script before inserting into the staging database:
   * Replace patient names, phone numbers, emails, and addresses with deterministic anonymized references (e.g., `PAT-1042`).
   * Retain non-identifying operational attributes (age group, gender, department, diagnosis codes, procedure codes, billing amounts, claim/payment status, timestamps).
3. **Database Security:** Create a dedicated PostgreSQL role (`medguard_readonly`) granted **only `SELECT`** privileges on the operational tables. The SQL Agent connects exclusively using this role.
4. **Schema Dictionary:** Maintain a concise JSON/text schema description (table names, column types, enum values, foreign keys) injected into the SQL Agent's system prompt to prevent column-name hallucinations.

### 4.2 Unstructured Data (Public Healthcare Policies & SOPs)
1. **Sources:** Curated public healthcare policy PDFs/Markdown files (e.g., ABDM Health Data Management Policy, IRDAI Health Insurance Claim & Pre-Auth Guidelines, clinic triage and record-retention SOPs).
2. **Structure-Aware Chunking:**
   * Parse documents by logical hierarchy (headings, sections, paragraphs) rather than fixed character counts.
   * Target chunk size: **400–600 tokens** with **10–15% overlap** (50–80 tokens).
   * Attach mandatory metadata to every chunk: `doc_name`, `section_title`, `page_number`, `effective_date`.
3. **Dual Indexing:**
   * Generate 768-dimensional embeddings using `text-embedding-004` and store in `doc_chunks.embedding`.
   * Automatically generate PostgreSQL `tsvector` full-text search vectors in `doc_chunks.tsv` for exact keyword/code matching (e.g., `ABDM-4.2`, `ICD-10`, drug names).

---

## 5. Database Schema (`app/db/models.sql`)

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 1. STRUCTURED OPERATIONAL TABLES (Anonymized ClinicOS Schema)
-- ============================================================
CREATE TABLE clinics (
    clinic_id SERIAL PRIMARY KEY,
    clinic_code TEXT UNIQUE NOT NULL,       -- e.g., 'CLN-001'
    city TEXT NOT NULL,
    tier TEXT NOT NULL                      -- 'Tier-2', 'Tier-3'
);

CREATE TABLE patients (
    patient_ref TEXT PRIMARY KEY,           -- Anonymized ID: 'PAT-1042'
    clinic_id INT REFERENCES clinics(clinic_id),
    age_group TEXT NOT NULL,                -- '0-18', '19-40', '41-60', '60+'
    insurance_tier TEXT NOT NULL,           -- 'None', 'Basic', 'Standard', 'Premium'
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE appointments (
    appointment_id SERIAL PRIMARY KEY,
    clinic_id INT REFERENCES clinics(clinic_id),
    patient_ref TEXT REFERENCES patients(patient_ref),
    department TEXT NOT NULL,               -- 'General', 'Cardiology', 'Orthopedics'
    status TEXT NOT NULL,                   -- 'Completed', 'Cancelled', 'No-Show'
    visit_date TIMESTAMP NOT NULL
);

CREATE TABLE prescriptions (
    rx_id SERIAL PRIMARY KEY,
    appointment_id INT REFERENCES appointments(appointment_id),
    diagnosis_code TEXT NOT NULL,
    medications JSONB NOT NULL,             -- [{"name": "...", "dosage": "...", "days": 5}]
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE billing_claims (
    bill_id SERIAL PRIMARY KEY,
    appointment_id INT REFERENCES appointments(appointment_id),
    procedure_code TEXT NOT NULL,
    amount_inr NUMERIC(10, 2) NOT NULL,
    pre_auth_submitted BOOLEAN NOT NULL DEFAULT FALSE,
    hours_since_admission INT DEFAULT 0,
    claim_status TEXT NOT NULL,             -- 'Self-Pay', 'Pending', 'Approved', 'Rejected'
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- 2. UNSTRUCTURED DOCUMENT STORE (Hybrid: pgvector + BM25)
-- ============================================================
CREATE TABLE doc_chunks (
    chunk_id SERIAL PRIMARY KEY,
    doc_name TEXT NOT NULL,
    section_title TEXT,
    page_number INT,
    content TEXT NOT NULL,
    embedding vector(768),                  -- Matches Gemini text-embedding-004
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_doc_chunks_tsv ON doc_chunks USING GIN (tsv);
CREATE INDEX idx_doc_chunks_embedding ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 3. LLM-NATIVE OBSERVABILITY & TRACING
-- ============================================================
CREATE TABLE llm_traces (
    trace_id UUID PRIMARY KEY,
    user_query TEXT NOT NULL,
    route_taken TEXT,                       -- 'SQL', 'RAG', 'HYBRID'
    sql_generated TEXT,
    latency_ms INT,
    tokens_in INT,
    tokens_out INT,
    tokens_per_sec FLOAT,
    cost_usd NUMERIC(10, 6),
    retry_count INT DEFAULT 0,
    grounding_passed BOOLEAN,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 6. Online Pipeline (Runtime Execution & LangGraph Design)

### 6.1 Shared Agent State (`app/agents/state.py`)
```python
from typing import TypedDict, List, Dict, Any, Optional, Literal

class AgentState(TypedDict):
    trace_id: str
    original_query: str
    sanitized_query: str
    route: Optional[Literal["SQL", "RAG", "HYBRID"]]
    
    # SQL Agent outputs
    sql_query: Optional[str]
    sql_results: Optional[List[Dict[str, Any]]]
    sql_error: Optional[str]
    
    # Hybrid RAG Agent outputs
    retrieved_chunks: List[Dict[str, Any]]
    
    # Synthesis & Self-Reflection
    draft_answer: Optional[str]
    citations: List[str]
    grounding_passed: bool
    grader_feedback: Optional[str]
    retry_count: int
    
    # Telemetry accumulators
    tokens_in: int
    tokens_out: int
    node_latencies_ms: Dict[str, int]
```

### 6.2 Node-by-Node Behavior
1. **PII Redaction Middleware (FastAPI Layer):**
   * Runs regex/pattern scrubbing on incoming `query` strings to mask Indian phone numbers (`+91...`), email addresses, and 12-digit Aadhaar-like sequences before passing `sanitized_query` to LangGraph.
2. **Router Node (`router.py`):**
   * Uses structured JSON output to classify `sanitized_query` into:
     * `SQL`: Quantitative/operational queries over clinic records.
     * `RAG`: Policy, compliance, or SOP questions.
     * `HYBRID`: Queries requiring cross-referencing live clinic data against policy rules.
3. **SQL Agent Node (`sql_agent.py`):**
   * Generates a PostgreSQL `SELECT` query using the injected schema dictionary.
   * **SQL Guardrail:** Validates query via regex/AST check—rejects any statement containing `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, or `GRANT`, and enforces a `LIMIT 100` cap.
   * Executes via the `medguard_readonly` DB connection. If Postgres returns a syntax error, feeds the error message back to the LLM for **1 self-correction attempt**.
4. **Hybrid RAG Agent Node (`rag_agent.py`):**
   * Runs two parallel queries against `doc_chunks`:
     * **Vector Search:** Top-15 chunks by cosine distance (`embedding <=> query_vec`).
     * **Keyword Search:** Top-15 chunks by `ts_rank(tsv, plainto_tsquery('english', query))`.
   * **Reciprocal Rank Fusion (RRF):** Merges both ranked lists using $RRF(d) = \sum \frac{1}{k + rank(d)}$ (with $k = 60$).
   * **Reranking:** Scores the top-10 fused candidates (via cross-encoder or fast LLM rerank prompt) and keeps the **top-4 chunks** for context efficiency.
5. **Synthesizer Node (`synthesizer.py`):**
   * Generates a clear, concise answer strictly from `sql_results` and `retrieved_chunks`.
   * Enforces inline citations formatted as `[Doc Name, Section X, p. Y]` or `[SQL Query Result]`.
6. **Grader / Critic Node (`grader.py`):**
   * Evaluates `draft_answer` against retrieved evidence on two binary criteria: **Faithfulness** (no unsupported claims) and **Completeness** (answers the prompt).
   * **Conditional Edge:**
     * If `grounding_passed == True`: Exit graph and return response.
     * If `grounding_passed == False` and `retry_count < 1`: Increment `retry_count`, rewrite `sanitized_query` using `grader_feedback`, and loop back to retrieval.
     * If `grounding_passed == False` and `retry_count >= 1`: Return a safe refusal (*"Insufficient verified data in clinic records or policy documents to answer this reliably."*).

---

## 7. API Contract

### `POST /v1/query`
* **Headers:** `X-API-Key: <secret>`, `Content-Type: application/json`
* **Request:**
  ```json
  {
    "query": "Which pending bills over 5000 INR from this week are missing pre-authorization under our insurance policy?"
  }
  ```
* **Response:**
  ```json
  {
    "trace_id": "550e8400-e29b-41d4-a716-446655440000",
    "route": "HYBRID",
    "answer": "Two pending bills exceed ₹5,000 without pre-authorization (Bill #104, Bill #118) [SQL Query Result]. Under IRDAI guidelines, claims above ₹5,000 require pre-authorization within 48 hours of admission [IRDAI Claim Policy, Sec 3.2, p. 11].",
    "citations": [
      "SQL Query Result",
      "IRDAI Claim Policy, Sec 3.2, p. 11"
    ],
    "grounding_passed": true,
    "telemetry": {
      "latency_ms": 1840,
      "tokens_in": 1420,
      "tokens_out": 185,
      "tokens_per_sec": 100.5,
      "cost_usd": 0.000412,
      "retry_count": 0
    }
  }
  ```

### `POST /v1/ingest`
* Accepts a PDF/Markdown file + metadata (`doc_name`, `effective_date`), runs structure-aware chunking, embeds chunks, and upserts into `doc_chunks`.

### `GET /v1/metrics`
* Aggregates `llm_traces` to return system-wide P50/P95 latency, average `tokens_per_sec`, total cost, grounding pass rate, and route distribution.

### `GET /health`
* Verifies FastAPI liveness and PostgreSQL/`pgvector` connectivity.

---

## 8. Evaluation Harness (`eval/`)

* **Dataset (`eval/golden_set.json`):** 25–30 curated test cases spanning:
  * 8 Pure SQL queries (aggregations, filters, joins)
  * 8 Pure RAG queries (specific policy clauses, exact code lookups)
  * 8 Hybrid queries (combining ClinicOS billing/Rx data with policy rules)
  * 4–6 Adversarial / Unanswerable queries (verifying refusal behavior & SQL injection blocking)
* **Runner (`eval/run_eval.py`):** Executes all queries against the live graph and computes:
  * **Routing Accuracy (%)**
  * **SQL Execution Success Rate (%)**
  * **Faithfulness & Citation Accuracy (%)** (LLM-as-a-Judge)
  * **Refusal Accuracy on Unanswerable Queries (%)**
  * **P95 Latency (ms) & Avg Cost per Query (USD)**

---

## 9. Repository Structure

```text
medguard-ai/
├── ARCHITECTURE.md            # This document
├── README.md                  # Setup guide, eval benchmarks, trade-offs
├── Dockerfile                 # Multi-stage production container
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI app, routes, PII middleware
│   ├── config.py              # Settings, env vars, token pricing table
│   ├── db/
│   │   ├── database.py        # Async Postgres pool (admin + readonly)
│   │   └── models.sql         # Full DDL schema
│   ├── ingestion/
│   │   ├── anonymize.py       # ClinicOS PII stripping & staging loader
│   │   └── pdf_pipeline.py    # Structure-aware chunking + dual indexing
│   ├── agents/
│   │   ├── state.py           # LangGraph AgentState TypedDict
│   │   ├── router.py          # Intent classifier
│   │   ├── sql_agent.py       # Guardrailed read-only SQL agent
│   │   ├── rag_agent.py       # pgvector + BM25 + RRF + Reranker
│   │   ├── synthesizer.py     # Citation-enforced answer generator
│   │   ├── grader.py          # Self-reflection hallucination check
│   │   └── graph.py           # Compiled LangGraph StateGraph
│   └── observability/
│       └── tracker.py         # Latency, tokens/sec, cost logger
├── data/
│   ├── docs/                  # Public healthcare policy PDFs/MDs
│   └── seed_db.py             # Sample ClinicOS operational data seeder
└── eval/
    ├── golden_set.json        # 25-30 golden evaluation queries
    └── run_eval.py            # Automated evaluation runner & report
```

---

## 10. Implementation Roadmap

### Phase 1: Core v1 (Days 1–5 — Ship & Deploy)
- [ ] Provision Neon/Supabase PostgreSQL + `pgvector` and apply `models.sql`.
- [ ] Build `seed_db.py` / `anonymize.py` to populate ClinicOS operational tables.
- [ ] Build `pdf_pipeline.py` to ingest 3–5 public healthcare policy PDFs into `doc_chunks`.
- [ ] Implement LangGraph workflow (`router` → `sql_agent` / `rag_agent` → `synthesizer` → `grader`).
- [ ] Wire FastAPI endpoints (`/v1/query`, `/v1/metrics`, `/health`) with PII middleware and `llm_traces` logging.
- [ ] Containerize with Docker and deploy to **Google Cloud Run**.

### Phase 2: Production Hardening (Days 6–10 — Benchmark & Polish)
- [ ] Tune Hybrid Search (RRF weights + reranker threshold).
- [ ] Build `eval/golden_set.json` (25–30 queries) and run `eval/run_eval.py`.
- [ ] Document benchmark metrics and architectural trade-offs in `README.md`.