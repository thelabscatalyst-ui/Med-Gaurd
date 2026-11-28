"""Create the three application roles and the tenant-context functions RLS relies on.

Revision: 0002  ·  Revises: 0001

ROLES (least privilege; table grants come with the tables in 0003/0004)
  medguard_agent  runs the AI's SQL. Read-only, RLS-filtered, no identity_vault.
  medguard_api    the API itself: re-identification (identity_vault), later telemetry/audit.
  medguard_sync   the ClinicOS sync job (M3): reads and writes every tenant's rows.

  Roles are created NOLOGIN here. Login + password are environment secrets, set outside
  migrations (Secret Manager in the cloud). Roles are CLUSTER-wide in Postgres, so they may
  already exist (e.g. created by another local database): creation is idempotent, and the
  security attributes are re-asserted every time.

TENANT CONTEXT
  The API sets four transaction-local settings from the verified ClinicOS token:
      app.doctor_id        e.g. '42'
      app.clinic_id        e.g. '7', or 'solo' for a doctor with no clinic
      app.is_clinic_admin  'true' only for an owner acting in their own clinic
      app.can_read_finance 'true' only when the ClinicOS PIN was verified
  RLS policies read them through the functions below. Missing or empty doctor/clinic
  context RAISES an error: the database fails closed, never open (THREAT_MODEL B4).
  'solo' is an explicit value so "solo doctor" can never be confused with "forgot to set".
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ROLES = ("medguard_agent", "medguard_api", "medguard_sync")
CONTEXT_FUNCTIONS = (
    "medguard.ctx_doctor_id()",
    "medguard.ctx_clinic_id()",
    "medguard.ctx_is_clinic_admin()",
    "medguard.ctx_can_read_finance()",
)


def upgrade() -> None:
    for role in ROLES:
        op.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} NOLOGIN;
                END IF;
            END $$;
        """)
        # Re-asserted even if the role already existed. LOGIN is deliberately NOT touched.
        op.execute(f"ALTER ROLE {role} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")

    op.execute("GRANT USAGE ON SCHEMA medguard TO medguard_agent, medguard_api, medguard_sync")

    op.execute("""
        CREATE FUNCTION medguard.ctx_doctor_id() RETURNS integer
        LANGUAGE plpgsql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
        DECLARE v text := current_setting('app.doctor_id', true);
        BEGIN
            IF v IS NULL OR v = '' THEN
                RAISE EXCEPTION 'medguard: tenant context not set (app.doctor_id)'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN v::integer;
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION medguard.ctx_clinic_id() RETURNS integer
        LANGUAGE plpgsql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
        DECLARE v text := current_setting('app.clinic_id', true);
        BEGIN
            IF v IS NULL OR v = '' THEN
                RAISE EXCEPTION 'medguard: tenant context not set (app.clinic_id)'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF v = 'solo' THEN
                RETURN NULL;  -- a doctor with no clinic: only clinic_id IS NULL rows
            END IF;
            RETURN v::integer;
        END $$;
    """)
    # The two flag functions FIRST assert that the doctor/clinic context exists. Otherwise a
    # cheap 'false' (no PIN) lets Postgres skip the scan and the doctor/clinic checks that
    # would have raised: no data leaks, but the missing context goes unnoticed
    # (docs/FAILURES.md 2026-11-26).
    op.execute("""
        CREATE FUNCTION medguard.ctx_is_clinic_admin() RETURNS boolean
        LANGUAGE plpgsql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
        DECLARE clinic integer := medguard.ctx_clinic_id();  -- raises if context missing
        BEGIN
            PERFORM medguard.ctx_doctor_id();                 -- raises if context missing
            -- An admin flag without a clinic means nothing: a solo doctor administers no clinic.
            RETURN clinic IS NOT NULL
               AND coalesce(current_setting('app.is_clinic_admin', true), '') = 'true';
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION medguard.ctx_can_read_finance() RETURNS boolean
        LANGUAGE plpgsql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
        BEGIN
            PERFORM medguard.ctx_doctor_id();                 -- raises if context missing
            PERFORM medguard.ctx_clinic_id();
            RETURN coalesce(current_setting('app.can_read_finance', true), '') = 'true';
        END $$;
    """)
    for fn in CONTEXT_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO medguard_agent, medguard_api")


def downgrade() -> None:
    for fn in reversed(CONTEXT_FUNCTIONS):
        op.execute(f"DROP FUNCTION {fn}")
    op.execute("REVOKE USAGE ON SCHEMA medguard FROM medguard_agent, medguard_api, medguard_sync")
    # Roles are NOT dropped: they are cluster-wide and may hold grants in other databases.
    # Drop them by hand (DROP OWNED BY …; DROP ROLE …) only when retiring MedGuard entirely.
