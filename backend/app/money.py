"""Money = an INTEGER number of minor units (paisa, cents) plus a currency code.

Contract
  * Database: BIGINT.  API: a JSON integer `amount_minor`.  Never floats.
  * Decimal text (forms, CSV) must be a STRING and goes through parse_money().
  * All arithmetic uses Python ints. Mixing currencies raises (no conversion exists).
Example: PKR "1250.50" -> 125050 ; JPY "500" -> 500 ; KWD "1.250" -> 1250.
"""

import re
from dataclasses import dataclass

# ISO 4217 minor-unit exponents. A `currencies` table replaces this in Phase 1.
CURRENCY_EXPONENTS: dict[str, int] = {
    "PKR": 2, "USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "AED": 2, "SAR": 2, "BDT": 2,
    "JPY": 0, "KRW": 0, "VND": 0, "CLP": 0,
    "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "TND": 3,
}  # fmt: skip

MAX_MINOR_UNITS = 9_223_372_036_854_775_807  # PostgreSQL BIGINT
_DECIMAL = re.compile(r"(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?")


class MoneyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Money:
    amount_minor: int
    currency: str


def currency_exponent(currency: str) -> int:
    exponent = CURRENCY_EXPONENTS.get(currency)
    if exponent is None:
        raise MoneyError("unknown_currency", "Unsupported or unknown currency code.")
    return exponent


def _in_range(value: int) -> int:
    if abs(value) > MAX_MINOR_UNITS:
        raise MoneyError("out_of_range", "Amount exceeds the supported range.")
    return value


def parse_money(text: object, currency: str, *, allow_negative: bool = False) -> Money:
    """Parse a decimal STRING. Numbers (int/float/Decimal) are rejected on purpose."""
    if not isinstance(text, str):
        raise MoneyError("invalid_type", "Monetary input must be a decimal string.")
    exponent = currency_exponent(currency)
    match = _DECIMAL.fullmatch(text)
    if match is None:
        raise MoneyError("invalid_format", "Not a valid decimal amount.")
    sign, whole, fraction = match.group(1), match.group(2), match.group(3) or ""
    if sign and not allow_negative:
        raise MoneyError("negative", "Negative amounts are not allowed here.")
    if len(fraction) > exponent:
        raise MoneyError(
            "excess_precision", f"{currency} allows at most {exponent} decimal place(s)."
        )
    magnitude = int(whole + fraction.ljust(exponent, "0"))
    return Money(_in_range(-magnitude if sign else magnitude), currency)


def format_money(money: Money) -> str:
    exponent = currency_exponent(money.currency)
    digits = str(abs(money.amount_minor)).rjust(exponent + 1, "0")
    sign = "-" if money.amount_minor < 0 else ""
    if exponent == 0:
        return f"{sign}{digits}"
    return f"{sign}{digits[:-exponent]}.{digits[-exponent:]}"


def money_from_wire(amount_minor: object, currency: str) -> Money:
    """From a JSON value. Must already be an integer of minor units (bool and float rejected)."""
    currency_exponent(currency)
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise MoneyError("invalid_integer", "Amount must be an integer number of minor units.")
    return Money(_in_range(amount_minor), currency)


def money_from_db(value: int, currency: str) -> Money:
    """From a BIGINT column (psycopg returns Python int)."""
    return money_from_wire(value, currency)


def _same(a: Money, b: Money) -> None:
    if a.currency != b.currency:
        raise MoneyError("currency_mismatch", "Currencies differ; conversion is not supported.")


def add_money(a: Money, b: Money) -> Money:
    _same(a, b)
    return Money(_in_range(a.amount_minor + b.amount_minor), a.currency)


def subtract_money(a: Money, b: Money) -> Money:
    _same(a, b)
    return Money(_in_range(a.amount_minor - b.amount_minor), a.currency)


def multiply_money(money: Money, quantity: int) -> Money:
    """Multiply by a WHOLE quantity. Fractional quantities are intentionally unsupported."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise MoneyError("invalid_integer", "Quantity must be a whole number.")
    return Money(_in_range(money.amount_minor * quantity), money.currency)
