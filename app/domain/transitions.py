"""The only place an invoice's state changes (spec §5.2, FR-60, approval-and-audit §5).

Each transition is one function. It checks that the edge is legal, that the actor may
take it (role on the invoice's entity, or the system for worker steps), that a reason is
given where required, then sets the state and writes exactly one audit event in the
caller's database transaction. Nothing here commits: the caller commits or rolls back
both the change and its audit event together.

Separation-of-duties rules that need approval and edit history (approval-and-audit §2)
are added to these functions by slice S12; flag checks before submission (FR-37) by S11.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.audit.chain import append_event
from app.domain.actor import Actor, Role
from app.domain.models import Invoice
from app.domain.states import InvoiceState as S


class TransitionError(Exception):
    """Base class for refused transitions."""


class IllegalTransition(TransitionError):
    """The state machine has no such edge from the invoice's current state."""


class PermissionDenied(TransitionError):
    """The actor may not take this transition on this invoice's entity."""


class ReasonRequired(TransitionError):
    """The transition needs a non-empty reason."""


@dataclass(frozen=True)
class Edge:
    name: str
    sources: frozenset[S]
    target: S
    roles: frozenset[Role] = frozenset()
    system: bool = False  # background workers may take it
    reason_required: bool = False


def _edge(name: str, sources: set[S] | S, target: S, *, roles=(), system=False, reason=False):
    srcs = frozenset(sources) if isinstance(sources, set) else frozenset({sources})
    return Edge(name, srcs, target, frozenset(roles), system, reason)


# The state machine of spec §5.2 (plus FR-23's touch-free path). One edge per function below.
EDGES: dict[str, Edge] = {
    e.name: e
    for e in (
        _edge("start_extraction", S.RECEIVED, S.EXTRACTING, system=True),
        _edge("extraction_succeeded", S.EXTRACTING, S.NEEDS_REVIEW, system=True),
        _edge("extraction_touch_free", S.EXTRACTING, S.READY_FOR_APPROVAL, system=True),
        _edge("extraction_failed", S.EXTRACTING, S.EXTRACTION_FAILED, system=True, reason=True),
        _edge("retry_extraction", S.EXTRACTION_FAILED, S.EXTRACTING, roles=[Role.CLERK]),
        _edge("enter_manually", S.EXTRACTION_FAILED, S.NEEDS_REVIEW, roles=[Role.CLERK]),
        _edge("submit_for_approval", S.NEEDS_REVIEW, S.READY_FOR_APPROVAL, roles=[Role.CLERK]),
        _edge("discard", S.NEEDS_REVIEW, S.DISCARDED, roles=[Role.CLERK], reason=True),
        _edge(
            "route_to_approval",
            S.READY_FOR_APPROVAL,
            S.IN_APPROVAL,
            roles=[Role.CLERK],
            system=True,
        ),
        _edge("approve", S.IN_APPROVAL, S.APPROVED, roles=[Role.APPROVER]),
        _edge("reject", S.IN_APPROVAL, S.REJECTED, roles=[Role.APPROVER], reason=True),
        _edge("return_to_review", S.REJECTED, S.NEEDS_REVIEW, roles=[Role.CLERK], system=True),
        _edge("export", S.APPROVED, S.EXPORTED, roles=[Role.ACCOUNTANT]),
        _edge("mark_paid", S.EXPORTED, S.PAID, roles=[Role.ACCOUNTANT]),
        _edge(
            "reopen",
            {S.APPROVED, S.EXPORTED, S.PAID},
            S.NEEDS_REVIEW,
            roles=[Role.ADMIN],
            reason=True,
        ),
    )
}

# (source, target) -> edge. Each pair is unique, so a target state names one edge.
LEGAL: dict[tuple[S, S], Edge] = {}
for _e in EDGES.values():
    for _src in _e.sources:
        if (_src, _e.target) in LEGAL:
            raise RuntimeError(f"duplicate edge {_src}->{_e.target}")
        LEGAL[(_src, _e.target)] = _e


APPROVAL_DECISIONS = frozenset({"approve", "reject"})


