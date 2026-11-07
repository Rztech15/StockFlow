"""Bulk import: row validation and permission checks. No database."""

from app.importing import MAX_NUMBER, check_row, check_rows
from tests.test_auth_unit import cookie, give_session, make_client


def test_a_complete_row_is_cleaned():
    check = check_row(
        {"name": "  Notebook A5 ", "sku": " NB-050 ", "quantity": "12", "minimum_stock": 5.0,
         "unit": "pack", "description": "Ruled"}, 2,
    )
    assert check.errors == [] and check.row is not None
    row = check.row
    assert (row.name, row.sku, row.quantity, row.minimum_stock) == ("Notebook A5", "NB-050", 12, 5)
    assert (row.unit, row.description, row.line) == ("pack", "Ruled", 2)


def test_missing_optional_fields_stay_none_so_existing_products_are_not_overwritten():
    row = check_row({"name": "Pens"}, 2).row
    assert row is not None
    assert (row.sku, row.quantity, row.minimum_stock, row.unit, row.description) == (
        None, None, None, None, None)


def test_name_is_required():
    for raw in ({}, {"name": ""}, {"name": "   "}, {"name": None}):
        check = check_row(raw, 3)
        assert check.row is None and check.errors == ["Name is missing"]


def test_quantities_must_be_whole_numbers_in_range():
    for bad in ("abc", "-5", -5, 1.5, "1.5", True, MAX_NUMBER + 1, [1]):
        check = check_row({"name": "x", "quantity": bad}, 2)
        assert check.row is None, bad
        assert check.errors and "Quantity must be a whole number" in check.errors[0]
    assert check_row({"name": "x", "quantity": 0}, 2).row.quantity == 0
    assert check_row({"name": "x", "quantity": "7 "}, 2).row.quantity == 7
    assert check_row({"name": "x", "quantity": MAX_NUMBER}, 2).row.quantity == MAX_NUMBER


def test_every_problem_in_a_row_is_reported_together():
    check = check_row({"name": "", "sku": "a" * 65, "quantity": "x", "minimum_stock": -1}, 4)
    assert len(check.errors) == 4 and check.row is None


def test_text_limits_and_control_characters():
    assert check_row({"name": "n" * 161}, 2).row is None
    assert check_row({"name": "ok", "unit": "u" * 25}, 2).row is None
    assert check_row({"name": "bad\x00name"}, 2).row is None
    assert check_row({"name": "ok", "description": "d" * 2001}, 2).row is None
    assert check_row({"name": ["list"]}, 2).row is None


def test_a_row_that_is_not_an_object_is_rejected():
    for raw in ("text", 5, None, ["a"]):
        assert check_row(raw, 2).errors == ["Row is not valid"]


def test_duplicate_skus_in_one_file_are_flagged_after_the_first():
    rows = [{"name": "A", "sku": "X-1"}, {"name": "B", "sku": "X-2"}, {"name": "C", "sku": "X-1"}]
    checks = check_rows(rows)
    assert [c.row is not None for c in checks] == [True, True, False]
    assert checks[2].errors == ["Duplicate SKU in this file (first used on line 2)"]
    assert [c.line for c in checks] == [2, 3, 4]


def test_rows_without_a_sku_never_count_as_duplicates():
    checks = check_rows([{"name": "A"}, {"name": "B"}, {"name": "A"}])
    assert all(c.row is not None for c in checks)


def test_line_numbers_from_the_browser_are_kept():
    checks = check_rows([{"name": "A", "line": 10}, {"name": "B", "line": "x"}, {"name": "C"}])
    assert [c.line for c in checks] == [10, 3, 4]


def test_import_requires_a_session_and_both_write_permissions():
    client, repo = make_client()
    give_session(repo, "tok-viewer", "viewer")
    give_session(repo, "tok-staff", "staff")  # can move stock but not edit the catalogue
    body = {"rows": [{"name": "Pens"}]}
    with client:
        anonymous = client.post("/api/v1/products/import", json=body)
        viewer = client.post("/api/v1/products/import", json=body, headers=cookie("tok-viewer"))
        staff = client.post("/api/v1/products/import", json=body, headers=cookie("tok-staff"))
    assert anonymous.status_code == 401
    assert viewer.status_code == 403
    assert staff.status_code == 403


def test_import_rejects_an_empty_or_oversized_file_before_touching_the_database():
    client, repo = make_client()
    give_session(repo, "tok-owner", "owner")
    with client:
        empty = client.post("/api/v1/products/import", json={"rows": []},
                            headers=cookie("tok-owner"))
        huge = client.post("/api/v1/products/import",
                           json={"rows": [{"name": "x"}] * 1001}, headers=cookie("tok-owner"))
        missing = client.post("/api/v1/products/import", json={}, headers=cookie("tok-owner"))
    assert empty.status_code == huge.status_code == missing.status_code == 422
