"""Alembic runtime: connects to the database and runs the migrations.

We don't use SQLAlchemy models (MedGuard writes raw SQL), so there is no autogenerate
and no `target_metadata`. Every migration is hand-written SQL in migrations/versions/.

URL resolution, in order:
  1. `-x db_url=...` on the command line, or `sqlalchemy.url` set by tests
  2. MIGRATIONS_DATABASE_URL (admin connection, from app.config)
  3. DATABASE_URL (local fallback: the Docker superuser)
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings

if context.config.config_file_name:
    # Shows "Running upgrade 0001 -> 0002 …" lines. Keep the app's own loggers alive.
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    explicit = context.get_x_argument(as_dictionary=True).get(
        "db_url"
    ) or context.config.get_main_option("sqlalchemy.url")
    if explicit:
        url = explicit
    else:
        settings = get_settings()
        url = str(settings.migrations_database_url or settings.database_url)
    # SQLAlchemy needs to be told which driver to use: psycopg 3, same as the app.
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online() -> None:
    engine = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
        # Fail fast instead of queueing behind a long lock (docs/MIGRATIONS.md §5).
        # Set as a CONNECTION OPTION, never with a statement here: any statement before
        # Alembic takes over opens a transaction ("autobegin"), Alembic then skips its own
        # commits, and every migration is silently rolled back (docs/FAILURES.md 2026-11-26).
        connect_args={"options": "-c lock_timeout=5s"},
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            # Each migration commits on its own, so a failure in 0004 leaves 0001–0003
            # applied and recorded: the logbook always matches reality.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL-script) mode is not used in MedGuard; run against a database.")
run_migrations_online()
