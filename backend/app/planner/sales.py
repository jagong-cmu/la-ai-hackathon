"""Weekly sales, customer facts and bill memos from the QuickBooks books.

`extract_sales` reads the dataset (invoices, bills, bank feed) once. The compact result is
saved as fixtures/company-001-sales.json so fixture mode and tests run without the
400 MB dataset. Weeks start on Monday; week 0 starts at the first day of the books.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.config import FIXTURES_DIR

try:
    _Loader: Any = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _Loader = yaml.SafeLoader

SALES_FIXTURE = FIXTURES_DIR / "company-001-sales.json"


@dataclass
class Product:
    item_id: str
    name: str
    unit_cost: float


@dataclass
class SalesData:
    business_id: str
    start: date
    weeks: int
    products: dict[str, Product]
    # (week, item_id) -> {customer_id: qty}
    by_customer: dict[tuple[int, str], dict[str, float]]
    customers: dict[str, str]
    bill_memos: list[dict[str, Any]] = field(default_factory=list)  # {week, itemIds, memo, vendor}
    cash: list[float] = field(default_factory=list)  # end-of-week bank balance
    purchases: dict[tuple[int, str], float] = field(default_factory=dict)  # (week, item_id) -> qty bought
    source: str = "fixture"

    def week_start(self, week: int) -> date:
        return self.start + timedelta(days=7 * week)

    def series(self, item_id: str) -> list[float]:
        return [sum(self.by_customer.get((w, item_id), {}).values()) for w in range(self.weeks)]

    def purchase_series(self, item_id: str) -> list[float]:
        return [self.purchases.get((w, item_id), 0.0) for w in range(self.weeks)]

    def launch_week(self, item_id: str) -> int | None:
        return next((w for w, v in enumerate(self.series(item_id)) if v > 0), None)


def _week(start: date, day: date) -> int:
    return (day - start).days // 7


def extract_sales(world_dir: Path, business_id: str) -> SalesData:
    def load(name: str) -> list[dict]:
        path = world_dir / name
        return yaml.load(path.open(), Loader=_Loader) or [] if path.exists() else []

    invoices, bills, bank = load("invoices.yaml"), load("bills.yaml"), load("bank_transactions.yaml")
    items = {i["id"]: i for i in load("items.yaml")}
    start = min(date.fromisoformat(str(t["date"])) for t in bank) if bank else \
        min(date.fromisoformat(str(i["txn_date"])) for i in invoices)
    start -= timedelta(days=start.weekday())
    last = max(date.fromisoformat(str(i["txn_date"])) for i in invoices)
    weeks = _week(start, last)  # only complete weeks

    by_customer: dict[tuple[int, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    customers: dict[str, str] = {}
    products: dict[str, Product] = {}
    for inv in invoices:
        week = _week(start, date.fromisoformat(str(inv["txn_date"])))
        if week >= weeks:
            continue
        customers[inv["customer_id"]] = inv["customer_name"]
        for line in inv["lines"]:
            item = items.get(line["item_id"], {})
            products[line["item_id"]] = Product(line["item_id"], line["item_name"],
                                                float(item.get("unit_cost_amount", 0) or 0))
            by_customer[(week, line["item_id"])][inv["customer_id"]] += float(line["quantity"])

    memos = []
    purchases: dict[tuple[int, str], float] = defaultdict(float)
    for bill in bills:
        lines = bill.get("item_lines") or []
        bweek = _week(start, date.fromisoformat(str(bill["txn_date"])))
        for line in lines:
            if bweek < weeks:
                purchases[(bweek, line["item_id"])] += float(line["quantity"])
        if lines and bill.get("memo"):
            memos.append({"week": _week(start, date.fromisoformat(str(bill["txn_date"]))),
                          "itemIds": sorted({line["item_id"] for line in lines}),
                          "memo": bill["memo"], "vendor": bill.get("vendor_name", "")})

    cash = [0.0] * weeks
    balance = 0.0
    for t in sorted(bank, key=lambda t: (str(t["date"]), t["id"])):
        week = _week(start, date.fromisoformat(str(t["date"])))
        balance = float(t["running_balance_amount"])
        if week < weeks:
            for w in range(week, weeks):
                cash[w] = balance

    return SalesData(business_id, start, weeks, products,
                     {k: dict(v) for k, v in by_customer.items()}, customers, memos, cash,
                     dict(purchases), source="dataset")


def save_fixture(data: SalesData, path: Path = SALES_FIXTURE) -> None:
    doc = {
        "businessId": data.business_id,
        "derivedFrom": "Intuit QuickBooks hackathon dataset, company-001 (invoices, bills, bank feed)",
        "start": data.start.isoformat(),
        "weeks": data.weeks,
        "products": [{"itemId": p.item_id, "name": p.name, "unitCost": p.unit_cost} for p in data.products.values()],
        "customers": data.customers,
        "lines": [[w, i, c, q] for (w, i), per in sorted(data.by_customer.items()) for c, q in sorted(per.items())],
        "billMemos": data.bill_memos,
        "cash": data.cash,
        "purchases": [[w, i, q] for (w, i), q in sorted(data.purchases.items())],
    }
    path.write_text(json.dumps(doc, separators=(",", ":")))


def load_fixture(path: Path = SALES_FIXTURE) -> SalesData:
    doc = json.loads(path.read_text())
    by_customer: dict[tuple[int, str], dict[str, float]] = defaultdict(dict)
    for week, item_id, customer_id, qty in doc["lines"]:
        by_customer[(week, item_id)][customer_id] = qty
    return SalesData(
        business_id=doc["businessId"],
        start=date.fromisoformat(doc["start"]),
        weeks=doc["weeks"],
        products={p["itemId"]: Product(p["itemId"], p["name"], p["unitCost"]) for p in doc["products"]},
        by_customer=dict(by_customer),
        customers=doc["customers"],
        bill_memos=doc["billMemos"],
        cash=doc["cash"],
        purchases={(w, i): q for w, i, q in doc.get("purchases", [])},
    )


def load_sales(qb_dir: Path | None, business_id: str) -> SalesData:
    if qb_dir is not None:
        worlds = sorted((qb_dir / business_id / "rwm-v4").glob("world-*"))
        if worlds:
            return extract_sales(worlds[0], business_id)
    return load_fixture()
