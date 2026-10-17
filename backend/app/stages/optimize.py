"""Optimizer: every assignment of items to suppliers, priced per supplier cart.

Free-shipping thresholds and minimum orders make a greedy choice wrong, so we list
every assignment (suppliers^items; 4^8 = 65,536 is fast). Past MAX_ASSIGNMENTS a PuLP MILP
(app/stages/solver.py) picks the candidate assignments instead; both paths price plans with
the same code below. Cart prices come from, in order:
  1. a verified cart with the exact same contents (cart_key),
  2. a quoted offer (phone/SMS) for that supplier,
  3. an estimate: shipping from the supplier's free-shipping threshold or its verified
     full-cart shipping, else a flat default; tax from the verified full cart's rate.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from app.contracts import (
    Assignment,
    CartLine,
    Plan,
    PlanCart,
    ShoppingItem,
    SupplierOffers,
    VerifiedCart,
    cart_key,
)
from app.db import stable_hash
from app.stages.normalize import NormalizeResult

log = logging.getLogger(__name__)

SolverMode = Literal["auto", "enumerate", "milp"]

DEFAULT_SHIPPING = 8.00
MAX_ASSIGNMENTS = 200_000
KEEP_SPLIT_PLANS = 6
KEEP_QUOTED_SPLIT_PLANS = 2


@dataclass
class SupplierPricing:
    """What we know about a supplier's cart costs beyond the line prices."""

    minimum_order_value: float = 0.0
    free_shipping_threshold: float | None = None
    reference_shipping: float | None = None  # from a verified cart
    tax_rate: float | None = None  # from a verified cart
    quoted: bool = False
    quoted_delivery_fee: float = 0.0


@dataclass
class OptimizeResult:
    plans: list[Plan]
    subset_requests: list[tuple[str, list[CartLine]]] = field(default_factory=list)


def supplier_pricing(
    offers: dict[str, SupplierOffers], carts: dict[str, VerifiedCart]
) -> dict[str, SupplierPricing]:
    pricing: dict[str, SupplierPricing] = {}
    for sid, offer in offers.items():
        p = SupplierPricing(
            minimum_order_value=offer.minimum_order_value,
            free_shipping_threshold=offer.free_shipping_threshold,
            quoted=offer.is_quoted,
            quoted_delivery_fee=offer.quoted_delivery_fee or 0.0,
        )
        verified = [
            c for c in carts.values()
            if c.supplier_id == sid and c.verification_status == "verified" and c.costs
        ]
        if verified:
            ref = max(verified, key=lambda c: len(c.cart_items))
            assert ref.costs is not None
            p.reference_shipping = ref.costs.shipping
            if ref.costs.subtotal > 0:
                p.tax_rate = ref.costs.tax / ref.costs.subtotal
        pricing[sid] = p
    return pricing


def base_shipping(pricing: SupplierPricing) -> float:
    """Estimated shipping for a cart below the free-shipping threshold (or with none)."""
    if pricing.reference_shipping is not None and (
        pricing.free_shipping_threshold is None or pricing.reference_shipping > 0
    ):
        return pricing.reference_shipping
    return DEFAULT_SHIPPING


def price_cart(
    supplier_id: str, lines: list[CartLine], pricing: SupplierPricing, carts: dict[str, VerifiedCart]
) -> PlanCart | None:
    key = cart_key(supplier_id, lines)
    subtotal = round(sum(line.unit_price * line.pack_quantity for line in lines), 2)
    if subtotal < pricing.minimum_order_value:
        return None
    verified = carts.get(key)
    if verified is not None and verified.verification_status == "verified" and verified.costs:
        c = verified.costs
        return PlanCart(cart_key=key, supplier_id=supplier_id, lines=lines, subtotal=c.subtotal,
                        shipping=c.shipping, tax=c.tax + c.other_fees, total=c.total, status="verified")
    if pricing.quoted:
        total = round(subtotal + pricing.quoted_delivery_fee, 2)
        return PlanCart(cart_key=key, supplier_id=supplier_id, lines=lines, subtotal=subtotal,
                        shipping=pricing.quoted_delivery_fee, tax=0.0, total=total, status="quoted")
    if pricing.free_shipping_threshold is not None and subtotal >= pricing.free_shipping_threshold:
        shipping = 0.0
    else:
        shipping = base_shipping(pricing)
    tax = round(subtotal * (pricing.tax_rate or 0.0), 2)
    return PlanCart(cart_key=key, supplier_id=supplier_id, lines=lines, subtotal=subtotal,
                    shipping=shipping, tax=tax, total=round(subtotal + shipping + tax, 2),
                    status="estimated", shipping_is_estimate=True)


def _plan_status(carts: list[PlanCart]) -> str:
    statuses = {c.status for c in carts}
    if statuses == {"verified"}:
        return "verified"
    if "estimated" in statuses:
        return "estimated"
    return "quoted"


def _label(kind: str, suppliers: list[str], names: dict[str, str]) -> str:
    if kind == "single_supplier":
        return f"Everything from {names.get(suppliers[0], suppliers[0])}"
    return "Split: " + " + ".join(names.get(s, s) for s in suppliers)


