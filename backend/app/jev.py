"""Jev (TypeSafe System One) client.

- HTTP API: POST {base}/v1/systemone with {state, model, questions}. Output tokens are free.
- Every answer is cached by (model, state, questions), so replays are exact.
- A semaphore limits parallel calls (the cookbook saw rate limits above ~8).
- When the mode is `cached` or no key is set, a cache miss goes to StubJev: deterministic
  rules for tests and fixture mode. Stub answers are never cached.

Known weak points (docs, jev-1.13): no arithmetic, no date math, first-option bias in
Choices, accuracy drops with long irrelevant state. Keep state small and filtered.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import RuntimeModes, Settings
from app.db import Database, stable_hash
from app.observability import span

Question = dict[str, Any]
Answers = dict[str, dict[str, Any]]


class JevError(Exception):
    pass


# ---------------------------------------------------------------- question builders


def noul(instructions: Any, true: str | None = None, false: str | None = None) -> Question:
    q: Question = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def choice(instructions: Any, options: dict[str, Any]) -> Question:
    if not 1 <= len(options) <= 255:
        raise ValueError("a Choice needs 1 to 255 options")
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: Any, levels: list[Any]) -> Question:
    if not 2 <= len(levels) <= 10:
        raise ValueError("a Score needs 2 to 10 levels")
    return {"type": "score", "instructions": instructions, "criteria": levels}


def confidence(answer: dict[str, Any]) -> float:
    """Noul has no confidence field; |2p - 1| is the docs' suggested equivalent."""
    if answer.get("type") == "noul":
        return abs(2 * float(answer["noul"]) - 1)
    value = answer.get("confidence")
    return float(value) if value is not None else 0.0


def choice_confidence(probs: dict[str, float]) -> float:
    n = len(probs)
    if n <= 1:
        return 1.0
    p_max = max(probs.values())
    return max(0.0, (p_max - 1 / n) / (1 - 1 / n))


@dataclass
class JevResult:
    answers: Answers
    latency_ms: float
    input_tokens: int
    source: Literal["jev", "cache", "stub"]


# ---------------------------------------------------------------- stub (fixture mode, tests)


def _default_answer(q: Question) -> dict[str, Any]:
    if q["type"] == "noul":
        return {"type": "noul", "noul": 0.9}
    if q["type"] == "choice":
        options = list(q["criteria"])
        return choice_answer(options, options[0], 0.9)
    levels = q["criteria"]
    top = len(levels) - 1
    probs = {str(i): (0.9 if i == top else 0.1 / top) for i in range(len(levels))}
    return {
        "type": "score",
        "score": sum(i * p for i, p in ((int(k), v) for k, v in probs.items())),
        "legend": {str(i): str(level) for i, level in enumerate(levels)},
        "probabilities": probs,
        "confidence": 0.85,
    }


def choice_answer(options: list[str], pick: str, p: float) -> dict[str, Any]:
    rest = (1 - p) / max(1, len(options) - 1)
    probs = {o: (p if o == pick else rest) for o in options}
    return {
        "type": "choice",
        "choice": pick,
        "probabilities": probs,
        "confidence": choice_confidence(probs),
    }


class StubJev:
    """Keyword rules per decision id. Register more with `rules[...] = fn`."""

    def __init__(self) -> None:
        from app import stub_rules

        self.rules = dict(stub_rules.RULES)

    def answer(self, purpose: str, state: Any, questions: dict[str, Question]) -> Answers:
        rule = self.rules.get(purpose)
        if rule is not None:
            answers = rule(state, questions)
            for key, q in questions.items():
                answers.setdefault(key, _default_answer(q))
            return answers
        return {key: _default_answer(q) for key, q in questions.items()}


# ---------------------------------------------------------------- client


class JevClient:
    def __init__(self, settings: Settings, db: Database, modes: RuntimeModes):
        self.settings = settings
        self.db = db
        self.modes = modes
        self.stub = StubJev()
        self._sem = asyncio.Semaphore(settings.jev_concurrency)
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.settings.jev_base_url, timeout=None)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    @property
    def live(self) -> bool:
        return self.modes.get("jev") == "live" and self.settings.jev_api_key is not None

    async def ask(
        self, purpose: str, state: Any, questions: dict[str, Question], *, timeout: float
    ) -> JevResult:
        # stable_hash sorts dict keys, so record each Choice's option ORDER explicitly: Jev has a
        # first-option bias, and a reordered question must not reuse a cached answer.
        order = {k: list(q["criteria"]) for k, q in questions.items() if isinstance(q.get("criteria"), dict)}
        key = "jev:" + stable_hash(self.settings.jev_model, state, questions, order)
        cached = self.db.cache_get(key)
        if cached is not None:
            return JevResult(cached["answers"], 0.0, cached.get("tokens", 0), "cache")
        if not self.live:
            return JevResult(self.stub.answer(purpose, state, questions), 0.0, 0, "stub")

        body = {"state": state, "model": self.settings.jev_model, "questions": questions}
        headers = {"Authorization": f"Bearer {self.settings.jev_api_key.get_secret_value()}"}
        deadline = time.monotonic() + timeout
        attempt = 0
        with span("jev", purpose, questions=len(questions)):
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JevError(f"{purpose}: timeout")
                try:
                    async with self._sem:
                        t0 = time.monotonic()  # latency excludes time waiting in the queue
                        resp = await asyncio.wait_for(
                            self._client().post("/v1/systemone", json=body, headers=headers),
                            timeout=remaining,
                        )
                except TimeoutError as exc:
                    raise JevError(f"{purpose}: timeout") from exc
                except httpx.HTTPError as exc:
                    raise JevError(f"{purpose}: {exc}") from exc
                if resp.status_code in (429, 529) and attempt < 2:
                    attempt += 1
                    wait = float(resp.headers.get("retry-after", 0.3 * 2**attempt))
                    await asyncio.sleep(min(wait + random.random() * 0.1, max(0.0, remaining)))
                    continue
                if resp.status_code != 200:
                    raise JevError(f"{purpose}: HTTP {resp.status_code} {resp.text[:200]}")
                data = resp.json()
                latency = (time.monotonic() - t0) * 1000
                tokens = int(data.get("usage", {}).get("input_tokens", 0))
                self.db.cache_set(key, {"answers": data["answers"], "tokens": tokens})
                return JevResult(data["answers"], latency, tokens, "jev")

    async def ping(self) -> float:
        """Latency in ms for one tiny live call (preflight)."""
        if not self.live:
            raise JevError("jev is not live (no key or cached mode)")
        t0 = time.monotonic()
        await self.ask(
            "ping",
            {"t": time.time()},  # unique state so the cache never answers
            {"ok": noul("Is this state a JSON object?")},
            timeout=self.settings.jev_timeout_s,
        )
        return (time.monotonic() - t0) * 1000
