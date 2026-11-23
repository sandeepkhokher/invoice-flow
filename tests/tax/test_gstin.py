"""GSTIN check character (gst-invoice-rules §1)."""

import pytest

from app.tax.gstin import check_character, gstin_problem


@pytest.mark.parametrize("gstin", ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4"])
def test_known_gstins_are_valid(gstin):
    assert check_character(gstin) == gstin[14]
    assert gstin_problem(gstin) is None


@pytest.mark.parametrize("gstin", ["27AAPFU0939F1ZA", "29AAGCB7383J1Z5"])
def test_wrong_check_character_is_reported(gstin):
    assert "check character" in gstin_problem(gstin)


def test_format_is_checked_before_check_character():
    assert "format" in gstin_problem("27AAPFU0939F1YV")  # 14th character must be Z
