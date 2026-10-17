"""The Jev-guided feedback loop (spec §13.2-13.4).

Each week: forecast from the clean history -> compare with the actual -> on a miss,
code lists the facts, a diagnoser picks the cause, code applies that cause's rule.

The same replay runs three variants (spec §13.6):
  A "plain": never changes the history (a normal forecaster).
  B "fixed_rule": every miss is treated as a lasting shift.
  C "jev": J11 picks the cause; unsure answers wait for the owner (no change).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from app.planner.model import LAUNCH_WEEKS, MIN_HISTORY, Z_RANGE, Fit, fit, launch_range
from app.planner.sales import SalesData

Variant = Literal["plain", "fixed_rule", "jev"]
LOOKBACK = 6


@dataclass
class MissContext:
    item_id: str
    item_name: str
    week: int
    week_start: str
    actual: float
    mean: float
    low: float
    high: float
    bucket: str
    previous_bucket: str
    facts: list[str]
    lost_customers: list[str]


@dataclass
class Diagnosis:
    cause: str | None  # None = not decided (owner question pending)
    band: str
    confidence: float | None
    needs_owner: bool = False
    source: str = "jev"


@dataclass
class MissRecord:
    miss_id: str
    ctx: MissContext
    diagnosis: Diagnosis
    applied: bool


@dataclass
class ItemState:
    item_id: str
    launch: int
    raw: list[float]
    clean: list[float]
    fit_start: int
    lead_time_extra_weeks: int = 0
    forecasts: list[tuple[int, float, float, float, float]] = field(default_factory=list)  # (week, actual, mean, lo, hi)
    misses: list[MissRecord] = field(default_factory=list)
    fit: Fit | None = None


Diagnoser = Callable[[MissContext], Awaitable[Diagnosis]]


@dataclass(frozen=True)
class LaunchError:
    """A one-step error from a product's first forecast weeks, relative to the forecast."""
    week: int
    item_id: str
    relative: float


async def launch_errors(sales: SalesData) -> list[LaunchError]:
    """The calibration set for launch ranges: plain one-step errors in each product's first weeks.

    The plain variant never changes its history, so its errors do not depend on the ranges."""
    errors: list[LaunchError] = []
    for item_id in sales.products:
        if sales.launch_week(item_id) is None:
            continue
        state = await replay(sales, item_id, "plain")
        for t, actual, mean, _, _ in state.forecasts[:LAUNCH_WEEKS]:
            errors.append(LaunchError(t, item_id, abs(actual - mean) / max(mean, 1.0)))
    return errors


def bucket(actual: float, low: float, high: float) -> str:
    width = max(high - low, 1.0)
    if actual < low:
        return "far below the range" if low - actual > 0.5 * width else "below the range"
    if actual > high:
        return "far above the range" if actual - high > 0.5 * width else "above the range"
    return "inside the range"


def facts_for(sales: SalesData, item_id: str, week: int, owner_notes: list[str]) -> tuple[list[str], list[str]]:
    """Code-detected facts, written as plain sentences. Returns (facts, lost customer ids)."""
    facts: list[str] = []
    lost: list[str] = []
    this_week = sales.by_customer.get((week, item_id), {})
    for cid, name in sales.customers.items():
        past = [sales.by_customer.get((w, item_id), {}).get(cid, 0.0) for w in range(max(0, week - LOOKBACK), week)]
        active = [q for q in past if q > 0]
        if len(active) >= 3 and this_week.get(cid, 0.0) == 0 and past[-1] == 0:
            usual = round(sum(active) / len(active))
            facts.append(f"Customer {name} stopped ordering: no order this week or last week (usually about {usual} a week).")
            lost.append(cid)
        earlier = any(sales.by_customer.get((w, item_id), {}).get(cid, 0.0) > 0 for w in range(0, week))
        if not earlier and this_week.get(cid, 0.0) > 0:
            facts.append(f"Customer {name} placed a first order for this product ({round(this_week[cid])} units).")
    for other_id, product in sales.products.items():
        launch = sales.launch_week(other_id)
        if other_id != item_id and launch is not None and week - 4 <= launch <= week:
            facts.append(f"New product {product.name} started selling {week - launch} weeks ago.")
    for memo in sales.bill_memos:
        if item_id in memo["itemIds"] and week - 2 <= memo["week"] <= week and "backorder" in memo["memo"].lower():
            facts.append(f"Supplier bill note {week - memo['week']} weeks ago: \"{memo['memo']}\"")
    facts.extend(owner_notes)
    return facts, lost


