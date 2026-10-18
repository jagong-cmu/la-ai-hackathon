"""Data contracts. One source of truth: docs/DATA_FLOW.md (v2).

Python fields are snake_case; JSON is camelCase. `scripts/export_schema.py` writes the
JSON Schema that the frontend turns into TypeScript types.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


def utcnow() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


# ---------------------------------------------------------------- request side


class SearchSpec(Model):
    item_id: str
    query: str
    attributes: dict[str, Any] = {}
    must_have: list[str] = []
    nice_to_have: list[str] = []
    unit: str
    acceptable_pack_sizes: list[int] = []
    spec_fallback: bool = False


class ShoppingItem(Model):
    id: str
    name: str
    quantity: int = Field(gt=0, le=1_000_000_000)  # keeps every money value exact to the cent
    unit: str
    past_unit_price: float | None = Field(default=None, ge=0)  # anchors the distributor call
    order_cadence_days: float | None = Field(default=None, ge=0)
    source_bill_ids: list[str] = []
    spec: SearchSpec | None = None


class SupplierRef(Model):
    id: str
    name: str
    url: str
    adapter: str | None = None  # None = unknown site, the lane uses the LLM agent
    origin: Literal["creao", "cache", "manual", "fixture"] = "manual"

    @field_validator("url", mode="before")
    @classmethod
    def _web_url(cls, value: Any) -> Any:
        # A live lane opens this URL, and POST /jobs is public: only http(s). CREAO may omit the scheme.
        if isinstance(value, str):
            value = value.strip()
            if "://" not in value and not value.lower().startswith(("javascript:", "data:", "file:")):
                value = "https://" + value
            if not re.match(r"https?://[^/\s]+", value, re.I):
                raise ValueError("url must be an http or https address")
        return value


class DistributorRef(Model):
    id: str
    name: str
    phone: str | None = None
    email: str | None = None


class ShoppingRequest(Model):
    business_id: str = "company-001"
    items: list[ShoppingItem] = Field(min_length=1)
    suppliers: list[SupplierRef] = Field(min_length=1)
    distributor: DistributorRef | None = None
    zip_code: str
    accept_quoted_default: bool = False


class SpendInsightItem(Model):
    item_id: str
    bill_item_name: str
    category: str
    unit: str
    jev: dict[str, Any] = {}
    past_unit_price: float
    past_order_qty: int
    order_cadence_days: float | None
    year_spend: float
    year_qty: int
    orders: int
    source_bill_ids: list[str]
    synthetic: bool


class SpendInsights(Model):
    business_id: str
    period_days: int
    supply_spend: float
    supply_orders: int
    items: list[SpendInsightItem]
    other_lines: int
    disclosure: str | None = None


class BillLineView(Model):
    """One supply line of a past QuickBooks bill: what the business paid, item by item."""

    bill_id: str
    txn_date: date  # QuickBooks TxnDate
    vendor_name: str
    item_id: str
    quantity: float
    unit_price: float
    amount: float
    synthetic: bool


class SupplierCandidate(Model):
    id: str
    name: str
    url: str
    phone: str | None = None
    email: str | None = None
    adapter: str | None = None
    jev: dict[str, float] = {}
    selected: bool = False
    reason: str | None = None
    creao_run_id: str | None = None
    cached_at: datetime | None = None


class DiscoveryResult(Model):
    category: str
    zip_code: str
    candidates: list[SupplierCandidate]
    distributor: DistributorRef | None
    source: Literal["creao", "cache", "fixture", "fallback"]
    age_s: float | None = None


# ---------------------------------------------------------------- supplier lane


class LaneState(StrEnum):
    queued = "queued"
    searching = "searching"
    carting = "carting"
    cart_ready = "cart_ready"
    subset_pricing = "subset_pricing"
    idle = "idle"
    checkout = "checkout"
    at_payment = "at_payment"
    failed = "failed"
    blocked = "blocked"


TERMINAL_SEARCH_STATES = {LaneState.cart_ready, LaneState.idle, LaneState.failed, LaneState.blocked}


class LaneStatus(Model):
    lane_id: str
    supplier_id: str
    mode: Literal["adapter", "llm_agent", "fixture"]
    state: LaneState = LaneState.queued
    reason: str | None = None
    session_id: str | None = None
    live_url: str | None = None
    last_screenshot_url: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PackCandidate(Model):
    text: str
    value: int


class JevListing(Model):
    match_score: float
    match_probs: dict[str, float]  # "0" different, "1" related, "2" same product
    must_have: dict[str, float] = {}
    pack_count: dict[str, Any] | None = None  # {"choice": text, "confidence": float}
    in_stock: float = 1.0
    escalated: bool = False


class Product(Model):
    item_id: str
    title: str
    sku: str
    url: str | None = None
    raw_listing: str | None = None
    pack_candidates: list[PackCandidate] = []
    pack_price: float
    units_per_pack: int | None = None
    jev: JevListing | None = None


def path_segment(value: str) -> str:
    """A safe file or folder name for an id from a request (supplier, distributor, item): no "/",
    no "..". Evidence files, their /evidence/... URLs and fixture paths use it."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", value).lstrip(".") or "_"
    if len(safe) > 80:  # a file name has at most 255 bytes; keep long ids distinct with a hash
        safe = safe[:64] + "-" + hashlib.sha256(value.encode()).hexdigest()[:12]
    return safe


