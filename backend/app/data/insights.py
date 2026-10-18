"""Spend insights (screen 0) and the prefilled shopping list (screen 1).

J1 sorts each distinct bill item into a category. Code does every number.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date

from app.contracts import (
    BillLineView,
    DistributorRef,
    ShoppingItem,
    ShoppingRequest,
    SpendInsightItem,
    SpendInsights,
    SupplierRef,
)
from app.data.loader import BillLine, Books
from app.decide import Decider
from app.judgments import j1_triage
from app.stages.rules import SpendingRules, derive_rules

SUPPLY_CATEGORIES = {"packaging_disposables", "cleaning_safety", "smallwares"}


async def build_insights(decider: Decider, books: Books) -> SpendInsights:
    by_item: dict[str, list[BillLine]] = defaultdict(list)
    for line in books.lines:
        by_item[line.item_id].append(line)

    items: list[SpendInsightItem] = []
    other_lines = 0
    for item_id, lines in by_item.items():
        first = lines[0]
        decision = await j1_triage(decider, first.item_name, first.description, first.vendor_name)
        category = decision.answers.get("category", {}).get("choice", "other")
        online = float(decision.answers.get("online_buyable", {}).get("noul", 0.0))
        if category not in SUPPLY_CATEGORIES or online < 0.5:
            other_lines += len(lines)
            continue
        qty = sum(line.quantity for line in lines)
        spend = round(sum(line.amount for line in lines), 2)
        dates = sorted({line.date for line in lines})
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        items.append(
            SpendInsightItem(
                item_id=item_id,
                bill_item_name=first.item_name,
                category=category,
                unit=first.unit,
                jev={"categoryProbs": decision.answers.get("category", {}).get("probabilities", {}),
                     "onlineBuyable": online, "band": decision.band},
                past_unit_price=round(spend / qty, 4) if qty else 0.0,
                past_order_qty=int(statistics.median(line.quantity for line in lines)),
                order_cadence_days=float(statistics.median(gaps)) if gaps else None,
                year_spend=spend,
                year_qty=int(qty),
                orders=len({line.bill_id for line in lines}),
                source_bill_ids=sorted({line.bill_id for line in lines}),
                synthetic=all(line.synthetic for line in lines),
            )
        )
    items.sort(key=lambda i: i.year_spend, reverse=True)
    supply_ids = {i.item_id for i in items}
    supply_bills = {line.bill_id for line in books.lines if line.item_id in supply_ids}
    period = _period_days(books.lines)
    return SpendInsights(
        business_id=books.business_id,
        period_days=period,
        supply_spend=round(sum(i.year_spend for i in items), 2),
        supply_orders=len(supply_bills),
        items=items,
        other_lines=other_lines,
        disclosure=books.disclosure,
    )


def supply_bill_lines(books: Books, insights: SpendInsights, item_id: str | None = None) -> list[BillLineView]:
    """The bill lines of the supply items, oldest first (the price history behind past_unit_price)."""
    supply_ids = {i.item_id for i in insights.items}
    lines = [line for line in books.lines
             if line.item_id in supply_ids and (item_id is None or line.item_id == item_id)]
    return [
        BillLineView(bill_id=line.bill_id, txn_date=line.date, vendor_name=line.vendor_name, item_id=line.item_id,
                     quantity=line.quantity, unit_price=line.unit_price, amount=line.amount,
                     synthetic=line.synthetic)
        for line in sorted(lines, key=lambda x: (x.date, x.bill_id))
    ]


def _period_days(lines: list[BillLine]) -> int:
    if not lines:
        return 0
    dates = [line.date for line in lines]
    return (max(dates) - min(dates)).days + 1


def draft_request(
    insights: SpendInsights,
    suppliers: list[SupplierRef],
    distributor: DistributorRef | None,
    zip_code: str,
) -> ShoppingRequest:
    return ShoppingRequest(
        business_id=insights.business_id,
        items=[
            ShoppingItem(
                id=i.item_id,
                name=i.bill_item_name,
                quantity=max(1, i.past_order_qty),
                unit=i.unit,
                past_unit_price=i.past_unit_price,
                order_cadence_days=i.order_cadence_days,
                source_bill_ids=i.source_bill_ids[-3:],
            )
            for i in insights.items
        ],
        suppliers=suppliers,
        distributor=distributor,
        zip_code=zip_code,
    )


def rules_from_books(
    books: Books, insights: SpendInsights, approved: list[str], today: date | None = None
) -> SpendingRules:
    supply_ids = {i.item_id for i in insights.items}
    per_bill: dict[str, float] = defaultdict(float)
    bill_dates: dict[str, date] = {}
    for line in books.lines:
        if line.item_id in supply_ids:
            per_bill[line.bill_id] += line.amount
            bill_dates[line.bill_id] = line.date
    largest = max(per_bill.values(), default=0.0)
    months = max(1.0, insights.period_days / 30.44)
    monthly = insights.supply_spend / months
    today = today or date.today()
    this_month = sum(v for b, v in per_bill.items()
                     if bill_dates[b].year == today.year and bill_dates[b].month == today.month)
    return derive_rules(largest, monthly, this_month, approved)
