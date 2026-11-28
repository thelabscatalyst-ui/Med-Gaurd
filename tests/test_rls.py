"""Row-Level Security and role permissions, attacked directly.

Every test runs as one of the real application roles against a freshly migrated database
(see tests/pg.py for the seeded world). Test names say what must hold; comments name the
docs/THREAT_MODEL.md entry each one proves.
"""

import asyncio
from collections import Counter

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from app.db.tenant import TenantContext, tenant_transaction
from tests.pg import BILLS, EXPENSES, VISITS, as_tenant, by_owner, expected, rows

pytestmark = pytest.mark.integration

AGENT, API, SYNC = "medguard_agent", "medguard_api", "medguard_sync"
OPS_TABLES = [
    "clinics", "clinic_doctors", "doctors", "patients", "appointments", "visits", "bills",
    "bill_items", "prescriptions", "prescription_items", "expenses", "feedback",
]  # fmt: skip

OWNER_1 = TenantContext(doctor_id=10, clinic_id=1, is_clinic_admin=True, can_read_finance=True)
OWNER_2 = TenantContext(doctor_id=20, clinic_id=2, is_clinic_admin=True, can_read_finance=True)
ASSOCIATE_1 = TenantContext(doctor_id=11, clinic_id=1, can_read_finance=True)
DOCTOR_11_SOLO = TenantContext(doctor_id=11, clinic_id=None, can_read_finance=True)
SOLO_30 = TenantContext(doctor_id=30, clinic_id=None, can_read_finance=True)


# ── Isolation between clinics and doctors (B1, B2) ────────────────────────────────────


def test_owner_sees_every_doctor_in_own_clinic_and_nothing_else(rls_db):
    with as_tenant(rls_db, AGENT, OWNER_1) as conn:
        visible = by_owner(conn, "visits")
    assert set(visible) == {(10, 1), (11, 1)}
    assert sum(visible.values()) == expected(VISITS, clinic=1)


def test_asking_for_another_clinic_by_name_returns_nothing(rls_db):
    # B1: the AI "forgets" the filter, or is told to look at clinic 1 explicitly.
    with as_tenant(rls_db, AGENT, OWNER_2) as conn:
        assert rows(conn, "SELECT count(*) FROM ops.visits WHERE clinic_id = 1") == [(0,)]
        assert rows(conn, "SELECT count(*) FROM ops.visits") == [(expected(VISITS, clinic=2),)]
        assert rows(conn, "SELECT DISTINCT clinic_id FROM ops.patients") == [(2,)]


def test_associate_sees_only_their_own_patients(rls_db):
    # B2: inside one clinic, an associate must not see the owner's rows.
    with as_tenant(rls_db, AGENT, ASSOCIATE_1) as conn:
        assert set(by_owner(conn, "visits")) == {(11, 1)}
        assert set(by_owner(conn, "patients")) == {(11, 1)}
        assert rows(conn, "SELECT count(*) FROM ops.visits WHERE doctor_id = 10") == [(0,)]


def test_doctor_working_in_two_places_sees_only_the_active_one(rls_db):
    # ClinicOS's clinic switcher: the same doctor, two workplaces, never merged.
    with as_tenant(rls_db, AGENT, ASSOCIATE_1) as conn:
        assert set(by_owner(conn, "visits")) == {(11, 1)}
    with as_tenant(rls_db, AGENT, DOCTOR_11_SOLO) as conn:
        assert set(by_owner(conn, "visits")) == {(11, None)}


def test_solo_doctor_sees_only_their_own_practice(rls_db):
    with as_tenant(rls_db, AGENT, SOLO_30) as conn:
        assert set(by_owner(conn, "visits")) == {(30, None)}  # not doctor 11's solo rows
        assert rows(conn, "SELECT count(*) FROM ops.clinics") == [(0,)]


def test_admin_flag_for_a_solo_doctor_is_ignored_by_the_database(rls_db):
    # TenantContext refuses this combination; prove the DATABASE refuses it too.
    raw = {"app.doctor_id": "30", "app.clinic_id": "solo", "app.is_clinic_admin": "true"}
    with as_tenant(rls_db, AGENT, raw_settings=raw) as conn:
        assert set(by_owner(conn, "visits")) == {(30, None)}


