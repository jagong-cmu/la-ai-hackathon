"""Regex candidate finders. Code finds every candidate; Jev only selects among them.

If a candidate is missing here, Jev cannot choose it, so add patterns when a golden
test shows a miss.
"""

from __future__ import annotations

import re

from app.contracts import MoneyCandidate, PackCandidate

FREE_SHIPPING = re.compile(r"(?=.*\b(shipping|delivery|freight)\b)(?=.*\bfree\b)", re.I)
MONEY = re.compile(r"(?<![\w.])\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")

CONTAINERS = r"(?:box|case|pack|bag|carton|roll|sleeve|bundle|package|pkg|tub|dispenser|ream)(?:e?s)?"
UNITS = (
    r"ct|count|pk|pack|pcs?|pieces|sheets|bags|boxes|rolls|gloves|liners|each|ea|units|"
    r"cups|lids|napkins|labels|containers|sleeves|tissues"
)
PACK_PATTERNS = [
    re.compile(rf"\b(?:{CONTAINERS})\s+of\s+(\d[\d,]*)\b", re.I),
    re.compile(rf"\b(\d[\d,]*)\s*[- ]?(?:{UNITS})\b(?:\s*/\s*(?:{CONTAINERS}))?", re.I),
    re.compile(rf"\b(\d[\d,]*)\s*(?:per|/)\s*(?:{CONTAINERS}|cs|bx)\b", re.I),
]
# Nested packs: "10 boxes of 100", or "100/Box" with "10 Boxes/Case". The case holds the product.
INNER = r"(?:box|pack|bag|sleeve|roll|carton|package)"
NESTED_OF = re.compile(rf"\b(\d[\d,]*)\s*{INNER}e?s\s+of\s+(\d[\d,]*)\b", re.I)
PER_INNER = re.compile(rf"\b(\d[\d,]*)\s*(?:[a-z]+\s*)?/\s*{INNER}\b", re.I)
INNER_PER_CASE = re.compile(rf"\b(\d[\d,]*)\s*{INNER}e?s\s*/\s*(?:case|cs|carton)\b", re.I)
RATIO = re.compile(r"\b(\d{1,4})\s*/\s*(\d{1,5})\b(?!\s*(?:in|inch|mm|cm|\"|oz|lb))", re.I)


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def money_candidates(text: str) -> list[MoneyCandidate]:
    """One candidate per amount; the label is the rest of the line it sits on.
    A shipping line that says FREE and shows no amount is a $0.00 candidate."""
    out: list[MoneyCandidate] = []
    for line in text.splitlines():
        matches = list(MONEY.finditer(line))
        for match in matches:
            label = (line[: match.start()] + line[match.end() :]).strip(" :-\t")
            out.append(MoneyCandidate(label=label or "(no label)", value=float(match.group(1).replace(",", ""))))
        if not matches and FREE_SHIPPING.search(line):  # a banner ("free over $75") has an amount: skipped
            label = re.sub(r"\bfree\b", "", line, flags=re.I).strip(" :-\t")
            out.append(MoneyCandidate(label=label or "Shipping", value=0.0))
    return out


def pack_candidates(text: str) -> list[PackCandidate]:
    found: dict[int, PackCandidate] = {}
    for pattern in PACK_PATTERNS:
        for match in pattern.finditer(text):
            value = _int(match.group(1))
            if value > 0 and value not in found:
                found[value] = PackCandidate(text=match.group(0).strip(), value=value)
    nested = [(_int(m.group(1)), _int(m.group(2)), m.group(0)) for m in NESTED_OF.finditer(text)]
    inner, outer = PER_INNER.search(text), INNER_PER_CASE.search(text)
    if inner and outer:
        nested.append((_int(outer.group(1)), _int(inner.group(1)), f"{outer.group(0)} x {inner.group(0)}"))
    for a, b, phrase in nested:
        if a > 0 and b > 0 and a * b not in found:
            found[a * b] = PackCandidate(text=phrase.strip(), value=a * b)
    for match in RATIO.finditer(text):
        a, b = int(match.group(1)), int(match.group(2))
        if a > 0 and b > 0 and a * b not in found:
            # "10/100" usually means 10 packs of 100 per case.
            found[a * b] = PackCandidate(text=match.group(0).strip(), value=a * b)
    return list(found.values())