def apply_cause(state: ItemState, ctx: MissContext, cause: str, sales: SalesData) -> None:
    t = ctx.week
    if cause == "one_time_event":
        state.clean[t] = ctx.mean
    elif cause in ("lasting_shift_up", "lasting_shift_down", "customer_gained", "product_substitution"):
        # Spec §13.4: reset the level to the mean of the last 2 weeks, trend 0. We rewrite the
        # older clean history to that level, so the next fit starts flat at the new level.
        # (Refitting on only the last 2-3 points would turn one spike into a steep trend.)
        new_level = (state.raw[t - 1] + state.raw[t]) / 2 if t - 1 >= state.launch else state.raw[t]
        for w in range(state.fit_start, t - 1):
            state.clean[w] = new_level
    elif cause == "customer_lost":
        for cid in ctx.lost_customers:
            for w in range(state.launch, t + 1):
                state.clean[w] = max(0.0, state.clean[w] - sales.by_customer.get((w, ctx.item_id), {}).get(cid, 0.0))
    elif cause == "supply_shortage":
        state.lead_time_extra_weeks = 1
    # "noise": no change


async def replay(
    sales: SalesData, item_id: str, variant: Variant, diagnose: Diagnoser | None = None,
    owner_notes: dict[int, list[str]] | None = None, answered: dict[str, str] | None = None,
    calibration: list[LaunchError] | None = None,
) -> ItemState:
    """Replay the year for one product. With `calibration`, launch weeks use the launch range,
    calibrated only on other products' errors from earlier weeks (no look-ahead)."""
    raw = sales.series(item_id)
    launch = sales.launch_week(item_id) or 0
    state = ItemState(item_id=item_id, launch=launch, raw=raw, clean=list(raw), fit_start=launch)
    name = sales.products[item_id].name
    prev_bucket = "inside the range"
    for t in range(launch + MIN_HISTORY, sales.weeks):
        f = fit(state.clean[state.fit_start:t])
        mean = f.mean(1)
        low, high = f.range(1)
        if calibration is not None and len(state.forecasts) < LAUNCH_WEEKS:
            pool = [e.relative for e in calibration if e.week < t and e.item_id != item_id]
            low, high = launch_range(mean, low, high, pool)
        state.forecasts.append((t, raw[t], mean, low, high))
        b = bucket(raw[t], low, high)
        if b == "inside the range":
            prev_bucket = b
            continue
        facts, lost = facts_for(sales, item_id, t, (owner_notes or {}).get(t, []))
        ctx = MissContext(item_id, name, t, sales.week_start(t).isoformat(), raw[t], mean, low, high, b,
                          prev_bucket, facts, lost)
        miss_id = f"miss_{item_id}_{t}"
        if variant == "plain":
            diag = Diagnosis(None, "none", None, source="plain")
        elif variant == "fixed_rule":
            diag = Diagnosis("lasting_shift_up" if "above" in b else "lasting_shift_down", "rule", None, source="rule")
        else:
            assert diagnose is not None
            if answered and miss_id in answered:
                diag = Diagnosis(answered[miss_id], "owner", 1.0, source="owner")
            else:
                diag = await diagnose(ctx)
        applied = diag.cause is not None and not diag.needs_owner
        if applied:
            apply_cause(state, ctx, diag.cause, sales)
        state.misses.append(MissRecord(miss_id, ctx, diag, applied))
        prev_bucket = b
    state.fit = fit(state.clean[state.fit_start:])
    if calibration is not None and len(state.forecasts) < LAUNCH_WEEKS:
        # Still in its launch weeks: widen the spread, so the future range and the safety stock
        # use the launch range too.
        pool = [e.relative for e in calibration if e.item_id != item_id]
        mean = state.fit.mean(1)
        _, high = launch_range(mean, 0.0, mean, pool)
        if high > mean:
            state.fit.sigma = max(state.fit.sigma, (high - mean) / Z_RANGE)
    return state
