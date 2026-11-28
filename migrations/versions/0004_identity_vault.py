"""identity_vault: the only place real patient and doctor identities live.

Revision: 0004  ·  Revises: 0003

  * Names and phones are stored ENCRYPTED by the application (key in Secret Manager,
    encryption added in M3), so even a database admin or a stolen backup sees ciphertext.
    `key_version` allows key rotation.
  * The AI agent (medguard_agent) gets NO access, not even USAGE on the schema. It cannot
    read this table even if every other defence fails (THREAT_MODEL A3).
  * medguard_api reads it to turn PAT-… codes back into names after an answer is final
    (re-identification), and only through the same tenant RLS as `ops` (THREAT_MODEL D1).
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_PREDICATE = """
    (clinic_id = (SELECT medguard.ctx_clinic_id())
     OR (clinic_id IS NULL AND (SELECT medguard.ctx_clinic_id()) IS NULL))
    AND (doctor_id = (SELECT medguard.ctx_doctor_id())
         OR (SELECT medguard.ctx_is_clinic_admin()))
"""
DOCTOR_PREDICATE = """
    doctor_id = (SELECT medguard.ctx_doctor_id())
    OR ((SELECT medguard.ctx_is_clinic_admin())
        AND EXISTS (SELECT 1 FROM ops.clinic_doctors cd
                    WHERE cd.doctor_id = doctors.doctor_id
                      AND cd.clinic_id = (SELECT medguard.ctx_clinic_id())
                      AND cd.is_active))
"""

TABLES = {
    "patients": (
        """
        patient_id        integer  PRIMARY KEY,
        patient_ref       text     NOT NULL UNIQUE,
        doctor_id         integer  NOT NULL,
        clinic_id         integer,
        name_ciphertext   bytea    NOT NULL,
        phone_ciphertext  bytea,
        key_version       smallint NOT NULL DEFAULT 1,
        synced_at         timestamptz NOT NULL DEFAULT now()
        """,
        TENANT_PREDICATE,
    ),
    "doctors": (
        """
        doctor_id        integer  PRIMARY KEY,
        name_ciphertext  bytea    NOT NULL,
        key_version      smallint NOT NULL DEFAULT 1,
        synced_at        timestamptz NOT NULL DEFAULT now()
        """,
        DOCTOR_PREDICATE,
    ),
}


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA identity_vault TO medguard_api, medguard_sync")
    for table, (columns, predicate) in TABLES.items():
        qualified = f"identity_vault.{table}"
        op.execute(f"CREATE TABLE {qualified} ({columns})")
        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_read ON {qualified}
                FOR SELECT TO medguard_api
                USING ({predicate})
        """)
        op.execute(f"""
            CREATE POLICY sync_all ON {qualified}
                FOR ALL TO medguard_sync
                USING (true) WITH CHECK (true)
        """)
        op.execute(f"REVOKE ALL ON {qualified} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON {qualified} TO medguard_api")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO medguard_sync")
    op.execute("CREATE INDEX ON identity_vault.patients (clinic_id, doctor_id)")


def downgrade() -> None:
    for table in reversed(list(TABLES)):
        op.execute(f"DROP TABLE identity_vault.{table}")
    op.execute("REVOKE USAGE ON SCHEMA identity_vault FROM medguard_api, medguard_sync")
