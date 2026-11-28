# Failures log

Every failure we hit while building MedGuard: what we saw, why it happened, how we
fixed it, and what we learned. Newest first. This is a learning record, not a blame log.

Template:

```
## YYYY-MM-DD · Mx · Short title
- **Symptom:** what we actually saw (error text, wrong number, slow request)
- **Root cause:** why, proven, not guessed
- **Fix:** what changed (commit)
- **Lesson:** what we'll do differently from now on
```

---

## 2026-11-26 · M1 · `alembic current` failed on Meher's first try
- **Symptom:** `uv run alembic current` crashed with `ValidationError … database_url
  Field required`.
- **Root cause:** there was no `.env` file. Inside Docker, compose passes `DATABASE_URL`
  to the containers, and the tests pass their URL explicitly, so neither needed one. Run
  directly on the laptop, Alembic reads settings from `.env`, and the one-time setup step
  `cp .env.example .env` (in the README) had never been done. My instructions to Meher
  skipped that step.
- **Fix:** `cp .env.example .env` (git-ignored; local Docker credentials only). Now prints
  `0004 (head)`. Added the step to CLAUDE.md's command list.
- **Lesson:** before handing someone a command, run it the way *they* will, in a fresh
  shell with only the documented setup. A command that works in my environment can still
  fail in theirs.

## 2026-11-26 · M1 · Migrations reported success, but nothing was saved
- **Symptom:** `alembic upgrade head` printed "Running upgrade -> 0001 … 0004" with no
  error. The next run started from 0001 again, `downgrade` printed nothing, and
  `alembic current` was empty. Checked from a separate psql connection, the database had
  no schemas and no version table.
- **Root cause:** `env.py` ran `SET lock_timeout` on the connection *before* handing it to
  Alembic. SQLAlchemy 2.x opens a transaction on the first statement ("autobegin").
  Alembic saw a transaction already open, assumed its owner would commit, and skipped its
  own commits. Nobody committed, so closing the connection rolled everything back.
- **Fix:** set the timeout as a connection option (`connect_args={"options":
  "-c lock_timeout=5s"}`), so no statement runs before Alembic takes over. Verified: each
  step now persists when checked from a new connection.
  `test_migrations_persist_reverse_and_reapply` checks every step that way.
- **Lesson:** a success message isn't proof of success. Verify the effect from
  **outside** (a separate connection), not from the same process. Two smaller lessons
  from the same run: Postgres validates a policy's table references when the policy is
  created, so table order inside a migration matters (`clinic_doctors` before `doctors`);
  and with `transaction_per_migration`, a migration that fails mid-run leaves every
  earlier one committed and recorded, so the logbook always matches reality.

## 2026-11-26 · M1 · A cheap "false" hid a missing tenant context
- **Symptom:** the test "no tenant context → error" passed for 10 of 12 tables, but
  `bills` and `bill_items` silently returned **0 rows** instead of raising.
- **Root cause:** finance tables add `AND ctx_can_read_finance()`. With no context that
  function returned `false` cheaply, and Postgres used it to skip the scan entirely, so
  the doctor/clinic checks that would have raised the error never ran. Nothing leaked
  (0 rows), but a forgotten context would have looked like "no bills" instead of a bug.
- **Fix:** `ctx_can_read_finance()` and `ctx_is_clinic_admin()` now assert the
  doctor/clinic context **first** (`PERFORM medguard.ctx_doctor_id()`), then return the
  flag. Both tests raise as designed. Proven by putting the old function back: exactly
  those two tests fail.
- **Lesson:** Postgres doesn't promise the order in which it evaluates conditions, and it
  skips work it can prove is unnecessary. A safety check must not depend on evaluation
  order: make each piece fail loudly on its own.

## 2026-11-26 · M1 · A test that could never fail in production
- **Symptom:** `SET ROLE medguard` (agent → admin) *succeeded* in the agent-role test.
- **Root cause:** the tests log in as the admin and switch to `medguard_agent`. Postgres
  checks `SET ROLE` against the **login** user (the admin), not the current role. In
  production the agent logs in as itself and has no route to any other role.
- **Fix:** test the real property instead: the application roles are members of **no**
  other role (`test_application_roles_belong_to_no_other_role`).
- **Lesson:** know what your test harness simulates and what it doesn't. Impersonating a
  role is faithful for table privileges and RLS (which use the current role), but not for
  role switching (which uses the login role).

## 2026-11-23 · M0 · After a DB restart, the first request fails, then waits seconds
- **Symptom:** while testing the outage behaviour of `/health/ready`, we restarted
  Postgres. Once it was back and accepting connections, the **first** request still
  returned 503 (`AdminShutdown`). After adding psycopg_pool's connection check, the
  test's next request hit `PoolTimeout` instead: it waited about 1 s before succeeding.
- **Root cause (two layers, both proven):**
  1. The pool lends out idle connections without testing them. A DB restart kills
     every server-side connection, but the pool doesn't know until one is used, so
     the first user eats the error. On Cloud SQL this happens after every maintenance
     window and failover.
  2. psycopg_pool's `check=` fixes that, but its retry loop
     (`_getconn_with_check_loop`) backs off **0 s, 1 s, 2 s, 4 s…** between dead
     connections (`AttemptWithBackoff`, INITIAL_DELAY = 1.0). A restart leaves *all*
     k pooled connections dead, so the next request waits about 2^(k−1) − 1 s, which
     is ~15 s for a full pool of 5. We read this in the library source and measured it
     (2 dead connections → 1,010 ms; 1 dead → 9 ms).
- **Fix:** a custom `check` in `app/db/pool.py`: if one connection is dead, assume
  they all are and call `pool.check()` to purge every idle connection in one pass,
  then retry with a fresh one. First request after a DB restart: **200 in 6 ms**.
  Regression test `test_ready_survives_killed_pool_connections` fills the pool to 4,
  kills them all with `pg_terminate_backend`, and requires a 200 within the 0.5 s
  timeout. Proven to fail both with no check (`AdminShutdown`) and with the plain
  check (`PoolTimeout`).
- **Lesson:** "the DB is back" ≠ "the app is back". Test recovery, not just failure.
  When a library's default behaviour matters, read its source and measure it; the
  backoff isn't in the docs we'd normally read. Also: a test can be wrong. Our first
  version waited on `pool_size`, which counts connections still being opened. Prove a
  regression test fails without the fix before trusting it.
