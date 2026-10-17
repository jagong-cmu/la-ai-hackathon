"""Load bill lines from the QuickBooks hackathon dataset plus the team's injected supply bills.

Dataset conventions (DATA_README): IDs are company-scoped; money is decimal strings.
The injected bills are synthetic and must be disclosed (dataset rules).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

try:
    _Loader: Any = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - libyaml missing
    _Loader = yaml.SafeLoader


@dataclass(frozen=True)
class BillLine:
    bill_id: str
    date: date
    vendor_id: str
    vendor_name: str
    item_id: str
    item_name: str
    description: str
    unit: str
    quantity: float
    unit_price: float
    amount: float
    synthetic: bool


@dataclass
class Books:
    business_id: str
    lines: list[BillLine]
    distributor: dict[str, Any] | None
    disclosure: str | None
    dataset_loaded: bool


def _company_dir(qb_dir: Path, business_id: str) -> Path | None:
    matches = sorted((qb_dir / business_id / "rwm-v4").glob("world-*"))
    return matches[0] if matches else None


def load_dataset_lines(qb_dir: Path | None, business_id: str) -> list[BillLine]:
    if qb_dir is None:
        return []
    world = _company_dir(qb_dir, business_id)
    if world is None or not (world / "bills.yaml").exists():
        return []
    with (world / "bills.yaml").open() as f:
        bills = yaml.load(f, Loader=_Loader) or []
    lines = []
    for bill in bills:
        for line in bill.get("item_lines") or []:
            lines.append(
                BillLine(
                    bill_id=bill["id"],
                    date=date.fromisoformat(str(bill["txn_date"])),
                    vendor_id=bill.get("vendor_id", ""),
                    vendor_name=bill.get("vendor_name", ""),
                    item_id=line["item_id"],
                    item_name=line["item_name"],
                    description=line.get("description", ""),
                    unit="unit",
                    quantity=float(line["quantity"]),
                    unit_price=float(line["unit_price_amount"]),
                    amount=float(line["amount"]),
                    synthetic=False,
                )
            )
    return lines


def load_injected(path: Path, business_id: str) -> tuple[list[BillLine], dict | None, str | None]:
    if not path.exists():
        return [], None, None
    with path.open() as f:
        doc = yaml.load(f, Loader=_Loader) or {}
    if doc.get("business_id") != business_id:
        return [], None, None
    vendor = doc["vendor"]
    items = {i["item_id"]: i for i in doc["items"]}
    lines = []
    for bill in doc["bills"]:
        for line in bill["lines"]:
            item = items[line["item_id"]]
            qty = float(line["quantity"])
            price = float(line["unit_price"])
            lines.append(
                BillLine(
                    bill_id=bill["id"],
                    date=date.fromisoformat(str(bill["date"])),
                    vendor_id=vendor["id"],
                    vendor_name=vendor["name"],
                    item_id=item["item_id"],
                    item_name=item["name"],
                    description=item.get("description", ""),
                    unit=item["unit"],
                    quantity=qty,
                    unit_price=price,
                    amount=round(qty * price, 2),
                    synthetic=True,
                )
            )
    return lines, vendor, doc.get("disclosure")


def load_books(qb_dir: Path | None, injected_path: Path, business_id: str) -> Books:
    dataset = load_dataset_lines(qb_dir, business_id)
    injected, vendor, disclosure = load_injected(injected_path, business_id)
    return Books(
        business_id=business_id,
        lines=dataset + injected,
        distributor=vendor,
        disclosure=disclosure,
        dataset_loaded=bool(dataset),
    )
