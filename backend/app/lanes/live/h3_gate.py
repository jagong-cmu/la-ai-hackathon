"""H3 gate: can the lane model (BROWSER_LLM_MODEL, else STEP5_MODEL) drive the Browser Use agent lane?

Runs BrowserUseAgentLane on the local test site with a local browser:
search -> J5 -> cart -> J7 -> checkout -> the guard stops at the payment page.

    cd backend
    uv run python -m app.lanes.live.h3_gate                 # the lane model from .env
    uv run python -m app.lanes.live.h3_gate --model step-5-preview   # compare another model

Needs OPENROUTER_API_KEY (the key for OPENROUTER_BASE_URL, StepFun for this team). Jev runs live if JEV_API_KEY is set, else the stub answers.
Pass: every line says OK. The script exits 1 on a failure. It never touches a real site.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import CheckoutContact, RuntimeModes, Settings
from app.contracts import CartLine, ShoppingItem, SupplierRef
from app.db import Database
from app.decide import Decider
from app.jev import JevClient
from app.lanes.base import LaneContext
from app.lanes.live.agent_lane import BrowserUseAgentLane
from app.lanes.live.config import LiveLaneSettings
from app.lanes.live.testsite import serve_site
from app.llm import Step5Client

ITEMS = [
    ShoppingItem(id="gloves", name="nitrile gloves medium", quantity=300, unit="gloves"),
    ShoppingItem(id="boxes", name="white bakery box 10 x 10 x 5", quantity=50, unit="boxes"),
]


async def run(model: str | None, headless: bool) -> int:
    cfg = LiveLaneSettings(browser_mode="local", browser_headless=headless)
    if model:
        cfg = cfg.model_copy(update={"browser_llm_model": model})
    if cfg.openrouter_api_key is None:
        print("FAIL: OPENROUTER_API_KEY is not set")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="h3-gate-"))
    settings = Settings(run_mode="live", var_dir=tmp)
    settings.var_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    modes = RuntimeModes(settings.initial_modes())
    decider = Decider(settings, db, JevClient(settings, db, modes), Step5Client(settings, db, modes))
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(kind: str, data: dict[str, Any]) -> None:
        events.append((kind, data))
        print(f"  event {kind}: {data}")

    failures = 0

    def report(ok: bool, what: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'} {what}")

    print(f"H3 gate: model={cfg.browser_llm_model or cfg.step5_model} force_json={cfg.browser_llm_force_json} jev={modes.get('jev')} evidence={tmp}")
    with serve_site() as base:
        supplier = SupplierRef(id="h3-example", name="Demo Supply Co.", url=base)
        ctx = LaneContext(job_id="h3", lane_id="h3-lane", supplier=supplier, settings=settings, decider=decider,
                          emit=emit, evidence_dir=settings.evidence_dir)
        lane = BrowserUseAgentLane(supplier, cfg)
        t0 = time.monotonic()
        try:
            await lane.open(ctx)
            offers = await lane.search(ctx, ITEMS)
            report(offers.status == "ok", f"search status={offers.status} products={len(offers.products)} "
                                          f"({time.monotonic() - t0:.0f}s)")
            lines = [CartLine(item_id=p.item_id, sku=p.sku, pack_quantity=1, unit_price=p.pack_price, url=p.url)
                     for p in offers.products]
            if lines:
                t1 = time.monotonic()
                cart = await lane.set_cart(ctx, lines)
                report(cart.verification_status == "verified",
                       f"cart status={cart.verification_status} reason={cart.reason} "
                       f"total={cart.costs.total if cart.costs else None} ({time.monotonic() - t1:.0f}s)")
                t2 = time.monotonic()
                handoff = await lane.checkout(ctx, CheckoutContact())
                report(handoff.status == "at_payment",
                       f"checkout status={handoff.status} reason={handoff.reason} ({time.monotonic() - t2:.0f}s)")
        except Exception as exc:
            report(False, f"lane raised {type(exc).__name__}: {exc}")
        finally:
            await lane.close(ctx)
    report(any(k == "guard_stop" for k, _ in events), "the guard stopped the lane")
    print(f"total {time.monotonic() - t0:.0f}s, failures={failures}")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="model id at OPENROUTER_BASE_URL (default: BROWSER_LLM_MODEL, else STEP5_MODEL)")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # keep the OK/FAIL lines if a timeout kills the run
    sys.exit(asyncio.run(run(args.model, headless=not args.headed)))


if __name__ == "__main__":
    main()