class Evidence(Model):
    screenshot_urls: list[str] = []
    visited_urls: list[str] = []
    transcript_url: str | None = None
    message_ids: list[str] = []


OfferSource = Literal["web", "sms", "email", "call"]


class SupplierOffers(Model):
    supplier_id: str
    source: OfferSource = "web"
    status: Literal["ok", "partial", "not_found", "blocked", "failed"] = "ok"
    products: list[Product] = []
    minimum_order_value: float = 0
    free_shipping_threshold: float | None = None
    bundle_total: float | None = None
    quoted_delivery_fee: float | None = None
    quoted_lead_time_days: int | None = None
    jev_terms: dict[str, float] = {}
    evidence: Evidence = Evidence()
    error: str | None = None

    @property
    def is_quoted(self) -> bool:
        return self.source != "web"


# ---------------------------------------------------------------- optimization


class NormalizedOption(Model):
    item_id: str
    supplier_id: str
    sku: str
    source: OfferSource
    requested_quantity: int
    units_per_pack: int | None
    pack_price: float
    packs_needed: int
    units_bought: int
    overbuy_units: int
    line_cost: float
    effective_unit_price: float
    flags: list[str] = []
    eligible: bool
    risk_penalty: float = 0.0  # ranking only, never part of a price (Thresholds.risk_penalty)
    url: str | None = None  # the product page; a lane opens it to add the item to the cart


class CartLine(Model):
    item_id: str
    sku: str
    pack_quantity: int
    unit_price: float  # price of one pack
    url: str | None = None


class MoneyCandidate(Model):
    label: str
    value: float


class Costs(Model):
    subtotal: float
    shipping: float
    tax: float
    other_fees: float = 0
    total: float


class CartItem(Model):
    item_id: str
    sku: str
    pack_quantity: int
    unit_price: float
    line_total: float


class VerifiedCart(Model):
    cart_key: str
    supplier_id: str
    session_id: str | None = None
    cart_items: list[CartItem] = []
    money_candidates: list[MoneyCandidate] = []
    costs: Costs | None = None
    jev: dict[str, float] = {}
    totals_check: Literal["consistent", "mismatch", "not_checked"] = "not_checked"
    minimum_order_met: bool = True
    verification_status: Literal[
        "verified", "estimated", "quoted", "price_changed", "out_of_stock", "blocked", "failed"
    ] = "failed"
    reason: str | None = None
    price_changes: list[dict[str, Any]] = []
    screenshot_url: str | None = None
    verified_at: datetime = Field(default_factory=utcnow)


class Assignment(Model):
    item_id: str
    supplier_id: str
    sku: str
    pack_quantity: int
    line_cost: float
    source: OfferSource


PlanStatus = Literal["verified", "estimated", "quoted"]


