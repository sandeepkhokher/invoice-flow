"""Invoice states (spec §5.2)."""

from enum import StrEnum


class InvoiceState(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    EXTRACTION_FAILED = "extraction_failed"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_APPROVAL = "ready_for_approval"
    IN_APPROVAL = "in_approval"
    REJECTED = "rejected"
    APPROVED = "approved"
    EXPORTED = "exported"
    PAID = "paid"
    DISCARDED = "discarded"