def _check_actor(edge: Edge, invoice: Invoice, actor: Actor) -> None:
    if actor.is_system:
        if not edge.system:
            raise PermissionDenied(f"{edge.name} needs a user, not {actor.audit_name}")
        return
    if not any(actor.has_role(invoice.entity_id, role) for role in edge.roles):
        needed = ", ".join(sorted(edge.roles)) or "the system"
        raise PermissionDenied(f"{edge.name} needs role {needed} on this invoice's entity")
    if edge.name in APPROVAL_DECISIONS and actor.has_role(invoice.entity_id, Role.ADMIN):
        # Spec §2 role table: admins cannot approve, even when they also hold APPROVER
        # (product decision on PR #25). Admins configure; they do not decide.
        raise PermissionDenied(f"admins cannot {edge.name} invoices on an entity they administer")


def _apply(
    session: Session,
    invoice: Invoice,
    edge: Edge,
    actor: Actor,
    reason: str | None,
    data: dict[str, Any] | None,
) -> Invoice:
    current = invoice.state
    if current not in edge.sources:
        raise IllegalTransition(f"cannot {edge.name} an invoice in state {current}")
    _check_actor(edge, invoice, actor)
    reason = reason.strip() if reason else None
    if edge.reason_required and not reason:
        raise ReasonRequired(f"{edge.name} needs a reason")

    if invoice.id is None:
        raise ValueError("create invoices with create_invoice before transitioning them")
    invoice._state = edge.target.value
    if edge.reason_required:
        invoice.status_reason = reason
    session.add(invoice)
    append_event(
        session,
        actor=actor.audit_name,
        action=f"invoice.{edge.name}",
        entity_id=invoice.entity_id,
        invoice_id=invoice.id,
        reason=reason,
        data={**(data or {}), "from": current.value, "to": edge.target.value},
    )
    session.flush()
    return invoice


def transition_to(
    session: Session,
    invoice: Invoice,
    target: S,
    actor: Actor,
    *,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> Invoice:
    """Move `invoice` to `target` by the one legal edge between them, or raise."""
    edge = LEGAL.get((invoice.state, S(target)))
    if edge is None:
        raise IllegalTransition(f"no transition from {invoice.state} to {target}")
    return _apply(session, invoice, edge, actor, reason, data)


def create_invoice(
    session: Session, actor: Actor, *, entity_id: int, source: str, **fields: Any
) -> Invoice:
    """Create an invoice in `received` (FR-13) and record `invoice.received`."""
    if not actor.is_system and not actor.has_role(entity_id, Role.CLERK):
        raise PermissionDenied("creating an invoice needs role clerk on the entity")
    invoice = Invoice(entity_id=entity_id, source=source, **fields)
    session.add(invoice)
    session.flush()
    append_event(
        session,
        actor=actor.audit_name,
        action="invoice.received",
        entity_id=entity_id,
        invoice_id=invoice.id,
        data={"to": S.RECEIVED.value, "source": source},
    )
    return invoice


# --- one function per transition -------------------------------------------------------


def _make(name: str):
    edge = EDGES[name]

    def fn(
        session: Session,
        invoice: Invoice,
        actor: Actor,
        *,
        reason: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Invoice:
        return _apply(session, invoice, edge, actor, reason, data)

    fn.__name__ = fn.__qualname__ = name
    sources = ", ".join(sorted(edge.sources))
    fn.__doc__ = f"{sources} -> {edge.target}" + (
        " (reason required)" if edge.reason_required else ""
    )
    return fn


start_extraction = _make("start_extraction")
extraction_succeeded = _make("extraction_succeeded")
extraction_touch_free = _make("extraction_touch_free")
extraction_failed = _make("extraction_failed")
retry_extraction = _make("retry_extraction")
enter_manually = _make("enter_manually")
submit_for_approval = _make("submit_for_approval")
discard = _make("discard")
route_to_approval = _make("route_to_approval")
approve = _make("approve")
reject = _make("reject")
return_to_review = _make("return_to_review")
export = _make("export")
mark_paid = _make("mark_paid")
reopen = _make("reopen")
