"""Planner data shapes (documented in docs/DATA_FLOW.md §9)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.contracts import Model, utcnow


class ForecastPoint(Model):
    week_start: date
    mean: float
    low: float
    high: float


class HistoryPoint(Model):
    week_start: date
    actual: float
    forecast: float | None = None
    low: float | None = None
    high: float | None = None
    miss: bool = False


class MissView(Model):
    miss_id: str
    item_id: str
    item_name: str
    week_start: date
    actual: float
    low: float
    high: float
    bucket: str
    facts: list[str]
    cause: str | None
    band: str
    confidence: float | None
    status: Literal["applied", "needs_owner", "answered", "not_used"]
    source: str


class ProductForecast(Model):
    item_id: str
    name: str
    launch_week_start: date
    level: float
    trend: float
    model: dict[str, float]
    history: list[HistoryPoint]
    forecast: list[ForecastPoint]
    adjustments_applied: list[str] = []


class SupplyPlan(Model):
    item_id: str
    name: str
    unit: str
    pack: int
    weekly_use_forecast: list[ForecastPoint]
    on_hand: float
    safety_stock: float
    order_up_to: float
    recommended_qty: int
    run_out_date: date | None
    order_by_date: date | None
    status: Literal["order_now", "order_soon", "ok"]
    drivers: dict[str, float]  # product item_id -> usage ratio
    ratios_assumed: bool = True


class ForecastAdjustment(Model):
    adjustment_id: str
    message: str
    item_id: str | None
    kind: str
    quantity: float | None
    start_date: date | None
    week_index: int | None
    confidence: float | None
    status: Literal["applied", "needs_confirmation", "ignored", "rejected"]
    received_at: datetime = Field(default_factory=utcnow)
    notes: list[str] = []


class OwnerMessage(Model):
    text: str
    received_at: datetime | None = None


class MissAnswer(Model):
    cause: str


class PlannerView(Model):
    business_id: str
    as_of: date
    products: list[ProductForecast]
    supplies: list[SupplyPlan]
    misses: list[MissView]
    adjustments: list[ForecastAdjustment]
    cash_low_point: float | None
    disclosure: str
    built_at: datetime = Field(default_factory=utcnow)


class VariantScore(Model):
    variant: Literal["plain", "fixed_rule", "jev"]
    wape: float | None
    bias: float | None
    coverage: float | None
    misses: int
    owner_questions: int
    stockout_weeks: int
    avg_stock_units: float
    fill_rate: float | None


class BacktestReport(Model):
    weeks: int
    products: int
    variants: list[VariantScore]
    actual_purchasing: dict[str, float]
    jev_source: str  # "jev" (live) or "stub" (fixture rules; not a Jev result)
    notes: list[str]


class PlannerCheck(Model):
    due: list[SupplyPlan]
    job_id: str | None = None
    message: str
