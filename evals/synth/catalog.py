"""Fixed vocabularies for the synthetic eval set: states, our entities, name parts, items.

Every name, address, phone number and bank here is invented. Email domains use the
reserved ``.example`` TLD and bank IFSC codes use the fictitious ``ZY`` bank prefix.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

# GST state codes: code -> (name, union territory without legislature, so UTGST applies)
STATES: dict[str, tuple[str, bool]] = {
    "04": ("Chandigarh", True),
    "06": ("Haryana", False),
    "07": ("Delhi", False),
    "08": ("Rajasthan", False),
    "09": ("Uttar Pradesh", False),
    "19": ("West Bengal", False),
    "24": ("Gujarat", False),
    "27": ("Maharashtra", False),
    "29": ("Karnataka", False),
    "32": ("Kerala", False),
    "33": ("Tamil Nadu", False),
    "36": ("Telangana", False),
}

# First digit of the PIN code by state, so addresses look plausible.
PIN_PREFIX = {
    "04": "16",
    "06": "12",
    "07": "11",
    "08": "30",
    "09": "20",
    "19": "70",
    "24": "38",
    "27": "41",
    "29": "56",
    "32": "68",
    "33": "60",
    "36": "50",
}

CITIES = {
    "04": ["Chandigarh"],
    "06": ["Gurugram", "Faridabad"],
    "07": ["New Delhi"],
    "08": ["Jaipur", "Udaipur"],
    "09": ["Noida", "Lucknow"],
    "19": ["Kolkata", "Howrah"],
    "24": ["Ahmedabad", "Vadodara"],
    "27": ["Pune", "Mumbai", "Nashik"],
    "29": ["Bengaluru", "Mysuru"],
    "32": ["Kochi"],
    "33": ["Chennai", "Coimbatore"],
    "36": ["Hyderabad"],
}

# Our own (buyer) entities and their GST registrations, one GSTIN per state (spec §1 Q1).
ENTITIES: list[tuple[str, str, str, list[str]]] = [
    # (entity key, legal name, PAN holder type, registration state codes)
    ("E1", "Nilgiri Crest Retail Pvt Ltd", "C", ["27", "29"]),
    ("E2", "Nilgiri Crest Logistics LLP", "F", ["07", "33"]),
    ("E3", "Nilgiri Crest Foods Pvt Ltd", "C", ["24", "04"]),
]

NAME_FIRST = [
    "Tamarind",
    "Peacock",
    "Monsoon",
    "Banyan",
    "Saffron",
    "Indigo",
    "Lotus",
    "Kestrel",
    "Granite",
    "Copperleaf",
    "Riverstone",
    "Sandalwood",
    "Bluejay",
    "Marigold",
    "Teakwood",
    "Silverline",
]
NAME_SECOND = [
    "Traders",
    "Industries",
    "Supplies",
    "Engineering Works",
    "Packaging",
    "Electricals",
    "Stationers",
    "Tech Services",
    "Agro Foods",
    "Facility Services",
]
LEGAL_FORM = [("Pvt Ltd", "C"), ("LLP", "F"), ("& Co", "F"), ("Enterprises", "P")]
STREETS = ["Nagar", "Industrial Estate", "Market Road", "Main Road", "Layout", "Udyog Vihar"]
LOCALITIES = ["Kesar", "Amrit", "Sagar", "Shanti", "Vikas", "Pragati", "Navjeevan", "Sundar"]

# (description, HSN/SAC, unit, price low, price high, rate before 22-Sep-2025, rate after)
ITEMS: list[tuple[str, str, str, int, int, str, str]] = [
    ("A4 copier paper 75 gsm", "4802", "ream", 260, 340, "12", "18"),
    ("Corrugated carton 5-ply", "481910", "pcs", 25, 60, "12", "18"),
    ("LED panel light 18W", "9405", "pcs", 450, 900, "18", "18"),
    ("Copper cable 2.5 sq mm 90m", "854449", "coil", 2500, 4200, "18", "18"),
    ("Office chair, mesh back", "9401", "pcs", 4500, 9000, "18", "18"),
    ("Laptop 14 inch i5 16GB", "8471", "pcs", 45000, 78000, "18", "18"),
    ("Basmati rice 25 kg bag", "1006", "bag", 1800, 2600, "5", "5"),
    ("Edible oil 15 L tin", "1512", "tin", 1900, 2400, "5", "5"),
    ("Packaged water 20 L jar", "2201", "jar", 60, 90, "18", "5"),
    ("Cement OPC 53 grade 50 kg", "2523", "bag", 350, 420, "28", "18"),
    ("Split AC 1.5 ton", "8415", "pcs", 32000, 45000, "28", "18"),
    ("Aerated beverage crate", "2202", "crate", 480, 720, "28", "40"),
    ("Gold coin 10 g", "7118", "pcs", 61000, 72000, "3", "3"),
    ("Fresh vegetables assorted", "0709", "kg", 30, 80, "0", "0"),
    ("Printer toner cartridge", "8443", "pcs", 2200, 4800, "18", "18"),
    ("Stainless steel shelving", "9403", "pcs", 6500, 12500, "18", "18"),
    ("Hand sanitiser 5 L can", "3808", "can", 650, 1100, "18", "18"),
    ("Road freight charges", "996511", "trip", 8000, 25000, "5", "5"),
    ("IT annual maintenance", "998713", "month", 15000, 40000, "18", "18"),
    ("Housekeeping services", "998533", "month", 30000, 90000, "18", "18"),
    ("Brochure printing", "998912", "job", 9000, 30000, "18", "18"),
    ("Security guard services", "998525", "month", 40000, 95000, "18", "18"),
]
SERVICE_UNITS = {"trip", "month", "job"}

RATE_CHANGE = date(2025, 9, 22)  # gst-invoice-rules §4: rate rationalisation effective date
RATES_BEFORE = {Decimal(r) for r in ("0", "0.25", "3", "5", "12", "18", "28")}
RATES_AFTER = {Decimal(r) for r in ("0", "0.25", "3", "5", "18", "40")}


def rates_on(d: date) -> set[Decimal]:
    return RATES_BEFORE if d < RATE_CHANGE else RATES_AFTER


SYNTHETIC_IFSC_BANK = ["ZYXB", "ZYQB", "ZYKB"]  # fictitious bank codes
BANK_NAMES = ["Konkan Coast Co-operative Bank", "Deccan Plateau Bank", "Ganga Valley Bank"]
