"""Import point for live lane registrations (agents team). The server imports it at startup.

Each registration is guarded: a missing optional dependency (`uv sync --extra browser`)
or a missing key logs a warning and never stops the server. Fixture lanes still work.
"""

from __future__ import annotations

import importlib.util
import logging

from app.lanes.registry import register_adapter, register_llm_agent

log = logging.getLogger(__name__)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def register_live_lanes() -> dict[str, bool]:
    done = {"example_site": False, "webstaurant": False, "wees_express": False, "llm_agent": False,
            "hosted_agent": False}
    playwright = _has("playwright")
    if not playwright:
        log.warning("adapter and Step 5 agent lanes off: playwright is not installed (uv sync --extra browser)")
    if playwright:
        try:
            from app.lanes.live.example_site import ExampleSiteLane

            register_adapter("example_site", lambda supplier: ExampleSiteLane(supplier))
            done["example_site"] = True
        except Exception as exc:  # never break server start
            log.warning("example_site adapter not registered: %s", exc)

        # Real supplier sites. The names match discovery.ADAPTER_DOMAINS.
        try:
            from app.lanes.live.shopify import WEES_EXPRESS, ShopifyAdapterLane
            from app.lanes.live.webstaurant import WebstaurantLane

            register_adapter("webstaurant", lambda supplier: WebstaurantLane(supplier))
            done["webstaurant"] = True
            register_adapter("wees_express", lambda supplier: ShopifyAdapterLane(supplier, WEES_EXPRESS))
            done["wees_express"] = True
        except Exception as exc:  # never break server start
            log.warning("real-site adapters not registered: %s", exc)

    # Unknown sites get one LLM lane: the hosted Browser Use agent (needs only httpx) or the
    # open-source Step 5 agent. With both available, HOSTED_AGENT_FIRST picks, and the hosted
    # lane falls back to the Step 5 agent when a run fails before any cart exists.
    try:
        from app.lanes.live.config import get_live_settings

        cfg = get_live_settings()
        step5_ok = False
        if not playwright:
            pass  # logged above
        elif not _has("browser_use"):
            log.warning("Step 5 agent lane off: browser-use is not installed (uv sync --extra browser)")
        elif cfg.openrouter_api_key is None:
            log.warning("Step 5 agent lane off: OPENROUTER_API_KEY is not set")
        elif cfg.browser_mode == "cloud" and cfg.browser_use_api_key is None:
            log.warning("Step 5 agent lane off: BROWSER_MODE=cloud needs BROWSER_USE_API_KEY")
        else:
            step5_ok = True
        hosted_ok = cfg.browser_use_api_key is not None
        if not hosted_ok:
            log.warning("hosted agent lane off: BROWSER_USE_API_KEY is not set")

        if hosted_ok and (cfg.hosted_agent_first or not step5_ok):
            from app.lanes.live.hosted_agent import HostedAgentLane

            register_llm_agent(lambda supplier: HostedAgentLane(supplier, step5_fallback=step5_ok))
            done["hosted_agent"] = True
        elif step5_ok:
            from app.lanes.live.agent_lane import BrowserUseAgentLane

            register_llm_agent(lambda supplier: BrowserUseAgentLane(supplier))
            done["llm_agent"] = True
    except Exception as exc:  # never break server start
        log.warning("llm agent lane not registered: %s", exc)
    return done


LIVE_LANES = register_live_lanes()
