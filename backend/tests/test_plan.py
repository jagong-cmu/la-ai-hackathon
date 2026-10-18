from app.config import Thresholds
from app.contracts import (
    CartLine,
    Costs,
    JevListing,
    Plan,
    PlanCart,
    Product,
    ShoppingItem,
    SupplierOffers,
    VerifiedCart,
    cart_key,
)
from app.stages.cards import mint_cards
from app.stages.normalize import normalize
from app.stages.optimize import optimize
from app.stages.rank import recommend, savings
from app.stages.rules import check_plan, derive_rules

TH = Thresholds()


def jev(p_same: float = 0.95, must: dict | None = None, stock: float = 0.95) -> JevListing:
    return JevListing(match_score=2 * p_same, match_probs={"0": 0.0, "1": 1 - p_same, "2": p_same},
                      must_have=must or {}, in_stock=stock)


def product(item: str, sku: str, units: int | None, price: float, url: str | None = None, **kw) -> Product:
    return Product(item_id=item, title=sku, sku=sku, pack_price=price, units_per_pack=units, url=url, jev=jev(**kw))


ITEMS = [
    ShoppingItem(id="g", name="gloves", quantity=200, unit="glove", past_unit_price=0.15, order_cadence_days=14),
    ShoppingItem(id="b", name="boxes", quantity=100, unit="box", past_unit_price=0.9, order_cadence_days=14),
]


def test_normalize_packs_overbuy_and_eligibility():
    offers = SupplierOffers(supplier_id="a", products=[
        product("g", "G100", 100, 10.0),
        product("b", "B300", 300, 60.0),  # 300 for 100 needed: overbuy
    ])
    res = normalize(ITEMS, [offers], TH)
    g = res.best[("g", "a")]
    assert (g.packs_needed, g.line_cost, g.effective_unit_price) == (2, 20.0, 0.1)
    assert "overbuy" in res.best[("b", "a")].flags


def test_normalize_excludes_bad_matches():
    offers = SupplierOffers(supplier_id="a", products=[
        product("g", "G1", 100, 1.0, p_same=0.3),  # not the same product
        product("g", "G2", None, 2.0),  # pack unknown
        product("g", "G3", 100, 3.0, must={"medium": 0.2}),  # must-have failed
        product("g", "G4", 100, 4.0, stock=0.1),  # out of stock
        product("g", "G5", 100, 5.0, p_same=0.7),  # eligible, low match
    ])
    res = normalize(ITEMS, [offers], TH)
    best = res.best[("g", "a")]
    assert best.sku == "G5"
    assert "low_match" in best.flags


def test_free_shipping_threshold_changes_the_best_plan():
    # Supplier a: free shipping at $100. Supplier b: cheaper lines but $25 shipping.
    a = SupplierOffers(supplier_id="a", free_shipping_threshold=100, products=[
        product("g", "AG", 100, 40.0), product("b", "AB", 100, 30.0)])
    b = SupplierOffers(supplier_id="b", free_shipping_threshold=None, products=[
        product("g", "BG", 100, 38.0), product("b", "BB", 100, 28.0)])
    carts = {
        cart_key("b", [CartLine(item_id="b", sku="BB", pack_quantity=1, unit_price=28.0),
                       CartLine(item_id="g", sku="BG", pack_quantity=2, unit_price=38.0)]):
        VerifiedCart(cart_key="", supplier_id="b", verification_status="verified",
                     costs=Costs(subtotal=104.0, shipping=25.0, tax=0.0, total=129.0)),
    }
    res = optimize(ITEMS, normalize(ITEMS, [a, b], TH), {"a": a, "b": b}, carts, {"a": "A", "b": "B"})
    best = res.plans[0]
    assert best.label == "Everything from A"  # 110 free shipping beats 104 + 25
    assert best.total == 110.0
    assert any(p.status == "verified" for p in res.plans)


def test_minimum_order_removes_plans_and_bundle_is_listed():
    a = SupplierOffers(supplier_id="a", minimum_order_value=500, products=[product("g", "AG", 100, 10.0)])
    q = SupplierOffers(supplier_id="q", source="call", bundle_total=50.0, quoted_delivery_fee=5.0)
    res = optimize(ITEMS[:1], normalize(ITEMS[:1], [a, q], TH), {"a": a, "q": q}, {}, {})
    assert [p.kind for p in res.plans] == ["quoted_bundle"]
    assert res.plans[0].total == 55.0 and res.plans[0].status == "quoted"


