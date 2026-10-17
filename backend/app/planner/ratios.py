"""Assumed supply-usage ratios (fixtures/usage_ratios.yaml). Labelled "assumed" in the UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import FIXTURES_DIR

RATIOS_PATH = FIXTURES_DIR / "usage_ratios.yaml"


@dataclass
class UsageRatio:
    supply_id: str
    unit: str
    pack: int
    per_product: dict[str, float]


def load_ratios(path: Path = RATIOS_PATH) -> dict[str, UsageRatio]:
    doc = yaml.safe_load(path.read_text())
    return {
        sid: UsageRatio(sid, s["unit"], int(s["pack"]), {k: float(v) for k, v in s["per_product"].items()})
        for sid, s in doc["supplies"].items()
    }
