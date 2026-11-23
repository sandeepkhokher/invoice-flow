"""Invoice state machine (spec §5.2) and the transitions module (ticket #6 AC2, AC3)."""

import itertools
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.audit import AuditEvent, verify_chain
from app.domain import transitions as t
from app.domain.actor import Actor, Role
from app.domain.models import Invoice
from app.domain.states import InvoiceState as S

APP_DIR = Path(__file__).resolve().parent.parent / "app"
ENTITY = 1
OTHER_ENTITY = 2

SYSTEM = Actor.system("worker")
EVERYONE = Actor.user(99, {ENTITY: set(Role)})  # every role on the entity
# Admins may not approve or reject (spec §2), so approval decisions use everyone-but-admin.
DECIDER = Actor.user(98, {ENTITY: set(Role) - {Role.ADMIN}})

# Spec §5.2, written out independently of the module so the test pins the design.
SPEC_EDGES = {
    (S.RECEIVED, S.EXTRACTING),
    (S.EXTRACTING, S.NEEDS_REVIEW),
    (S.EXTRACTING, S.READY_FOR_APPROVAL),  # FR-23 touch-free path
    (S.EXTRACTING, S.EXTRACTION_FAILED),
    (S.EXTRACTION_FAILED, S.EXTRACTING),
    (S.EXTRACTION_FAILED, S.NEEDS_REVIEW),
    (S.NEEDS_REVIEW, S.READY_FOR_APPROVAL),
    (S.NEEDS_REVIEW, S.DISCARDED),
    (S.READY_FOR_APPROVAL, S.IN_APPROVAL),
    (S.IN_APPROVAL, S.APPROVED),
    (S.IN_APPROVAL, S.REJECTED),
    (S.REJECTED, S.NEEDS_REVIEW),
    (S.APPROVED, S.EXPORTED),
    (S.EXPORTED, S.PAID),
    (S.APPROVED, S.NEEDS_REVIEW),
    (S.EXPORTED, S.NEEDS_REVIEW),
    (S.PAID, S.NEEDS_REVIEW),
}
ILLEGAL_EDGES = sorted(set(itertools.product(S, S)) - SPEC_EDGES)

# Shortest legal path from `received` to each state, as edge names.
PATH_TO = {
    S.RECEIVED: [],
    S.EXTRACTING: ["start_extraction"],
    S.EXTRACTION_FAILED: ["start_extraction", "extraction_failed"],
    S.NEEDS_REVIEW: ["start_extraction", "extraction_succeeded"],
    S.READY_FOR_APPROVAL: ["start_extraction", "extraction_touch_free"],
    S.DISCARDED: ["start_extraction", "extraction_succeeded", "discard"],
    S.IN_APPROVAL: ["start_extraction", "extraction_touch_free", "route_to_approval"],
}
PATH_TO[S.APPROVED] = [*PATH_TO[S.IN_APPROVAL], "approve"]
PATH_TO[S.REJECTED] = [*PATH_TO[S.IN_APPROVAL], "reject"]
PATH_TO[S.EXPORTED] = [*PATH_TO[S.APPROVED], "export"]
PATH_TO[S.PAID] = [*PATH_TO[S.EXPORTED], "mark_paid"]


def actor_for(edge: t.Edge) -> Actor:
    if edge.system:
        return SYSTEM
    return DECIDER if edge.name in t.APPROVAL_DECISIONS else EVERYONE


def new_invoice(session) -> Invoice:
    return t.create_invoice(session, SYSTEM, entity_id=ENTITY, source="upload")


def drive_to(session, state: S) -> Invoice:
    inv = new_invoice(session)
    for name in PATH_TO[state]:
        edge = t.EDGES[name]
        getattr(t, name)(session, inv, actor_for(edge), reason="a reason for the record")
    assert inv.state == state
    return inv


def audit_count(session) -> int:
    return session.scalar(select(func.count()).select_from(AuditEvent))


# --- the state machine matches the spec -----------------------------------------------


def test_module_has_exactly_the_spec_edges():
    assert set(t.LEGAL) == SPEC_EDGES
    assert {s.value for s in S} == {
        "received", "extracting", "extraction_failed", "needs_review", "ready_for_approval",
        "in_approval", "rejected", "approved", "exported", "paid", "discarded",
    }  # fmt: skip


