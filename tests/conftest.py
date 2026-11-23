import re
from pathlib import Path

import pytest
from alembic.config import Config
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient

from alembic import command
from app.accounts import passwords
from app.accounts.models import Entity
from app.accounts.service import EntityGrants, create_user, set_grants
from app.config import Settings
from app.db import make_engine, make_sessionmaker
from app.domain.actor import Actor, Permission, Role
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"  # test-only; synthetic users in temp databases
SEEDER = Actor.system("test")


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Cheap Argon2id parameters so tests stay fast. Still Argon2id (type ID)."""
    monkeypatch.setattr(
        passwords,
        "HASHER",
        PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID),
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


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


@pytest.fixture
def entity_rows(sessions):
    """Entities 1 and 2, for tests that create invoices (invoices.entity_id is a FK)."""
    with sessions() as s:
        s.add_all(
            [
                Entity(id=1, legal_name="Entity One Pvt Ltd", pan="ZXCPA0001A"),
                Entity(id=2, legal_name="Entity Two Pvt Ltd", pan="ZXCPB0002B"),
            ]
        )
        s.commit()


# --- users and signing in ------------------------------------------------------------------


def grants(*roles: Role, tier: int | None = None, perms=()) -> EntityGrants:
    if Role.APPROVER in roles and tier is None:
        tier = 1
    return EntityGrants(roles=frozenset(roles), tier=tier, permissions=frozenset(perms))


def add_user(sessions, email: str, by_entity: dict[int, EntityGrants], name: str | None = None):
    """Create a user with grants; returns the user's id."""
    with sessions() as s:
        user = create_user(
            s, SEEDER, email=email, name=name or email.split("@")[0], password=PASSWORD
        )
        set_grants(s, SEEDER, user, by_entity, managed_entity_ids=list(by_entity))
        s.commit()
        return user.id


def login(client: TestClient, email: str, password: str = PASSWORD, follow: bool = False):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token, "next": "/"},
        follow_redirects=follow,
    )


def csrf_of(client: TestClient, path: str = "/account") -> str:
    """The signed-in session's CSRF token, read from a page's forms."""
    page = client.get(path)
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


@pytest.fixture
def world(app, entity_rows):
    """Two entities and one user per role on entity 1, plus a user of entity 2 only.

    Returns the user ids by key. Everyone's password is `PASSWORD`.
    """
    sessions = app.state.sessions
    ids = {}
    for role in Role:
        perms = {Permission.FLAGS_OVERRIDE} if role == Role.CLERK else set()
        ids[role.value] = add_user(
            sessions, f"{role.value}@a.example", {1: grants(role, perms=perms)}
        )
    ids["admin_b"] = add_user(sessions, "admin@b.example", {2: grants(Role.ADMIN)})
    ids["clerk_b"] = add_user(sessions, "clerk@b.example", {2: grants(Role.CLERK)})
    return ids


def signed_in(app, email: str) -> TestClient:
    c = TestClient(app)
    resp = login(c, email)
    assert resp.status_code == 303, resp.text
    return c
