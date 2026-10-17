"""Preflight (spec §10.7): run 15 minutes and again 2 minutes before judging.

Each check is green / amber / red. A red check on a live dependency switches it to
cached mode, so the demo uses recorded data instead of failing on stage.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any, Literal

from app.config import RuntimeModes, Settings
from app.contracts import Model
from app.jev import JevClient, JevError
from app.lanes.registry import registered
from app.llm import LLMError, Step5Client

Status = Literal["green", "amber", "red"]


class Check(Model):
    name: str
    status: Status
    detail: str
    data: dict[str, Any] = {}


class PreflightReport(Model):
    overall: Status
    checks: list[Check]
    modes: dict[str, str]
    switched_to_cached: list[str] = []


async def run_preflight(
    settings: Settings, modes: RuntimeModes, jev: JevClient, llm: Step5Client, *,
    dataset_loaded: bool, injected_loaded: bool, apply: bool = True,
) -> PreflightReport:
    checks: list[Check] = []
    switched: list[str] = []

    def red(dep: str) -> None:
        if apply and modes.get(dep) == "live":
            modes.set(dep, "cached")
            switched.append(dep)

    # Jev
    if jev.live:
        try:
            samples = [await jev.ping() for _ in range(5)]
            p50 = sorted(samples)[2]
            status: Status = "green" if p50 < 1500 else "amber"
            checks.append(Check(name="jev", status=status, detail=f"p50 {p50:.0f} ms", data={"samplesMs": samples}))
        except JevError as exc:
            checks.append(Check(name="jev", status="red", detail=str(exc)))
            red("jev")
    else:
        checks.append(Check(name="jev", status="amber", detail="cached mode or no key: stub answers on cache miss"))

    # Step 5 Preview
    if llm.live:
        try:
            ms = await asyncio.wait_for(llm.ping(), timeout=settings.llm_timeout_s)
            checks.append(Check(name="step5", status="green" if ms < 8000 else "amber", detail=f"{ms:.0f} ms"))
        except (LLMError, TimeoutError) as exc:
            checks.append(Check(name="step5", status="red", detail=str(exc)))
            red("llm")
    else:
        checks.append(Check(name="step5", status="amber", detail="cached mode or no key: template fallbacks"))

    # Lanes
    reg = registered()
    if modes.get("browser") == "live":
        ok = bool(reg["adapters"]) or bool(reg["llmAgent"])
        checks.append(Check(name="browser_lanes", status="green" if ok else "red",
                            detail=f"adapters={reg['adapters']} llmAgent={reg['llmAgent']}"))
        if not ok:
            red("browser")
    else:
        checks.append(Check(name="browser_lanes", status="amber", detail="cached mode: fixture lanes"))

    # CREAO, phone
    checks.append(Check(name="creao", status="green" if modes.get("creao") == "live" else "amber",
                        detail=f"mode={modes.get('creao')}"))
    checks.append(Check(name="phone", status="green" if modes.get("phone") == "live" else "amber",
                        detail=f"mode={modes.get('phone')}"))

    # Data and disk
    checks.append(Check(name="dataset", status="green" if dataset_loaded else "amber",
                        detail="QuickBooks dataset loaded" if dataset_loaded else "QB_DATA_DIR not set: injected bills only"))
    checks.append(Check(name="injected_bills", status="green" if injected_loaded else "red",
                        detail="synthetic supply bills loaded" if injected_loaded else "missing injected bills file"))
    free_gb = shutil.disk_usage(settings.var_dir).free / 1e9
    checks.append(Check(name="disk", status="green" if free_gb > 2 else "red", detail=f"{free_gb:.1f} GB free"))
    checks.append(Check(name="checkout_contact",
                        status="red" if "NOT SET" in settings.checkout_contact.address1 else "green",
                        detail="team checkout address set" if "NOT SET" not in settings.checkout_contact.address1
                        else "set CHECKOUT_CONTACT__* before live checkout"))

    order = {"green": 0, "amber": 1, "red": 2}
    overall = max((c.status for c in checks), key=lambda s: order[s])
    return PreflightReport(overall=overall, checks=checks, modes=modes.as_dict(), switched_to_cached=switched)
