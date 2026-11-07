"""Sales orders: permissions and request validation. No database."""

from tests.test_auth_unit import cookie, give_session, make_client

PRODUCT = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
CUSTOMER = "00000000-0000-4000-8000-000000000003"


def line(product: str = PRODUCT, quantity: int = 1) -> dict[str, object]:
    return {"product_id": product, "quantity": quantity}


def order(items: list[dict[str, object]]) -> dict[str, object]:
    return {"customer_id": CUSTOMER, "items": items}


def test_sales_need_a_session_and_the_right_permission():
    client, repo = make_client()
    give_session(repo, "tok-viewer", "viewer")  # can read sales, cannot write
    with client:
        for method, path, body in (
            ("post", "/api/v1/customers", {"name": "Acme"}),
            ("post", "/api/v1/sales-orders", order([line()])),
            ("post", f"/api/v1/sales-orders/{CUSTOMER}/ship", None),
            ("post", f"/api/v1/sales-orders/{CUSTOMER}/cancel", None),
        ):
            call = getattr(client, method)
            anonymous = call(path, json=body) if body else call(path)
            viewer = (call(path, json=body, headers=cookie("tok-viewer")) if body
                      else call(path, headers=cookie("tok-viewer")))
            assert anonymous.status_code == 401, path
            assert viewer.status_code == 403, path
        assert client.get("/api/v1/customers").status_code == 401
        assert client.get("/api/v1/sales-orders").status_code == 401
        assert client.get("/api/v1/sales/availability").status_code == 401


def test_a_bad_order_is_rejected_before_any_database_work():
    client, repo = make_client()
    give_session(repo, "tok-owner", "owner")
    headers = cookie("tok-owner")
    bad_orders = {
        "no items": order([]),
        "same product twice": order([line(), line()]),
        "zero quantity": order([line(quantity=0)]),
        "negative quantity": order([line(quantity=-3)]),
        "huge quantity": order([line(quantity=1_000_001)]),
        "not a uuid": order([line(product="abc")]),
        "no customer": {"items": [line()]},
        "too many lines": order([line(product=f"00000000-0000-4000-8000-{n:012d}")
                                 for n in range(101)]),
    }
    with client:
        for name, body in bad_orders.items():
            res = client.post("/api/v1/sales-orders", json=body, headers=headers)
            assert res.status_code == 422, name
        blank = client.post("/api/v1/customers", json={"name": ""}, headers=headers)
        assert blank.status_code == 422
        assert client.post("/api/v1/customers", json={"name": "x" * 161},
                           headers=headers).status_code == 422
        assert client.post("/api/v1/sales-orders/not-a-uuid/ship",
                           headers=headers).status_code == 422
