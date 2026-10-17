"""Metric functions. Pure Python, no numpy, so the tests can check them on tiny inputs."""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence


def accuracy(gold: Sequence[Hashable], pred: Sequence[Hashable]) -> float | None:
    if len(gold) != len(pred):
        raise ValueError("gold and pred differ in length")
    if not gold:
        return None
    return sum(g == p for g, p in zip(gold, pred, strict=True)) / len(gold)


def macro_f1(gold: Sequence[Hashable], pred: Sequence[Hashable],
             labels: Sequence[Hashable] | None = None) -> float | None:
    """Unweighted mean of per-class F1.

    By default the classes are the ones that occur in `gold` (a class that is only predicted
    still costs precision on the gold classes, because those predictions are false negatives
    for them). A class with no gold and no predicted items is skipped.
    """
    if len(gold) != len(pred):
        raise ValueError("gold and pred differ in length")
    if not gold:
        return None
    classes = list(dict.fromkeys(labels if labels is not None else gold))
    scores = []
    for c in classes:
        tp = sum(g == c and p == c for g, p in zip(gold, pred, strict=True))
        fp = sum(g != c and p == c for g, p in zip(gold, pred, strict=True))
        fn = sum(g == c and p != c for g, p in zip(gold, pred, strict=True))
        if tp + fp + fn == 0:
            continue
        scores.append(2 * tp / (2 * tp + fp + fn))
    return sum(scores) / len(scores) if scores else None


def brier_multiclass(probs: Sequence[dict[str, float]], gold: Sequence[str]) -> float | None:
    """Mean over items of sum_k (p_k - y_k)^2. Range 0 (perfect) to 2. A missing key counts as p=0."""
    if len(probs) != len(gold):
        raise ValueError("probs and gold differ in length")
    if not probs:
        return None
    total = 0.0
    for p, g in zip(probs, gold, strict=True):
        keys = set(p) | {g}
        total += sum((float(p.get(k, 0.0)) - (1.0 if k == g else 0.0)) ** 2 for k in keys)
    return total / len(probs)


def brier_top(conf: Sequence[float], correct: Sequence[bool]) -> float | None:
    """Top-label Brier: mean of (p_top - 1[top is correct])^2. Range 0 to 1."""
    if len(conf) != len(correct):
        raise ValueError("conf and correct differ in length")
    if not conf:
        return None
    return sum((c - float(ok)) ** 2 for c, ok in zip(conf, correct, strict=True)) / len(conf)


def reliability_bins(conf: Sequence[float], correct: Sequence[bool], n_bins: int = 10) -> list[dict]:
    """Equal-width bins on [0, 1]. Bin i holds i/n <= p < (i+1)/n; p = 1.0 goes in the last bin."""
    if len(conf) != len(correct):
        raise ValueError("conf and correct differ in length")
    bins: list[dict] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        members = [(c, ok) for c, ok in zip(conf, correct, strict=True)
                   if (lo <= c < hi) or (i == n_bins - 1 and c == hi)]
        n = len(members)
        bins.append({
            "lo": round(lo, 2), "hi": round(hi, 2), "n": n,
            "meanConfidence": sum(c for c, _ in members) / n if n else None,
            "accuracy": sum(ok for _, ok in members) / n if n else None,
        })
    return bins


def ece(bins: list[dict]) -> float | None:
    """Expected calibration error: size-weighted mean |accuracy - mean confidence| over bins."""
    total = sum(b["n"] for b in bins)
    if not total:
        return None
    return sum(b["n"] * abs(b["accuracy"] - b["meanConfidence"]) for b in bins if b["n"]) / total


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (numpy's default method). q in [0, 100]."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
