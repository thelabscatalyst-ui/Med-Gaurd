"""The `ops` schema: a pseudonymized mirror of ClinicOS, protected by Row-Level Security.

Revision: 0003  ·  Revises: 0002

WHAT'S MIRRORED (columns follow ClinicOS database/models.py, verified 2026-11-20)
  Names, phones, addresses and free-text notes are NOT here. Patients are identified by
  `patient_ref` (PAT-<hmac>); real identities live in identity_vault (0004).
  `id` is the ClinicOS primary key, so a re-sync updates rows in place.
  Every row carries `row_hash` (change detection for the M3 sync) and `synced_at`.

WHO SEES WHICH ROWS (mirrors ClinicOS's scope_to_active_clinic + roles)
  Every tenant table carries doctor_id (NOT NULL) and clinic_id (NULL = solo practice).
  Child tables (bill_items, prescription_items) carry them too, copied from the parent
  at sync time, so each policy is a simple column comparison (fast, indexable).

    a row is visible  ⇔  it belongs to the active clinic (or both are solo)
                         AND (it is my row  OR  I am this clinic's admin)
    finance tables    ⇔  the above AND the ClinicOS PIN was verified

  The ctx_* calls are wrapped in (SELECT …) so Postgres evaluates them once per query
  instead of once per row.

POLICIES
  tenant_read  FOR SELECT  TO medguard_agent, medguard_api   (no write policy = no writes)
  sync_all     FOR ALL     TO medguard_sync                  (the sync job loads every tenant)
  Any other non-superuser role matches no policy and sees zero rows.
  FORCE ROW LEVEL SECURITY makes the policies apply even to the tables' owner.

NO FOREIGN KEYS, on purpose: this is a mirror of an external system. ClinicOS owns
referential integrity; strict FKs here would make the sync fail on any source anomaly and
dictate delete ordering. M3's contract checks report orphans instead.
NO TRUNCATE grants: TRUNCATE bypasses RLS.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SYNC_COLUMNS = """
    row_hash   bytea       NOT NULL,
    synced_at  timestamptz NOT NULL DEFAULT now()
"""

TABLES: dict[str, str] = {
    # Order matters: the `doctors` policy reads ops.clinic_doctors, so that table must exist first.
    "clinics": f"""
        id          integer PRIMARY KEY,
        city        text,
        plan_type   text,
        created_at  timestamptz,
        {SYNC_COLUMNS}""",
    "clinic_doctors": f"""
        id          integer PRIMARY KEY,
        clinic_id   integer NOT NULL,
        doctor_id   integer NOT NULL,
        role        text    NOT NULL CHECK (role IN ('owner', 'associate')),
        is_active   boolean NOT NULL,
        joined_at   timestamptz,
        {SYNC_COLUMNS}""",
    "doctors": f"""
        id              integer PRIMARY KEY,
        specialization  text,
        city            text,
        created_at      timestamptz,
        {SYNC_COLUMNS}""",
    "patients": f"""
        id               integer PRIMARY KEY,
        patient_ref      text    NOT NULL UNIQUE,
        doctor_id        integer NOT NULL,
        clinic_id        integer,
        age              integer,
        gender           text,
        blood_group      text,
        referral_source  text,
        visit_count      integer,
        first_visit      date,
        last_visit       date,
        created_at       timestamptz,
        {SYNC_COLUMNS}""",
    "appointments": f"""
        id                integer PRIMARY KEY,
        doctor_id         integer NOT NULL,
        clinic_id         integer,
        patient_id        integer NOT NULL,
        visit_id          integer,
        appointment_date  date    NOT NULL,
        appointment_time  time    NOT NULL,
        duration_mins     integer,
        appointment_type  text,
        status            text    NOT NULL,
        booked_by         text,
        is_emergency      boolean,
        arrival_status    text,
        follow_up_date    date,
        created_at        timestamptz,
        {SYNC_COLUMNS}""",
    "visits": f"""
        id              integer PRIMARY KEY,
        doctor_id       integer NOT NULL,
        clinic_id       integer,
        patient_id      integer NOT NULL,
        appointment_id  integer,
        visit_date      date    NOT NULL,
        token_number    integer NOT NULL,
        status          text    NOT NULL,
        source          text,
        is_emergency    boolean,
        check_in_time   timestamptz,
        call_time       timestamptz,
        complete_time   timestamptz,
        created_at      timestamptz,
        {SYNC_COLUMNS}""",
    "bills": f"""
        id            integer PRIMARY KEY,
        doctor_id     integer NOT NULL,
        clinic_id     integer,
        patient_id    integer NOT NULL,
        visit_id      integer NOT NULL,
        subtotal      numeric(10, 2),
        discount      numeric(10, 2),
        gst_amount    numeric(10, 2),
        total         numeric(10, 2),
        paid_amount   numeric(10, 2),
        payment_mode  text,
        paid_at       timestamptz,
        created_at    timestamptz,
        {SYNC_COLUMNS}""",
    "bill_items": f"""
        id           integer PRIMARY KEY,
        bill_id      integer NOT NULL,
        doctor_id    integer NOT NULL,
        clinic_id    integer,
        description  text    NOT NULL,
        category     text,
        quantity     integer,
        unit_price   numeric(10, 2) NOT NULL,
        total        numeric(10, 2) NOT NULL,
        gst_rate     numeric(4, 2),
        {SYNC_COLUMNS}""",
    "prescriptions": f"""
        id          integer PRIMARY KEY,
        doctor_id   integer NOT NULL,
        clinic_id   integer,
        patient_id  integer NOT NULL,
        visit_id    integer,
        diagnosis   text,
        follow_up   text,
        created_at  timestamptz,
        updated_at  timestamptz,
        {SYNC_COLUMNS}""",
    "prescription_items": f"""
        id               integer PRIMARY KEY,
        prescription_id  integer NOT NULL,
        doctor_id        integer NOT NULL,
        clinic_id        integer,
        drug_name        text    NOT NULL,
        dosage           text,
        frequency        text,
        duration         text,
        instructions     text,
        {SYNC_COLUMNS}""",
    "expenses": f"""
        id            integer PRIMARY KEY,
        doctor_id     integer NOT NULL,
        clinic_id     integer,
        category      text    NOT NULL,
        amount        numeric(10, 2) NOT NULL,
        expense_date  date    NOT NULL,
        created_at    timestamptz,
        {SYNC_COLUMNS}""",
    "feedback": f"""
        id            integer PRIMARY KEY,
        doctor_id     integer NOT NULL,
        clinic_id     integer,
        patient_id    integer NOT NULL,
        bill_id       integer,
        rating        integer,
        created_at    timestamptz,
        submitted_at  timestamptz,
        {SYNC_COLUMNS}""",
}

FINANCE_TABLES = {"bills", "bill_items", "expenses"}
TENANT_TABLES = [t for t in TABLES if t not in {"clinics", "doctors", "clinic_doctors"}]

# The row-visibility rule for every table that carries doctor_id + clinic_id.
TENANT_PREDICATE = """
    (clinic_id = (SELECT medguard.ctx_clinic_id())
     OR (clinic_id IS NULL AND (SELECT medguard.ctx_clinic_id()) IS NULL))
    AND (doctor_id = (SELECT medguard.ctx_doctor_id())
         OR (SELECT medguard.ctx_is_clinic_admin()))