def test_subset_requests_come_from_top_split_plans():
    a = SupplierOffers(supplier_id="a", products=[product("g", "AG", 100, 10.0), product("b", "AB", 100, 90.0)])
    b = SupplierOffers(supplier_id="b", products=[product("g", "BG", 100, 30.0), product("b", "BB", 100, 20.0)])
    res = optimize(ITEMS, normalize(ITEMS, [a, b], TH), {"a": a, "b": b}, {}, {})
    assert res.plans[0].kind == "split"
    assert {sid for sid, _ in res.subset_requests} == {"a", "b"}


def test_product_url_reaches_every_cart_line():
    # A lane opens the product page to add an item. Without the URL an adapter fails at cart (found in a live run).
    a = SupplierOffers(supplier_id="a", products=[product("g", "AG", 100, 10.0, url="https://a/AG"),
                                                  product("b", "AB", 100, 90.0, url="https://a/AB")])
    b = SupplierOffers(supplier_id="b", products=[product("g", "BG", 100, 30.0, url="https://b/BG"),
                                                  product("b", "BB", 100, 20.0, url="https://b/BB")])
    normalized = normalize(ITEMS, [a, b], TH)
    assert normalized.best[("b", "a")].url == "https://a/AB"
    res = optimize(ITEMS, normalized, {"a": a, "b": b}, {}, {})
    lines = [line for plan in res.plans for cart in plan.carts for line in cart.lines]
    lines += [line for _, subset in res.subset_requests for line in subset]
    assert lines and all(line.url == f"https://{line.sku[0].lower()}/{line.sku}" for line in lines)


def _plan(total: float, status: str = "verified", sid: str = "a") -> Plan:
    cart = PlanCart(cart_key=f"{sid}:x", supplier_id=sid, lines=[], subtotal=total, shipping=0, tax=0,
                    total=total, status=status)
    return Plan(plan_id=f"p{total}", kind="single_supplier", label="x", assignments=[], carts=[cart],
                total=total, status=status)


def test_rules_and_recommendation():
    rules = derive_rules(largest_past_order=100, monthly_average_spend=500, spent_this_month=0,
                         approved_suppliers=["a"])
    assert rules.per_order_cap == 150
    assert check_plan(_plan(120), rules).passed
    failed = check_plan(_plan(200, sid="z"), rules).failed
    assert {f.code for f in failed} == {"per_order_cap", "supplier_not_approved"}

    plans = [_plan(90, "estimated"), _plan(95, "quoted"), _plan(120)]
    best, _ = recommend(plans, accept_quoted=False)
    assert best is not None and best.total == 120  # the estimate and the quote do not win by default
    best, _ = recommend(plans, accept_quoted=True)
    assert best is not None and best.total == 95


def test_savings_and_cards():
    plan = _plan(100.0)
    per_order, yearly = savings(plan, ITEMS)  # past = 200*0.15 + 100*0.9 = 120
    assert per_order == 20.0
    assert yearly == round(20.0 * 365 / 14, 2)
    cards = mint_cards("job_1", plan, {"a": "https://shop.example"})
    assert cards[0].merchant_lock == "shop.example"
    assert cards[0].limit_usd == 102.0 and cards[0].simulated


def test_agent_lane_searches_only_the_largest_spend_items():
    """An LLM-agent lane is too slow for every item (H3 gate), so it gets the largest past spend first."""
    from types import SimpleNamespace

    from app.config import Settings
    from app.contracts import ShoppingItem
    from app.orchestrator import Orchestrator

    items = [
        ShoppingItem(id="gloves", name="gloves", quantity=2000, unit="glove", past_unit_price=0.05),  # 100
        ShoppingItem(id="boxes", name="boxes", quantity=500, unit="box", past_unit_price=0.89),  # 445
        ShoppingItem(id="bags", name="bags", quantity=100, unit="bag"),  # no price: 0
    ]
    orch = SimpleNamespace(settings=Settings(_env_file=None))
    rt = SimpleNamespace(request=SimpleNamespace(items=items))
    agent = Orchestrator._lane_items(orch, rt, SimpleNamespace(is_agent=True))
    assert [i.id for i in agent] == ["boxes"]
    assert Orchestrator._lane_items(orch, rt, SimpleNamespace(is_agent=False)) == items
