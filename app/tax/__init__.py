"""Tax layer (spec FR-25-27): the `TaxRegime` interface and its India GST implementation."""

from app.tax.flags import Flag, Severity
from app.tax.india_gst import IndiaGST
from app.tax.invoice import Invoice, InvoiceLine, LineTax, Party, TaxHead
from app.tax.rates import RateTable, india_seed_rate_table
from app.tax.regime import TaxRegime

__all__ = [
    "Flag",
    "IndiaGST",
    "Invoice",
    "InvoiceLine",
    "LineTax",
    "Party",
    "RateTable",
    "Severity",
    "TaxHead",
    "TaxRegime",
    "india_seed_rate_table",
]
