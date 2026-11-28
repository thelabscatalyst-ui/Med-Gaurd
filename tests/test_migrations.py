"""Migrations: they persist, they reverse, and they never leave a table unprotected.

See docs/MIGRATIONS.md for the rules these tests enforce.
"""

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests import pg

pytestmark = pytest.mark.integration

PROTECTED_SCHEMAS = ("ops", "identity_vault")


def test_there_is_exactly_one_head():
    # Two branches that each added "the next" migration would create two heads.
    heads = ScriptDirectory.from_config(Config(str(pg.ALEMBIC_INI))).get_heads()
    assert len(heads) == 1, heads


def test_every_protected_table_has_rls_enabled_forced_and_a_policy(rls_db):
    """The meta-test: a future migration that forgets RLS on a new table fails the build."""
    with psycopg.connect(rls_db) as conn:
        tables = conn.execute(
            """
            SELECT n.nspname || '.' || c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')
            ORDER BY 1
            """,
            (list(PROTECTED_SCHEMAS),),
        ).fetchall()
    assert len(tables) == 14, [t[0] for t in tables]
    unprotected = [
        name for name, enabled, forced, policies in tables if not (enabled and forced and policies)
    ]
    assert unprotected == []


def test_no_protected_table_is_granted_to_public(rls_db):
    with psycopg.connect(rls_db) as conn:
        public_grants = conn.execute(
            "SELECT table_schema, table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'PUBLIC' AND table_schema = ANY(%s)",
            (list(PROTECTED_SCHEMAS),),
        ).fetchall()
    assert public_grants == []


def test_migrations_persist_reverse_and_reapply():
    """upgrade → downgrade to nothing → upgrade, each step checked from a NEW connection.

    Checking from a fresh connection matters: on 2026-11-26 every step reported success
    while nothing was committed (docs/FAILURES.md). Only a separate connection sees that.
    """
    url = pg.recreate_database("medguard_migrations_test")

    def state() -> tuple[str | None, int]:
        with psycopg.connect(url) as conn:
            has_table = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
            # After `downgrade base` the version table still exists, just empty.
            row = (
                conn.execute("SELECT version_num FROM alembic_version").fetchone()
                if has_table
                else None
            )
            version = row[0] if row else None
            schemas = conn.execute(
                "SELECT count(*) FROM pg_namespace WHERE nspname IN "
                "('medguard','ops','identity_vault','semantic','knowledge','telemetry','audit')"
            ).fetchone()[0]
        return version, schemas

    head = ScriptDirectory.from_config(Config(str(pg.ALEMBIC_INI))).get_current_head()

    pg.alembic(url, "upgrade", "head")
    assert state() == (head, 7)

    pg.alembic(url, "downgrade", "base")
    assert state() == (None, 0)

    pg.alembic(url, "upgrade", "head")
    assert state() == (head, 7)
