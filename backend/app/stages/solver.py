"""MILP fallback for the optimizer (PuLP + CBC), used when the full enumeration is too large.

The enumerator in optimize.py prices every assignment exactly. Past MAX_ASSIGNMENTS that is too
slow, so this module picks a short list of candidate assignments instead, and optimize.py prices
each one with the same price_cart() as the enumerator. The solver only chooses; it never sets a
number that the owner sees.

Model, for each covered item i and allowed supplier s:
    x[i,s] in {0,1}     item i is bought from supplier s
    y[s]   in {0,1}     supplier s gets a cart
    z[s]   in {0,1}     supplier s's cart reaches free shipping (only when it has a threshold)
    sum_s x[i,s] = 1                     every item from exactly one supplier
    x[i,s] <= y[s] <= sum_i x[i,s]
    sub[s] = sum_i line_cost[i,s] x[i,s]
    sub[s] >= minimum_order_value[s] y[s]
    sub[s] >= free_shipping_threshold[s] z[s],  z[s] <= y[s]
minimize sum_s sub[s] (1 + tax_rate[s]) + shipping[s] (y[s] - z[s])   (estimated suppliers)
                    + sub[s] + quoted_delivery_fee[s] y[s]            (quoted suppliers)

The objective is the estimate price_cart() would give with no verified cart. A verified cart can
cost a little more or less than its estimate, so we ask for more candidates than optimize.py
keeps (POOL_FACTOR) and let the exact prices decide the order. The top-k list comes from solving
again with a "no-good" cut that removes each assignment already found.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Sequence

import pulp

from app.contracts import ShoppingItem
from app.stages.normalize import NormalizeResult
from app.stages.optimize import SupplierPricing, base_shipping

log = logging.getLogger(__name__)

POOL_FACTOR = 3
TIME_LIMIT_S = 2.0

Combo = tuple[str, ...]


def candidate_assignments(
    covered: Sequence[ShoppingItem],
    choices: Sequence[Sequence[str]],
    normalized: NormalizeResult,
    pricing: dict[str, SupplierPricing],
    *,
    web_splits: int,
    quoted_splits: int,
    time_limit_s: float = TIME_LIMIT_S,
) -> list[Combo]:
    """Assignments (one supplier id per covered item, in order) worth pricing exactly.

    Returns every single-supplier assignment, the cheapest web-only assignments, and the
    cheapest assignments that use at least one quoted supplier, matching optimize.py's quotas.
    """
    combos: list[Combo] = []
    seen: set[Combo] = set()

    def add(combo: Combo) -> None:
        if combo not in seen:
            seen.add(combo)
            combos.append(combo)

    # Single-supplier plans have no quota in optimize.py, so list all of them directly.
    for sid in sorted(set().union(*map(set, choices))) if choices else []:
        if all(sid in c for c in choices):
            add(tuple(sid for _ in covered))

    quoted = {s for s, p in pricing.items() if p.quoted}
    for mode, k in (("web_only", web_splits), ("with_quote", quoted_splits)):
        if mode == "with_quote" and not quoted:
            continue
        with warnings.catch_warnings():  # PuLP 3.x warns about its 4.0 API; we pin <4
            warnings.simplefilter("ignore", DeprecationWarning)
            found = _top_k(covered, choices, normalized, pricing, quoted, mode, k * POOL_FACTOR, time_limit_s)
        for combo in found:
            add(combo)
    return combos


def _top_k(
    covered: Sequence[ShoppingItem],
    choices: Sequence[Sequence[str]],
    normalized: NormalizeResult,
    pricing: dict[str, SupplierPricing],
    quoted: set[str],
    mode: str,
    k: int,
    time_limit_s: float,
) -> list[Combo]:
    prob = pulp.LpProblem(f"restock_{mode}", pulp.LpMinimize)
    suppliers = sorted(set().union(*map(set, choices)))
    pairs = [(n, s) for n, c in enumerate(choices) for s in c]
    x = {(n, s): pulp.LpVariable(f"x_{n}_{suppliers.index(s)}", cat="Binary") for n, s in pairs}
    y = {s: pulp.LpVariable(f"y_{j}", cat="Binary") for j, s in enumerate(suppliers)}

    for n, c in enumerate(choices):
        prob += pulp.lpSum(x[(n, s)] for s in c) == 1
    for (_, s), var in x.items():
        prob += var <= y[s]
    for s in suppliers:  # y[s] = 1 only when s gets an item, so "uses a quote" means a real cart
        prob += y[s] <= pulp.lpSum(x[(n, s)] for n, c in enumerate(choices) if s in c)

    cost = []
    for j, s in enumerate(suppliers):
        p = pricing[s]
        sub = pulp.lpSum(_line_cost(normalized, covered[n].id, s) * x[(n, s)]
                         for n, c in enumerate(choices) if s in c)
        if p.minimum_order_value > 0:
            prob += sub >= p.minimum_order_value * y[s]
        if p.quoted:
            cost.append(sub + p.quoted_delivery_fee * y[s])
            continue
        shipping = base_shipping(p)
        cost.append(sub * (1 + (p.tax_rate or 0.0)))
        if p.free_shipping_threshold is None:
            cost.append(shipping * y[s])
        else:
            z = pulp.LpVariable(f"z_{j}", cat="Binary")
            prob += z <= y[s]
            prob += sub >= p.free_shipping_threshold * z
            cost.append(shipping * (y[s] - z))
    prob += pulp.lpSum(cost)

    if mode == "web_only":
        for s in quoted:
            if s in y:
                prob += y[s] == 0
    else:
        prob += pulp.lpSum(y[s] for s in quoted if s in y) >= 1

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit_s)
    found: list[Combo] = []
    for _ in range(k):
        prob.solve(solver)
        if prob.sol_status not in (pulp.LpSolutionOptimal, pulp.LpSolutionIntegerFeasible):
            break
        combo = tuple(
            next(s for s in c if (x[(n, s)].value() or 0) > 0.5) for n, c in enumerate(choices)
        )
        found.append(combo)
        # No-good cut: forbid exactly this assignment and solve for the next best one.
        prob += pulp.lpSum(x[(n, s)] for n, s in enumerate(combo)) <= len(combo) - 1
    log.debug("milp %s: %d candidates", mode, len(found))
    return found


def _line_cost(normalized: NormalizeResult, item_id: str, supplier_id: str) -> float:
    """The cart line's cost exactly as price_cart() sums it."""
    opt = normalized.best[(item_id, supplier_id)]
    return opt.pack_price * opt.packs_needed
