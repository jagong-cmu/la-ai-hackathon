"""J11 miss cases with WEAK labels, derived from the replay of the year.

Where the cases come from:
  `app.planner.loop.replay` gives every week in which a product's actual sales fell outside
  the one-step forecast range. We use two deterministic variants: "plain" (no history changes)
  and "fixed_rule" (every miss resets the level, so the range is narrower and more weeks miss).
  A week that misses in both variants is kept once (the plain state). For each miss we
  keep the same `state` that `Planner._diagnose` sends to J11: item, week, the miss bucket,
  the previous week's bucket and the code-detected facts.

How the labels are made (WEAK labels, not human labels):
  A fixed rule list reads the facts AND the next weeks of sales (hindsight). The methods
  under test see only the state; they never see the future weeks. The first rule that
  fires gives the label, and the rule id is saved as `provenance.rule`.

    R1 backorder memo + below the range            -> supply_shortage
    R2 lost-customer fact + below the range         -> customer_lost
    R3 new-product fact + below + still below later -> product_substitution
    R4 first order + above, the customer reorders   -> customer_gained
    R5 first order + above, no reorder              -> one_time_event
    R6 >= 2 of the next 3 weeks also outside, same side -> lasting_shift_up / lasting_shift_down
    R7 far above, then back inside                  -> one_time_event
    R8 anything else                                -> noise

Known limits: R1 and R2 use the same facts as the keyword baseline (`app.stub_rules`), so the
baseline scores well on those causes by construction. The data is simulated (qb-world-sim).
Misses with fewer than 2 later weeks get no label and are left out.

Regenerate the file: `uv run python -m eval.j11_labels`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.planner.loop import replay
from app.planner.sales import SalesData, load_fixture

MISS_CASES = Path(__file__).resolve().parent / "data" / "miss_cases.jsonl"
HINDSIGHT_WEEKS = 3
LABEL_KIND = "weak (rule over facts + hindsight; not human-verified)"


def _side(actual: float, low: float, high: float) -> int:
    return -1 if actual < low else (1 if actual > high else 0)


def weak_label(sales: SalesData, item_id: str, ctx: Any, raw: list[float]) -> tuple[str | None, dict]:
    t = ctx.week
    later = raw[t + 1:t + 1 + HINDSIGHT_WEEKS]
    hind = {"laterWeeks": [round(x, 1) for x in later], "low": round(ctx.low, 1), "high": round(ctx.high, 1)}
    if len(later) < 2:
        return None, {"rule": "skip:insufficient_hindsight", **hind}
    below = "below" in ctx.bucket
    facts = " ".join(ctx.facts).lower()
    sides = [_side(x, ctx.low, ctx.high) for x in later]
    same_side = sum(s == (-1 if below else 1) for s in sides)
    hind["laterSameSide"] = same_side

    if below and "backorder" in facts:
        return "supply_shortage", {"rule": "R1:backorder_memo+below", **hind}
    if below and ctx.lost_customers:
        return "customer_lost", {"rule": "R2:lost_customer+below", "lostCustomers": ctx.lost_customers, **hind}
    if below and "new product" in facts and same_side >= 2:
        return "product_substitution", {"rule": "R3:launch+below+persists", **hind}
    this_week = sales.by_customer.get((t, item_id), {})
    new_customers = [cid for cid, q in this_week.items() if q > 0 and not any(
        sales.by_customer.get((w, item_id), {}).get(cid, 0.0) > 0 for w in range(t))]
    if not below and new_customers:
        reorders = sum(any(sales.by_customer.get((w, item_id), {}).get(cid, 0.0) > 0 for cid in new_customers)
                       for w in range(t + 1, t + 1 + HINDSIGHT_WEEKS))
        hind["newCustomers"] = new_customers
        hind["newCustomerReorderWeeks"] = reorders
        if reorders >= 2:
            return "customer_gained", {"rule": "R4:first_order+reorders", **hind}
        return "one_time_event", {"rule": "R5:first_order+no_reorder", **hind}
    if same_side >= 2:
        return ("lasting_shift_down" if below else "lasting_shift_up"), {"rule": "R6:persists", **hind}
    if ctx.bucket.startswith("far above") and sides[0] == 0:
        return "one_time_event", {"rule": "R7:far_above+reverts", **hind}
    return "noise", {"rule": "R8:default", **hind}


async def build_cases(sales: SalesData | None = None) -> list[dict]:
    sales = sales or load_fixture()
    cases: list[dict] = []
    seen: set[str] = set()
    for variant in ("plain", "fixed_rule"):
        for item_id in sales.products:
            if sales.launch_week(item_id) is None:
                continue
            state = await replay(sales, item_id, variant)
            for m in state.misses:
                if m.miss_id in seen:
                    continue
                seen.add(m.miss_id)
                case = _case(sales, item_id, variant, m, state.raw)
                if case is not None:
                    cases.append(case)
    return cases


def _case(sales: SalesData, item_id: str, variant: str, m: Any, raw: list[float]) -> dict | None:
    ctx = m.ctx
    label, prov = weak_label(sales, item_id, ctx, raw)
    if label is None:
        return None
    return {
        "id": m.miss_id,
        "split": "dev" if ctx.week % 3 == 0 else "test",
        "state": {  # exactly what Planner._diagnose sends to J11
            "item": ctx.item_name, "week": ctx.week_start, "miss": ctx.bucket,
            "previousWeek": ctx.previous_bucket,
            "facts": ctx.facts or ["No unusual events were found."],
        },
        "label": label,
        "labelKind": LABEL_KIND,
        "provenance": {"source": f"replay variant={variant} on fixtures/company-001-sales.json",
                       "itemId": item_id, "weekIndex": ctx.week, "actual": ctx.actual,
                       "forecastMean": round(ctx.mean, 1), **prov},
    }


def write_cases(cases: list[dict], path: Path = MISS_CASES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(c) + "\n" for c in cases))


def load_cases(path: Path = MISS_CASES) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    rows = asyncio.run(build_cases())
    write_cases(rows)
    print(f"wrote {len(rows)} miss cases to {MISS_CASES}")
