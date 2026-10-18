"""decide(): the one place where the system asks for a judgment.

Cascade (spec §6):
  1. Ask Jev.
  2. Safety stakes: never escalate. If Jev fails, return the caller's safe default.
  3. Confidence >= act band: act on Jev's answer.
  4. Confidence in the confirm band: ask Step 5 Preview the same question.
  5. Lower: return Jev's answer marked "unsure"; the caller flags it.
Every call is logged to the `decisions` table (the Intuit evaluation data).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from app.config import Settings
from app.db import Database, stable_hash
from app.jev import JevClient, JevError, Question, confidence
from app.llm import LLMError, Step5Client
from app.observability import outcome, span

Band = Literal["act", "confirm", "unsure", "safe_default"]


class Stakes(StrEnum):
    safety = "safety"
    normal = "normal"


@dataclass
class Decision:
    answers: dict[str, Any]
    band: Band
    escalated: bool = False
    source: str = "jev"  # jev | cache | stub | step5 | default
    confidence: float | None = None
    jev_answers: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


class Decider:
    def __init__(self, settings: Settings, db: Database, jev: JevClient, llm: Step5Client):
        self.settings = settings
        self.db = db
        self.jev = jev
        self.llm = llm

    async def decide(
        self,
        j_id: str,
        state: Any,
        questions: dict[str, Question],
        *,
        stakes: Stakes = Stakes.normal,
        primary: list[str] | None = None,
        safe_default: dict[str, Any] | None = None,
        job_id: str | None = None,
        act: float | None = None,
        confirm: float | None = None,
    ) -> Decision:
        # One span per judgment: the model calls are its children, and its result shows which
        # decisions are unsure or escalate (the cases to add to the eval).
        with span("decide", f"decide {j_id}", **{"decision.id": j_id, "decision.stakes": stakes.value,
                                                   "decision.questions": len(questions)}) as s:
            decision = await self._decide(j_id, state, questions, stakes=stakes, primary=primary,
                                          safe_default=safe_default, job_id=job_id, act=act, confirm=confirm)
            outcome(s, "ok" if decision.band in ("act", "confirm") else "unknown", **{
                "decision.band": decision.band, "decision.confidence": decision.confidence,
                "decision.escalated": decision.escalated, "decision.source": decision.source,
                "decision.notes": "; ".join(decision.notes) or None})
            return decision

    async def _decide(
        self,
        j_id: str,
        state: Any,
        questions: dict[str, Question],
        *,
        stakes: Stakes,
        primary: list[str] | None,
        safe_default: dict[str, Any] | None,
        job_id: str | None,
        act: float | None,
        confirm: float | None,
    ) -> Decision:
        th = self.settings.thresholds
        act = th.act_band if act is None else act
        confirm = th.confirm_band if confirm is None else confirm
        safety = stakes is Stakes.safety
        timeout = self.settings.jev_safety_timeout_s if safety else self.settings.jev_timeout_s
        log = {
            "job_id": job_id,
            "j_id": j_id,
            "state_hash": stable_hash(state),
            "questions": questions,
        }

        try:
            res = await self.jev.ask(j_id, state, questions, timeout=timeout)
        except JevError as exc:
            if safety:
                self.db.log_decision(**log, band="safe_default", escalated=0, final=safe_default)
                return Decision(safe_default or {}, "safe_default", source="default", notes=[str(exc)])
            decision = await self._escalate(state, questions, None, reason=str(exc))
            self._log(log, None, None, decision)
            return decision

        conf = min(confidence(res.answers[k]) for k in (primary or list(questions)))
        if safety or conf >= act:
            decision = Decision(res.answers, "act" if conf >= act else "confirm", source=res.source,
                                confidence=conf, jev_answers=res.answers)
        elif conf >= confirm:
            decision = await self._escalate(state, questions, res.answers, reason="confirm band",
                                            jev_source=res.source)
            decision.confidence = conf
        else:
            decision = Decision(res.answers, "unsure", source=res.source, confidence=conf,
                                jev_answers=res.answers)
        self._log(log, res, conf, decision)
        return decision

    async def _escalate(
        self, state: Any, questions: dict[str, Question], jev_answers: dict | None, reason: str,
        jev_source: str = "jev",
    ) -> Decision:
        try:
            answers = await self.llm.answer_questions(state, questions)
        except (LLMError, ValueError, TypeError) as exc:
            if jev_answers is not None:
                return Decision(jev_answers, "unsure", source=jev_source, jev_answers=jev_answers,
                                notes=[reason, f"step5 failed: {exc}"])
            return Decision({}, "unsure", source="default", notes=[reason, f"step5 failed: {exc}"])
        latency = answers.pop("_latency_ms", None)
        decision = Decision(answers, "confirm", escalated=True, source="step5",
                            jev_answers=jev_answers, notes=[reason])
        decision.notes.append(f"step5_latency_ms={latency}")
        return decision

    def _log(self, base: dict, res: Any, conf: float | None, decision: Decision) -> None:
        self.db.log_decision(
            **base,
            jev_answer=res.answers if res else None,
            jev_source=res.source if res else None,
            jev_latency_ms=res.latency_ms if res else None,
            jev_tokens=res.input_tokens if res else None,
            band=decision.band,
            escalated=int(decision.escalated),
            step5_answer=decision.answers if decision.escalated else None,
            final=decision.answers,
        )
