from app.contracts import CartLine, MoneyCandidate
from app.stages.extract import money_candidates, pack_candidates
from app.stages.totals import build_verified_cart, resolve_costs


def values(text: str) -> set[int]:
    return {c.value for c in pack_candidates(text)}


def test_pack_candidates_common_forms():
    assert values("Nitrile Gloves - Box of 100") == {100}
    assert 250 in values('10" x 10" x 5" Bakery Box - 250/Case')
    assert 1000 in values("Case of 1,000 sheets")
    assert 100 in values("Case (10 boxes of 100)")
    assert 1000 in values("10/100")  # 10 packs of 100


def test_pack_candidates_ignore_dimensions():
    assert values("Bread Bag 8 x 4 x 18 in") == set()


def test_money_candidates_keep_labels():
    cands = money_candidates("Subtotal (2 items) $1,260.50\nEstimated shipping $9.99\nOrder total $1,270.49")
    assert [(c.label, c.value) for c in cands] == [
        ("Subtotal (2 items)", 1260.50),
        ("Estimated shipping", 9.99),
        ("Order total", 1270.49),
    ]


def test_sum_check_consistent_and_mismatch():
    cands = [MoneyCandidate(label="Subtotal", value=100.0), MoneyCandidate(label="Shipping", value=10.0),
             MoneyCandidate(label="Tax", value=6.0), MoneyCandidate(label="Total", value=116.0),
             MoneyCandidate(label="You saved", value=4.0)]
    costs, flags = resolve_costs(cands, {"subtotal": 0, "shipping": 1, "tax": 2, "total": 3})
    assert costs is not None and costs.total == 116.0 and flags == []
    # A wrong pick (the "You saved" amount as shipping) breaks the sum, so the cart is not verified.
    lines = [CartLine(item_id="a", sku="S1", pack_quantity=1, unit_price=100.0)]
    cart = build_verified_cart("x", lines, cands, {"subtotal": 0, "shipping": 4, "tax": 2, "total": 3}, {})
    assert cart.totals_check == "mismatch"
    assert cart.verification_status == "failed"


def test_missing_total_is_estimated_not_verified():
    # A real cart page with a subtotal and a ZIP freight quote but no total (tax at checkout).
    lines = [CartLine(item_id="a", sku="S1", pack_quantity=2, unit_price=43.99)]
    cands = [MoneyCandidate(label="Cart Subtotal", value=87.98), MoneyCandidate(label="Ground", value=13.83)]
    cart = build_verified_cart("x", lines, cands, {"subtotal": 0, "shipping": 1, "total": None}, {})
    assert cart.verification_status == "estimated"
    assert cart.totals_check == "not_checked"
    assert cart.costs is not None and (cart.costs.shipping, cart.costs.total) == (13.83, 101.81)
    assert "total_not_shown" in cart.reason and "tax_not_shown" in cart.reason


def test_missing_subtotal_still_fails():
    lines = [CartLine(item_id="a", sku="S1", pack_quantity=1, unit_price=5.0)]
    cands = [MoneyCandidate(label="Total", value=5.0)]
    cart = build_verified_cart("x", lines, cands, {"subtotal": None, "total": 0}, {})
    assert cart.verification_status == "failed" and cart.costs is None
