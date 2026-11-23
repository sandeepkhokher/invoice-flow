"""The entity-scoped repository (data-protection §2, FR-3, CLAUDE.md conventions).

Every query that reads invoices, vendors, exports, or any other per-entity record goes
through an `EntityScope`. It is built from the signed-in user's grants and is the one
place that adds "entity_id IN (the user's entities)" to a query. A record of an entity the
user has no grant on (or no grant with the needed role) is reported as `NotFound`, which
the web layer turns into 404, so the UI never reveals that the record exists.

Models declare their entity column as `entity_id`, or name it in `__entity_column__`
(the `entities` table uses its own `id`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import Select, false, select
from sqlalchemy.orm import Session

from app.domain.actor import Actor, Role

T = TypeVar("T")


class NotFound(LookupError):
    """No such record, or the user may not see it. Rendered as 404 either way."""


class Forbidden(PermissionError):
    """The user is signed in but none of their roles allows this section. Rendered as 403."""


def entity_column(model: Any) -> Any:
    return getattr(model, getattr(model, "__entity_column__", "entity_id"))


@dataclass(frozen=True)
class EntityScope:
    """What one user may see. `selected_entity_id` is the header switcher (None = all)."""

    actor: Actor
    selected_entity_id: int | None = None

    # --- which entities ---------------------------------------------------------------

    def entity_ids(self, *roles: Role) -> frozenset[int]:
        """Entities where the user holds any role, or (if given) one of `roles`."""
        wanted = frozenset(roles)
        return frozenset(
            entity_id
            for entity_id, held in self.actor.roles_by_entity.items()
            if held and (not wanted or held & wanted)
        )

    def listing_entity_ids(self, *roles: Role) -> frozenset[int]:
        """Like `entity_ids`, narrowed to the switcher's entity when one is picked."""
        ids = self.entity_ids(*roles)
        if self.selected_entity_id is not None:
            return ids & {self.selected_entity_id}
        return ids

    def roles(self) -> frozenset[Role]:
        """Roles in the switcher's entity, or across all the user's entities (for menus)."""
        if self.selected_entity_id is not None:
            return self.actor.roles_by_entity.get(self.selected_entity_id, frozenset())
        return frozenset().union(*self.actor.roles_by_entity.values())

    def require_any(self, *roles: Role) -> frozenset[int]:
        """Section-level check: the user holds one of `roles` somewhere, else `Forbidden`."""
        ids = self.entity_ids(*roles)
        if not ids:
            raise Forbidden("none of your roles gives access to this page")
        return ids

    def require_entity(self, entity_id: int, *roles: Role) -> int:
        """Record-level check on an entity id: `NotFound` unless the user holds a role there."""
        if entity_id not in self.entity_ids(*roles):
            raise NotFound
        return entity_id

    # --- queries ------------------------------------------------------------------------

    def where(self, stmt: Select, model: Any, *roles: Role, listing: bool = False) -> Select:
        """Restrict `stmt` to rows of `model` in the user's entities."""
        ids = self.listing_entity_ids(*roles) if listing else self.entity_ids(*roles)
        if not ids:
            return stmt.where(false())
        return stmt.where(entity_column(model).in_(sorted(ids)))

    def select(self, model: type[T], *roles: Role, listing: bool = True) -> Select:
        return self.where(select(model), model, *roles, listing=listing)

    def list(self, session: Session, model: type[T], *roles: Role, order_by: Iterable = ()):
        stmt = self.select(model, *roles).order_by(*order_by)
        return list(session.scalars(stmt))

    def get(self, session: Session, model: type[T], record_id: int, *roles: Role) -> T:
        """One record by primary key, or `NotFound` if it is missing or out of scope."""
        stmt = self.where(select(model), model, *roles).where(model.id == record_id)  # type: ignore[attr-defined]
        obj = session.scalars(stmt).first()
        if obj is None:
            raise NotFound
        return obj
