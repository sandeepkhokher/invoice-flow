"""Run the Alembic migrations programmatically (app start-up, `make seed-demo`)."""

from pathlib import Path

from alembic.config import Config

from alembic import command

REPO_ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    cfg = Config()  # no ini file: keep the app's logging configuration untouched
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade_to_head(url: str) -> None:
    command.upgrade(alembic_config(url), "head")
