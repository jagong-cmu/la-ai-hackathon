"""Guard helpers shared by the adapter lanes and the agent lane.

`ctx.guard` runs the three layers (DOM card fields, J6, fail closed). These helpers turn
its verdict into control flow, so no lane code can forget to stop.
"""

from __future__ import annotations

from app.contracts import GuardVerdict, PageSnapshot
from app.guard import dom_card_fields
from app.lanes.base import LaneBlocked, LaneContext, LaneGuardStopped


async def check(ctx: LaneContext, snap: PageSnapshot) -> GuardVerdict:
    """Run the guard. A blocking page (login wall, bot check, out of stock) raises LaneBlocked."""
    verdict = await ctx.guard(snap)
    if not verdict.stop and verdict.blocked:
        raise LaneBlocked(verdict.blocked)
    return verdict


async def require_safe(ctx: LaneContext, snap: PageSnapshot) -> GuardVerdict:
    """Use before every click or type, and after every navigation outside the checkout end.

    A stop raises LaneGuardStopped: the lane must not act on this page.
    """
    verdict = await check(ctx, snap)
    if verdict.stop:
        raise LaneGuardStopped(verdict)
    return verdict


def has_card_field(snap: PageSnapshot) -> bool:
    """Layer 1 only (no model): a fast check for hooks that run before every agent action."""
    return dom_card_fields(snap)
