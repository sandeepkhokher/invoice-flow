"""Money: integer paise, Decimal arithmetic, no floats (ticket #6 AC1)."""

import ast
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.domain import transitions as t
from app.domain.actor import Actor
from app.domain.models import Invoice
from app.domain.money import Money

MONEY_SRC = Path(__file__).resolve().parent.parent / "app" / "domain" / "money.py"


def test_stores_integer_paise():
    m = Money.of("1234.50")
    assert m.paise == 123450
    assert isinstance(m.paise, int)
    assert m.amount == Decimal("1234.50")
    assert str(m) == "1234.50"


@pytest.mark.parametrize(
    ("value", "paise"),
    [(Decimal("0.01"), 1), (5, 500), ("  10.10 ", 1010), ("-3.5", -350), (Decimal("1E+2"), 10000)],
)
def test_accepts_decimal_int_and_str(value, paise):
    assert Money.of(value).paise == paise


@pytest.mark.parametrize("bad", [0.1, 1.0, float("nan"), True])
def test_rejects_float_and_bool(bad):
    with pytest.raises(TypeError):
        Money.of(bad)


def test_rejects_float_paise():
    with pytest.raises(TypeError):
        Money(1.0)  # type: ignore[arg-type]


def test_rejects_float_multiplier():
    with pytest.raises(TypeError):
        Money.of(10) * 1.5  # type: ignore[operator]
    with pytest.raises(TypeError):
        Money.of(10).times(0.18)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["1.005", Decimal("0.001")])
def test_fractions_of_a_paisa_need_explicit_rounding(bad):
    with pytest.raises(ValueError):
        Money.of(bad)


@pytest.mark.parametrize("bad", ["abc", "NaN", "Infinity"])
def test_rejects_non_numbers(bad):
    with pytest.raises(ValueError):
        Money.of(bad)


def test_round_from_half_up_by_default():
    assert Money.round_from("1.005").paise == 101
    assert Money.round_from("1.005", rounding=ROUND_HALF_EVEN).paise == 100


def test_decimal_arithmetic_is_exact():
    # 0.1 + 0.2 is the classic float failure.
    assert Money.of("0.10") + Money.of("0.20") == Money.of("0.30")
    assert Money.of("100.00") - Money.of("0.01") == Money.of("99.99")
    assert -Money.of("5") == Money.of("-5")
    assert abs(Money.of("-5")) == Money.of("5")
    assert Money.of("2.50") * 3 == 3 * Money.of("2.50") == Money.of("7.50")
    assert Money.sum([Money.of("0.10")] * 10) == Money.of("1.00")
    assert Money.sum([]) == Money.zero()


def test_times_rounds_to_paisa():
    # 18% GST on 999.99 = 179.9982 -> 180.00
    assert Money.of("999.99").times(Decimal("0.18")) == Money.of("180.00")
    assert Money.of("10.05").times("0.5") == Money.of("5.03")  # 5.025 half-up


def test_ordering_and_type_safety():
    assert Money.of("1") < Money.of("1.01")
    with pytest.raises(TypeError):
        Money.of("1") + 1  # type: ignore[operator]
    with pytest.raises(TypeError):
        Money.sum([Money.of("1"), Decimal("1")])


def test_money_module_has_no_floats():
    """No float literal or float() call anywhere in the money code."""
    tree = ast.parse(MONEY_SRC.read_text())
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float)), node.lineno
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "float", f"float() call at line {node.lineno}"


@pytest.mark.usefixtures("entity_rows")
def test_money_columns_store_integer_paise(session):
    inv = t.create_invoice(
        session,
        Actor.system("intake"),
        entity_id=1,
        source="upload",
        grand_total=Money.of("118000.50"),
    )
    session.commit()
    raw = session.execute(
        text("SELECT grand_total_paise, typeof(grand_total_paise) FROM invoices WHERE id = :id"),
        {"id": inv.id},
    ).one()
    assert tuple(raw) == (11800050, "integer")
    session.expire_all()
    assert session.get(Invoice, inv.id).grand_total == Money.of("118000.50")


@pytest.mark.usefixtures("entity_rows")
def test_money_column_rejects_non_money(session):
    inv = t.create_invoice(session, Actor.system("intake"), entity_id=1, source="upload")
    inv.grand_total = 1.5  # type: ignore[assignment]
    with pytest.raises(Exception, match="Money"):
        session.flush()
