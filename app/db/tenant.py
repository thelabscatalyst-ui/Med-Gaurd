"""Tenant context: the ONE way the application tells Postgres who is asking.

Row-Level Security policies (migrations 0003/0004) decide which rows are visible by
reading four transaction-local settings. This module is the only code allowed to set
them, and it only ever sets them with `is_local = true`, which is the same as SET LOCAL:

    BEGIN
      SET TRANSACTION READ ONLY                       ← agent queries can never write
      set_config('app.doctor_id', '42', true)         ← true = LOCAL: wiped at COMMIT/ROLLBACK
      set_config('app.clinic_id', '7' | 'solo', true)
      set_config('app.is_clinic_admin', 'true'|'false', true)
      set_config('app.can_read_finance', 'true'|'false', true)
      … queries …
    COMMIT                                            ← context gone; the pooled connection is clean

Why LOCAL matters: the pool lends the same connection to the next request, possibly a
different clinic. A session-level SET would leak this clinic's identity into it
(THREAT_MODEL B3). LOCAL can't leak past the transaction.

Why set_config() and not `SET LOCAL app.doctor_id = …`: SET can't take query parameters,
so it would need string formatting. set_config() takes normal bound parameters, so there
is no injection path. (The LLM's own SQL is never allowed to call set_config: M7 guard.)

Where the values come from: the verified ClinicOS token (M9). This module trusts its
caller; the token check is the caller's job.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Who is asking. Built from the ClinicOS token; immutable once built."""

    doctor_id: int
    clinic_id: int | None  # None = solo practice (no clinic)
    is_clinic_admin: bool = False  # owner acting in their own clinic (clinic:admin scope)
    can_read_finance: bool = False  # ClinicOS PIN verified (finance:read scope)

    def __post_init__(self) -> None:
        # `bool` is a subclass of `int` in Python, so check it explicitly: doctor_id=True
        # must not silently become doctor 1.
        if not isinstance(self.doctor_id, int) or isinstance(self.doctor_id, bool):
            raise TypeError("doctor_id must be an int")
        if self.doctor_id <= 0:
            raise ValueError("doctor_id must be positive")
        if self.clinic_id is not None:
            if not isinstance(self.clinic_id, int) or isinstance(self.clinic_id, bool):
                raise TypeError("clinic_id must be an int or None")
            if self.clinic_id <= 0:
                raise ValueError("clinic_id must be positive")
        if self.is_clinic_admin and self.clinic_id is None:
            raise ValueError("a solo doctor (no clinic) cannot be a clinic admin")

    def as_settings(self) -> dict[str, str]:
        """The exact values handed to Postgres. 'solo' is explicit, never an empty string."""
        return {
            "app.doctor_id": str(self.doctor_id),
            "app.clinic_id": "solo" if self.clinic_id is None else str(self.clinic_id),
            "app.is_clinic_admin": "true" if self.is_clinic_admin else "false",
            "app.can_read_finance": "true" if self.can_read_finance else "false",
        }


_SETTING_NAMES = ("app.doctor_id", "app.clinic_id", "app.is_clinic_admin", "app.can_read_finance")
# One set_config(name, value, is_local=true) per setting; names and values are both bound
# parameters, so nothing is ever formatted into the SQL string.
_SET_CONTEXT_SQL = "SELECT " + ", ".join(["set_config(%s, %s, true)"] * len(_SETTING_NAMES))


@asynccontextmanager
async def tenant_transaction(
    pool: AsyncConnectionPool, ctx: TenantContext, *, read_only: bool = True
) -> AsyncIterator[AsyncConnection]:
    """Borrow a connection and run a transaction scoped to `ctx`.

    Everything inside sees only the rows RLS allows for this tenant. On exit the
    transaction commits (or rolls back on error) and the context disappears with it.
    """
    async with pool.connection() as conn:
        async with conn.transaction():
            if read_only:
                # Must be the first statement of the transaction.
                await conn.execute("SET TRANSACTION READ ONLY")
            settings = ctx.as_settings()
            params = [part for name in _SETTING_NAMES for part in (name, settings[name])]
            await conn.execute(_SET_CONTEXT_SQL, params)
            yield conn
