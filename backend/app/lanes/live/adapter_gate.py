"""Real-site gate (spec H10): run one site adapter on its live site.

search -> J4 -> J5 -> cart -> J7 -> (optional) checkout entry, where the guard stops.

    cd backend
    uv run python -m app.lanes.live.adapter_gate webstaurant --headed
    uv run python -m app.lanes.live.adapter_gate wees_express
    uv run python -m app.lanes.live.adapter_gate wees_express --checkout
    uv run python -m app.lanes.live.adapter_gate webstaurant --cloud     # Browser Use cloud browser

It uses a real guest cart on the site and never submits anything past the cart; --checkout
opens the checkout page, where the payment guard stops the lane. Jev runs live if JEV_API_KEY
is set, else the stub answers. Pass: every line says OK. Exits 1 on a failure.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import RuntimeModes, Settings
from app.contracts import CartLine, ShoppingItem, SupplierRef
from app.db import Database
from app.decide import Decider
from app.jev import JevClient
from app.lanes.base import LaneContext
from app.lanes.live.config import LiveLaneSettings
from app.llm import Step5Client

SITES = {
    "webstaurant": ("WebstaurantStore", "https://www.webstaurantstore.com/"),
    "wees_express": ("Wees Express", "https://wees-express.com/"),
}
ITEMS = [
    ShoppingItem(id="gloves", name="nitrile gloves medium", quantity=1000, unit="glove"),
    ShoppingItem(id="boxes", name="9 x 9 hinged to-go box", quantity=200, unit="box"),
]


def make_lane(name: str, supplier: SupplierRef, cfg: LiveLaneSettings):
    if name == "webstaurant":
        from app.lanes.live.webstaurant import WebstaurantLane

        return WebstaurantLane(supplier, cfg)
    from app.lanes.live.shopify import WEES_EXPRESS, ShopifyAdapterLane

    return ShopifyAdapterLane(supplier, WEES_EXPRESS, cfg)


async def run(name: str, headless: bool, checkout: bool, cloud: bool = False, warm: bool = False) -> int:
    cfg = LiveLaneSettings(browser_mode="cloud" if cloud else "local", browser_headless=headless).model_copy(
        update={"openrouter_api_key": None})  # no agent fallback: the gate tests the adapter alone
    tmp = Path(tempfile.mkdtemp(prefix=f"gate-{name}-"))
    settings = Settings(run_mode="live", var_dir=tmp)
    settings.var_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    modes = RuntimeModes(settings.initial_modes())
    decider = Decider(settings, db, JevClient(settings, db, modes), Step5Client(settings, db, modes))
    failures = 0

    async def emit(kind: str, data: dict[str, Any]) -> None:
        if kind in ("guard_stop", "lane_fallback", "checkout_filled"):
            print(f"  event {kind}: {data}")

    def report(ok: bool, what: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'OK  ' if ok else 'FAIL'} {what}")

    title, url = SITES[name]
    supplier = SupplierRef(id=name, name=title, url=url, adapter=name)
    ctx = LaneContext(job_id="gate", lane_id=f"gate-{name}", supplier=supplier, settings=settings,
                      decider=decider, emit=emit, evidence_dir=settings.evidence_dir)
    lane = make_lane(name, supplier, cfg)
    print(f"gate {name}: browser={cfg.browser_mode} headless={headless} warm={warm} jev={modes.get('jev')} "
          f"evidence={tmp}")
    if warm:  # as the shopping-list screen does: the browser starts before the job
        from app.lanes.live import warm as pool

        await pool.prewarm([supplier], cfg)
        await asyncio.gather(*pool._pending.values())
    t0 = time.monotonic()
    try:
        await lane.open(ctx)
        if cloud:
            report(bool(ctx.live_url), f"cloud browser live view: {ctx.live_url}")
        offers = await lane.search(ctx, ITEMS)
        report(offers.status in ("ok", "partial"), f"search status={offers.status} ({time.monotonic() - t0:.0f}s)")
        for p in offers.products:
            print(f"  {p.item_id}: {p.title[:70]} | sku={p.sku} ${p.pack_price} units={p.units_per_pack}")
        lines = [CartLine(item_id=p.item_id, sku=p.sku, pack_quantity=1, unit_price=p.pack_price, url=p.url)
                 for p in offers.products]
        if lines:
            t1 = time.monotonic()
            cart = await lane.set_cart(ctx, lines)
            c = cart.costs
            report(cart.verification_status in ("verified", "estimated"),
                   f"cart status={cart.verification_status} subtotal={c and c.subtotal} shipping={c and c.shipping} "
                   f"total={c and c.total} reason={cart.reason} ({time.monotonic() - t1:.0f}s)")
            if checkout:
                t2 = time.monotonic()
                handoff = await lane.checkout(ctx, settings.checkout_contact)
                report(handoff.status == "at_payment",
                       f"checkout status={handoff.status} reason={handoff.reason} ({time.monotonic() - t2:.0f}s)")
    except Exception as exc:
        report(False, f"lane raised {type(exc).__name__}: {exc}")
    finally:
        await lane.close(ctx)
    print(f"total {time.monotonic() - t0:.0f}s, failures={failures}")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("site", choices=sorted(SITES))
    parser.add_argument("--headed", action="store_true", help="show the browser (WebstaurantStore needs it)")
    parser.add_argument("--checkout", action="store_true", help="also open checkout, where the guard stops")
    parser.add_argument("--cloud", action="store_true", help="use the Browser Use cloud browser (BROWSER_USE_API_KEY)")
    parser.add_argument("--warm", action="store_true", help="start the browser before timing (as prewarm does)")
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(asyncio.run(run(args.site, headless=not args.headed, checkout=args.checkout, cloud=args.cloud,
                             warm=args.warm)))


if __name__ == "__main__":
    main()
