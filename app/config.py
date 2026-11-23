"""Application settings, read from environment variables (prefix INVOICE_FLOW_)."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVOICE_FLOW_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    secret_key: str = "dev-only-change-me"  # noqa: S105 - overridden via env outside dev
    field_key: str | None = None
    # In-process job worker (NFR-1: 4 by default; 0 switches the worker off).
    worker_threads: int = 4
    worker_poll_seconds: float = 1.0
    # NFR-4: a job claimed this long ago without finishing is retried by another worker.
    job_lock_minutes: int = 10
    job_max_attempts: int = 3
    # FR-10 upload limits (data-protection §4).
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pages: int = 50
    max_files_per_upload: int = 200  # NFR-1 peak burst
    # Apply Alembic migrations when the app starts (one process, one SQLite file; NFR-6).
    auto_migrate: bool = True
    # data-protection §3: idle timeout 30 minutes.
    session_idle_minutes: int = 30
    # SC-1: lock an account for `login_lockout_minutes` after this many failed sign-ins in a row.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    # S9 extraction (spec §5.3). The key comes only from the environment (data-protection §5);
    # without it the demo extractor runs (walking skeleton: the flow works offline).
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key")
    )
    extraction_model: str = "claude-opus-5-5"
    # Starting at `medium` (spec §5.3); S10's eval tunes it (C6).
    extraction_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    extraction_max_tokens: int = 16000
    # Ground truth for the demo extractor: `make gen-invoices` writes it.
    demo_manifest: Path = Path("evals/data/manifest.json")

    @property
    def files_dir(self) -> Path:
        """Uploaded invoice files: under the data directory, never under `app/static`."""
        return self.data_dir / "files"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'invoice_flow.db'}"


def get_settings() -> Settings:
    return Settings()
