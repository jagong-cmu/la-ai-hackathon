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
    done = {"example_site": False, "llm_agent": False}
    if not _has("playwright"):
        log.warning("live lanes off: playwright is not installed (uv sync --extra browser)")
        return done
    try:
        from app.lanes.live.example_site import ExampleSiteLane

        register_adapter("example_site", lambda supplier: ExampleSiteLane(supplier))
        done["example_site"] = True
    except Exception as exc:  # never break server start
        log.warning("example_site adapter not registered: %s", exc)

    try:
        from app.lanes.live.config import get_live_settings

        cfg = get_live_settings()
        if not _has("browser_use"):
            log.warning("llm agent lane off: browser-use is not installed (uv sync --extra browser)")
        elif cfg.openrouter_api_key is None:
            log.warning("llm agent lane off: OPENROUTER_API_KEY is not set")
        elif cfg.browser_mode == "cloud" and cfg.browser_use_api_key is None:
            log.warning("llm agent lane off: BROWSER_MODE=cloud needs BROWSER_USE_API_KEY")
        else:
            from app.lanes.live.agent_lane import BrowserUseAgentLane

            register_llm_agent(lambda supplier: BrowserUseAgentLane(supplier))
            done["llm_agent"] = True
    except Exception as exc:  # never break server start
        log.warning("llm agent lane not registered: %s", exc)
    return done


LIVE_LANES = register_live_lanes()
