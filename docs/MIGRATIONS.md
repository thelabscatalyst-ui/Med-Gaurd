# Database migrations: how MedGuard changes its database safely

> Tool: **Alembic, with raw SQL inside** (decided 2026-11-24). Exact commands are added
> in M1, when Alembic is set up.

---

## 1. The idea in plain words

MedGuard's database exists in several places that must always have **the same structure**:
your laptop, CI (the test machine), staging and production.

A **migration** is one numbered, permanent instruction that changes that structure
("create the bills table", "add a wait_minutes column"). Each database keeps a
**logbook** of which instructions it has already run. Running migrations means: *read the
logbook, run only the missing steps, in order, and tick them off.*

```
📘 migrations/ (in git, same for everyone)     📋 logbook inside each database
   0001 create schemas                            laptop:      ✔0001 ✔0002 ✔0003
   0002 create roles                              production:  ✔0001 ✔0002
   0003 create ops tables + RLS                   → production runs only 0003
```

Without migrations, people change databases by hand, the copies drift apart, and nobody
knows which database has what. (ClinicOS today runs `_add_column()` at startup and
swallows errors. A bad `DEFAULT 0` once left a column silently missing in production,
and every query on that table returned 500. Migrations exist to prevent exactly that.)

---

## 2. The five rules

1. **Never edit a migration that has run anywhere.** To fix a mistake, write a *new*
   migration. History is append-only, like git.
2. **One migration = one transaction.** It fully succeeds or fully rolls back. (One
   exception: `CREATE INDEX CONCURRENTLY`, see §4.)
3. **Migrations run before the new code, as a separate step.** On Cloud Run, a one-off
   job runs them; the new app version goes live only if they succeed. The app never
   migrates at startup (ten instances would race each other).
4. **The database must work for both the old and the new code** during a deploy,
   because both run side by side for a minute (§4, expand/contract).
5. **Never change production by hand.** Every change goes through a migration, or the
   logbook stops telling the truth.

---

## 3. The life of one migration

```
WRITE   → new numbered file, raw SQL inside
TEST    → locally: upgrade → downgrade → upgrade again (proves it's repeatable)
CI      → a brand-new empty DB gets EVERY migration from 0001 (proves a fresh
          machine can always rebuild the database), then the full test suite runs
REVIEW  → Meher reads the SQL before it is merged
DEPLOY  → Cloud Run Job runs `alembic upgrade head` on production → app goes live
RECORD  → the alembic_version table now holds the new revision
```

---

## 4. Techniques (the toolbox)

| Situation | Unsafe way | Safe technique |
|---|---|---|
| **Add a column** | — | `ADD COLUMN x type NULL` is instant. A constant `DEFAULT` is also instant (Postgres 11+). A *volatile* default (e.g. `now()`) rewrites the whole table: avoid it on big tables |
| **Add NOT NULL to an existing column** | `SET NOT NULL` directly (scans the table under a lock, fails if any NULL exists) | 1) add a `CHECK (x IS NOT NULL) NOT VALID` constraint, 2) backfill, 3) `VALIDATE CONSTRAINT` (no heavy lock), 4) `SET NOT NULL` (uses the validated check) |
| **Add an index on a big table** | `CREATE INDEX` (blocks writes until done) | `CREATE INDEX CONCURRENTLY` in its **own** migration, outside a transaction. If it fails, it leaves an `INVALID` index: drop it and retry |
| **Rename a column** | `RENAME COLUMN` (old code breaks instantly) | **Expand/contract:** add the new column → write to both → backfill → switch reads → drop the old one in a *later* release |
| **Drop a column or table** | Drop it in the same release that stops using it | Release 1 stops using it; release 2 drops it. Dropping deletes data, so make sure a backup exists |
| **Change a column's type** | `ALTER TYPE` (rewrites and locks the table) | Add a new column of the new type → backfill → switch → drop the old one |
| **Fill or fix data (backfill)** | One giant `UPDATE` (long locks, huge undo log) | Batches (e.g. 5,000 rows each), in a script or job, separate from the schema change |
| **Roles and permissions** | `GRANT`s by hand | In migrations, like everything else (`0002_roles`). The same grants everywhere |
| **RLS on a new table** | Remember to add it | Same migration: `ENABLE` + `FORCE ROW LEVEL SECURITY` + policies. **Meta-test:** every `ops` table must have RLS enabled and forced, or CI fails |
| **Views over RLS tables** | `CREATE VIEW` (runs as the creator → **skips RLS**) | `CREATE VIEW … WITH (security_invoker = true)` |
| **Vector index (HNSW)** | Build it inside the schema migration on a full table (slow, memory-hungry) | Create it `CONCURRENTLY`, with enough `maintenance_work_mem`, ideally after the bulk load |
| **Change embedding model** | Overwrite vectors in place (mixes two embedding spaces) | New column for the new model → re-embed in the background → switch queries → drop the old column later |

### Schema migrations vs data migrations
- **Schema migration:** changes structure (tables, columns, indexes, policies). Lives
  in `migrations/`. Small, fast, transactional.
- **Data migration:** changes rows (backfills, fixes). Can be slow, so run it as a
  separate batched job, never inside a schema migration on a big table.

---

## 5. Problems, and what solves them

| Problem | Solution | Fully undoable? |
|---|---|---|
| A migration deleted data | **Backups only:** Cloud SQL automated backups + point-in-time recovery, on from day one | ❌ Recover, not undo |
| A long lock froze the sync job | The §4 techniques; `lock_timeout` in migrations so they fail fast instead of queueing | ✅ |
| Old and new code clash during a deploy | Expand/contract | ✅ |
| A `CONCURRENTLY` index failed halfway | Find `INVALID` indexes, drop them, rerun | ✅ (manual) |
| Two branches both added "the next" migration | Alembic links each revision to its parent and reports two heads; `alembic merge` joins them | ✅ |
| Someone changed production by hand (drift) | Rule 5, plus a CI check comparing the real schema with the migrations | ✅ |
| **ClinicOS changed its tables** | Not our migrations: ClinicOS's schema is external. The M3 **contract test** checks it before every sync and stops loudly | ✅ Detected, not prevented |
| A new table forgot RLS | The meta-test in §4 | ✅ |

**Nothing here is unsolvable, but two things can only be recovered from, never undone:**
deleted data (backups) and history (you can't rewrite a migration that already ran;
you can only add a new one).

---

## 6. Review checklist (use for every migration)

- [ ] One logical change; the filename says what it is
- [ ] Runs inside a transaction (or is deliberately non-transactional, e.g. `CONCURRENTLY`)
- [ ] Safe while the *old* code is still running
- [ ] No full-table rewrite or long lock on a big table
- [ ] New `ops` tables: RLS enabled + forced, policies, `(clinic_id, doctor_id)` index
- [ ] New views: `security_invoker = true`
- [ ] Grants: each role gets only what it needs
- [ ] Downgrade written, or marked irreversible with a reason
- [ ] Tested: upgrade → downgrade → upgrade, then the full test suite
