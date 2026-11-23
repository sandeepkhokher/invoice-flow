"""Validation flags: the only output of a tax regime (gst-invoice-rules)."""

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """How strongly a flag stops the invoice. Flags never auto-reject or auto-approve."""

    BLOCK = "block"  # cannot be approved until fixed or overridden with a reason
    WARN = "warn"  # approver must acknowledge
    INFO = "info"  # shown, no action needed


@dataclass(frozen=True, slots=True)
class Flag:
    """One finding on an invoice.

    `rule_id` is stable (for example `GST3.WRONG_TAX_HEAD`) so the UI, overrides and the audit
    trail can refer to it. `field` points at the offending field (`lines[2].hsn_sac`) when there is
    one. `fix` tells the clerk what to do about it.
    """

    rule_id: str
    severity: Severity
    message: str
    fix: str
    field: str | None = None
