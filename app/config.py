"""Application settings, read from environment variables (and `.env` when running locally).

There is exactly one Settings object per process, built lazily by `get_settings()`.
Nothing else in the codebase reads `os.environ` directly — if a value is configurable,
it lives here, typed and validated, so a typo in an env var fails at startup instead of
at 2 a.m. on the first request that happens to need it.

Rule carried over from ClinicOS: no `@property` on this class (it took ClinicOS
production down once). Derive values at the call site instead.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # MedGuard's OWN database (never ClinicOS's). Required: no default on purpose.
    database_url: PostgresDsn
    # Admin connection used ONLY by migrations (DDL: tables, roles, policies). The running
    # app should connect with a least-privilege role instead. Falls back to database_url
    # locally, where both are the Docker superuser.
    migrations_database_url: PostgresDsn | None = None
    db_pool_min_size: int = Field(default=1, ge=0)
    db_pool_max_size: int = Field(default=5, ge=1)
    db_connect_timeout_s: int = Field(default=3, ge=1)

    # How long /health/ready waits for a DB connection before declaring "not ready".
    health_db_timeout_s: float = Field(default=2.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
