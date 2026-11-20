"""Application settings, read from environment variables (prefix INVOICE_FLOW_)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INVOICE_FLOW_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    secret_key: str = "dev-only-change-me"  # noqa: S105 - overridden via env outside dev
    field_key: str | None = None
    worker_threads: int = 4

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'invoice_flow.db'}"


def get_settings() -> Settings:
    return Settings()
