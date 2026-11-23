# MedGuard AI: Positioning

## In one sentence
**MedGuard is the AI layer of the ClinicOS platform: it lets every clinic ask plain-
language questions about its own live records and the Indian rules it must follow, and
get cited answers, without ever seeing another clinic's data.**

## Who it serves
| | |
|---|---|
| **Enterprise customer** | The ClinicOS platform, which serves many clinics from one system |
| **Tenants** | Independent doctors and multi-doctor clinics (outpatient, Tier-2/3 India) |
| **Users** | Clinic owner, associate doctors, receptionist |

## What it answers
| Type | Example | Source |
|---|---|---|
| **Records** | "How many no-shows did I have this month vs last?" | The clinic's live ClinicOS data, synced every 5 minutes |
| **Compliance** | "How long must I keep prescription records?" | Official Indian regulations, cited to the clause |
| **Both** | "Which drugs I prescribed this month are Schedule H1, and what register must I keep?" | Records + regulations, in one answer |

## The hard part (why this is an enterprise system)
The questions are simple. The guarantees behind them aren't:

1. **Isolation:** many clinics on one platform, and no clinic can ever read another's
   data. Enforced by the database (Postgres Row-Level Security), not by the prompt.
2. **Roles:** an owner sees every doctor in the clinic, an associate sees only their own
   patients, and billing is invisible without the ClinicOS PIN. MedGuard never grants
   more than ClinicOS would.
3. **Privacy:** the LLM sees pseudonyms (`PAT-7f3a`), never names or phone numbers.
   Built to India's DPDP Act from day one.
4. **Trust:** every answer is checked against its sources before it is shown. When the
   evidence is thin, MedGuard says so instead of guessing.
5. **Proof:** correctness, latency, cost per request and leakage are measured on every
   build and published, including the misses.

## What it deliberately does not do
- **No clinical advice.** No diagnosis, treatment or dosing. That keeps it out of
  medical-device territory, and the router refuses such questions.
- **No hospital claims.** ClinicOS has no inpatient, claims or department data, so
  MedGuard doesn't promise answers it can't ground. Hospitals are a future extension.
- **No writes.** It reads and explains; it never books, bills or edits.
- **Not a merge.** ClinicOS and MedGuard stay separate systems joined by a thin,
  audited contract: a read-only DB role, short-lived signed tokens and an embedded panel.

## The pitch, 30 seconds
> "Small clinics in India run on ClinicOS. Their owners have data they can't query and
> rules they can't keep up with, and no analyst or compliance team. MedGuard gives each
> clinic both, through one question box. The clinic sees a simple assistant; underneath,
> the platform enforces per-clinic isolation, role and PIN permissions, and privacy, and
> measures every answer. Built on Google Cloud with LangGraph, hybrid RAG and pgvector."
