"""`make seed-demo`: two demo entities, GST registrations in two states, one user per role.

All data is synthetic (data-protection §7): PANs start with `ZX` like the S4 eval set, and
emails use the reserved `.example` domain. Passwords are never committed: each run sets new
random passwords (or `INVOICE_FLOW_DEMO_PASSWORD` for every demo user, if set) and prints them.
Running it again keeps the entities and resets the demo users' passwords and grants.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy.orm import Session

from app.accounts import passwords
from app.accounts.models import Entity, GstRegistration, User
from app.accounts.service import (
    EntityGrants,
    add_registration,
    create_entity,
    create_user,
    find_user_by_email,
    set_grants,
    set_password,
)
from app.config import get_settings
from app.db import make_engine, make_sessionmaker
from app.domain.actor import Actor, Permission, Role
from app.migrate import upgrade_to_head
from app.tax.gstin import check_character

SYSTEM = Actor.system("seed-demo")
PASSWORD_ENV = "INVOICE_FLOW_DEMO_PASSWORD"  # noqa: S105 - an env var name, not a secret


def gstin(state: str, pan: str) -> str:
    first14 = f"{state}{pan}1Z"
    return first14 + check_character(first14)


ENTITIES = (
    {
        "legal_name": "Zenith Demo Manufacturing Pvt Ltd",
        "pan": "ZXCPA1234F",
        "registrations": (
            ("27", "Plot 12, MIDC Industrial Area, Pune, Maharashtra 411019"),
            ("29", "45 Residency Road, Bengaluru, Karnataka 560025"),
        ),
    },
    {
        "legal_name": "Zenith Demo Services LLP",
        "pan": "ZXFPB5678K",
        "registrations": (("33", "8 Cathedral Road, Chennai, Tamil Nadu 600086"),),
    },
)

# One user per role, each with that role on both demo entities.
USERS = (
    ("clerk@demo.example", "Demo Clerk", Role.CLERK),
    ("approver@demo.example", "Demo Approver", Role.APPROVER),
    ("accountant@demo.example", "Demo Accountant", Role.ACCOUNTANT),
    ("admin@demo.example", "Demo Admin", Role.ADMIN),
    ("auditor@demo.example", "Demo Auditor", Role.AUDITOR),
)
EXTRA_PERMISSIONS = {
    Role.CLERK: frozenset({Permission.FLAGS_OVERRIDE}),
    Role.ACCOUNTANT: frozenset({Permission.BANK_DETAILS_VIEW}),
}


def _entity(db: Session, spec: dict) -> Entity:
    entity = db.query(Entity).filter_by(pan=spec["pan"]).one_or_none()
    if entity is None:
        entity = create_entity(db, SYSTEM, legal_name=spec["legal_name"], pan=spec["pan"])
    for state, address in spec["registrations"]:
        number = gstin(state, spec["pan"])
        if db.query(GstRegistration).filter_by(gstin=number).one_or_none() is None:
            add_registration(db, SYSTEM, entity, gstin=number, address=address)
    return entity


def seed(db: Session, password: str | None = None) -> list[tuple[str, str, str]]:
    """Create or refresh the demo data. Returns (role, email, password) for each user."""
    entities = [_entity(db, spec) for spec in ENTITIES]
    entity_ids = [e.id for e in entities]
    out = []
    for email, name, role in USERS:
        pw = password or passwords.generate_password()
        user: User | None = find_user_by_email(db, email)
        if user is None:
            user = create_user(db, SYSTEM, email=email, name=name, password=pw)
        else:
            set_password(db, SYSTEM, user, pw)
            user.is_active = True
        grants = EntityGrants(
            roles=frozenset({role}),
            tier=1 if role == Role.APPROVER else None,
            permissions=EXTRA_PERMISSIONS.get(role, frozenset()),
        )
        set_grants(
            db, SYSTEM, user, dict.fromkeys(entity_ids, grants), managed_entity_ids=entity_ids
        )
        out.append((role.value, email, pw))
    return out


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade_to_head(settings.database_url)
    password = os.environ.get(PASSWORD_ENV) or None
    if password and (problem := passwords.password_problem(password)):
        print(f"{PASSWORD_ENV}: {problem}", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    try:
        with make_sessionmaker(engine)() as db:
            users = seed(db, password)
            entities = [(e["legal_name"], e["pan"], len(e["registrations"])) for e in ENTITIES]
            db.commit()
    finally:
        engine.dispose()

    print(f"Demo data in {settings.data_dir}/:")
    for name, pan, regs in entities:
        print(f"  entity  {name} (PAN {pan}, {regs} GST registration(s))")
    print("\nDemo users (synthetic; every role on both entities). Passwords change on each run:")
    width = max(len(e) for _, e, _ in users)
    for role, email, pw in users:
        print(f"  {role:<10}  {email:<{width}}  {pw}")
    print("\nSign in at http://127.0.0.1:8000/login (make run).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