"""
FINANCE_PREDICATE = "AND (SELECT medguard.ctx_can_read_finance())"

SPECIAL_PREDICATES = {
    # Members see their own clinic's row. Solo doctors (clinic NULL) see none.
    "clinics": "id = (SELECT medguard.ctx_clinic_id())",
    # My own membership; an admin sees every membership in the clinic.
    "clinic_doctors": """
        clinic_id = (SELECT medguard.ctx_clinic_id())
        AND (doctor_id = (SELECT medguard.ctx_doctor_id())
             OR (SELECT medguard.ctx_is_clinic_admin()))""",
    # Myself; an admin also sees the active doctors of their clinic.
    "doctors": """
        id = (SELECT medguard.ctx_doctor_id())
        OR ((SELECT medguard.ctx_is_clinic_admin())
            AND EXISTS (SELECT 1 FROM ops.clinic_doctors cd
                        WHERE cd.doctor_id = doctors.id
                          AND cd.clinic_id = (SELECT medguard.ctx_clinic_id())
                          AND cd.is_active))""",
}

INDEXES = [
    "CREATE INDEX ON ops.clinic_doctors (clinic_id, doctor_id)",
    "CREATE INDEX ON ops.appointments (clinic_id, appointment_date)",
    "CREATE INDEX ON ops.visits (clinic_id, visit_date)",
    "CREATE INDEX ON ops.bills (clinic_id, created_at)",
    "CREATE INDEX ON ops.bill_items (bill_id)",
    "CREATE INDEX ON ops.prescriptions (clinic_id, created_at)",
    "CREATE INDEX ON ops.prescription_items (prescription_id)",
    "CREATE INDEX ON ops.expenses (clinic_id, expense_date)",
] + [f"CREATE INDEX ON ops.{t} (clinic_id, doctor_id)" for t in TENANT_TABLES]


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA ops TO medguard_agent, medguard_api, medguard_sync")

    for table, columns in TABLES.items():
        qualified = f"ops.{table}"
        op.execute(f"CREATE TABLE {qualified} ({columns})")

        if table in SPECIAL_PREDICATES:
            predicate = SPECIAL_PREDICATES[table]
        else:
            predicate = TENANT_PREDICATE + (FINANCE_PREDICATE if table in FINANCE_TABLES else "")

        op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_read ON {qualified}
                FOR SELECT TO medguard_agent, medguard_api
                USING ({predicate})
        """)
        op.execute(f"""
            CREATE POLICY sync_all ON {qualified}
                FOR ALL TO medguard_sync
                USING (true) WITH CHECK (true)
        """)

        op.execute(f"REVOKE ALL ON {qualified} FROM PUBLIC")
        op.execute(f"GRANT SELECT ON {qualified} TO medguard_agent, medguard_api")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO medguard_sync")

    for statement in INDEXES:
        op.execute(statement)

    op.execute("COMMENT ON TABLE ops.bills IS 'Finance: visible only with the ClinicOS PIN.'")
    op.execute(
        "COMMENT ON TABLE ops.patients IS 'Pseudonymized: identities live in identity_vault.'"
    )


def downgrade() -> None:
    for table in reversed(list(TABLES)):
        op.execute(f"DROP TABLE ops.{table}")  # policies and indexes go with the table
    op.execute("REVOKE USAGE ON SCHEMA ops FROM medguard_agent, medguard_api, medguard_sync")