def _assignments(
    covered: list[ShoppingItem],
    choices: list[list[str]],
    normalized: NormalizeResult,
    pricing: dict[str, SupplierPricing],
    solver: SolverMode,
) -> Iterable[tuple[str, ...]]:
    """The assignments to price: all of them, or the MILP's candidates when there are too many."""
    total_combos = math.prod(max(1, len(c)) for c in choices)
    if solver == "enumerate" or (solver == "auto" and total_combos <= MAX_ASSIGNMENTS):
        return itertools.product(*choices)
    try:
        from app.stages.solver import candidate_assignments

        return candidate_assignments(covered, choices, normalized, pricing,
                                     web_splits=KEEP_SPLIT_PLANS, quoted_splits=KEEP_QUOTED_SPLIT_PLANS)
    except Exception as exc:  # PuLP missing or CBC failed: the run must still end with plans
        if solver == "milp":
            raise
        log.warning("milp solver failed (%s); trimming to the 3 cheapest suppliers per item", exc)
    trimmed = [
        sorted(c, key=lambda s, i=item: normalized.best[(i.id, s)].line_cost)[:3]
        for c, item in zip(choices, covered, strict=True)
    ]
    return itertools.product(*trimmed)


def _build_plan(
    covered: list[ShoppingItem],
    combo: tuple[str, ...],
    normalized: NormalizeResult,
    pricing: dict[str, SupplierPricing],
    carts: dict[str, VerifiedCart],
    names: dict[str, str],
    uncovered: list[str],
) -> Plan | None:
    """Price one assignment exactly. None when a cart misses its supplier's minimum order."""
    by_supplier: dict[str, list[CartLine]] = {}
    assignments: list[Assignment] = []
    for item, sid in zip(covered, combo, strict=True):
        opt = normalized.best[(item.id, sid)]
        by_supplier.setdefault(sid, []).append(
            CartLine(item_id=item.id, sku=opt.sku, pack_quantity=opt.packs_needed,
                     unit_price=opt.pack_price)
        )
        assignments.append(Assignment(item_id=item.id, supplier_id=sid, sku=opt.sku,
                                      pack_quantity=opt.packs_needed, line_cost=opt.line_cost,
                                      source=opt.source))
    plan_carts: list[PlanCart] = []
    for sid, lines in sorted(by_supplier.items()):
        priced = price_cart(sid, sorted(lines, key=lambda line: line.item_id), pricing[sid], carts)
        if priced is None:
            return None
        plan_carts.append(priced)
    suppliers = [c.supplier_id for c in plan_carts]
    kind = "single_supplier" if len(suppliers) == 1 else "split"
    plan_id = "plan_" + stable_hash(sorted(c.cart_key for c in plan_carts))[:10]
    return Plan(
        plan_id=plan_id,
        kind=kind,
        label=_label(kind, suppliers, names),
        assignments=assignments,
        carts=plan_carts,
        uncovered_items=uncovered,
        total=round(sum(c.total for c in plan_carts), 2),
        status=_plan_status(plan_carts),
    )


def optimize(
    items: list[ShoppingItem],
    normalized: NormalizeResult,
    offers: dict[str, SupplierOffers],
    carts: dict[str, VerifiedCart],
    names: dict[str, str],
    solver: SolverMode = "auto",
) -> OptimizeResult:
    """Rank the plans. `solver`: "auto" enumerates up to MAX_ASSIGNMENTS and uses the MILP past
    that; "enumerate" and "milp" force one path (tests compare the two)."""
    pricing = supplier_pricing(offers, carts)
    covered = [item for item in items if normalized.suppliers_for(item.id)]
    uncovered = [item.id for item in items if not normalized.suppliers_for(item.id)]
    choices = [sorted(normalized.suppliers_for(item.id)) for item in covered]

    plans: dict[str, Plan] = {}
    for combo in _assignments(covered, choices, normalized, pricing, solver) if covered else []:
        plan = _build_plan(covered, combo, normalized, pricing, carts, names, uncovered)
        if plan is not None:
            plans[plan.plan_id] = plan

    for sid, offer in offers.items():
        if offer.bundle_total is not None:
            total = round(offer.bundle_total + (offer.quoted_delivery_fee or 0.0), 2)
            plan_id = f"plan_bundle_{sid}"
            plans[plan_id] = Plan(
                plan_id=plan_id, kind="quoted_bundle",
                label=f"{names.get(sid, sid)} quote for the whole list",
                assignments=[], uncovered_items=[],
                carts=[PlanCart(cart_key=f"{sid}:bundle", supplier_id=sid, lines=[],
                                subtotal=offer.bundle_total, shipping=offer.quoted_delivery_fee or 0.0,
                                tax=0.0, total=total, status="quoted")],
                total=total, status="quoted",
            )

    # plan_id breaks ties, so the order does not depend on which path listed the plans.
    ordered = sorted(plans.values(), key=lambda p: (len(p.uncovered_items), p.total, p.plan_id))
    # Web-only splits and splits that include a phone/SMS quote are kept in separate quotas,
    # so cheap quoted mixes never push the best verifiable web plans off the list.
    keep: list[Plan] = []
    web_splits = quoted_splits = 0
    for plan in ordered:
        if plan.kind == "split":
            has_quote = any(pricing[c.supplier_id].quoted for c in plan.carts)
            if has_quote:
                if quoted_splits >= KEEP_QUOTED_SPLIT_PLANS:
                    continue
                quoted_splits += 1
            else:
                if web_splits >= KEEP_SPLIT_PLANS:
                    continue
                web_splits += 1
        keep.append(plan)
    for rank, plan in enumerate(keep, start=1):
        plan.rank = rank

    subset_requests: list[tuple[str, list[CartLine]]] = []
    seen: set[str] = set()
    web_split_plans = [p for p in keep if p.kind == "split"
                       and not any(pricing[c.supplier_id].quoted for c in p.carts)]
    for plan in web_split_plans[:2]:
        for cart in plan.carts:
            if cart.status == "estimated" and cart.cart_key not in seen:
                seen.add(cart.cart_key)
                subset_requests.append((cart.supplier_id, cart.lines))
    return OptimizeResult(plans=keep, subset_requests=subset_requests)
