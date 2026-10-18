"""The distributor conversation: SMS request -> (no reply) call -> SMS recap (spec §4).

The agents team implements a live channel (Photon SMS + Voice -> SIP -> ElevenLabs).
Inbound SMS and call events reach the backend at /webhooks/{provider} and are passed
to `on_webhook` of the active channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Settings
from app.contracts import DistributorRef, Plan, ShoppingItem, SupplierOffers
from app.decide import Decider


@dataclass
class DistributorContext:
    job_id: str
    distributor: DistributorRef
    items: list[ShoppingItem]
    settings: Settings
    decider: Decider
    emit: Callable[[str, dict[str, Any]], Awaitable[None]]
    best_plan: Callable[[], Plan | None]
    first_plan_ready: asyncio.Event
    on_offer: Callable[[SupplierOffers], Awaitable[None]]
    started_at: float = 0.0
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)  # webhook payloads for this job

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds * self.settings.fixture_time_scale))


class DistributorChannel(Protocol):
    async def run(self, ctx: DistributorContext) -> SupplierOffers | None:
        """Run the whole conversation. Call ctx.on_offer() for each new offer (live re-plan)."""
        ...


@dataclass(frozen=True)
class CallAnchor:
    """The reference price that the call quotes. Code builds it; no model computes it."""

    line_costs: dict[str, float]  # item id -> cost of the full quantity
    total: float
    source: str  # "web_plan" (best plan so far) or "past_bills" (what the business paid before)


def call_anchor(items: list[ShoppingItem], plan: Plan | None) -> CallAnchor | None:
    """The best plan so far; with no plan, the past bill prices; None if neither exists."""
    if plan is not None:
        return CallAnchor({a.item_id: a.line_cost for a in plan.assignments}, plan.total, "web_plan")
    costs = {i.id: round(i.past_unit_price * i.quantity, 2) for i in items if i.past_unit_price}
    if not costs:
        return None
    return CallAnchor(costs, round(sum(costs.values()), 2), "past_bills")


def call_scope(items: list[ShoppingItem], line_costs: dict[str, float], max_items: int) -> list[ShoppingItem]:
    """The items the call covers: the N with the highest line cost (most money at stake)."""
    if max_items <= 0 or max_items >= len(items):
        return list(items)
    ranked = sorted(items, key=lambda i: line_costs.get(i.id, 0.0), reverse=True)
    keep = {i.id for i in ranked[:max_items]}
    return [i for i in items if i.id in keep]  # keep the shopping-list order


def anchored_items(items: list[ShoppingItem], anchor: CallAnchor) -> list[ShoppingItem]:
    """A web plan covers the whole list. Past bills cover only the items with a past price."""
    if anchor.source == "web_plan":
        return list(items)
    return [i for i in items if i.id in anchor.line_costs]


def allocate_total(total: float, weights: dict[str, float]) -> dict[str, float]:
    """Split a quoted total across items in proportion to their web line costs (code, not a model).
    The last item takes the rounding remainder, so the parts add up to the total exactly."""
    ids = list(weights)
    base = sum(weights.values()) or float(len(ids))
    parts: dict[str, float] = {}
    for item_id in ids[:-1]:
        parts[item_id] = round(total * (weights[item_id] or 1.0) / base, 2)
    parts[ids[-1]] = round(total - sum(parts.values()), 2)
    return parts


async def wait_for_call_start(ctx: DistributorContext, loop_time: Callable[[], float]) -> None:
    """Call when the first plan exists, at the latest T0 + distributor_call_latest_s."""
    if ctx.first_plan_ready.is_set():
        return
    latest = ctx.started_at + ctx.settings.distributor_call_latest_s * max(ctx.settings.fixture_time_scale, 0)
    remaining = latest - loop_time()
    if remaining <= 0:
        return
    try:
        await asyncio.wait_for(ctx.first_plan_ready.wait(), timeout=remaining)
    except TimeoutError:
        pass
