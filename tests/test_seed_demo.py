"""`make seed-demo` (ticket #10): 2 entities, registrations in 2 states, one user per role;
prints emails and passwords; no password is committed to the repository."""

import ast
import re

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import seed_demo
from app.accounts.models import Entity, GstRegistration, User
from app.accounts.service import actor_for
from app.audit.chain import verify_chain
from app.domain.actor import Role
from app.tax.gstin import gstin_problem
from tests.conftest import REPO_ROOT, login


def test_seed_creates_entities_registrations_and_one_user_per_role(app):
    with app.state.sessions() as db:
        users = seed_demo.seed(db)
        db.commit()
        entities = db.scalars(select(Entity)).all()
        regs = db.scalars(select(GstRegistration)).all()
        assert len(entities) == 2
        assert len({r.state_code for r in regs}) >= 2
        assert {r.entity_id for r in regs} == {e.id for e in entities}
        assert all(gstin_problem(r.gstin) is None for r in regs)
        assert all(e.pan.startswith("ZX") for e in entities)  # synthetic marker (S4)
        assert sorted(role for role, _, _ in users) == sorted(r.value for r in Role)
        for role, email, _ in users:
            uid = db.scalar(select(User.id).where(User.email == email))
            actor = actor_for(db, uid)
            assert set(actor.roles_by_entity) == {e.id for e in entities}
            assert all(rs == {Role(role)} for rs in actor.roles_by_entity.values())
        assert verify_chain(db).ok
    for _, email, password in users:
        assert email.endswith(".example")
        assert login(TestClient(app), email, password).status_code == 303


def test_seed_is_rerunnable_and_resets_passwords(app):
    with app.state.sessions() as db:
        first = seed_demo.seed(db)
        db.commit()
        second = seed_demo.seed(db)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Entity)) == 2
        assert db.scalar(select(func.count()).select_from(User)) == len(Role)
    assert [p for *_, p in first] != [p for *_, p in second]
    assert login(TestClient(app), first[0][1], first[0][2]).status_code == 401
    assert login(TestClient(app), second[0][1], second[0][2]).status_code == 303


def test_main_prints_emails_and_passwords(settings, monkeypatch, capsys):
    monkeypatch.setenv("INVOICE_FLOW_DATA_DIR", str(settings.data_dir))
    monkeypatch.delenv(seed_demo.PASSWORD_ENV, raising=False)
    assert seed_demo.main([]) == 0
    out = capsys.readouterr().out
    rows = re.findall(r"^\s+(\w+)\s+(\S+@demo\.example)\s+(\S+)$", out, re.M)
    assert sorted(r[0] for r in rows) == sorted(r.value for r in Role)
    assert len({r[2] for r in rows}) == len(rows)  # generated, one per user


def test_main_uses_the_password_from_the_environment(settings, monkeypatch, capsys):
    monkeypatch.setenv("INVOICE_FLOW_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv(seed_demo.PASSWORD_ENV, "a demo password from env")
    assert seed_demo.main([]) == 0
    assert capsys.readouterr().out.count("a demo password from env") == len(Role)
    monkeypatch.setenv(seed_demo.PASSWORD_ENV, "short")
    assert seed_demo.main([]) == 2


def test_makefile_has_seed_demo_target():
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert re.search(r"^seed-demo:.*\n\tuv run python -m app\.seed_demo$", makefile, re.M)


def test_no_demo_password_is_committed():
    """No password literal in the seed script: every `password=` argument is computed."""
    tree = ast.parse((REPO_ROOT / "app" / "seed_demo.py").read_text())
    keywords = [kw for node in ast.walk(tree) if isinstance(node, ast.Call) for kw in node.keywords]
    password_args = [kw for kw in keywords if kw.arg == "password"]
    assert password_args, "seed_demo should set passwords"
    assert not any(isinstance(kw.value, ast.Constant) for kw in password_args)
