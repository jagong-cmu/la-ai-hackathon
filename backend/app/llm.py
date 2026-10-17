"""Step 5 Preview client (OpenRouter, OpenAI-compatible chat completions).

Step 5 Preview writes text and answers escalated questions. Temperature 0 and a cache
keyed by the input make it repeatable. Every public method has a template fallback,
so a Step 5 outage degrades the output and never stops the job.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from app.config import RuntimeModes, Settings
from app.contracts import SearchSpec, ShoppingItem
from app.db import Database, stable_hash
from app.observability import span


class LLMError(Exception):
    pass


JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_json_object(text: str) -> dict[str, Any]:
    match = JSON_BLOCK.search(text)
    if not match:
        raise LLMError("no JSON object in the response")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON: {exc}") from exc


class Step5Client:
    def __init__(self, settings: Settings, db: Database, modes: RuntimeModes):
        self.settings = settings
        self.db = db
        self.modes = modes
        self._sem = asyncio.Semaphore(settings.llm_concurrency)
        self._http: httpx.AsyncClient | None = None

    @property
    def live(self) -> bool:
        return self.modes.get("llm") == "live" and self.settings.openrouter_api_key is not None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self.settings.openrouter_base_url, timeout=None)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def chat(self, purpose: str, system: str, user: str, max_tokens: int = 600) -> str:
        key = "llm:" + stable_hash(self.settings.step5_model, system, user, max_tokens)
        cached = self.db.cache_get(key)
        if cached is not None:
            return cached
        if not self.live:
            raise LLMError("step-5-preview is not live (no key or cached mode)")
        body = {
            "model": self.settings.step5_model,
            "temperature": 0,
            "max_tokens": max(max_tokens, self.settings.llm_min_max_tokens),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key.get_secret_value()}"
        }
        last_error: Exception | None = None
        with span("llm", purpose, model=self.settings.step5_model):
            for attempt in range(3):
                try:
                    async with self._sem:
                        resp = await asyncio.wait_for(
                            self._client().post("/chat/completions", json=body, headers=headers),
                            timeout=self.settings.llm_timeout_s,
                        )
                    if resp.status_code in (429, 500, 502, 503, 529) and attempt < 2:
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    resp.raise_for_status()
                    choice = resp.json()["choices"][0]
                    text = choice["message"]["content"] or ""
                    if not text.strip():
                        # Do not cache it: the reasoning used all the tokens (finish_reason "length").
                        raise LLMError(f"{purpose}: empty reply (finish_reason={choice.get('finish_reason')})")
                    self.db.cache_set(key, text)
                    return text
                except (TimeoutError, httpx.HTTPError, KeyError, IndexError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(0.5 * 2**attempt)
        raise LLMError(f"{purpose}: {last_error}")

    # ------------------------------------------------------------ tasks

    async def make_spec(self, item: ShoppingItem) -> SearchSpec:
        system = (
            "You turn a small business's past purchase line into a product search spec. "
            "Return only a JSON object with keys: query (short search text), attributes "
            "(object), mustHave (list of short phrases a listing must match), niceToHave "
            "(list), acceptablePackSizes (list of integers, in the item's unit)."
        )
        user = json.dumps({"name": item.name, "unit": item.unit, "quantity": item.quantity})
        try:
            data = parse_json_object(await self.chat("spec", system, user, max_tokens=300))
            return SearchSpec(
                item_id=item.id,
                query=str(data.get("query") or item.name),
                attributes=data.get("attributes") or {},
                must_have=[str(x) for x in data.get("mustHave") or []],
                nice_to_have=[str(x) for x in data.get("niceToHave") or []],
                unit=item.unit,
                acceptable_pack_sizes=[int(x) for x in data.get("acceptablePackSizes") or []],
            )
        except (LLMError, ValueError, TypeError):
            return template_spec(item)

    async def answer_questions(self, state: Any, questions: dict[str, dict]) -> dict[str, Any]:
        """Escalation path for decide(): same state, same questions, JSON answers."""
        system = (
            "Answer each question about STATE. Return only a JSON object that maps each "
            "question key to an answer. For type noul: a probability from 0 to 1 that the "
            "answer is yes. For type choice: exactly one option name from criteria. For type "
            "score: the integer index of the best level in criteria (0 = first)."
        )
        user = json.dumps({"STATE": state, "QUESTIONS": questions}, default=str)
        t0 = time.monotonic()
        raw = parse_json_object(await self.chat("escalate", system, user, max_tokens=400))
        answers: dict[str, Any] = {}
        for key, q in questions.items():
            if key not in raw:
                raise LLMError(f"missing answer for {key}")
            value = raw[key]
            if q["type"] == "noul":
                answers[key] = {"type": "noul", "noul": min(1.0, max(0.0, float(value)))}
            elif q["type"] == "choice":
                if value not in q["criteria"]:
                    raise LLMError(f"{key}: {value!r} is not an option")
                answers[key] = {"type": "choice", "choice": value, "probabilities": {value: 1.0},
                                "calibrated": False}  # not a real probability
            else:
                idx = int(value)
                answers[key] = {"type": "score", "score": idx, "probabilities": {str(idx): 1.0},
                                "calibrated": False}
        answers["_latency_ms"] = (time.monotonic() - t0) * 1000
        return answers

    async def summarize(self, facts: dict[str, Any]) -> str:
        system = (
            "You write one short paragraph (at most 70 words) for a busy business owner. "
            "Explain the recommended purchase plan and why, using only the facts given. "
            "Do not invent numbers."
        )
        try:
            return (await self.chat("summary", system, json.dumps(facts, default=str))).strip()
        except LLMError:
            return template_summary(facts)

    async def ping(self) -> float:
        t0 = time.monotonic()
        await self.chat("ping", "Reply with OK.", f"ping {time.time()}", max_tokens=5)
        return (time.monotonic() - t0) * 1000


def template_spec(item: ShoppingItem) -> SearchSpec:
    return SearchSpec(item_id=item.id, query=item.name, unit=item.unit, spec_fallback=True)


def template_summary(facts: dict[str, Any]) -> str:
    plan = facts.get("recommended")
    if not plan:
        return facts.get("noRecommendationReason") or "No plan could be recommended."
    text = f"Recommended: {plan['label']} for ${plan['total']:.2f}"
    saving = facts.get("savingsVsPaidBefore")
    if saving is not None and saving >= 0.005:
        text += f", ${saving:.2f} less than you paid last time"
    elif saving is not None and saving <= -0.005:
        text += f", ${-saving:.2f} more than you paid last time"
    return text + "."
