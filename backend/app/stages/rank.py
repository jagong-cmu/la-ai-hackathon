"""Rank plans and assemble JobResults. Warnings are code templates over Jev answers (M4)."""

from __future__ import annotations

import statistics

from app.contracts import (
    JobResults,
    LaneOutcome,
    LaneStatus,
    Plan,
    ResultWarning,
    ShoppingItem,
)
from app.stages.normalize import NormalizeResult
from app.stages.rules import SpendingRules, check_plan

FLAG_TEXT = {
    "low_match": "match is uncertain",
    "overbuy": "pack size buys more than you need",
    "pack_unknown": "pack size not stated",
}


def recommend(plans: list[Plan], accept_quoted: bool) -> tuple[Plan | None, str | None]:
    for plan in sorted(plans, key=lambda p: p.total):
        if plan.uncovered_items:
            continue
        if plan.rules_check is not None and not plan.rules_check.passed:
            continue
        if plan.status == "verified" or (plan.status == "quoted" and accept_quoted):
            return plan, None
    if not plans:
        return None, "No supplier returned a usable offer for these items."
    if not any(p.status == "verified" for p in plans):
        return None, "No plan has verified carts yet. The totals shown are estimates."
    return None, "Every verified plan breaks a spending rule or misses an item."


def savings(plan: Plan | None, items: list[ShoppingItem]) -> tuple[float | None, float | None]:
    if plan is None or plan.uncovered_items or any(i.past_unit_price is None for i in items):
        return None, None
    past = sum(i.quantity * (i.past_unit_price or 0.0) for i in items)
    per_order = round(past - plan.total, 2)
    cadences = [i.order_cadence_days for i in items if i.order_cadence_days]
    if not cadences:
        return per_order, None
    orders_per_year = 365 / statistics.median(cadences)
    return per_order, round(per_order * orders_per_year, 2)


def build_results(
    job_id: str,
    items: list[ShoppingItem],
    plans: list[Plan],
    normalized: NormalizeResult,
    lanes: list[LaneStatus],
    rules: SpendingRules,
    names: dict[str, str],
    *,
    accept_quoted: bool,
    deadline_hit: bool,
    summary: str = "",
    terms: dict[str, dict[str, float]] | None = None,
) -> JobResults:
    for plan in plans:
        plan.rules_check = check_plan(plan, rules)
    best, reason = recommend(plans, accept_quoted)
    per_order, yearly = savings(best, items)
    quoted = next((p for p in sorted(plans, key=lambda p: p.total) if p.status == "quoted"), None)

    warnings: list[ResultWarning] = []
    item_names = {i.id: i.name for i in items}
    if best is not None:
        for a in best.assignments:
            opt = normalized.best.get((a.item_id, a.supplier_id))
            for flag in opt.flags if opt else []:
                name = names.get(a.supplier_id, a.supplier_id)
                item = item_names.get(a.item_id, a.item_id)
                if flag.startswith("must_have_unsure:"):
                    text = f"{name} {item}: '{flag.split(':', 1)[1]}' not confirmed."
                else:
                    text = f"{name} {item}: {FLAG_TEXT.get(flag, flag)}."
                warnings.append(ResultWarning(code=flag.split(":")[0], text=text,
                                              item_id=a.item_id, supplier_id=a.supplier_id))
        for cart in best.carts:
            if cart.shipping_is_estimate:
                warnings.append(ResultWarning(code="shipping_estimate", supplier_id=cart.supplier_id,
                                              text=f"{names.get(cart.supplier_id, cart.supplier_id)}: shipping is an estimate."))
    for sid, t in (terms or {}).items():
        for key, label in (("conditional_price", "the price has conditions"),
                           ("availability_risk", "availability is uncertain")):
            if t.get(key, 0.0) >= 0.5:
                warnings.append(ResultWarning(code=key, supplier_id=sid,
                                              text=f"{names.get(sid, sid)} quote: {label}."))
    for lane in lanes:
        if lane.state.value in ("failed", "blocked"):
            warnings.append(ResultWarning(code="lane_" + lane.state.value, supplier_id=lane.supplier_id,
                                          text=f"{names.get(lane.supplier_id, lane.supplier_id)} was not checked ({lane.reason})."))

    return JobResults(
        job_id=job_id,
        status="partial" if deadline_hit else "complete",
        deadline_hit=deadline_hit,
        recommended_plan_id=best.plan_id if best else None,
        no_recommendation_reason=reason,
        plans=plans,
        quoted_plan_id=quoted.plan_id if quoted else None,
        savings_vs_paid_before=per_order,
        projected_yearly_savings=yearly,
        warnings=warnings,
        summary=summary,
        lane_outcomes=[LaneOutcome(supplier_id=lane.supplier_id, state=lane.state, reason=lane.reason)
                       for lane in lanes],
    )


def summary_facts(results: JobResults, plans: list[Plan]) -> dict:
    best = next((p for p in plans if p.plan_id == results.recommended_plan_id), None)
    return {
        "recommended": {"label": best.label, "total": best.total, "status": best.status} if best else None,
        "alternatives": [{"label": p.label, "total": p.total, "status": p.status} for p in plans[:4]],
        "savingsVsPaidBefore": results.savings_vs_paid_before,
        "projectedYearlySavings": results.projected_yearly_savings,
        "warnings": [w.text for w in results.warnings],
        "noRecommendationReason": results.no_recommendation_reason,
    }
