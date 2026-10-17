"""The three methods under test. Each one gets the SAME state and questions.

- jev:   `app.jev.JevClient.ask` with the live API. No key -> NotRun (never the stub).
- step5: `app.llm.Step5Client.answer_questions` (Step 5 Preview through OpenRouter). No key -> NotRun.
- rules: the keyword rules in `app.stub_rules` (what fixture mode uses), through `StubJev`.

Every pass (normal order, reversed, shuffled) gets a NEW empty database. Reason:
`app.db.stable_hash` sorts dict keys, so the cache key of a Choice does not change when the
option order changes. With one shared cache, a shuffled pass would get the cached answer of
the first pass, and the order-stability test would be fake.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import DEPENDENCIES, RuntimeModes, Settings
from app.db import Database
from app.jev import JevClient, JevError, StubJev
from app.llm import LLMError, Step5Client

NOT_RUN_NO_KEY = "not run (no key)"
JEV_USD_PER_M_INPUT = 0.042  # company-reported price (PROJECT_BRIEF §6.10); output tokens are free


class NotRun(Exception):
    """The method cannot run here (for example, no API key). The report says so."""


@dataclass
class Call:
    answers: dict[str, Any]
    latency_ms: float
    input_tokens: int | None = None
    error: str | None = None
    source: str | None = None


def _modes(jev: bool, llm: bool) -> RuntimeModes:
    modes: dict[str, Any] = {dep: "cached" for dep in DEPENDENCIES}
    modes["jev"] = "live" if jev else "cached"
    modes["llm"] = "live" if llm else "cached"
    return RuntimeModes(modes)


class Method:
    name = "base"
    has_probabilities = True
    probability_note = ""

    def __init__(self, settings: Settings, work_dir: Path | None = None):
        self.settings = settings
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="eval-"))
        self._pass = "main"

    def new_pass(self, tag: str) -> None:
        """Start a pass with a fresh, empty cache (see the module docstring)."""
        self._pass = tag

    def _db(self) -> Database:
        return Database(self.work_dir / f"{self.name}-{self._pass}.db")

    async def ask(self, j_id: str, state: Any, questions: dict[str, Any]) -> Call:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class RulesMethod(Method):
    name = "rules"
    probability_note = ("The rules return fixed constants (for example 0.9) as probabilities. "
                        "They are not estimates, so read their calibration as a reference only.")

    def __init__(self, settings: Settings, work_dir: Path | None = None):
        super().__init__(settings, work_dir)
        self.stub = StubJev()

    async def ask(self, j_id: str, state: Any, questions: dict[str, Any]) -> Call:
        t0 = time.perf_counter()
        answers = self.stub.answer(j_id, state, questions)
        return Call(answers, (time.perf_counter() - t0) * 1000, 0, source="rules")


class JevMethod(Method):
    name = "jev"

    def __init__(self, settings: Settings, work_dir: Path | None = None):
        if settings.jev_api_key is None:
            raise NotRun(NOT_RUN_NO_KEY + ": set JEV_API_KEY")
        super().__init__(settings, work_dir)
        self._client: JevClient | None = None

    def new_pass(self, tag: str) -> None:
        super().new_pass(tag)
        self._client = None

    def _jev(self) -> JevClient:
        if self._client is None:
            self._client = JevClient(self.settings, self._db(), _modes(jev=True, llm=False))
            if not self._client.live:
                raise NotRun("JevClient is not live")
        return self._client

    async def ask(self, j_id: str, state: Any, questions: dict[str, Any]) -> Call:
        client = self._jev()
        t0 = time.perf_counter()
        try:
            res = await client.ask(j_id, state, questions, timeout=self.settings.jev_timeout_s)
        except JevError as exc:
            return Call({}, (time.perf_counter() - t0) * 1000, None, error=str(exc))
        if res.source != "jev":
            # Should not happen with a fresh cache. Never report a stub or cache answer as Jev.
            return Call({}, 0.0, None, error=f"unexpected source {res.source}")
        return Call(res.answers, res.latency_ms, res.input_tokens, source="jev")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class Step5Method(Method):
    name = "step5"
    has_probabilities = False
    probability_note = "Step 5 Preview returns one option with no probability, so calibration is N/A."

    def __init__(self, settings: Settings, work_dir: Path | None = None):
        if settings.openrouter_api_key is None:
            raise NotRun(NOT_RUN_NO_KEY + ": set OPENROUTER_API_KEY")
        super().__init__(settings, work_dir)
        self._client: Step5Client | None = None

    def new_pass(self, tag: str) -> None:
        super().new_pass(tag)
        self._client = None

    def _llm(self) -> Step5Client:
        if self._client is None:
            self._client = Step5Client(self.settings, self._db(), _modes(jev=False, llm=True))
            if not self._client.live:
                raise NotRun("Step5Client is not live")
        return self._client

    async def ask(self, j_id: str, state: Any, questions: dict[str, Any]) -> Call:
        client = self._llm()
        t0 = time.perf_counter()
        try:
            answers = await client.answer_questions(state, questions)
        except (LLMError, ValueError, TypeError) as exc:
            return Call({}, (time.perf_counter() - t0) * 1000, None, error=str(exc)[:200])
        answers.pop("_latency_ms", None)
        # Step5Client.chat() drops the OpenRouter `usage` field, so tokens are unknown (None).
        return Call(answers, (time.perf_counter() - t0) * 1000, None, source="step5")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


METHODS: dict[str, type[Method]] = {"jev": JevMethod, "step5": Step5Method, "rules": RulesMethod}
