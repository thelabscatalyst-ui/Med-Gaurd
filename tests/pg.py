"""Database helpers for integration tests: throwaway databases, migrations, seed data.

Integration tests never touch the dev database (`medguard`). Each test session creates
its own `medguard_test` database from scratch, runs every migration, and seeds a small
world designed to catch leaks:

    clinic 1  owner doctor 10, associate doctor 11
    clinic 2  owner doctor 20
    solo      doctor 30 (no clinic)
    doctor 11 ALSO has a solo practice: the "works in two places" case
"""

from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.db.tenant import _SET_CONTEXT_SQL, _SETTING_NAMES, TenantContext

SERVER = "postgresql://medguard:medguard@localhost:5433"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

# (doctor_id, clinic_id) → how many rows of each kind. clinic_id None = solo practice.
VISITS = {(10, 1): 3, (11, 1): 2, (11, None): 1, (20, 2): 4, (30, None): 2}
BILLS = {(10, 1): 2, (11, 1): 1, (20, 2): 2, (30, None): 1}
EXPENSES = {(10, 1): 1, (20, 2): 1}


def database_url(name: str) -> str:
    return f"{SERVER}/{name}"


def recreate_database(name: str) -> str:
    with psycopg.connect(database_url("postgres"), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return database_url(name)


def alembic(url: str, action: str, target: str) -> None:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    getattr(command, action)(cfg, target)


def seed(url: str) -> None:
    """Insert the test world as the superuser (superusers bypass RLS)."""
    h = b"\x00"  # row_hash placeholder
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO ops.clinics (id, city, row_hash) VALUES (1, 'Ludhiana', %s), (2, 'Mohali', %s)",
            (h, h),
        )
        conn.execute(
            "INSERT INTO ops.doctors (id, specialization, row_hash) VALUES "
            "(10, 'GP', %s), (11, 'Pediatrics', %s), (20, 'Dental', %s), (30, 'GP', %s)",
            (h, h, h, h),
        )
        conn.execute(
            "INSERT INTO ops.clinic_doctors (id, clinic_id, doctor_id, role, is_active, row_hash) VALUES "
            "(1, 1, 10, 'owner', true, %s), (2, 1, 11, 'associate', true, %s), (3, 2, 20, 'owner', true, %s)",
            (h, h, h),
        )
        # One patient per (doctor, clinic) pair, mirrored in the identity vault.
        pairs = sorted(set(VISITS) | set(BILLS), key=lambda p: (p[0], p[1] or 0))
        patient_of = {}
        for pid, (doctor, clinic) in enumerate(pairs, start=100):
            patient_of[(doctor, clinic)] = pid
            conn.execute(
                "INSERT INTO ops.patients (id, patient_ref, doctor_id, clinic_id, row_hash) VALUES (%s, %s, %s, %s, %s)",
                (pid, f"PAT-{pid}", doctor, clinic, h),
            )
            conn.execute(
                "INSERT INTO identity_vault.patients (patient_id, patient_ref, doctor_id, clinic_id, name_ciphertext) "
                "VALUES (%s, %s, %s, %s, %s)",
                (pid, f"PAT-{pid}", doctor, clinic, b"ciphertext"),
            )
        visit_id = 1000
        for (doctor, clinic), n in VISITS.items():
            for token in range(1, n + 1):
                visit_id += 1
                conn.execute(
                    "INSERT INTO ops.visits (id, doctor_id, clinic_id, patient_id, visit_date, token_number, status, row_hash) "
                    "VALUES (%s, %s, %s, %s, current_date, %s, 'done', %s)",
                    (visit_id, doctor, clinic, patient_of[(doctor, clinic)], token, h),
                )
        bill_id = 2000
        for (doctor, clinic), n in BILLS.items():
            for _ in range(n):
                bill_id += 1
                conn.execute(
                    "INSERT INTO ops.bills (id, doctor_id, clinic_id, patient_id, visit_id, total, paid_amount, row_hash) "
                    "VALUES (%s, %s, %s, %s, 0, 500, 500, %s)",
                    (bill_id, doctor, clinic, patient_of[(doctor, clinic)], h),
                )
                conn.execute(
                    "INSERT INTO ops.bill_items (id, bill_id, doctor_id, clinic_id, description, unit_price, total, row_hash) "
                    "VALUES (%s, %s, %s, %s, 'Consultation', 500, 500, %s)",
                    (bill_id, bill_id, doctor, clinic, h),
                )
        # At least one row in every remaining table: a policy is only evaluated against
        # rows that exist, so an empty table would hide a broken policy.
        for i, (doctor, clinic) in enumerate([(10, 1), (20, 2), (30, None)], start=1):
            patient = patient_of[(doctor, clinic)]
            conn.execute(
                "INSERT INTO ops.appointments (id, doctor_id, clinic_id, patient_id, appointment_date,"
                " appointment_time, status, row_hash) VALUES (%s, %s, %s, %s, current_date, '10:00', 'scheduled', %s)",
                (4000 + i, doctor, clinic, patient, h),
            )
            conn.execute(
                "INSERT INTO ops.prescriptions (id, doctor_id, clinic_id, patient_id, diagnosis, row_hash)"
                " VALUES (%s, %s, %s, %s, 'Viral fever', %s)",
                (5000 + i, doctor, clinic, patient, h),
            )
            conn.execute(
                "INSERT INTO ops.prescription_items (id, prescription_id, doctor_id, clinic_id, drug_name, row_hash)"
                " VALUES (%s, %s, %s, %s, 'Paracetamol', %s)",
                (6000 + i, 5000 + i, doctor, clinic, h),
            )
            conn.execute(
                "INSERT INTO ops.feedback (id, doctor_id, clinic_id, patient_id, rating, row_hash)"
                " VALUES (%s, %s, %s, %s, 5, %s)",
                (7000 + i, doctor, clinic, patient, h),
            )
        expense_id = 3000
        for (doctor, clinic), n in EXPENSES.items():
            for _ in range(n):
                expense_id += 1
                conn.execute(
                    "INSERT INTO ops.expenses (id, doctor_id, clinic_id, category, amount, expense_date, row_hash) "
                    "VALUES (%s, %s, %s, 'rent', 10000, current_date, %s)",
                    (expense_id, doctor, clinic, h),
                )


def expected(counts: dict, *, doctor: int | None = None, clinic: int | None | str = "any") -> int:
    """How many seeded rows match a doctor and/or clinic (clinic=None means solo)."""
    return sum(
        n
        for (d, c), n in counts.items()
        if (doctor is None or d == doctor) and (clinic == "any" or c == clinic)
    )


@contextmanager
def as_tenant(
    url: str, role: str, ctx: TenantContext | None = None, *, raw_settings: dict | None = None
):
    """A transaction running as `role` (like a real login as that role), with tenant context.

    `ctx` goes through the same SQL the application uses. `raw_settings` lets a test set
    values the TenantContext class would refuse, to prove the DATABASE also refuses them.
    """
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        if ctx is not None:
            settings = ctx.as_settings()
            conn.execute(_SET_CONTEXT_SQL, [p for n in _SETTING_NAMES for p in (n, settings[n])])
        for name, value in (raw_settings or {}).items():
            conn.execute("SELECT set_config(%s, %s, true)", (name, value))
        yield conn


def rows(conn, query: str) -> list[tuple]:
    return conn.execute(query).fetchall()


def by_owner(conn, table: str) -> Counter:
    """Visible rows of a table, counted per (doctor_id, clinic_id)."""
    return Counter(rows(conn, f"SELECT doctor_id, clinic_id FROM ops.{table}"))
