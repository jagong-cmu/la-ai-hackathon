"""Warm browsers: start a supplier's browser before the job, so the lane does not wait for it.

The shopping-list screen (GET /shopping-list/draft) calls prewarm() with the selected suppliers
that have a registered site adapter. A lane claims its warm browser in PlaywrightAdapterLane.open()
(claim(), which waits for one that is still starting). A browser nobody takes closes after TTL_S, so idle cloud browsers do not
run up credits. Only started browsers are kept: nothing is loaded before the lane's own guarded
steps run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from app.contracts import SupplierRef
from app.lanes.live.config import LiveLaneSettings, get_live_settings
from app.lanes.live.session import LaneBrowser

log = logging.getLogger(__name__)

TTL_S = 600  # the cloud browser's own timeout is cloud_timeout_min (15 min by default)

_warm: dict[str, tuple[LaneBrowser, float]] = {}
_pending: dict[str, asyncio.Task] = {}


def take(supplier: SupplierRef) -> LaneBrowser | None:
    """The started browser for this supplier, if one is warm (it leaves the pool)."""
    if not _warm:
        return None
    entry = _warm.pop(supplier.id, None)
    if entry is None:
        return None
    browser, started = entry
    if time.monotonic() - started > TTL_S:
        asyncio.get_running_loop().create_task(browser.close())
        return None
    return browser


async def claim(supplier: SupplierRef, wait_s: float = 20.0) -> LaneBrowser | None:
    """take(), but first wait (up to wait_s) for a browser that is still starting."""
    task = _pending.get(supplier.id)
    if task is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(task), wait_s)
    return take(supplier)


async def prewarm(suppliers: list[SupplierRef], cfg: LiveLaneSettings | None = None) -> None:
    """Start one browser per supplier that has none warm or starting. Never raises."""
    cfg = cfg or get_live_settings()
    for supplier in suppliers:
        if supplier.id in _warm or supplier.id in _pending:
            continue
        _pending[supplier.id] = asyncio.get_running_loop().create_task(_start(supplier, cfg))


async def _start(supplier: SupplierRef, cfg: LiveLaneSettings) -> None:
    browser = LaneBrowser(cfg, label=supplier.id)
    try:
        await browser.start()
        _warm[supplier.id] = (browser, time.monotonic())
        asyncio.get_running_loop().call_later(TTL_S, _expire, supplier.id, browser)
    except Exception as exc:  # a lane without a warm browser just starts its own
        log.warning("prewarm %s failed: %s", supplier.id, exc)
        with contextlib.suppress(Exception):
            await browser.close()
    finally:
        _pending.pop(supplier.id, None)


def _expire(supplier_id: str, browser: LaneBrowser) -> None:
    entry = _warm.get(supplier_id)
    if entry is not None and entry[0] is browser:
        del _warm[supplier_id]
        asyncio.get_running_loop().create_task(browser.close())


async def close_all() -> None:
    for task in list(_pending.values()):
        task.cancel()
    for browser, _ in list(_warm.values()):
        with contextlib.suppress(Exception):
            await browser.close()
    _warm.clear()
    _pending.clear()
