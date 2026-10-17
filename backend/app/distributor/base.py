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


def call_scope(items: list[ShoppingItem], plan: Plan, max_items: int) -> list[ShoppingItem]:
    """The items the call covers: the N with the highest line cost in the plan (most money at stake)."""
    if max_items <= 0 or max_items >= len(items):
        return list(items)
    cost = {a.item_id: a.line_cost for a in plan.assignments}
    ranked = sorted(items, key=lambda i: cost.get(i.id, 0.0), reverse=True)
    keep = {i.id for i in ranked[:max_items]}
    return [i for i in items if i.id in keep]  # keep the shopping-list order


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
