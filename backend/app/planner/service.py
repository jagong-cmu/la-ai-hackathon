"""Restock planner service: forecasts, the feedback loop, owner messages, supply plans, backtest.

State (owner answers, adjustments) lives in memory and in the cache table, so it survives
a restart. Rebuilding replays the year through the loop; it is fast (pure Python) and uses
cached Jev answers after the first run.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Any

from app.config import Settings
from app.data.loader import Books
from app.db import Database
from app.decide import Decider
from app.judgments import MISS_CAUSES, j11_miss_cause, j12_owner_message
from app.planner.adjust import date_phrases, phrase_quantity, quantity_phrases, resolve_date
from app.planner.loop import Diagnosis, ItemState, LaunchError, MissContext, launch_errors, replay
from app.planner.model import LAUNCH_WEEKS, Z_RANGE, bias, wape
from app.planner.policy import advise
from app.planner.ratios import load_ratios
from app.planner.sales import SalesData
from app.planner.schemas import (
    BacktestReport,
    ForecastAdjustment,
    ForecastPoint,
    HistoryPoint,
    MissView,
    PlannerView,
    ProductForecast,
    SupplyPlan,
    VariantScore,
)

HORIZON = 6
LEAD_WEEKS = 1
STATE_KEY = "planner:state"
DISCLOSURE = (
    "Product sales are the real company-001 books. Supply use = real sales x assumed usage ratios. "
    "Supply bills are synthetic. The data covers one year, so the model learns no seasonality."
)


class Planner:
    def __init__(self, settings: Settings, db: Database, decider: Decider, sales: SalesData, books: Books):
        self.settings = settings
        self.db = db
        self.decider = decider
        self.sales = sales
        self.books = books
        self.ratios = load_ratios()
        saved = db.cache_get(STATE_KEY) or {}
        self.answers: dict[str, str] = saved.get("answers", {})
        self.adjustments: list[ForecastAdjustment] = [
            ForecastAdjustment.model_validate(a) for a in saved.get("adjustments", [])
        ]
        self._view: PlannerView | None = None
        self._launch_errors: list[LaunchError] | None = None
        self._states: dict[str, ItemState] = {}
        self._lock = asyncio.Lock()

    @property
    def as_of(self) -> date:
        return self.sales.week_start(self.sales.weeks)

    # ------------------------------------------------------------ build

    async def _diagnose(self, ctx: MissContext) -> Diagnosis:
        state = {"item": ctx.item_name, "week": ctx.week_start, "miss": ctx.bucket,
                 "previousWeek": ctx.previous_bucket, "facts": ctx.facts or ["No unusual events were found."]}
        d = await j11_miss_cause(self.decider, state)
        cause = d.answers.get("cause", {}).get("choice")
        if d.band == "act" and cause in MISS_CAUSES:
            return Diagnosis(cause, d.band, d.confidence, source=d.source)
        return Diagnosis(cause if cause in MISS_CAUSES else None, d.band, d.confidence,
                         needs_owner=True, source=d.source)

    def _owner_notes(self) -> dict[int, list[str]]:
        notes: dict[int, list[str]] = {}
        for a in self.adjustments:
            if a.status == "applied" and a.week_index is not None and a.week_index < self.sales.weeks:
                notes.setdefault(a.week_index, []).append(f"Owner note: \"{a.message}\"")
        return notes

    async def view(self, rebuild: bool = False) -> PlannerView:
        async with self._lock:
            if self._view is None or rebuild:
                self._view = await self._build()
            return self._view

    async def _build(self) -> PlannerView:
        notes = self._owner_notes()
        self._states = {}
        calibration = await self._calibration()
        for item_id in self.sales.products:
            if self.sales.launch_week(item_id) is None:
                continue
            self._states[item_id] = await replay(self.sales, item_id, "jev", self._diagnose, notes, self.answers,
                                                 calibration)
        products = [self._product_view(s) for s in self._states.values()]
        supplies = [self._supply_plan(sid) for sid in self.ratios]
        misses = [m for s in self._states.values() for m in self._miss_views(s)]
        return PlannerView(
            business_id=self.sales.business_id, as_of=self.as_of, products=products, supplies=supplies,
            misses=misses, adjustments=self.adjustments,
            cash_low_point=min(self.sales.cash) if self.sales.cash else None, disclosure=DISCLOSURE,
        )

    async def _calibration(self) -> list[LaunchError]:
        if self._launch_errors is None:
            self._launch_errors = await launch_errors(self.sales)
        return self._launch_errors

    def _future(self, state: ItemState) -> list[ForecastPoint]:
        assert state.fit is not None
        points = []
        for h in range(1, HORIZON + 1):
            week = self.sales.weeks + h - 1
            low, high = state.fit.range(h)
            mean = state.fit.mean(h)
            for a in self.adjustments:
                if a.status != "applied" or a.item_id != state.item_id or a.week_index is None:
                    continue
                delta = self._adjust(a, week, mean)
                mean, low, high = max(0.0, mean + delta), max(0.0, low + delta), max(0.0, high + delta)
            points.append(ForecastPoint(week_start=self.sales.week_start(week), mean=round(mean, 1),
                                        low=round(low, 1), high=round(high, 1)))
        return points

    @staticmethod
    def _adjust(a: ForecastAdjustment, week: int, mean: float) -> float:
        qty = a.quantity or 0.0
        if a.kind == "one_time_extra" and week == a.week_index:
            return qty
        if a.kind == "lasting_increase" and week >= a.week_index:
            return qty
        if a.kind == "lasting_decrease" and week >= a.week_index:
            return -(qty or 0.25 * mean)
        if a.kind == "pause" and week == a.week_index:
            return -mean
        if a.kind == "discontinue" and week >= a.week_index:
            return -mean
        return 0.0

    def _product_view(self, state: ItemState) -> ProductForecast:
        assert state.fit is not None
        misses = {m.ctx.week for m in state.misses}
        by_week = {w: (mean, lo, hi) for w, _, mean, lo, hi in state.forecasts}
        history = []
        for w in range(max(state.launch, self.sales.weeks - 26), self.sales.weeks):
            f = by_week.get(w)
            history.append(HistoryPoint(week_start=self.sales.week_start(w), actual=state.raw[w],
                                        forecast=round(f[0], 1) if f else None, low=round(f[1], 1) if f else None,
                                        high=round(f[2], 1) if f else None, miss=w in misses))
        return ProductForecast(
            item_id=state.item_id, name=self.sales.products[state.item_id].name,
            launch_week_start=self.sales.week_start(state.launch), level=round(state.fit.level, 1),
            trend=round(state.fit.trend, 2),
            model={"alpha": state.fit.alpha, "beta": state.fit.beta, "phi": state.fit.phi,
                   "sigma": round(state.fit.sigma, 1)},
            history=history, forecast=self._future(state),
            adjustments_applied=[a.adjustment_id for a in self.adjustments
                                 if a.status == "applied" and a.item_id == state.item_id],
        )

    def _miss_views(self, state: ItemState) -> list[MissView]:
        out = []
        for m in state.misses:
            d = m.diagnosis
            status = "answered" if d.source == "owner" else ("applied" if m.applied else
                                                             ("needs_owner" if d.needs_owner else "not_used"))
            out.append(MissView(
                miss_id=m.miss_id, item_id=m.ctx.item_id, item_name=m.ctx.item_name,
                week_start=self.sales.week_start(m.ctx.week), actual=m.ctx.actual, low=round(m.ctx.low, 1),
                high=round(m.ctx.high, 1), bucket=m.ctx.bucket, facts=m.ctx.facts, cause=d.cause, band=d.band,
                confidence=d.confidence, status=status, source=d.source,
            ))
        return out

    # ------------------------------------------------------------ supplies

    def _on_hand(self, supply_id: str) -> float:
        ratio = self.ratios[supply_id]
        receipts = [0.0] * self.sales.weeks
        for line in self.books.lines:
            if line.item_id == supply_id:
                w = (line.date - self.sales.start).days // 7
                if 0 <= w < self.sales.weeks:
                    receipts[w] += line.quantity
        stock = 0.0
        for w in range(self.sales.weeks):
            use = sum(r * self.sales.series(p)[w] for p, r in ratio.per_product.items() if p in self.sales.products)
            stock = max(0.0, stock + receipts[w] - use)
        return stock

    def _supply_plan(self, supply_id: str) -> SupplyPlan:
        ratio = self.ratios[supply_id]
        means = [0.0] * HORIZON
        variance = 0.0
        for pid, r in ratio.per_product.items():
            state = self._states.get(pid)
            if state is None or state.fit is None:
                continue
            for h, point in enumerate(self._future(state)):
                means[h] += r * point.mean
            variance += (r * state.fit.sigma) ** 2
        sigma = math.sqrt(variance)
        # A supply_shortage cause concerns the PRODUCT's vendor, so it does not change the lead time
        # of the supplies we buy elsewhere. (ItemState.lead_time_extra_weeks stays for product sourcing.)
        lead = LEAD_WEEKS
        on_hand = self._on_hand(supply_id)
        advice = advise(on_hand, means, sigma, lead, ratio.pack, self.as_of)
        name = next((line.item_name for line in self.books.lines if line.item_id == supply_id), supply_id)
        points = [ForecastPoint(week_start=self.as_of + timedelta(days=7 * h), mean=round(m, 1),
                                low=round(max(0.0, m - Z_RANGE * sigma * math.sqrt(h + 1)), 1),
                                high=round(m + Z_RANGE * sigma * math.sqrt(h + 1), 1))
                  for h, m in enumerate(means)]
        return SupplyPlan(
            item_id=supply_id, name=name, unit=ratio.unit, pack=ratio.pack, weekly_use_forecast=points,
            on_hand=round(on_hand, 1), safety_stock=round(advice.safety_stock, 1),
            order_up_to=round(advice.order_up_to, 1), recommended_qty=advice.recommended_qty,
            run_out_date=advice.run_out_date, order_by_date=advice.order_by_date, status=advice.status,
            drivers=ratio.per_product,
        )

    # ------------------------------------------------------------ owner inputs

    def _save(self) -> None:
        self.db.cache_set(STATE_KEY, {"answers": self.answers,
                                      "adjustments": [a.model_dump(mode="json") for a in self.adjustments]})

    async def answer_miss(self, miss_id: str, cause: str) -> PlannerView:
        if cause not in MISS_CAUSES:
            raise ValueError(f"cause must be one of {sorted(MISS_CAUSES)}")
        self.answers[miss_id] = cause
        self.db.log_decision(job_id=None, j_id="J11", state_hash=miss_id, questions={"ownerAnswer": True},
                             band="owner", escalated=0, final={"cause": cause}, label=cause)
        self._save()
        return await self.view(rebuild=True)

    async def owner_message(self, text: str, received_at: datetime | None = None) -> ForecastAdjustment:
        today = (received_at.date() if received_at else self.as_of)
        catalog = {pid: p.name for pid, p in self.sales.products.items()}
        dates = date_phrases(text)
        quantities = quantity_phrases(text, dates)
        d = await j12_owner_message(self.decider, text, catalog, quantities, dates)
        a = d.answers
        item_name = a.get("item", {}).get("choice")
        item_id = next((pid for pid, name in catalog.items() if name == item_name), None)
        kind = a.get("kind", {}).get("choice", "not_a_forecast_change")
        qty_phrase = a.get("quantity", {}).get("choice")
        qty = phrase_quantity(qty_phrase) if qty_phrase and qty_phrase != "not stated" else None
        date_phrase = a.get("date", {}).get("choice")
        start = resolve_date(date_phrase, today) if date_phrase and date_phrase != "not stated" else today
        week_index = (start - self.sales.start).days // 7 if start else None
        notes = [f"quantity phrases: {quantities}", f"date phrases: {dates}"]
        if kind == "cancel_previous":
            previous = next((x for x in reversed(self.adjustments)
                             if x.status == "applied" and (item_id is None or x.item_id == item_id)), None)
            if previous is not None and d.band == "act":
                previous.status = "rejected"
                notes.append(f"cancelled {previous.adjustment_id}")
                status = "applied"
                item_id = previous.item_id
            else:
                status = "needs_confirmation" if previous else "ignored"
        elif kind == "not_a_forecast_change" or item_id is None:
            status = "ignored"
        elif d.band == "act":
            status = "applied"
        else:
            status = "needs_confirmation"
            notes.append(f"Jev band: {d.band}; ask the owner to confirm")
        adj = ForecastAdjustment(
            adjustment_id=f"adj_{len(self.adjustments) + 1:03d}", message=text, item_id=item_id, kind=kind,
            quantity=qty, start_date=start, week_index=week_index, confidence=d.confidence, status=status,
            notes=notes,
        )
        self.adjustments.append(adj)
        self._save()
        await self.view(rebuild=True)
        return adj

    async def confirm_adjustment(self, adjustment_id: str, accept: bool) -> ForecastAdjustment:
        adj = next((a for a in self.adjustments if a.adjustment_id == adjustment_id), None)
        if adj is None:
            raise KeyError(adjustment_id)
        adj.status = "applied" if accept else "rejected"
        self._save()
        await self.view(rebuild=True)
        return adj

    # ------------------------------------------------------------ evaluation

    async def backtest(self) -> BacktestReport:
        scores = []
        sources: set[str] = set()
        calibration = await self._calibration()

        async def diagnose(ctx: MissContext) -> Diagnosis:
            diag = await self._diagnose(ctx)
            sources.add(diag.source)
            return diag

        for variant in ("plain", "fixed_rule", "jev"):
            actual: list[float] = []
            forecast: list[float] = []
            covered = misses = questions = 0
            sim = {"stockout_weeks": 0, "stock": 0.0, "weeks": 0, "served": 0.0, "demand": 0.0}
            for item_id in self.sales.products:
                if self.sales.launch_week(item_id) is None:
                    continue
                state = await replay(self.sales, item_id, variant, diagnose if variant == "jev" else None,
                                     calibration=calibration)
                for _, a, mean, lo, hi in state.forecasts:
                    actual.append(a)
                    forecast.append(mean)
                    covered += lo <= a <= hi
                misses += len(state.misses)
                questions += sum(1 for m in state.misses if m.diagnosis.needs_owner)
                _simulate(state, sim)
            scores.append(VariantScore(
                variant=variant, wape=_r(wape(actual, forecast)), bias=_r(bias(actual, forecast)),
                coverage=_r(covered / len(actual)) if actual else None, misses=misses, owner_questions=questions,
                stockout_weeks=sim["stockout_weeks"],
                avg_stock_units=round(sim["stock"] / max(sim["weeks"], 1), 1),
                fill_rate=_r(sim["served"] / sim["demand"]) if sim["demand"] else None,
            ))
        return BacktestReport(
            weeks=self.sales.weeks, products=len(self.sales.products), variants=scores,
            actual_purchasing=self._actual_purchasing(),
            jev_source="jev" if "jev" in sources else ("stub" if "stub" in sources else ",".join(sorted(sources))),
            notes=[
                "One-step-ahead forecasts, refit every week (rolling origin).",
                f"Launch weeks (the first {LAUNCH_WEEKS} forecast weeks) use a split conformal 90% range from "
                "earlier launches' errors; the calibration uses no later weeks.",
                "Stock simulation: weekly order-up-to rule, 1-week delivery, same rule for every variant.",
                "jevSource 'stub' means fixture rules answered J11; it is NOT a Jev result.",
                "Data is simulated (qb-world-sim); results apply to this simulator.",
            ],
        )

    def _actual_purchasing(self) -> dict[str, float]:
        """What the company actually did: implied stock from real purchases minus real sales.

        Assumes zero starting stock and no waste (the books record neither)."""
        stock_sum = 0.0
        weeks = 0
        short_weeks = 0
        for item_id in self.sales.products:
            launch = self.sales.launch_week(item_id)
            if launch is None:
                continue
            stock = 0.0
            sales = self.sales.series(item_id)
            buys = self.sales.purchase_series(item_id)
            for w in range(launch, self.sales.weeks):
                short_weeks += stock + buys[w] < sales[w]
                stock = max(0.0, stock + buys[w] - sales[w])
                stock_sum += stock
                weeks += 1
        return {"avgImpliedStockUnits": round(stock_sum / max(weeks, 1), 1), "shortWeeks": float(short_weeks)}


def _simulate(state: ItemState, sim: dict[str, Any]) -> None:
    """Weekly order-up-to with 1-week delivery, driven by the variant's one-step forecasts."""
    if not state.forecasts:
        return
    stock = 2 * state.forecasts[0][2]  # start with two weeks of forecast demand on hand
    pending = 0.0
    for _, demand, mean, lo, hi in state.forecasts:
        stock += pending
        pending = 0.0
        served = min(stock, demand)
        if demand > stock:
            sim["stockout_weeks"] += 1
        stock -= served
        sim["served"] += served
        sim["demand"] += demand
        sim["stock"] += stock
        sim["weeks"] += 1
        sigma = (hi - lo) / (2 * Z_RANGE)
        target = 2 * mean + 1.65 * sigma * math.sqrt(2)
        pending = max(0.0, target - stock)


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None
