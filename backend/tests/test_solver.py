import random
import time

import pytest

import app.stages.solver as solver
from app.config import Thresholds
from app.contracts import (
    CartLine,
    Costs,
    JevListing,
    Product,
    ShoppingItem,
    SupplierOffers,
    VerifiedCart,
    cart_key,
)
from app.stages.normalize import normalize
from app.stages.optimize import MAX_ASSIGNMENTS, optimize, supplier_pricing

TH = Thresholds()
JEV = JevListing(match_score=1.9, match_probs={"0": 0.0, "1": 0.05, "2": 0.95}, must_have={}, in_stock=0.95)
# J5 doubts: low_match, and low_match + must_have_unsure (normalize.py adds a ranking penalty).
DOUBTS = [JEV,
          JevListing(match_score=1.4, match_probs={"0": 0.0, "1": 0.3, "2": 0.7}, must_have={}, in_stock=0.95),
          JevListing(match_score=1.4, match_probs={"0": 0.0, "1": 0.3, "2": 0.7}, must_have={"nitrile": 0.6},
                     in_stock=0.95)]


def _instance(seed: int, n_items: int, n_suppliers: int, quoted: bool = True, doubts: bool = False):
    rng = random.Random(seed)
    items = [ShoppingItem(id=f"i{n}", name=f"item {n}", quantity=rng.choice([50, 100, 200]), unit="ea",
                          past_unit_price=1.0, order_cadence_days=14) for n in range(n_items)]
    offers: dict[str, SupplierOffers] = {}
    for j in range(n_suppliers):
        sid = f"s{j}"
        is_quoted = quoted and j == n_suppliers - 1
        products = [Product(item_id=it.id, title=f"{sid}-{it.id}", sku=f"{sid}-{it.id}",
                            pack_price=round(rng.uniform(5, 60), 2), units_per_pack=rng.choice([50, 100]),
                            jev=rng.choice(DOUBTS) if doubts else JEV)
                    for it in items if rng.random() < 0.85]
        offers[sid] = SupplierOffers(
            supplier_id=sid,
            source="call" if is_quoted else "web",
            products=products,
            minimum_order_value=rng.choice([0, 0, 40]),
            free_shipping_threshold=None if is_quoted else rng.choice([None, 75.0, 150.0]),
            quoted_delivery_fee=rng.choice([0.0, 12.0]) if is_quoted else None,
        )
    return items, offers


def _run(items, offers, carts=None, mode="auto"):
    normalized = normalize(items, list(offers.values()), TH)
    return optimize(items, normalized, offers, carts or {}, {s: s.upper() for s in offers}, solver=mode)


def _summary(res):
    return [(p.plan_id, p.total) for p in res.plans], res.subset_requests


@pytest.mark.parametrize("seed", range(12))
def test_milp_matches_enumeration(seed):
    items, offers = _instance(seed, n_items=5, n_suppliers=4)
    assert _summary(_run(items, offers, mode="milp")) == _summary(_run(items, offers, mode="enumerate"))


@pytest.mark.parametrize("seed", range(8))
def test_milp_matches_enumeration_with_jev_doubts(seed):
    items, offers = _instance(100 + seed, n_items=5, n_suppliers=4, doubts=True)
    milp = _run(items, offers, mode="milp")
    assert any(p.risk_penalty > 0 for p in milp.plans)
    assert _summary(milp) == _summary(_run(items, offers, mode="enumerate"))


def test_backend_is_one_that_runs():
    assert solver._backend() in ("HiGHS", "PULP_CBC_CMD")


def test_no_runnable_backend_falls_back_to_trimming(monkeypatch):
    def broken(name, time_limit_s):
        raise OSError(f"{name}: bad CPU type")

    monkeypatch.setattr(solver, "_make_solver", broken)
    solver._backend.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="no MILP solver"):
            solver._backend()
        items, offers = _instance(3, n_items=5, n_suppliers=4)
        with pytest.raises(RuntimeError):
            _run(items, offers, mode="milp")
        monkeypatch.setattr("app.stages.optimize.MAX_ASSIGNMENTS", 10)
        assert _run(items, offers).plans  # auto mode trims to 3 suppliers per item
    finally:
        monkeypatch.undo()
        solver._backend.cache_clear()


