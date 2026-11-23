from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.config import Settings
from app.db import make_engine, make_sessionmaker
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path)


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


def alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def engine(settings):
    """A fresh SQLite database migrated to head with the real Alembic migrations."""
    command.upgrade(alembic_config(settings.database_url), "head")
    eng = make_engine(settings.database_url)
    yield eng
    eng.dispose()


@pytest.fixture
def sessions(engine):
    return make_sessionmaker(engine)


@pytest.fixture
def session(sessions):
    with sessions() as s:
        yield s
