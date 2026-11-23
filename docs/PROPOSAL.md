# MedGuard AI — Discovery Brief and Solution Proposal

> Written the way a Forward Deployed Engineer would present an engagement to a
> customer: what we learned, what we propose, how we'll measure it, and what could go
> wrong. The technical detail lives in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Summary

Independent clinics on **ClinicOS** already capture their whole day digitally:
appointments, walk-in queues, bills, prescriptions and expenses. But the data only
answers the questions someone built a screen for. Owners still can't ask *"why did
revenue dip in October?"* or *"am I keeping the records the law requires for the
drugs I prescribe?"*

**MedGuard** is a copilot inside ClinicOS that answers these questions in plain
language. It combines the clinic's own live records with the regulations that apply to
them, cites every claim, and never shows a clinic anything outside its own data.

---

## 2. Customer and context

| | |
|---|---|
| **Customer** | The ClinicOS platform and its tenants: independent doctors and multi-doctor clinics (outpatient, Tier-2/3 India) |
| **Not the target (yet)** | Hospitals and clinic chains. ClinicOS has no inpatient, claims or branch data, so MedGuard won't promise answers it has no data for |
| **Users** | Clinic owner (doctor), associate doctors, receptionist |
| **Existing system** | ClinicOS: FastAPI + Postgres on Railway, multi-tenant, role- and PIN-gated |
| **Data available** | Appointments, visits/queue, bills + line items, prescriptions + drugs, expenses, patient feedback |
| **Current state** | ClinicOS runs in production with pilot clinic accounts operated by test users, who generate realistic daily volume |
| **Constraints** | No changes that put ClinicOS's front desk at risk; India's DPDP Act; no clinical advice |

---

## 3. Discovery: what the clinic can't do today

| Stakeholder | Question they have | Today's workaround |
|---|---|---|
| Owner | "Which days and hours lose the most patients to no-shows?" | Gut feel |
| Owner | "Who still owes me money, and how much?" | Scroll the billing screen |
| Owner | "Is my associate's follow-up rate lower than mine?" | Not possible |
| Owner | "Do I need a separate register for the Schedule H1 drugs I prescribe?" | Ask a friend, or search the web |
| Owner | "A patient wants their data deleted. What am I obliged to do?" | Unknown |
| Associate | "How many of *my* patients came back within 7 days?" | Not possible |
| Receptionist | "What's the average wait on Monday mornings?" | Not possible |

**Pattern:** the answers exist in two places, the clinic's records and the rulebook,
and nothing connects them.

### Questions for a live discovery session with a clinic
1. Walk me through the last time you wished you had a number you couldn't get.
2. Which decisions do you make weekly (staffing, timings, pricing), and what do you look at?
3. Which regulations worry you, and how do you keep up with changes?
4. Who in the clinic should *not* see billing data?
5. If the assistant were wrong once, what would the damage be? (This sets the refusal threshold.)
6. Would you trust an answer more with a link to the exact record or clause behind it?

---

## 4. Proposed solution

An "Ask MedGuard" panel in the ClinicOS dashboard, backed by a separate service on
Google Cloud:

- **Records answers:** guarded, read-only SQL over a curated, tenant-isolated copy of
  the clinic's data, refreshed every 5 minutes.
- **Policy answers:** hybrid search over verified regulatory documents, with clause-
  level citations.
- **Combined answers:** both at once, merged into one cited response.
- **Trust built in:** every answer is checked against its evidence before it is shown.
  If the evidence is thin, MedGuard says so instead of guessing.

ClinicOS stays the system of record. MedGuard attaches through a thin, documented
contract: a read-only database role, short-lived signed tokens and an embedded panel.

---

## 5. How we'll measure success

| Layer | Metric | v1 target (set before tuning) |
|---|---|---|
| Correctness | Records answers numerically correct | ≥ 90% of golden set |
| Correctness | Policy answers faithful to the cited clause | ≥ 90% |
| Safety | Cross-tenant leakage | **0**, always |
| Safety | Unanswerable or clinical questions correctly refused | ≥ 95% |
| Performance | p95 latency (records / policy / hybrid) | Measure first, then set |
| Cost | Average cost per request | Measured and published |
| Freshness | Data age when answering | ≤ 5 minutes |

The targets are commitments to *measure*, not claims. The README will report the
real numbers, including the ones that miss.

---

## 6. Scope

**In v1:** records, policy and hybrid Q&A; the regulatory corpus; tenant isolation;
tracing and cost metrics; the eval harness; GCP deployment; the ClinicOS panel.

**Not in v1:** clinic-uploaded documents (v1.5); patient vault documents and OCR (v3);
real-time CDC (v2); any write actions ("book this appointment"); any clinical advice
(never).

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A wrong number erodes trust | Critic checks every answer; SQL is shown on request; refusal over guessing |
| Data leaks across clinics | Row-Level Security in the database, not the prompt, plus an automated leakage test in CI |
| Patient identity reaches the LLM | Pseudonymized at sync; names re-attached only after the answer, only for authorized users |
| Outdated regulations | Each document has a source URL, effective date and version; superseded chunks are excluded |
| Agent load hurts ClinicOS | Separate database; one bounded read every 5 minutes; ClinicOS never waits on MedGuard |
| Being treated as a medical device | Scope is operational and compliance only; clinical questions are refused |
| Runaway LLM cost | Per-clinic daily token budget tied to the ClinicOS plan |

---

## 8. Rollout

1. **Build and eval** against ClinicOS production data from the pilot accounts.
2. **Internal pilot:** turn on the panel for one pilot clinic account, review every
   trace for a week, and add each failure to the golden set.
3. **Expand** to all pilot accounts once the safety metrics hold.
4. **Real clinics:** consent language in ClinicOS's terms, a DPDP review, then clinic-
   private documents.
