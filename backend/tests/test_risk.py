"""The J5 doubt penalty: a ranking cost for listings that Jev was not sure of, never a price."""

from app.config import Thresholds
from app.contracts import CartLine, Costs, ShoppingItem, SupplierOffers, VerifiedCart, cart_key
from app.stages.normalize import normalize
from app.stages.optimize import optimize, ranking_total
from app.stages.rank import build_results
from app.stages.rules import derive_rules
from tests.test_plan import product

TH = Thresholds()
GLOVES = [ShoppingItem(id="g", name="gloves", quantity=200, unit="glove", past_unit_price=0.5,
                       order_cadence_days=14)]


def test_doubt_penalty_on_the_line():
    offers = SupplierOffers(supplier_id="a", products=[
        product("g", "LOW", 100, 40.0, p_same=0.7),  # low_match
        product("g", "BOTH", 100, 40.0, p_same=0.7, must={"nitrile": 0.6}),  # two doubts
    ])
    res = normalize(GLOVES, [offers], TH)
    by_sku = {o.sku: o for o in res.all_options}
    assert by_sku["LOW"].line_cost == 80.0 and by_sku["LOW"].risk_penalty == 4.0
    assert by_sku["BOTH"].risk_penalty == 8.0
    assert res.best[("g", "a")].sku == "LOW"


def test_confident_listing_beats_a_slightly_cheaper_doubtful_one():
    close = SupplierOffers(supplier_id="a", products=[
        product("g", "LOW", 100, 40.0, p_same=0.7),  # 80 + 4 penalty
        product("g", "SURE", 100, 41.0),  # 82
    ])
    assert normalize(GLOVES, [close], TH).best[("g", "a")].sku == "SURE"

    far = SupplierOffers(supplier_id="a", products=[
        product("g", "LOW", 100, 30.0, p_same=0.7),  # 60 + 3 penalty
        product("g", "SURE", 100, 41.0),
    ])
    assert normalize(GLOVES, [far], TH).best[("g", "a")].sku == "LOW"


def test_no_penalty_when_turned_off():
    offers = SupplierOffers(supplier_id="a", products=[product("g", "LOW", 100, 40.0, p_same=0.7)])
    res = normalize(GLOVES, [offers], Thresholds(risk_penalty=0.0))
    assert res.best[("g", "a")].risk_penalty == 0.0


def _two_suppliers():
    offers = {
        "a": SupplierOffers(supplier_id="a", products=[product("g", "AG", 100, 40.0, p_same=0.7)]),
        "b": SupplierOffers(supplier_id="b", products=[product("g", "BG", 100, 41.0)]),
    }
    carts = {}
    for sid, sku, price in (("a", "AG", 40.0), ("b", "BG", 41.0)):
        key = cart_key(sid, [CartLine(item_id="g", sku=sku, pack_quantity=2, unit_price=price)])
        carts[key] = VerifiedCart(cart_key=key, supplier_id=sid, verification_status="verified",
                                  costs=Costs(subtotal=2 * price, shipping=8.0, tax=0.0, total=2 * price + 8))
    normalized = normalize(GLOVES, list(offers.values()), TH)
    return offers, carts, normalized


def test_plans_rank_by_total_plus_penalty_and_keep_real_totals():
    offers, carts, normalized = _two_suppliers()
    res = optimize(GLOVES, normalized, offers, carts, {"a": "A", "b": "B"})
    first, second = res.plans
    assert (first.label, first.total, first.risk_penalty) == ("Everything from B", 90.0, 0.0)
    assert (second.label, second.total, second.risk_penalty) == ("Everything from A", 88.0, 4.0)
    assert ranking_total(second) == 92.0


def test_results_explain_a_cheaper_plan_that_ranks_lower():
    offers, carts, normalized = _two_suppliers()
    names = {"a": "A", "b": "B"}
    plans = optimize(GLOVES, normalized, offers, carts, names).plans
    rules = derive_rules(largest_past_order=1000, monthly_average_spend=5000, spent_this_month=0,
                         approved_suppliers=["a", "b"])
    results = build_results("job_1", GLOVES, plans, normalized, [], rules, names,
                            accept_quoted=False, deadline_hit=False)
    best = next(p for p in results.plans if p.plan_id == results.recommended_plan_id)
    assert best.label == "Everything from B"
    warning = next(w for w in results.warnings if w.code == "ranked_for_confidence")
    assert warning.text == ("Everything from A costs $2.00 less, but the match is uncertain "
                            "for 1 of its items, so it ranks lower.")
