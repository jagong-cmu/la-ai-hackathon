"""Ordering rule (spec §13.5): weekly review, order-up-to level, order date. All code."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

SERVICE_Z = 1.65  # about 95% of review periods without running out
REVIEW_WEEKS = 1


@dataclass
class OrderAdvice:
    safety_stock: float
    order_up_to: float
    recommended_qty: int
    run_out_date: date | None
    order_by_date: date | None
    status: str


def advise(
    on_hand: float, weekly_mean: list[float], weekly_sigma: float, lead_weeks: int, pack: int, as_of: date,
) -> OrderAdvice:
    cover = lead_weeks + REVIEW_WEEKS
    demand_cover = sum(weekly_mean[:cover])
    safety = SERVICE_Z * weekly_sigma * math.sqrt(cover)
    order_up_to = demand_cover + safety
    need = max(0.0, order_up_to - on_hand)
    qty = int(math.ceil(need / pack) * pack) if need > 0 else 0

    # Project the stock week by week with the mean forecast.
    stock = on_hand
    run_out: date | None = None
    reorder_week: int | None = None
    stock_at_reorder = on_hand
    lead_demand = sum(weekly_mean[:lead_weeks]) if lead_weeks else 0.0
    for h, use in enumerate(weekly_mean):
        if reorder_week is None and stock < safety + lead_demand:
            reorder_week = h
            stock_at_reorder = stock
        if use > 0 and stock < use:
            run_out = as_of + timedelta(days=7 * h + int(7 * max(stock, 0.0) / use))
            break
        stock -= use
    order_by = None
    if reorder_week is not None:
        order_by = as_of + timedelta(days=7 * max(0, reorder_week - lead_weeks))
    if order_by is not None and order_by <= as_of + timedelta(days=3):
        status = "order_now"
    elif order_by is not None and order_by <= as_of + timedelta(days=10):
        status = "order_soon"
    else:
        status = "ok"
    if qty == 0 and status != "ok":
        # Nothing is needed today, but an order is due soon: show the quantity for that date.
        later_need = max(0.0, order_up_to - stock_at_reorder)
        qty = int(math.ceil(later_need / pack) * pack) if later_need > 0 else 0
        status = "order_soon"
    return OrderAdvice(safety, order_up_to, qty, run_out, order_by, status)
