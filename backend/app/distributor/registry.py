"""Choose the distributor channel. The agents team registers the live Photon channel."""

from __future__ import annotations

from collections.abc import Callable

from app.config import RuntimeModes
from app.distributor.base import DistributorChannel
from app.distributor.fixture import FixtureDistributor

_LIVE: Callable[[], DistributorChannel] | None = None


def register_live_channel(factory: Callable[[], DistributorChannel]) -> None:
    global _LIVE
    _LIVE = factory


def channel_for(modes: RuntimeModes) -> DistributorChannel | None:
    if modes.get("phone") == "cached":
        return FixtureDistributor()
    return _LIVE() if _LIVE is not None else None
