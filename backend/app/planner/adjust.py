"""J12 helpers: find quantity and date phrases (code), resolve dates (code).

Jev only chooses which phrase is the quantity and which is the start date. Code turns the
chosen phrase into a number or a date (TypeSafe date-extraction pattern).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_RX = r"(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?\.?"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
MONTH_RX = "|".join(m[:3] + r"[a-z]*\.?" for m in MONTHS)

# "1,500", "1.5k", "2.5", "300". A "k" multiplies by 1,000 (phrase_quantity).
QTY = re.compile(r"\b\d+(?:\.\d+)?k\b|\b\d{1,3}(?:,\d{3})+\b|\b\d+(?:\.\d+)?\b", re.I)
DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b(?:{MONTH_RX})\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.I),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2}(?:\d{2})?)?\b"),
    re.compile(rf"\b(?:this|next|on)?\s*{WEEKDAY_RX}(?![a-z])", re.I),
    re.compile(r"\b(?:today|tomorrow|tonight|tmrw|tmr)\b", re.I),
    re.compile(r"\b(?:this|next)\s+weekend\b", re.I),
    re.compile(r"\bthe\s+\d{1,2}(?:st|nd|rd|th)\b", re.I),
    re.compile(r"\b(?:next|this)\s+(?:week|month)\b", re.I),
    re.compile(r"\bin\s+\d+\s+(?:days?|weeks?)\b", re.I),
]


def date_phrases(text: str) -> list[str]:
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for rx in DATE_PATTERNS:
        for m in rx.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in spans):
                continue
            phrase = m.group(0).strip()
            if phrase:
                found.append(phrase)
                spans.append((m.start(), m.end()))
    return found


def quantity_phrases(text: str, dates: list[str]) -> list[str]:
    # Skip numbers that sit inside a date phrase (by position, not by text: "2" is not "Oct 24").
    date_spans = [(m.start(), m.end()) for phrase in dates for m in re.finditer(re.escape(phrase), text)]
    out = []
    for m in QTY.finditer(text):
        token = m.group(0)
        if any(s <= m.start() and m.end() <= e for s, e in date_spans):
            continue
        tail = text[m.end():m.end() + 12].strip()
        out.append(f"{token} {tail.split()[0]}" if tail else token)
    return list(dict.fromkeys(out))


def phrase_quantity(phrase: str) -> float | None:
    m = QTY.search(phrase)
    if not m:
        return None
    token = m.group(0).replace(",", "").lower()
    return float(token[:-1]) * 1000 if token.endswith("k") else float(token)


def resolve_date(phrase: str, today: date) -> date | None:
    p = phrase.lower().strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
        try:
            return date.fromisoformat(p)
        except ValueError:
            return None
    if p in ("today", "tonight"):
        return today
    if p in ("tomorrow", "tmrw", "tmr"):
        return today + timedelta(days=1)
    if p.endswith("weekend"):
        ahead = (5 - today.weekday()) % 7 or 7  # the coming Saturday
        return today + timedelta(days=ahead + (7 if p.startswith("next") and ahead < 2 else 0))
    m = re.match(r"the\s+(\d{1,2})", p)
    if m:
        day = int(m.group(1))
        month, year = (today.month, today.year) if day >= today.day else (
            (today.month % 12) + 1, today.year + (today.month == 12))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    if p in ("this week",):
        return today
    if p == "next week":
        return today + timedelta(days=7 - today.weekday())
    if p == "next month":
        first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return first
    if p == "this month":
        return today
    m = re.match(r"in\s+(\d+)\s+(day|week)", p)
    if m:
        n = int(m.group(1))
        return today + timedelta(days=n * (7 if m.group(2) == "week" else 1))
    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b{wd[:3]}", p):
            # "Saturday", "this Saturday" and "next Saturday" all mean the coming Saturday.
            # People use "next" both ways; the UI shows the resolved date for the owner to check.
            ahead = (i - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    m = re.match(rf"({MONTH_RX})\s+(\d{{1,2}})", p)
    if m:
        month = next(i for i, name in enumerate(MONTHS, 1) if name.startswith(m.group(1)[:3]))
        day = int(m.group(2))
        year = today.year + (1 if month < today.month else 0)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}(?:\d{2})?))?", p)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = today.year + (1 if month < today.month else 0)
        if m.group(3):
            year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None