class PlanCart(Model):
    cart_key: str
    supplier_id: str
    lines: list[CartLine]
    subtotal: float
    shipping: float
    tax: float
    total: float
    status: PlanStatus
    shipping_is_estimate: bool = False


class RuleFailure(Model):
    code: str
    text: str


class RulesCheck(Model):
    passed: bool = Field(alias="pass")
    failed: list[RuleFailure] = []


class Plan(Model):
    plan_id: str
    kind: Literal["split", "single_supplier", "quoted_bundle"]
    label: str
    assignments: list[Assignment]
    carts: list[PlanCart]
    uncovered_items: list[str] = []
    total: float
    status: PlanStatus
    # Ranking only: the sum of the line penalties for J5 doubts. Plans rank by total + this.
    risk_penalty: float = 0.0
    rules_check: RulesCheck | None = None
    rank: int = 0


# ---------------------------------------------------------------- results and approval


class ResultWarning(Model):
    code: str
    text: str
    item_id: str | None = None
    supplier_id: str | None = None


class LaneOutcome(Model):
    supplier_id: str
    state: LaneState
    reason: str | None = None


class JobResults(Model):
    job_id: str
    status: Literal["complete", "partial"]
    deadline_hit: bool
    recommended_plan_id: str | None
    no_recommendation_reason: str | None = None
    plans: list[Plan]
    quoted_plan_id: str | None = None
    savings_vs_paid_before: float | None = None
    projected_yearly_savings: float | None = None
    warnings: list[ResultWarning] = []
    summary: str
    lane_outcomes: list[LaneOutcome] = []
    generated_at: datetime = Field(default_factory=utcnow)


class ApprovalDecision(Model):
    action: Literal["approve", "reject"]
    plan_id: str | None = None
    accept_quoted: bool = False
    decided_by: str = "owner"
    reason: str | None = None


class VirtualCard(Model):
    card_id: str
    supplier_id: str
    last4: str
    limit_usd: float
    merchant_lock: str
    expires_at: datetime
    status: Literal["ready", "used", "expired"] = "ready"
    simulated: bool = True


class CheckoutHandoff(Model):
    supplier_id: str
    status: Literal["pending", "at_payment", "failed", "quoted_confirm_by_phone"]
    reason: str | None = None
    live_url: str | None = None
    screenshot_url: str | None = None
    card_id: str | None = None


class JobState(StrEnum):
    created = "created"
    gathering = "gathering"
    results_ready = "results_ready"
    approved = "approved"
    handing_off = "handing_off"
    at_payment = "at_payment"
    partial_handoff = "partial_handoff"
    rejected = "rejected"
    failed = "failed"


class Event(Model):
    seq: int
    job_id: str
    type: str
    ts: datetime
    lane_id: str | None = None
    data: dict[str, Any] = {}


class JobView(Model):
    job_id: str
    state: JobState
    replay_of: str | None = None
    request: ShoppingRequest
    lanes: list[LaneStatus] = []
    offers: list[SupplierOffers] = []
    carts: list[VerifiedCart] = []
    plans: list[Plan] = []
    results: JobResults | None = None
    approval: ApprovalDecision | None = None
    cards: list[VirtualCard] = []
    handoffs: list[CheckoutHandoff] = []
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class JobCreated(Model):
    job_id: str
    state: JobState


# ---------------------------------------------------------------- payment guard


class PageInput(Model):
    name: str | None = None
    id: str | None = None
    type: str | None = None
    autocomplete: str | None = None
    label: str | None = None


class PageSnapshot(Model):
    url: str
    visible_text: str
    inputs: list[PageInput] = []


class GuardVerdict(Model):
    stop: bool
    reason: str | None = None
    page_state: str | None = None
    p_payment: float | None = None
    blocked: str | None = None  # login_wall | bot_check | out_of_stock


# ---------------------------------------------------------------- helpers


def cart_key(supplier_id: str, lines: list[CartLine]) -> str:
    parts = sorted(f"{line.sku}x{line.pack_quantity}" for line in lines)
    return f"{supplier_id}:" + "+".join(parts)
