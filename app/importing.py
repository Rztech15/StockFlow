"""Validation of rows for the bulk product import. Pure functions: no database, easy to test.

The browser sends rows it parsed from a CSV, XLSX or JSON file. The server never trusts them:
every row is checked again here, and a bad row is reported instead of failing the whole file.
"""

from dataclasses import dataclass, field

MAX_ROWS = 1000
MAX_NUMBER = 1_000_000


@dataclass(frozen=True)
class CleanRow:
    line: int
    name: str
    sku: str | None  # None: generate one from the workspace SKU convention
    # None means "the file has no value": leave an existing product's field alone.
    quantity: int | None
    minimum_stock: int | None
    unit: str | None
    description: str | None


@dataclass
class RowCheck:
    line: int
    row: CleanRow | None
    errors: list[str] = field(default_factory=list)
    sku: str = ""
    name: str = ""


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(
    raw: dict[str, object], key: str, label: str, limit: int, errors: list[str]
) -> str | None:
    value = raw.get(key)
    if _blank(value):
        return None
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        errors.append(f"{label} must be text")
        return None
    text = str(value).strip()
    if any(ord(ch) < 32 for ch in text):
        errors.append(f"{label} contains control characters")
        return None
    if len(text) > limit:
        errors.append(f"{label} is longer than {limit} characters")
        return None
    return text


def _whole(raw: dict[str, object], key: str, label: str, errors: list[str]) -> int | None:
    value = raw.get(key)
    if _blank(value):
        return None
    number: int | None = None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    if number is None or number < 0 or number > MAX_NUMBER:
        errors.append(f"{label} must be a whole number from 0 to {MAX_NUMBER:,}")
        return None
    return number


def check_row(raw: object, line: int) -> RowCheck:
    if not isinstance(raw, dict):
        return RowCheck(line, None, ["Row is not valid"])
    errors: list[str] = []
    name = _text(raw, "name", "Name", 160, errors)
    if name is None and not errors:
        errors.append("Name is missing")
    sku = _text(raw, "sku", "SKU", 64, errors)
    unit = _text(raw, "unit", "Unit", 24, errors)
    description = _text(raw, "description", "Description", 2000, errors)
    quantity = _whole(raw, "quantity", "Quantity", errors)
    minimum = _whole(raw, "minimum_stock", "Minimum stock", errors)
    shown_sku = sku or ""
    shown_name = name or ""
    if errors:
        return RowCheck(line, None, errors, shown_sku, shown_name)
    assert name is not None
    return RowCheck(
        line, CleanRow(line, name, sku, quantity, minimum, unit, description), [],
        shown_sku, shown_name,
    )


def check_rows(rows: list[object]) -> list[RowCheck]:
    """Check every row and flag a SKU that appears twice in the same file."""
    checks: list[RowCheck] = []
    first_seen: dict[str, int] = {}
    for index, raw in enumerate(rows):
        line = raw.get("line") if isinstance(raw, dict) else None
        number = line if isinstance(line, int) and not isinstance(line, bool) and line >= 1 \
            else index + 2
        check = check_row(raw, number)
        if check.row is not None and check.row.sku:
            earlier = first_seen.get(check.row.sku)
            if earlier is not None:
                check.errors.append(f"Duplicate SKU in this file (first used on line {earlier})")
                check.row = None
            else:
                first_seen[check.row.sku] = number
        checks.append(check)
    return checks
