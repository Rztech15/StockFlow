import pytest

from app.money import (
    Money, MoneyError, add_money, format_money, money_from_db, money_from_wire,
    multiply_money, parse_money, subtract_money,
)  # fmt: skip


def code_of(fn) -> str:
    with pytest.raises(MoneyError) as info:
        fn()
    return info.value.code


def test_pkr_1250_50_is_125050():
    assert parse_money("1250.50", "PKR") == Money(125050, "PKR")


def test_jpy_500_is_500():
    assert parse_money("500", "JPY").amount_minor == 500


def test_kwd_1_250_is_1250():
    assert parse_money("1.250", "KWD").amount_minor == 1250


def test_short_fractions_are_padded():
    assert parse_money("1.25", "KWD").amount_minor == 1250
    assert parse_money("7", "PKR").amount_minor == 700
    assert parse_money("0.05", "PKR").amount_minor == 5


@pytest.mark.parametrize(
    ("text", "currency"),
    [("1250.505", "PKR"), ("1250.500", "PKR"), ("500.5", "JPY"), ("1.2501", "KWD")],
)
def test_excess_precision_is_rejected(text, currency):
    assert code_of(lambda: parse_money(text, currency)) == "excess_precision"


@pytest.mark.parametrize(
    "bad",
    ["", " 12.00", "12.00 ", "1,250.50", "1e3", "+5", ".5", "5.", "abc", "1.2.3", "007", "NaN"],
)
def test_invalid_strings_are_rejected(bad):
    assert code_of(lambda: parse_money(bad, "PKR")) == "invalid_format"


@pytest.mark.parametrize("value", [1250.5, 500, None, 10])
def test_numbers_are_never_accepted(value):
    assert code_of(lambda: parse_money(value, "PKR")) == "invalid_type"


def test_negative_and_unknown_currency():
    assert code_of(lambda: parse_money("-1.00", "PKR")) == "negative"
    assert parse_money("-1.00", "PKR", allow_negative=True).amount_minor == -100
    assert code_of(lambda: parse_money("1.00", "XXX")) == "unknown_currency"


def test_bigint_limit():
    assert code_of(lambda: parse_money("92233720368547758.08", "PKR")) == "out_of_range"
    assert parse_money("92233720368547758.07", "PKR").amount_minor == 9223372036854775807


def test_format_and_round_trip():
    assert format_money(Money(125050, "PKR")) == "1250.50"
    assert format_money(Money(500, "JPY")) == "500"
    assert format_money(Money(1250, "KWD")) == "1.250"
    assert format_money(Money(5, "PKR")) == "0.05"
    assert format_money(Money(-125050, "PKR")) == "-1250.50"
    for text, cur in (("1250.50", "PKR"), ("500", "JPY"), ("1.250", "KWD")):
        assert format_money(parse_money(text, cur)) == text


def test_wire_and_db_are_integers_only():
    assert money_from_wire(125050, "PKR").amount_minor == 125050
    assert money_from_db(125050, "PKR").amount_minor == 125050
    for bad in (1250.5, "125050", True, None):
        assert code_of(lambda bad=bad: money_from_wire(bad, "PKR")) == "invalid_integer"


def test_arithmetic_is_exact_and_single_currency():
    a, b = Money(125050, "PKR"), Money(50, "PKR")
    assert add_money(a, b).amount_minor == 125100
    assert subtract_money(a, b).amount_minor == 125000
    assert multiply_money(b, 3).amount_minor == 150
    assert code_of(lambda: add_money(a, Money(1, "USD"))) == "currency_mismatch"
    assert code_of(lambda: multiply_money(a, 1.5)) == "invalid_integer"