def test_owner_sees_their_clinics_doctors_associate_sees_only_self(rls_db):
    with as_tenant(rls_db, AGENT, OWNER_1) as conn:
        assert rows(conn, "SELECT id FROM ops.doctors ORDER BY id") == [(10,), (11,)]
    with as_tenant(rls_db, AGENT, ASSOCIATE_1) as conn:
        assert rows(conn, "SELECT id FROM ops.doctors") == [(11,)]
        assert rows(conn, "SELECT doctor_id FROM ops.clinic_doctors") == [(11,)]


# ── Finance behind the ClinicOS PIN (C5) ─────────────────────────────────────────────


def test_finance_tables_are_invisible_without_the_pin(rls_db):
    no_pin = TenantContext(doctor_id=10, clinic_id=1, is_clinic_admin=True, can_read_finance=False)
    with as_tenant(rls_db, AGENT, no_pin) as conn:
        for table in ("bills", "bill_items", "expenses"):
            assert rows(conn, f"SELECT count(*) FROM ops.{table}") == [(0,)], table
        assert rows(conn, "SELECT count(*) FROM ops.visits") == [(expected(VISITS, clinic=1),)]
    with as_tenant(rls_db, AGENT, OWNER_1) as conn:
        assert rows(conn, "SELECT count(*) FROM ops.bills") == [(expected(BILLS, clinic=1),)]
        assert rows(conn, "SELECT count(*) FROM ops.expenses") == [(expected(EXPENSES, clinic=1),)]


# ── Missing context fails closed (B4) ────────────────────────────────────────────────


@pytest.mark.parametrize("table", OPS_TABLES)
def test_no_tenant_context_is_an_error_never_all_rows(rls_db, table):
    with pytest.raises(errors.InsufficientPrivilege, match="tenant context not set"):
        with as_tenant(rls_db, AGENT) as conn:
            conn.execute(f"SELECT * FROM ops.{table}").fetchall()


def test_context_does_not_survive_into_the_next_pooled_request(rls_db):
    # B3: the pool lends the SAME connection to the next request. Prove the first
    # request's clinic is gone, using the application's real tenant_transaction().
    async def scenario():
        async def login_as_agent(conn):
            await conn.execute("SET ROLE medguard_agent")
            await conn.commit()

        pool = AsyncConnectionPool(
            rls_db, min_size=1, max_size=1, open=False, configure=login_as_agent
        )
        await pool.open(wait=True)
        try:
            async with tenant_transaction(pool, OWNER_1) as conn:
                first_pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
                assert (await (await conn.execute("SELECT count(*) FROM ops.visits")).fetchone())[
                    0
                ] > 0
            async with pool.connection() as conn:  # next request, no context set
                pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
                assert pid == first_pid, "test needs the same pooled connection"
                with pytest.raises(errors.InsufficientPrivilege):
                    await conn.execute("SELECT count(*) FROM ops.visits")
        finally:
            await pool.close()

    asyncio.run(scenario())


def test_tenant_transaction_is_read_only_even_for_a_role_that_can_write(rls_db):
    # The READ ONLY layer holds on its own, independent of grants.
    async def scenario():
        async def login_as_sync(conn):
            await conn.execute("SET ROLE medguard_sync")
            await conn.commit()

        pool = AsyncConnectionPool(
            rls_db, min_size=1, max_size=1, open=False, configure=login_as_sync
        )
        await pool.open(wait=True)
        try:
            with pytest.raises(errors.ReadOnlySqlTransaction):
                async with tenant_transaction(pool, OWNER_1) as conn:
                    await conn.execute("DELETE FROM ops.visits")
        finally:
            await pool.close()

    asyncio.run(scenario())


# ── What the AI's role can never do (B5, B7, B8, B9, A3) ─────────────────────────────

