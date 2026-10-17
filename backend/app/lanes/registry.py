"""Choose a lane implementation for a supplier.

Agents team: register live implementations here, for example in app/lanes/live/__init__.py:

    from app.lanes.registry import register_adapter, register_llm_agent
    register_adapter("uline", lambda supplier: UlineAdapterLane(supplier))
    register_llm_agent(lambda supplier: BrowserUseAgentLane(supplier))

Then import that module in app/lanes/live_setup.py (already imported at startup).
"""

from __future__ import annotations

from app.config import RuntimeModes
from app.contracts import SupplierRef
from app.lanes.base import LaneFactory, SupplierLane
from app.lanes.fixture import FixtureLane, fixture_path

_ADAPTERS: dict[str, LaneFactory] = {}
_LLM_AGENT: LaneFactory | None = None


class NoLaneAvailable(Exception):
    pass


def register_adapter(name: str, factory: LaneFactory) -> None:
    _ADAPTERS[name] = factory


def register_llm_agent(factory: LaneFactory) -> None:
    global _LLM_AGENT
    _LLM_AGENT = factory


def registered() -> dict[str, list[str] | bool]:
    return {"adapters": sorted(_ADAPTERS), "llmAgent": _LLM_AGENT is not None}


def lane_for(supplier: SupplierRef, modes: RuntimeModes) -> SupplierLane:
    if modes.get("browser") == "cached":
        if fixture_path(supplier.id).exists():
            return FixtureLane.load(supplier)
        raise NoLaneAvailable(f"no fixture for {supplier.id}")
    if supplier.adapter and supplier.adapter in _ADAPTERS:
        return _ADAPTERS[supplier.adapter](supplier)
    if _LLM_AGENT is not None:
        return _LLM_AGENT(supplier)
    raise NoLaneAvailable(f"no live lane registered for {supplier.id}")
