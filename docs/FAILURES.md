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
