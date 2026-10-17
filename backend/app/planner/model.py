"""Damped-trend exponential smoothing (additive Holt with damping), in plain Python.

Why this model: one year of data, no seasonality to learn, launches and declines. Damping
stops a trend from running away. Parameters are chosen by grid search on one-step error.
The range uses the spread of recent one-step errors, widened with the horizon.

In a product's first weeks the model has too few errors to know its own spread (the backtest
found that only about half of those weeks fell inside the range). For those weeks the range
comes from split conformal prediction instead: the 90% quantile of the relative errors that
earlier product launches had in their first weeks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ALPHAS = (0.1, 0.2, 0.3, 0.5, 0.7, 0.9)
BETAS = (0.0, 0.05, 0.1, 0.2)
PHIS = (0.8, 0.9, 0.98)
Z_RANGE = 1.645  # 90% range: a week outside it is a "miss"
MIN_HISTORY = 3
RESID_WINDOW = 12
LAUNCH_WEEKS = 6  # forecast weeks after a launch that use the launch range
RANGE_LEVEL = 0.90  # the same 90% as Z_RANGE
MIN_LAUNCH_SCORES = 3  # fewer earlier launch errors than this: keep the normal range


@dataclass
class Fit:
    level: float
    trend: float
    alpha: float
    beta: float
    phi: float
    sigma: float
    n: int

    def mean(self, h: int) -> float:
        damp = sum(self.phi ** i for i in range(1, h + 1))
        return max(0.0, self.level + damp * self.trend)

    def range(self, h: int, z: float = Z_RANGE) -> tuple[float, float]:
        m = self.mean(h)
        half = z * self.sigma * math.sqrt(h)
        return max(0.0, m - half), m + half


def _run(y: list[float], alpha: float, beta: float, phi: float) -> tuple[float, float, list[float]]:
    level = y[0]
    trend = y[1] - y[0] if len(y) > 1 else 0.0
    resid = []
    for t in range(1, len(y)):
        pred = level + phi * trend
        resid.append(y[t] - pred)
        new_level = alpha * y[t] + (1 - alpha) * (level + phi * trend)
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    return level, trend, resid


def fit(y: list[float]) -> Fit:
    """Fit on a series that starts at the product's first sale."""
    if not y:
        return Fit(0.0, 0.0, 0.5, 0.0, 0.9, 1.0, 0)
    if len(y) < MIN_HISTORY:
        mean = sum(y) / len(y)
        return Fit(mean, 0.0, 0.5, 0.0, 0.9, max(1.0, 0.5 * mean), len(y))
    best = None
    for a in ALPHAS:
        for b in BETAS:
            for p in PHIS:
                level, trend, resid = _run(y, a, b, p)
                sse = sum(r * r for r in resid[1:])  # skip the start-up error
                if best is None or sse < best[0]:
                    best = (sse, a, b, p, level, trend, resid)
    _, a, b, p, level, trend, resid = best
    recent = resid[-RESID_WINDOW:]
    sigma = math.sqrt(sum(r * r for r in recent) / len(recent)) if recent else 1.0
    sigma = max(sigma, 0.1 * max(level, 1.0), 1.0)
    return Fit(level, trend, a, b, p, sigma, len(y))


def conformal_quantile(scores: list[float], level: float = RANGE_LEVEL) -> float:
    """Split conformal quantile: the ceil((n + 1) * level)-th smallest score (the largest if n is small)."""
    ordered = sorted(scores)
    k = math.ceil((len(ordered) + 1) * level)
    return ordered[min(k, len(ordered)) - 1]


def launch_range(mean: float, low: float, high: float, launch_errors: list[float]) -> tuple[float, float]:
    """Widen a launch week's range to the conformal quantile of earlier launches' relative errors.

    The range never gets narrower than the normal one."""
    if len(launch_errors) < MIN_LAUNCH_SCORES:
        return low, high
    half = conformal_quantile(launch_errors) * max(mean, 1.0)
    return min(low, max(0.0, mean - half)), max(high, mean + half)


def wape(actual: list[float], forecast: list[float]) -> float | None:
    total = sum(actual)
    return sum(abs(a - f) for a, f in zip(actual, forecast, strict=True)) / total if total else None


def bias(actual: list[float], forecast: list[float]) -> float | None:
    total = sum(actual)
    return sum(f - a for a, f in zip(actual, forecast, strict=True)) / total if total else None
