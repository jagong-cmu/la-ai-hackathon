"""Fixture distributor: scripted SMS + call that runs the real J9/J10 code paths.

The supplier's counter-offer is computed from the best plan at call time (offerFactor),
so the "they beat the web price" moment is consistent with whatever the lanes found.
"""

from __future__ import annotations

import asyncio
import json

from app.config import FIXTURES_DIR
from app.contracts import Evidence, Product, SupplierOffers
from app.distributor.base import DistributorContext, wait_for_call_start
from app.judgments import j9_call_line, j10_terms


def _save_transcript(ctx: DistributorContext, transcript: list[str]) -> str:
    """Writes the call transcript to the evidence folder and returns its served path."""
    folder = ctx.settings.evidence_dir / ctx.job_id / ctx.distributor.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "call.txt").write_text("\n".join(transcript) + "\n")
    return f"/evidence/{ctx.job_id}/{ctx.distributor.id}/call.txt"


def _spoken_list(ctx: DistributorContext) -> str:
    parts = [f"{i.quantity:,} {i.name}" for i in ctx.items]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


class FixtureDistributor:
    def __init__(self) -> None:
        self.script = json.loads((FIXTURES_DIR / "distributor.json").read_text())

    async def run(self, ctx: DistributorContext) -> SupplierOffers | None:
        s = self.script
        loop = asyncio.get_running_loop()
        if ctx.settings.distributor_flow == "sms_first":
            await ctx.emit("message_sent", {"channel": "sms", "to": ctx.distributor.name,
                                            "text": "Hi, this is an AI assistant for Hearthloaf Bakeries. "
                                                    "Could you quote these supplies for delivery?"})
            await ctx.sleep(ctx.settings.distributor_reply_wait_s)
            if s.get("smsReply") is None:
                await ctx.emit("reply_received", {"channel": "sms", "intent": "no_reply"})
        await wait_for_call_start(ctx, loop.time)

        plan = ctx.best_plan()
        if plan is None:
            await ctx.emit("call_skipped", {"reason": "no plan to anchor the call"})
            return None
        lines = {a.item_id: a for a in plan.assignments}
        factor = s["offerFactor"]
        offer_total = round(sum(a.line_cost for a in lines.values()) * factor, 2)
        best_total = plan.total

        await ctx.emit("call_started", {"to": ctx.distributor.name, "disclosure": "AI disclosed"})
        await ctx.sleep(s["callConnectSeconds"])
        transcript: list[str] = []
        offers: SupplierOffers | None = None
        names = {i.id: i.name for i in ctx.items}
        for turn in s["turns"]:
            await ctx.sleep(turn["after"])
            text = turn["text"].format(best_total=f"${best_total:.2f}", offer_total=f"${offer_total:.2f}",
                                       items=_spoken_list(ctx))
            transcript.append(f"{turn['speaker']}: {text}")
            await ctx.emit("transcript_line", {"speaker": turn["speaker"], "text": text})
            if turn["speaker"] != "supplier":
                continue
            decision = await j9_call_line(ctx.decider, text, transcript, names, job_id=ctx.job_id)
            if turn.get("offer"):
                p_offer = float(decision.answers.get("new_offer", {}).get("noul", 0.0))
                products = [
                    Product(item_id=item_id, title=f"{names.get(item_id, item_id)} (phone quote)",
                            sku=f"LARK-{item_id}", pack_price=round(a.line_cost * factor, 2),
                            units_per_pack=next(i.quantity for i in ctx.items if i.id == item_id))
                    for item_id, a in lines.items()
                ]
                offers = SupplierOffers(
                    supplier_id=ctx.distributor.id, source="call", status="ok", products=products,
                    quoted_delivery_fee=s["quotedDeliveryFee"], quoted_lead_time_days=s["quotedLeadTimeDays"],
                    evidence=Evidence(transcript_url=_save_transcript(ctx, transcript)),
                )
                await ctx.emit("call_offer", {"total": offer_total, "p": p_offer, "beats": offer_total < best_total})
                await ctx.on_offer(offers)
        await ctx.emit("call_ended", {"outcome": "quote" if offers else "no_quote"})
        if offers is not None:
            _save_transcript(ctx, transcript)  # the full call, including the turns after the offer
        if offers is not None:
            quote_text = s["quoteText"].format(offer_total=f"${offer_total:.2f}")
            offers.jev_terms = await j10_terms(ctx.decider, quote_text, job_id=ctx.job_id)
            await ctx.emit("message_sent", {"channel": "sms", "to": ctx.distributor.name,
                                            "text": "Thank you. Summary of our call: " + quote_text})
            await ctx.on_offer(offers)
        return offers