def test_one_function_per_transition():
    for name, edge in t.EDGES.items():
        fn = getattr(t, name)
        assert callable(fn) and fn.__name__ == name, edge


@pytest.mark.parametrize(("source", "target"), ILLEGAL_EDGES, ids=lambda s: str(s))
def test_illegal_transition_raises(session, source, target):
    inv = drive_to(session, source)
    before = audit_count(session)
    with pytest.raises(t.IllegalTransition):
        t.transition_to(session, inv, target, EVERYONE, reason="a reason for the record")
    with pytest.raises(t.IllegalTransition):
        t.transition_to(session, inv, target, SYSTEM, reason="a reason for the record")
    assert inv.state == source
    assert audit_count(session) == before


@pytest.mark.parametrize(
    ("name", "state"),
    [(n, s) for n, e in t.EDGES.items() for s in S if s not in e.sources],
)
def test_named_transition_from_wrong_state_raises(session, name, state):
    inv = drive_to(session, state)
    with pytest.raises(t.IllegalTransition):
        getattr(t, name)(session, inv, actor_for(t.EDGES[name]), reason="a reason for the record")
    assert inv.state == state


@pytest.mark.parametrize(("source", "target"), sorted(SPEC_EDGES), ids=lambda s: str(s))
def test_every_legal_transition_writes_exactly_one_audit_event(session, source, target):
    inv = drive_to(session, source)
    before = audit_count(session)
    edge = t.LEGAL[(source, target)]
    t.transition_to(session, inv, target, actor_for(edge), reason="a reason for the record")
    session.commit()
    assert inv.state == target
    assert audit_count(session) == before + 1
    last = session.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc())).first()
    assert last.action == f"invoice.{edge.name}"
    assert last.invoice_id == inv.id and last.entity_id == ENTITY
    data = json.loads(last.data_json)
    assert (data["from"], data["to"]) == (source.value, target.value)
    assert verify_chain(session).ok


def test_create_invoice_starts_received_with_one_event(session):
    inv = new_invoice(session)
    assert inv.state == S.RECEIVED
    assert audit_count(session) == 1
    assert session.scalars(select(AuditEvent)).one().action == "invoice.received"


# --- same transaction: rollback leaves neither -----------------------------------------


def test_rollback_leaves_neither_state_change_nor_audit_event(sessions):
    with sessions() as s:
        inv = new_invoice(s)
        s.commit()
        inv_id, events = inv.id, audit_count(s)

    with sessions() as s:
        inv = s.get(Invoice, inv_id)
        t.start_extraction(s, inv, SYSTEM)
        assert audit_count(s) == events + 1  # visible inside the transaction
        s.rollback()

    with sessions() as s:
        assert s.get(Invoice, inv_id).state == S.RECEIVED
        assert audit_count(s) == events


def test_failed_audit_write_rolls_back_state_change(sessions, monkeypatch):
    with sessions() as s:
        inv_id = new_invoice(s).id
        s.commit()

    def boom(*_a, **_k):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(t, "append_event", boom)
    with sessions() as s:
        inv = s.get(Invoice, inv_id)
        with pytest.raises(RuntimeError):
            t.start_extraction(s, inv, SYSTEM)
        s.rollback()

    with sessions() as s:
        assert s.get(Invoice, inv_id).state == S.RECEIVED
        assert audit_count(s) == 1


def test_commit_persists_both(sessions):
    with sessions() as s:
        inv = new_invoice(s)
        t.start_extraction(s, inv, SYSTEM)
        s.commit()
        inv_id = inv.id
    with sessions() as s:
        assert s.get(Invoice, inv_id).state == S.EXTRACTING
        assert audit_count(s) == 2


# --- permissions and reasons -------------------------------------------------------------


@pytest.mark.parametrize("name", [n for n, e in t.EDGES.items() if not e.system])
def test_user_only_transitions_refuse_the_system(session, name):
    inv = drive_to(session, next(iter(t.EDGES[name].sources)))
    with pytest.raises(t.PermissionDenied):
        getattr(t, name)(session, inv, SYSTEM, reason="a reason for the record")


