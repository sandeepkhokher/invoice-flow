"""Who is acting: a user with per-entity roles, or the system (background workers).

Roles are granted per entity (spec §2). Slice S5 builds `Actor` values from the
`role_grants` table; the domain core only reads them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    CLERK = "clerk"
    APPROVER = "approver"
    ACCOUNTANT = "accountant"
    ADMIN = "admin"
    AUDITOR = "auditor"


class Permission(StrEnum):
    """Extra per-entity permissions on top of roles (spec §2)."""

    FLAGS_OVERRIDE = "flags:override"
    BANK_DETAILS_VIEW = "bank_details:view"


@dataclass(frozen=True)
class Actor:
    """`user_id` is None only for the system actor; `name` labels it in the audit trail."""

    user_id: int | None
    roles_by_entity: Mapping[int, frozenset[Role]] = field(default_factory=dict)
    system_name: str | None = None
    permissions_by_entity: Mapping[int, frozenset[Permission]] = field(default_factory=dict)
    # The approval tier an approver acts at, per entity (spec §2 "approver (with tier 1..n)").
    approver_tier_by_entity: Mapping[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.user_id is None) == (self.system_name is None):
            raise ValueError("an actor is either a user (user_id) or the system (system_name)")
        frozen = {k: frozenset(Role(r) for r in v) for k, v in self.roles_by_entity.items()}
        object.__setattr__(self, "roles_by_entity", MappingProxyType(frozen))
        perms = {
            k: frozenset(Permission(p) for p in v) for k, v in self.permissions_by_entity.items()
        }
        object.__setattr__(self, "permissions_by_entity", MappingProxyType(perms))
        tiers = dict(self.approver_tier_by_entity)
        object.__setattr__(self, "approver_tier_by_entity", MappingProxyType(tiers))

    @classmethod
    def user(
        cls,
        user_id: int,
        roles_by_entity: Mapping[int, set[Role] | frozenset[Role]],
        *,
        permissions_by_entity: Mapping[int, set[Permission] | frozenset[Permission]] | None = None,
        approver_tier_by_entity: Mapping[int, int] | None = None,
    ) -> Actor:
        return cls(
            user_id=user_id,
            roles_by_entity={k: frozenset(v) for k, v in roles_by_entity.items()},
            permissions_by_entity={
                k: frozenset(v) for k, v in (permissions_by_entity or {}).items()
            },
            approver_tier_by_entity=dict(approver_tier_by_entity or {}),
        )

    @classmethod
    def system(cls, name: str) -> Actor:
        return cls(user_id=None, system_name=name)

    @property
    def is_system(self) -> bool:
        return self.user_id is None

    @property
    def audit_name(self) -> str:
        return f"system:{self.system_name}" if self.is_system else f"user:{self.user_id}"

    def has_role(self, entity_id: int, role: Role) -> bool:
        return role in self.roles_by_entity.get(entity_id, frozenset())

    def has_permission(self, entity_id: int, permission: Permission) -> bool:
        return permission in self.permissions_by_entity.get(entity_id, frozenset())
