"""Browser Use custom tools that call the backend judgments (spec §5, review C4/C7).

`ToolCore` holds the logic as plain async methods, so tests and lanes can call it without
Browser Use. `build_tools()` wraps it as `@tools.action` functions for the agent.

The tools read the page themselves (DOM text through the lane browser). The agent never
supplies the text that a judgment reads, and it never supplies a number that code does
not find on the page. No tool takes or returns card data; the lane has none.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from app.contracts import (
    CartLine,
    GuardVerdict,
    PageSnapshot,
    Product,
    SearchSpec,
    VerifiedCart,
)
from app.judgments import j4_rerank, j5_listing, j7_totals
from app.lanes.base import LaneContext
from app.lanes.live.guards import has_card_field
from app.stages.extract import money_candidates
from app.stages.totals import build_verified_cart

STOP_MESSAGE = (
    "STOP. The payment guard stopped this browser: {reason}. Do not click, type or scroll. "
    "Call done now. The owner continues from here."
)

# Built-in actions the lane never needs. They widen the attack surface; `search` leaves the site.
EXCLUDED_ACTIONS = ["search", "upload_file", "evaluate", "write_file", "replace_file", "read_file", "save_as_pdf"]

SnapshotFn = Callable[[], Awaitable[PageSnapshot]]


class ResultRow(BaseModel):
    title: str = Field(description="The result title as shown on the page")
    snippet: str = Field("", description="Short text under the title (price, pack size), as shown")
    url: str | None = Field(None, description="The result link, if visible")


class RankParams(BaseModel):
    item_id: str = Field(description="The shopping list item id")
    results: list[ResultRow] = Field(description="Up to 10 search results, in page order")


class JudgeParams(BaseModel):
    item_id: str = Field(description="The shopping list item id this product page is for")
    title: str = Field(description="The product title exactly as shown")
    sku: str = Field("", description="The SKU or item number as shown, or empty")
    price: float = Field(description="The price of one purchase unit as shown, e.g. 24.99")


class ToolCore:
    def __init__(self, ctx: LaneContext, snapshot: SnapshotFn, *, minimum_order_value: float = 0.0):
        self.ctx = ctx
        self.snapshot = snapshot
        self.minimum_order_value = minimum_order_value
        self.stop_verdict: GuardVerdict | None = None
        self.blocked: str | None = None
        self.products: dict[str, list[Product]] = {}
        self.visited: list[str] = []
        self.cart_lines: list[CartLine] = []
        self.last_cart: VerifiedCart | None = None
        self._n = 0

    def spec_for(self, item_id: str) -> SearchSpec | None:
        return self.ctx.specs.get(item_id)

    async def check_dom(self) -> bool:
        """Layer 1 only. True means: stop before the next action."""
        if self.stop_verdict is not None:
            return True
        try:
            snap = await self.snapshot()
        except Exception:
            return False  # the page is navigating; page_state after the step runs the full guard
        if has_card_field(snap):
            self.stop_verdict = GuardVerdict(stop=True, reason="dom_card_field", page_state="payment_step",
                                             p_payment=1.0)
            await self.ctx.emit("guard_stop", {"reason": "dom_card_field", "url": snap.url})
            return True
        return False

    async def page_state(self) -> tuple[str, bool]:
        """Full guard on the current page. Returns (message for the agent, stop)."""
        snap = await self.snapshot()
        verdict = await self.ctx.guard(snap)
        if verdict.stop:
            self.stop_verdict = verdict
            return STOP_MESSAGE.format(reason=verdict.reason), True
        if verdict.blocked:
            self.blocked = verdict.blocked
            return f"The site blocks this browser ({verdict.blocked}). Call done now.", True
        return f"Page state: {verdict.page_state}. You may continue.", False

    async def rank_results(self, item_id: str, results: list[dict[str, str]]) -> str:
        spec = self.spec_for(item_id)
        if spec is None:
            return f"Unknown item_id {item_id!r}. Use an item_id from the task."
        rows = [{"title": r.get("title", ""), "snippet": r.get("snippet", "")} for r in results[:10]]
        if not rows:
            return "No results given."
        ranked = await j4_rerank(self.ctx.decider, spec, rows, job_id=self.ctx.job_id)
        top = [{"index": i, "title": rows[i]["title"], "pSame": round(p, 2)} for i, p in ranked[:3]]
        return "Open these results in this order, then call judge_listing on each: " + json.dumps(top)

    async def judge_listing(self, item_id: str, title: str, sku: str, price: float) -> str:
        spec = self.spec_for(item_id)
        if spec is None:
            return f"Unknown item_id {item_id!r}. Use an item_id from the task."
        snap = await self.snapshot()
        amounts = {round(c.value, 2) for c in money_candidates(snap.visible_text)}
        if round(price, 2) not in amounts:
            return (f"The price {price:.2f} is not on this page. Read the price again from the page "
                    f"and call judge_listing again.")
        jev, candidates, units = await j5_listing(self.ctx.decider, spec, title, snap.visible_text,
                                                  job_id=self.ctx.job_id)
        self._n += 1
        path, rel = self.ctx.evidence(f"listing_{item_id}_{self._n}.json")
        path.write_text(json.dumps({"url": snap.url, "title": title, "text": snap.visible_text}))
        self.visited.append(snap.url)
        product = Product(item_id=item_id, title=title, sku=sku or snap.url, url=snap.url, raw_listing=rel,
                          pack_candidates=candidates, pack_price=round(price, 2), units_per_pack=units, jev=jev)
        self.products.setdefault(item_id, []).append(product)
        p_same = jev.match_probs.get("2", 0.0)
        verdict = "good match" if p_same >= self.ctx.settings.thresholds.match_act else (
            "possible match" if p_same >= self.ctx.settings.thresholds.match_min else "not a match")
        return (f"Recorded. {verdict} (pSame={p_same:.2f}), units per purchase: "
                f"{units if units is not None else 'not stated'}. Do not add it to the cart now.")

    def best_product(self, item_id: str) -> Product | None:
        found = self.products.get(item_id) or []
        if not found:
            return None
        return max(found, key=lambda p: (p.jev.match_probs.get("2", 0.0) if p.jev else 0.0))

    async def read_cart_totals(self) -> str:
        if not self.cart_lines:
            return "No cart target is set for this task."
        snap = await self.snapshot()
        if has_card_field(snap):
            await self.check_dom()
            return STOP_MESSAGE.format(reason="dom_card_field")
        # Line rows carry the SKU; the summary lines do not. J7 reads the summary only.
        skus = tuple(line.sku for line in self.cart_lines if line.sku)
        summary = "\n".join(t for t in snap.visible_text.splitlines() if not any(s in t for s in skus))
        candidates = money_candidates(summary)
        picks, confs = await j7_totals(self.ctx.decider, candidates, job_id=self.ctx.job_id)
        self._n += 1
        path, rel = self.ctx.evidence(f"cart_{self._n}.txt")
        path.write_text(snap.visible_text)
        cart = build_verified_cart(
            self.ctx.supplier.id, self.cart_lines, candidates, picks, confs,
            minimum_order_value=self.minimum_order_value, session_id=self.ctx.session_id, screenshot_url=rel,
        )
        self.last_cart = cart
        if cart.verification_status == "verified":
            return f"Cart totals verified: total {cart.costs.total:.2f}. Call done now." if cart.costs else "Done."
        return (f"Cart totals not verified ({cart.reason}). Check that the cart page shows the full "
                f"order summary, then call read_cart_totals once more, or call done.")


def build_tools(core: ToolCore, output_model: type[BaseModel] | None = None) -> Any:
    """Wrap ToolCore as Browser Use actions. Import Browser Use only here."""
    from browser_use import ActionResult, Tools

    tools = Tools(exclude_actions=list(EXCLUDED_ACTIONS), output_model=output_model)

    @tools.action("Check the current page with the payment guard. Call it after every navigation and "
                  "before you type into any form. If it says STOP, call done at once.")
    async def page_state() -> ActionResult:
        message, stop = await core.page_state()
        if stop:
            return ActionResult(extracted_content=message, long_term_memory=message, is_done=True, success=False)
        return ActionResult(extracted_content=message)

    @tools.action("Rank search results for one shopping list item. Pass the visible titles and snippets.",
                  param_model=RankParams)
    async def rank_results(params: RankParams) -> ActionResult:
        msg = await core.rank_results(params.item_id, [r.model_dump() for r in params.results])
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    @tools.action("Judge the product page that is open now for one shopping list item. "
                  "The backend reads the page text itself.", param_model=JudgeParams)
    async def judge_listing(params: JudgeParams) -> ActionResult:
        msg = await core.judge_listing(params.item_id, params.title, params.sku, params.price)
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    @tools.action("Read and verify the cart totals on the cart page that is open now. "
                  "The backend reads the page text itself.")
    async def read_cart_totals() -> ActionResult:
        msg = await core.read_cart_totals()
        return ActionResult(extracted_content=msg, long_term_memory=msg)

    return tools