def test_spent_budget_returns_only_single_supplier_plans():
    items = [ShoppingItem(id=f"i{n}", name=f"item {n}", quantity=100, unit="ea", past_unit_price=1.0,
                          order_cadence_days=14) for n in range(4)]
    offers = {sid: SupplierOffers(supplier_id=sid, products=[
        Product(item_id=it.id, title=it.id, sku=f"{sid}-{it.id}", pack_price=10.0, units_per_pack=100, jev=JEV)
        for it in items]) for sid in ("s0", "s1", "s2")}
    normalized = normalize(items, list(offers.values()), TH)
    choices = [sorted(normalized.suppliers_for(it.id)) for it in items]
    combos = solver.candidate_assignments(items, choices, normalized, supplier_pricing(offers, {}),
                                          web_splits=6, quoted_splits=3, total_budget_s=0)
    assert combos == [("s0",) * 4, ("s1",) * 4, ("s2",) * 4]


def test_milp_uses_verified_cart_prices():
    # Same scenario as test_plan.test_free_shipping_threshold_changes_the_best_plan.
    items = [ShoppingItem(id="g", name="gloves", quantity=200, unit="glove", past_unit_price=0.15,
                          order_cadence_days=14),
             ShoppingItem(id="b", name="boxes", quantity=100, unit="box", past_unit_price=0.9,
                          order_cadence_days=14)]

    def prod(item, sku, price):
        return Product(item_id=item, title=sku, sku=sku, pack_price=price, units_per_pack=100, jev=JEV)

    offers = {
        "a": SupplierOffers(supplier_id="a", free_shipping_threshold=100,
                            products=[prod("g", "AG", 40.0), prod("b", "AB", 30.0)]),
        "b": SupplierOffers(supplier_id="b", products=[prod("g", "BG", 38.0), prod("b", "BB", 28.0)]),
    }
    carts = {
        cart_key("b", [CartLine(item_id="b", sku="BB", pack_quantity=1, unit_price=28.0),
                       CartLine(item_id="g", sku="BG", pack_quantity=2, unit_price=38.0)]):
        VerifiedCart(cart_key="", supplier_id="b", verification_status="verified",
                     costs=Costs(subtotal=104.0, shipping=25.0, tax=0.0, total=129.0)),
    }
    milp = _run(items, offers, carts, mode="milp")
    assert _summary(milp) == _summary(_run(items, offers, carts, mode="enumerate"))
    assert milp.plans[0].label == "Everything from A" and milp.plans[0].total == 110.0


def test_large_order_uses_milp_and_beats_trimming(monkeypatch):
    items, offers = _instance(7, n_items=9, n_suppliers=5)
    normalized = normalize(items, list(offers.values()), TH)
    combos = 1
    for it in items:
        combos *= max(1, len(normalized.suppliers_for(it.id)))
    assert combos > MAX_ASSIGNMENTS

    calls = []
    real = solver.candidate_assignments
    assert solver._backend()  # the MILP itself runs here, not the trim fallback
    monkeypatch.setattr(solver, "candidate_assignments", lambda *a, **kw: calls.append(1) or real(*a, **kw))
    start = time.monotonic()
    milp = _run(items, offers)
    assert calls and time.monotonic() - start < 20
    assert milp.plans

    def broken(*a, **kw):
        raise RuntimeError("cbc missing")

    monkeypatch.setattr(solver, "candidate_assignments", broken)
    trimmed = _run(items, offers)  # auto mode falls back to the old 3-per-item trim
    assert milp.plans[0].total <= trimmed.plans[0].total
    with pytest.raises(RuntimeError):
        _run(items, offers, mode="milp")