@pytest.mark.parametrize("name", [n for n, e in t.EDGES.items() if e.roles])
def test_transition_needs_its_role_on_the_invoice_entity(session, name):
    edge = t.EDGES[name]
    inv = drive_to(session, next(iter(edge.sources)))
    other_roles = set(Role) - edge.roles
    wrong_role = Actor.user(5, {ENTITY: other_roles})
    right_role_other_entity = Actor.user(6, {OTHER_ENTITY: set(edge.roles)})
    for actor in (wrong_role, right_role_other_entity):
        with pytest.raises(t.PermissionDenied):
            getattr(t, name)(session, inv, actor, reason="a reason for the record")
    assert inv.state in edge.sources


def test_system_only_transitions_refuse_users(session):
    inv = drive_to(session, S.RECEIVED)
    with pytest.raises(t.PermissionDenied):
        t.start_extraction(session, inv, EVERYONE)


def test_admin_cannot_approve(session):
    inv = drive_to(session, S.IN_APPROVAL)
    admin = Actor.user(7, {ENTITY: {Role.ADMIN, Role.CLERK, Role.ACCOUNTANT, Role.AUDITOR}})
    with pytest.raises(t.PermissionDenied):
        t.approve(session, inv, admin)


@pytest.mark.parametrize("name", ["approve", "reject"])
def test_admin_who_is_also_approver_cannot_decide(session, name):
    inv = drive_to(session, S.IN_APPROVAL)
    both = Actor.user(9, {ENTITY: {Role.ADMIN, Role.APPROVER}})
    before = audit_count(session)
    with pytest.raises(t.PermissionDenied):
        getattr(t, name)(session, inv, both, reason="checked")
    assert inv.state == S.IN_APPROVAL and audit_count(session) == before


def test_auditor_is_read_only(session):
    auditor = Actor.user(8, {ENTITY: {Role.AUDITOR}})
    for state, name in [(S.NEEDS_REVIEW, "submit_for_approval"), (S.APPROVED, "export")]:
        inv = drive_to(session, state)
        with pytest.raises(t.PermissionDenied):
            getattr(t, name)(session, inv, auditor)


@pytest.mark.parametrize("name", [n for n, e in t.EDGES.items() if e.reason_required])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reason_required(session, name, reason):
    edge = t.EDGES[name]
    inv = drive_to(session, next(iter(edge.sources)))
    before = audit_count(session)
    with pytest.raises(t.ReasonRequired):
        getattr(t, name)(session, inv, actor_for(edge), reason=reason)
    assert inv.state in edge.sources and audit_count(session) == before


def test_reason_is_recorded(session):
    inv = drive_to(session, S.APPROVED)
    t.reopen(session, inv, EVERYONE, reason="  wrong GSTIN on the approved invoice ")
    event = session.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc())).first()
    assert event.reason == "wrong GSTIN on the approved invoice"
    assert inv.status_reason == "wrong GSTIN on the approved invoice"
    assert event.actor == "user:99"


def test_create_invoice_permissions(session):
    with pytest.raises(t.PermissionDenied):
        t.create_invoice(session, Actor.user(1, {OTHER_ENTITY: {Role.CLERK}}), entity_id=ENTITY,
                         source="upload")  # fmt: skip
    clerk = Actor.user(1, {ENTITY: {Role.CLERK}})
    assert t.create_invoice(session, clerk, entity_id=ENTITY, source="upload").state == S.RECEIVED


# --- nothing else sets state ------------------------------------------------------------


def test_state_is_read_only_on_the_model():
    inv = Invoice(entity_id=ENTITY, source="upload")
    with pytest.raises(AttributeError):
        inv.state = S.APPROVED  # type: ignore[misc]
    with pytest.raises(TypeError):
        Invoice(entity_id=ENTITY, source="upload", state="approved")


def test_only_transitions_module_writes_state():
    """The private `_state` attribute is assigned only in transitions.py (and the initial value)."""
    allowed = {APP_DIR / "domain" / "transitions.py", APP_DIR / "domain" / "models.py"}
    pattern = re.compile(r"\._state\s*=|\bstate\s*=\s*['\"]|values\(\s*state\s*=")
    offenders = [
        str(p.relative_to(APP_DIR))
        for p in APP_DIR.rglob("*.py")
        if p not in allowed and pattern.search(p.read_text())
    ]
    assert offenders == []


def test_actor_is_user_or_system():
    with pytest.raises(ValueError):
        Actor(user_id=None)
    with pytest.raises(ValueError):
        Actor(user_id=1, system_name="worker")
