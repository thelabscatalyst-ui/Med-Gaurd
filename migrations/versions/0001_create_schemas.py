"""Create MedGuard's schemas and lock down database-wide defaults.

Revision: 0001  ·  Revises: (none)

A schema is a named section of the database. Splitting by purpose lets us grant each
role access to whole sections (the AI agent never even gets USAGE on identity_vault).

Hardening (docs/THREAT_MODEL.md):
  * nobody may CREATE objects in `public`                     (B9: no planted functions)
  * nobody gets TEMPORARY tables/functions by default          (B9: no pg_temp functions)
    Roles that genuinely need temp tables (the M3 sync job) are granted it explicitly.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = {
    "medguard": "Internal helpers: tenant-context functions used by RLS policies.",
    "ops": "Pseudonymized mirror of ClinicOS operational data. Every table is RLS-protected.",
    "identity_vault": "Encrypted patient and doctor identities. Never readable by the AI agent.",
    "semantic": "Curated, documented views the SQL agent queries (M4).",
    "knowledge": "Regulatory documents and chunks for retrieval (M5).",
    "telemetry": "LLM traces: latency, tokens, cost (M9).",
    "audit": "Append-only record of who asked what (M9).",
}


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for name, comment in SCHEMAS.items():
        op.execute(f"CREATE SCHEMA {name}")
        op.execute(f"COMMENT ON SCHEMA {name} IS '{comment}'")

    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("""
        DO $$ BEGIN
            EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database());
        END $$;
    """)
    for name in reversed(list(SCHEMAS)):
        op.execute(f"DROP SCHEMA {name}")  # no CASCADE: fails loudly if anything is left inside
    op.execute("DROP EXTENSION IF EXISTS vector")
