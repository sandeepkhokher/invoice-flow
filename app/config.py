"""Application settings, read from environment variables (prefix INVOICE_FLOW_)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVOICE_FLOW_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    secret_key: str = "dev-only-change-me"  # noqa: S105 - overridden via env outside dev
    field_key: str | None = None
    worker_threads: int = 4
    # Apply Alembic migrations when the app starts (one process, one SQLite file; NFR-6).
    auto_migrate: bool = True
    # data-protection §3: idle timeout 30 minutes.
    session_idle_minutes: int = 30
    # SC-1: lock an account for `login_lockout_minutes` after this many failed sign-ins in a row.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'invoice_flow.db'}"


def get_settings() -> Settings:
    return Settings()
