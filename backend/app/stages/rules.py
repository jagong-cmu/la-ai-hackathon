"""Spending rules (Visa track). Checked on every plan before approval, and again at approval."""

from __future__ import annotations

from app.contracts import Model, Plan, RuleFailure, RulesCheck


class SpendingRules(Model):
    per_order_cap: float
    approved_suppliers: list[str]
    monthly_budget_remaining: float
    source: str = "derived from the books"


def derive_rules(
    largest_past_order: float,
    monthly_average_spend: float,
    spent_this_month: float,
    approved_suppliers: list[str],
) -> SpendingRules:
    return SpendingRules(
        per_order_cap=round(1.5 * largest_past_order, 2),
        approved_suppliers=sorted(set(approved_suppliers)),
        monthly_budget_remaining=round(max(0.0, 1.2 * monthly_average_spend - spent_this_month), 2),
    )


def check_plan(plan: Plan, rules: SpendingRules) -> RulesCheck:
    failed: list[RuleFailure] = []
    if plan.total > rules.per_order_cap:
        failed.append(RuleFailure(code="per_order_cap",
                                  text=f"${plan.total:.2f} is above the per-order cap of ${rules.per_order_cap:.2f}."))
    for cart in plan.carts:
        if cart.supplier_id not in rules.approved_suppliers:
            failed.append(RuleFailure(code="supplier_not_approved",
                                      text=f"{cart.supplier_id} is not on the approved supplier list."))
    if plan.total > rules.monthly_budget_remaining:
        failed.append(RuleFailure(code="monthly_budget",
                                  text=f"${plan.total:.2f} is above this month's remaining budget of ${rules.monthly_budget_remaining:.2f}."))
    return RulesCheck(passed=not failed, failed=failed)