FORBIDDEN_FOR_AGENT = {
    "insert": "INSERT INTO ops.visits (id, doctor_id, visit_date, token_number, status, patient_id, row_hash)"
    " VALUES (9999, 10, current_date, 1, 'x', 1, '\\x00')",
    "update": "UPDATE ops.bills SET paid_amount = 0",
    "delete": "DELETE FROM ops.visits",
    "truncate": "TRUNCATE ops.visits",  # TRUNCATE would bypass RLS entirely
    "drop": "DROP TABLE ops.visits",
    "alter": "ALTER TABLE ops.visits DISABLE ROW LEVEL SECURITY",
    "identity_vault": "SELECT * FROM identity_vault.patients",  # A3: names never reach the AI
    "create_function": "CREATE FUNCTION public.leak() RETURNS int LANGUAGE sql AS 'SELECT 1'",  # B9
    "temp_table": "CREATE TEMP TABLE stash (x int)",  # B9: no pg_temp objects either
    "read_server_file": "SELECT pg_read_file('/etc/passwd')",  # B8
    "list_server_dir": "SELECT pg_ls_dir('.')",  # B8
    "copy_to_program": "COPY ops.visits TO PROGRAM 'id'",  # B8
    "large_object_import": "SELECT lo_import('/etc/passwd')",  # B8
}


@pytest.mark.parametrize("attempt", FORBIDDEN_FOR_AGENT, ids=list(FORBIDDEN_FOR_AGENT))
def test_agent_role_is_refused(rls_db, attempt):
    # Context is set and the transaction is NOT read-only, so this proves the ROLE's
    # privileges alone stop it.
    with pytest.raises(errors.InsufficientPrivilege):
        with as_tenant(rls_db, AGENT, OWNER_1) as conn:
            conn.execute(FORBIDDEN_FOR_AGENT[attempt])


def test_application_roles_belong_to_no_other_role(rls_db):
    """SET ROLE / RESET ROLE can only reach roles you are a member of, so with zero
    memberships a role logged in as itself can never climb to another role.

    (Why not just try `SET ROLE medguard` in a test? These tests log in as the admin and
    switch roles; Postgres checks SET ROLE against the LOGIN user, so that attempt would
    succeed here but fail in production. Testing the membership itself is the honest check.)
    """
    with psycopg.connect(rls_db) as conn:
        memberships = conn.execute(
            "SELECT member.rolname, granted.rolname FROM pg_auth_members m "
            "JOIN pg_roles member ON member.oid = m.member "
            "JOIN pg_roles granted ON granted.oid = m.roleid "
            "WHERE member.rolname IN ('medguard_agent', 'medguard_api', 'medguard_sync')"
        ).fetchall()
    assert memberships == []


# ── The other roles ──────────────────────────────────────────────────────────────────


def test_api_reidentifies_only_its_own_tenants_patients(rls_db):
    # D1: turning PAT codes back into names goes through the same RLS.
    with as_tenant(rls_db, API, OWNER_1) as conn:
        assert set(rows(conn, "SELECT doctor_id, clinic_id FROM identity_vault.patients")) == {
            (10, 1),
            (11, 1),
        }
    with as_tenant(rls_db, API, SOLO_30) as conn:
        assert set(rows(conn, "SELECT doctor_id, clinic_id FROM identity_vault.patients")) == {
            (30, None)
        }


def test_sync_role_loads_every_tenant_without_context(rls_db):
    with as_tenant(rls_db, SYNC) as conn:
        assert rows(conn, "SELECT count(*) FROM ops.visits") == [(expected(VISITS),)]
        assert rows(conn, "SELECT count(*) FROM identity_vault.patients")[0][0] > 0


def test_application_roles_can_never_bypass_rls(rls_db):
    with psycopg.connect(rls_db) as conn:
        flags = rows(
            conn,
            "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
            "WHERE rolname IN ('medguard_agent', 'medguard_api', 'medguard_sync') ORDER BY rolname",
        )
    assert [f[0] for f in flags] == ["medguard_agent", "medguard_api", "medguard_sync"]
    assert all(not any(f[1:]) for f in flags), flags


def test_rls_world_is_seeded_as_designed(rls_db):
    # Guards the tests themselves: if seeding changed, the counts above would mean nothing.
    with as_tenant(rls_db, SYNC) as conn:
        assert by_owner(conn, "visits") == Counter(VISITS)
        for table in OPS_TABLES:
            assert rows(conn, f"SELECT count(*) FROM ops.{table}")[0][0] > 0, (
                f"{table} must not be empty"
            )
