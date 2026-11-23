"""The role-based side menu of the layout shell (screens.md §1.1, §2).

The menu is the union of what the user's roles allow (in the switcher's entity, or across
all their entities). An item is shown only once its slice has built the route, found by
route name, so later slices add a page and its menu item appears.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import FastAPI
from starlette.routing import NoMatchFound

from app.domain.actor import Role

ALL_ROLES = frozenset(Role)


@dataclass(frozen=True)
class MenuItem:
    label: str
    route_name: str
    roles: frozenset[Role]
    children: tuple[MenuItem, ...] = ()


MENU: tuple[MenuItem, ...] = (
    MenuItem("My work", "my_work", ALL_ROLES),
    MenuItem("Invoices", "invoice_list", ALL_ROLES),
    MenuItem("Upload", "upload", frozenset({Role.CLERK})),
    MenuItem("Approvals", "approval_inbox", frozenset({Role.APPROVER})),
    MenuItem("Export", "export_new", frozenset({Role.ACCOUNTANT})),
    MenuItem(
        "Vendors",
        "vendor_list",
        frozenset({Role.CLERK, Role.ACCOUNTANT, Role.ADMIN, Role.AUDITOR}),
    ),
    MenuItem("Audit", "audit_search", frozenset({Role.AUDITOR})),
    MenuItem(
        "Admin",
        "admin_entities",
        frozenset({Role.ADMIN}),
        children=(
            MenuItem("Entities", "admin_entities", frozenset({Role.ADMIN})),
            MenuItem("Users", "admin_users", frozenset({Role.ADMIN})),
        ),
    ),
)


def items_for_roles(roles: Iterable[Role]) -> list[MenuItem]:
    """Menu items the roles allow (screens.md §2), whether or not the page exists yet."""
    held = frozenset(roles)
    return [item for item in MENU if item.roles & held]


@dataclass(frozen=True)
class Link:
    label: str
    href: str
    children: tuple[Link, ...] = ()


def _href(app: FastAPI, name: str) -> str | None:
    try:
        return app.url_path_for(name)
    except NoMatchFound:
        return None


def links_for(app: FastAPI, roles: Iterable[Role]) -> list[Link]:
    links = []
    for item in items_for_roles(roles):
        href = _href(app, item.route_name)
        if href is None:
            continue
        children = tuple(
            Link(c.label, h)
            for c in item.children
            if (c.roles & frozenset(roles)) and (h := _href(app, c.route_name))
        )
        links.append(Link(item.label, href, children))
    return links
