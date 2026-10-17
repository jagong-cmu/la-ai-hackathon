"""The supplier lane interface. The agents team implements it; the orchestrator runs it.

One lane = one supplier = one browser session for the whole job (spec §5):
    open -> search -> set_cart(full) -> set_cart(subsets)* -> [approve] -> set_cart(plan) -> checkout -> close

Rules for every implementation:
- Call `await ctx.guard(snapshot)` after every navigation and before every click or type.
  If it returns stop=True, stop at once and return. Never type card data; you have none.
- Raise LaneBlocked("login_wall" | "bot_check" | "out_of_stock") when the site blocks you.
- Use ctx.decider / app.judgments for J4, J5, J7. Do not compute prices in a prompt.
- Do not handle timeouts yourself; the orchestrator enforces the budgets.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from app.config import CheckoutContact, Settings
from app.contracts import (
    CartLine,
    CheckoutHandoff,
    GuardVerdict,
    PageSnapshot,
    SearchSpec,
    ShoppingItem,
    SupplierOffers,
    SupplierRef,
    VerifiedCart,
)
from app.decide import Decider
from app.guard import check_page

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


class LaneBlocked(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LaneGuardStopped(Exception):
    """Raised when the guard stopped the lane before the expected point (not at checkout)."""

    def __init__(self, verdict: GuardVerdict):
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass
class LaneContext:
    job_id: str
    lane_id: str
    supplier: SupplierRef
    settings: Settings
    decider: Decider
    emit: Emit
    evidence_dir: Path
    specs: dict[str, SearchSpec] = field(default_factory=dict)
    session_id: str | None = None
    live_url: str | None = None

    async def guard(self, page: PageSnapshot) -> GuardVerdict:
        verdict = await check_page(self.decider, page, self.settings.thresholds, job_id=self.job_id)
        await self.emit("page_state", {"state": verdict.page_state, "p": verdict.p_payment, "url": page.url})
        if verdict.stop:
            await self.emit("guard_stop", {"reason": verdict.reason, "url": page.url})
        return verdict

    async def sleep(self, seconds: float) -> None:
        """Simulated work time for fixture lanes; scaled by FIXTURE_TIME_SCALE."""
        await asyncio.sleep(max(0.0, seconds * self.settings.fixture_time_scale))

    def evidence(self, name: str) -> tuple[Path, str]:
        folder = self.evidence_dir / self.job_id / self.supplier.id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / name, f"/evidence/{self.job_id}/{self.supplier.id}/{name}"


class SupplierLane(Protocol):
    mode: Literal["adapter", "llm_agent", "fixture"]

    async def open(self, ctx: LaneContext) -> None: ...

    async def search(self, ctx: LaneContext, items: list[ShoppingItem]) -> SupplierOffers: ...

    async def set_cart(self, ctx: LaneContext, lines: list[CartLine]) -> VerifiedCart:
        """Make the cart contain exactly `lines`, then read and check the totals (J7)."""
        ...

    async def checkout(self, ctx: LaneContext, contact: CheckoutContact) -> CheckoutHandoff:
        """Go to checkout, fill the team contact, stop at the payment step (guard)."""
        ...

    async def close(self, ctx: LaneContext) -> None: ...


LaneFactory = Callable[[SupplierRef], SupplierLane]
