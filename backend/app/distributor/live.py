"""Live distributor channel: SMS request -> (no quote) call -> SMS recap, over the Photon service.

The Photon service (`services/photon`, Node + TypeScript) owns the SMS number and the call
(Photon Voice / SIP -> ElevenLabs agent). This module talks to it over HTTP:

- `POST {PHOTON_SERVICE_URL}/sms   {jobId, to, text}`
- `POST {PHOTON_SERVICE_URL}/calls {jobId, to, openingContext: {bestTotal, items, businessName}}`

Inbound events come back through `/webhooks/{photon|elevenlabs}` and the orchestrator puts
them on `ctx.inbox` as `{"provider", "eventId", "jobId", "type", ...}` with `type` one of
`sms_in`, `call_started`, `transcript_line`, `call_ended`.

Pattern (spec §6): code finds the money candidates, Jev (J8/J9) selects or classifies, code
computes the offer. Every HTTP call has a timeout; every failure emits `dependency_degraded`
and falls back (no answer -> SMS-only quote). The channel sends to DEMO_DISTRIBUTOR_PHONE
only, never to `ctx.distributor.phone`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import re
import time
from typing import Any

import httpx
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import BACKEND_DIR, REPO_DIR
from app.contracts import Evidence, Product, SupplierOffers
from app.distributor.base import DistributorContext, allocate_total, call_scope, wait_for_call_start
from app.judgments import j8_reply, j9_call_line, j10_terms
from app.stages.extract import money_candidates

BUSINESS_NAME = "Hearthloaf Bakeries"
DISCLOSURE = f"Hi, this is an AI assistant for {BUSINESS_NAME}."


class LiveDistributorConfig(BaseSettings):
    """Module-local settings (repo-root `.env`). `app/config.py` stays unchanged."""

    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"), extra="ignore",
    )

    photon_service_url: str = "http://localhost:8787"
    photon_service_token: SecretStr | None = None  # optional shared token for the service routes
    demo_distributor_phone: str | None = None  # the ONLY number that this channel texts or calls
    photon_http_timeout_s: float = 10  # spec §10.2: Photon SMS 10 s per send
    # POST /calls returns only after ElevenLabs has set up the SIP call (up to the ringing time).
    photon_call_start_timeout_s: float = 50
    photon_sms_retries: int = 2
    # spec §10.2: 20 s to answer, 1 redial. The service sets ElevenLabs ringing_timeout_secs=20;
    # the extra seconds cover dialing and the service's poll interval.
    call_connect_timeout_s: float = 30
    call_redials: int = 1
    call_max_s: float = 300  # hard limit for one call, then the channel stops waiting
    post_call_grace_s: float = 20  # wait for the post-call transcript after a live hang-up
    offer_min_p: float = 0.5  # J9 P(new offer) at or above this, plus a code-found amount = an offer

    @field_validator("photon_service_token", "demo_distributor_phone", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


# ---------------------------------------------------------------- code helpers (no Jev here)

SPOKEN_MONEY = re.compile(r"(?<![\w$.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*(?:dollars|bucks|usd)\b", re.I)
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FREE_DELIVERY = re.compile(r"free (?:delivery|shipping)|deliver(?:ed|y)? (?:is |for )?free|no delivery (?:fee|charge)", re.I)
DELIVERY_FEE = re.compile(r"(?:delivery|shipping|freight)[^$.]{0,30}(\$\s?\d+(?:\.\d{1,2})?)", re.I)
LEAD_DAYS = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven)\s+(?:business\s+)?days?\b", re.I)
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


SPOKEN_CENTS = re.compile(r"(?<![\w$.])(\d{1,2})\s*cents?\b", re.I)
DOLLAR_AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
GENERIC_UNITS = {"each", "apiece", "unit", "piece", "item", "one"}
PACK_WORDS = {"case", "pack", "roll", "carton", "bundle", "sleeve", "pallet", "dozen", "ream"}
AFTER_AMOUNT = re.compile(r"^\s*(?:(each|apiece)\b|(?:per|a|an|/)\s*([a-z]+))", re.I)


def normalize_spoken_money(text: str) -> str:
    """'395 dollars' -> '$395' and '50 cents' -> '$0.50', so that the '$' parsers find them."""

    def repl(m: re.Match[str]) -> str:
        cents = f".{m.group(2)}" if m.group(2) else ""
        return f"${m.group(1)}{cents}"

    text = SPOKEN_MONEY.sub(repl, text)
    return SPOKEN_CENTS.sub(lambda m: f"$0.{int(m.group(1)):02d}", text)


def _singular(word: str) -> str:
    word = word.lower()
    if word.endswith("es") and word[:-2] in ("box",):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def spoken_price(text: str, anchor: float | None, unit: str | None) -> tuple[float, str] | None:
    """The offered amount in one supplier line and its basis: "total", "per_unit" or "per_pack".

    "50 cents a glove" and "$1.60 per box" (for an item sold by the box) are per unit, so code
    multiplies them by the requested quantity. "$40 a case" is per pack: the pack size is unknown.
    Our own anchor total is dropped when other amounts exist. Arithmetic stays in code."""
    norm = normalize_spoken_money(text)
    found: list[tuple[float, str]] = []
    for m in DOLLAR_AMOUNT.finditer(norm):
        value = float(m.group(1).replace(",", "") + (f".{m.group(2)}" if m.group(2) else ""))
        if value <= 0:
            continue
        basis = "total"
        after = AFTER_AMOUNT.match(norm[m.end():m.end() + 24])
        if after:
            word = _singular(after.group(1) or after.group(2) or "")
            if word in GENERIC_UNITS or (unit and word == _singular(unit)):
                basis = "per_unit"
            elif word in PACK_WORDS or word in ("box", "bag"):
                basis = "per_pack"  # a box of gloves, a bag of liners: a pack, not one unit
        found.append((value, basis))
    if not found:
        return None
    if anchor is not None:
        others = [f for f in found if abs(f[0] - anchor) > 0.01]
        found = others or found
    return max(found, key=lambda f: f[0])


def select_price(text: str, anchor: float | None) -> float | None:
    """Pick the offered amount in one line: drop our own anchor total when other amounts exist,
    then take the largest amount (a total or item price is larger than a fee in the same line)."""
    values = [m.value for m in money_candidates(normalize_spoken_money(text)) if m.value > 0]
    if not values:
        return None
    if anchor is not None:
        others = [v for v in values if abs(v - anchor) > 0.01]
        values = others or values
    return max(values)


def delivery_fee(text: str) -> float | None:
    if FREE_DELIVERY.search(text):
        return 0.0
    m = DELIVERY_FEE.search(normalize_spoken_money(text))
    return float(m.group(1).replace("$", "").strip()) if m else None


def lead_time_days(text: str, today: dt.date | None = None) -> int | None:
    m = LEAD_DAYS.search(text)
    if m:
        word = m.group(1).lower()
        return WORD_NUM.get(word) or int(word)
    low = text.lower()
    if "tomorrow" in low:
        return 1
    today = today or dt.date.today()
    for i, day in enumerate(WEEKDAYS):
        if re.search(rf"\b{day}\b", low):
            return (i - today.weekday()) % 7 or 7
    return None


def _choice(decision_answers: dict[str, Any], key: str) -> str | None:
    value = decision_answers.get(key) or {}
    return value.get("choice") if isinstance(value, dict) else None


def _noul(decision_answers: dict[str, Any], key: str) -> float:
    value = decision_answers.get(key) or {}
    try:
        return float(value.get("noul", 0.0)) if isinstance(value, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- the channel


class _CallOffer:
    """The offer that grows during one call (per-item prices and/or a whole-list total)."""

    def __init__(self) -> None:
        self.item_prices: dict[str, float] = {}
        self.bundle_total: float | None = None
        self.delivery_fee: float | None = None
        self.lead_days: int | None = None
        self.supplier_lines: list[str] = []

    @property
    def empty(self) -> bool:
        return not self.item_prices and self.bundle_total is None


class LiveDistributor:
    _line_costs: dict[str, float] = {}
    _scoped: bool = False

    def __init__(self, config: LiveDistributorConfig | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.cfg = config or LiveDistributorConfig()
        self._transport = transport
        self._seen: set[str] = set()

    # ------------------------------------------------------------ HTTP to the Photon service

    async def _post(self, path: str, body: dict[str, Any], retries: int,
                    timeout_s: float | None = None) -> dict[str, Any]:
        timeout_s = timeout_s or self.cfg.photon_http_timeout_s
        headers = {}
        if self.cfg.photon_service_token is not None:
            headers["X-Service-Token"] = self.cfg.photon_service_token.get_secret_value()
        last: Exception | None = None
        async with httpx.AsyncClient(base_url=self.cfg.photon_service_url, transport=self._transport,
                                     timeout=timeout_s) as client:
            for attempt in range(retries + 1):
                try:
                    resp = await asyncio.wait_for(client.post(path, json=body, headers=headers),
                                                  timeout=timeout_s)
                    if resp.status_code >= 500 and attempt < retries:
                        last = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request,
                                                     response=resp)
                        await asyncio.sleep(0.3 * 2**attempt)
                        continue
                    resp.raise_for_status()
                    return resp.json() if resp.content else {}
                except (TimeoutError, httpx.HTTPError, ValueError) as exc:
                    last = exc
                    if attempt < retries:
                        await asyncio.sleep(0.3 * 2**attempt)
        raise RuntimeError(f"photon {path}: {type(last).__name__ if last else 'error'}")

    async def _send_sms(self, ctx: DistributorContext, to: str, text: str) -> bool:
        try:
            await self._post("/sms", {"jobId": ctx.job_id, "to": to, "text": text},
                             retries=self.cfg.photon_sms_retries)
        except RuntimeError as exc:
            await ctx.emit("dependency_degraded", {"dep": "phone", "reason": "sms_failed", "detail": str(exc)})
            return False
        await ctx.emit("message_sent", {"channel": "sms", "to": ctx.distributor.name, "text": text})
        return True

    # ------------------------------------------------------------ inbox

    async def _next_event(self, ctx: DistributorContext, deadline: float) -> dict[str, Any] | None:
        """The next new inbox event before `deadline` (loop time), or None at the deadline."""
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                event = await asyncio.wait_for(ctx.inbox.get(), timeout=remaining)
            except TimeoutError:
                return None
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("eventId") or "")
            if event_id:
                if event_id in self._seen:
                    continue
                self._seen.add(event_id)
            return event

    # ------------------------------------------------------------ SMS request + J8

    async def _draft_request(self, ctx: DistributorContext) -> str:
        items = "; ".join(f"{i.quantity} {i.unit} {i.name}" for i in ctx.items)
        template = (f"{DISCLOSURE} Could you quote these supplies for delivery: {items}? "
                    "Please include the delivery fee and lead time. We are not placing an order yet.")
        llm = getattr(ctx.decider, "llm", None)
        if llm is None:
            return template
        system = (f"You write one short SMS for {BUSINESS_NAME}, a bakery, to its supply distributor. "
                  f"The first sentence must be exactly: '{DISCLOSURE}'. Ask for a price for each item, "
                  "the delivery fee and the lead time. Do not agree to buy. Do not mention payment. "
                  "Under 400 characters. Return only the SMS text.")
        try:
            text = await asyncio.wait_for(llm.chat("distributor_sms", system, items, max_tokens=200),
                                          timeout=ctx.settings.llm_timeout_s)
        except Exception:  # LLMError, timeout, not live: the template is the fallback
            return template
        text = " ".join(str(text).split()).strip().strip('"')
        if not text:
            return template
        if not text.startswith(DISCLOSURE):  # code enforces the disclosure, whatever the draft says
            text = f"{DISCLOSURE} {text}"
        if re.search(r"card|payment|pay now|credit", text, re.I):
            return template
        return text[:480]

    async def _handle_sms_reply(self, ctx: DistributorContext, event: dict[str, Any]) -> tuple[str, SupplierOffers | None]:
        text = str(event.get("text") or "")
        names = {i.id: i.name for i in ctx.items}
        money = money_candidates(normalize_spoken_money(text))
        decision = await j8_reply(ctx.decider, text, names, money, job_id=ctx.job_id)
        intent = _choice(decision.answers, "intent") or "unrelated"
        await ctx.emit("reply_received", {"channel": "sms", "intent": intent, "text": text})
        if intent != "quote" or decision.band == "unsure":
            return intent, None
        products = []
        for item in ctx.items:
            pick = _choice(decision.answers, f"price_{item.id}")
            if not pick or not pick.startswith("c"):
                continue
            idx = int(pick[1:]) if pick[1:].isdigit() else -1
            if 0 <= idx < len(money):
                products.append(Product(item_id=item.id, title=f"{item.name} (SMS quote)", sku=f"DIST-{item.id}",
                                        raw_listing=text[:500], pack_price=money[idx].value,
                                        units_per_pack=item.quantity))
        if not products:
            return intent, None
        offers = SupplierOffers(
            supplier_id=ctx.distributor.id, source="sms",
            status="ok" if len(products) == len(ctx.items) else "partial", products=products,
            quoted_delivery_fee=delivery_fee(text), quoted_lead_time_days=lead_time_days(text),
            evidence=Evidence(message_ids=[str(event.get("messageId") or event.get("eventId") or "")]),
        )
        offers.jev_terms = await j10_terms(ctx.decider, text, job_id=ctx.job_id)
        await ctx.on_offer(offers)
        return intent, offers

    # ------------------------------------------------------------ the call

    async def _start_call(self, ctx: DistributorContext, to: str, best_total: float) -> str | None:
        body = {
            "jobId": ctx.job_id, "to": to,
            "openingContext": {
                "bestTotal": round(best_total, 2),
                "items": [{"id": i.id, "name": i.name, "quantity": i.quantity, "unit": i.unit} for i in ctx.items],
                "businessName": BUSINESS_NAME,
            },
        }
        try:
            data = await self._post("/calls", body, retries=0, timeout_s=self.cfg.photon_call_start_timeout_s)
        except RuntimeError as exc:
            await ctx.emit("dependency_degraded", {"dep": "phone", "reason": "call_failed", "detail": str(exc)})
            return None
        return str(data.get("callId") or "") or "call"

    def _offers_from(self, ctx: DistributorContext, call: _CallOffer, transcript_url: str | None) -> SupplierOffers:
        names = {i.id: i for i in ctx.items}
        prices = dict(call.item_prices)
        bundle = call.bundle_total
        if bundle is not None and self._scoped:
            # The total covers only the call's items, not the whole list: split it across them in
            # proportion to their web line costs, so the optimizer prices those items, not the list.
            prices = allocate_total(bundle, {i: self._line_costs.get(i, 0.0) for i in names})
            bundle = None
        products = [Product(item_id=item_id, title=f"{names[item_id].name} (phone quote"
                            + (", split from a total)" if call.bundle_total is not None and self._scoped else ")"),
                            sku=f"DIST-{item_id}", pack_price=price, units_per_pack=names[item_id].quantity)
                    for item_id, price in prices.items() if item_id in names]
        complete = bundle is not None or len(products) == len(ctx.items)
        return SupplierOffers(
            supplier_id=ctx.distributor.id, source="call", status="ok" if complete else "partial",
            products=products, bundle_total=bundle,
            quoted_delivery_fee=call.delivery_fee, quoted_lead_time_days=call.lead_days,
            evidence=Evidence(transcript_url=transcript_url),
        )

    async def _on_supplier_line(self, ctx: DistributorContext, text: str, transcript: list[str],
                                call: _CallOffer, best_total: float, line_costs: dict[str, float]) -> None:
        names = {i.id: i.name for i in ctx.items}
        call.supplier_lines.append(text)
        fee = delivery_fee(text)
        if fee is not None:
            call.delivery_fee = fee
        days = lead_time_days(text)
        if days is not None:
            call.lead_days = days
        decision = await j9_call_line(ctx.decider, text, transcript, names, job_id=ctx.job_id)
        p_offer = _noul(decision.answers, "new_offer")
        target = _choice(decision.answers, "offer_for") or "whole_list"
        item_id = target[len("item_"):] if target.startswith("item_") else None
        if item_id is not None and item_id not in names:
            item_id = None
        item = next((i for i in ctx.items if i.id == item_id), None)
        parsed = spoken_price(text, best_total, item.unit if item else None)
        if parsed is None or p_offer < self.cfg.offer_min_p:
            return
        price, basis = parsed
        unit_price = None
        if basis == "per_unit" and item is not None:
            unit_price, price = price, round(price * item.quantity, 2)  # code does the arithmetic
        unconfirmed = None
        if decision.band == "unsure":  # spec §6 J9 fallback: show it, do not re-plan on it
            unconfirmed = "j9_unsure"
        elif basis == "per_pack":
            unconfirmed = "pack_size_unknown"
        elif basis == "per_unit" and item is None:
            unconfirmed = "unit_price_without_item"
        if unconfirmed:
            await ctx.emit("call_offer", {"itemId": item_id, "price": price, "basis": basis, "p": p_offer,
                                          "confirmed": False, "reason": unconfirmed})
            return
        if item_id is None:
            call.bundle_total = price
            total: float | None = price
            beats = price < best_total
        else:
            call.item_prices[item_id] = price
            total = round(sum(call.item_prices.values()), 2) if len(call.item_prices) == len(names) else None
            beats = price < line_costs.get(item_id, float("inf"))
        await ctx.emit("call_offer", {"itemId": item_id, "price": price, "unitPrice": unit_price, "basis": basis,
                                      "total": total, "p": p_offer, "beats": beats, "confirmed": True})
        await ctx.on_offer(self._offers_from(ctx, call, None))

    def _write_transcript(self, ctx: DistributorContext, transcript: list[str]) -> str | None:
        try:
            path = ctx.settings.evidence_dir / ctx.job_id / "distributor" / "call.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(transcript) + "\n")
            return f"/evidence/{ctx.job_id}/distributor/call.txt"
        except OSError:
            return None

    async def _run_call(self, ctx: DistributorContext, to: str, best_total: float,
                        line_costs: dict[str, float], sms_offers: SupplierOffers | None) -> SupplierOffers | None:
        loop = asyncio.get_running_loop()
        self._line_costs = line_costs
        self._scoped = len(ctx.items) < len(line_costs)
        call_id: str | None = None
        connected = False
        for _attempt in range(self.cfg.call_redials + 1):
            call_id = await self._start_call(ctx, to, best_total)
            if call_id is None:
                return sms_offers
            deadline = loop.time() + self.cfg.call_connect_timeout_s
            while not connected:
                event = await self._next_event(ctx, deadline)
                if event is None:
                    break
                etype = event.get("type")
                if etype == "sms_in":
                    await self._handle_sms_reply(ctx, event)
                elif etype == "call_started" and event.get("callId", call_id) in (call_id, None, ""):
                    connected = True
                elif etype == "call_ended" and event.get("callId", call_id) in (call_id, None, ""):
                    break  # failed / busy / no answer before connect
            if connected:
                break
        if not connected:
            await ctx.emit("call_ended", {"outcome": "no_answer"})
            await ctx.emit("dependency_degraded", {"dep": "phone", "reason": "no_answer"})
            return sms_offers  # SMS-only quote (spec §10.2)

        await ctx.emit("call_started", {"to": ctx.distributor.name, "disclosure": "AI disclosed"})
        transcript: list[str] = []
        call = _CallOffer()
        seen_lines: set[Any] = set()
        shown_live = False  # the agent's live-log tool already put this call's lines on screen

        async def line(speaker: str, text: str, key: Any, show: bool = True) -> None:
            if key in seen_lines or not text.strip():
                return
            seen_lines.add(key)
            speaker = "supplier" if speaker in ("supplier", "user") else "agent"
            transcript.append(f"{speaker}: {text}")
            if show:
                await ctx.emit("transcript_line", {"speaker": speaker, "text": text})
            if speaker == "supplier":
                await self._on_supplier_line(ctx, text, transcript, call, best_total, line_costs)

        outcome = "timeout"
        deadline = loop.time() + self.cfg.call_max_s
        while True:
            event = await self._next_event(ctx, deadline)
            if event is None:
                await ctx.emit("dependency_degraded", {"dep": "phone", "reason": "call_timeout"})
                break
            if event.get("callId") not in (call_id, None, ""):
                continue
            etype = event.get("type")
            if etype == "sms_in":
                await self._handle_sms_reply(ctx, event)
            elif etype == "transcript_line" and event.get("live"):
                # Display only: the agent wrote these words down during the call. Prices come
                # from the exact post-call transcript below, so J9 never reads a paraphrase.
                speaker = "supplier" if event.get("speaker") in ("supplier", "user") else "agent"
                text = str(event.get("text") or "").strip()
                if text:
                    shown_live = True
                    await ctx.emit("transcript_line", {"speaker": speaker, "text": text, "live": True})
            elif etype == "transcript_line":
                key = event.get("seq") if event.get("seq") is not None else (event.get("speaker"), event.get("text"))
                await line(str(event.get("speaker") or ""), str(event.get("text") or ""), key)
            elif etype == "call_ended":
                outcome = str(event.get("outcome") or "completed")
                if event.get("transcript"):
                    # Spec §10.2: the post-call transcript fills in every line that the live
                    # stream missed (same `seq` key as the live lines, so nothing repeats).
                    # After live-tool lines, the screen already shows the call: read the
                    # exact lines for the quote, but do not show them a second time.
                    for i, turn in enumerate(event["transcript"]):
                        if isinstance(turn, dict):
                            await line(str(turn.get("speaker") or turn.get("role") or ""),
                                       str(turn.get("text") or turn.get("message") or ""),
                                       turn.get("seq", i), show=not shown_live)
                elif event.get("final") is False:
                    # The live call hung up; the post-call transcript comes in a later call_ended.
                    deadline = min(deadline, loop.time() + self.cfg.post_call_grace_s)
                    continue
                break

        offers = None if call.empty else call
        await ctx.emit("call_ended", {"outcome": "quote" if offers else ("no_quote" if outcome != "timeout" else "timeout")})
        if offers is None:
            return sms_offers
        result = self._offers_from(ctx, call, self._write_transcript(ctx, transcript))
        quote_text = " ".join(call.supplier_lines)
        result.jev_terms = await j10_terms(ctx.decider, quote_text, job_id=ctx.job_id)
        await self._send_sms(ctx, to, self._recap_text(ctx, call))
        await ctx.on_offer(result)
        return result

    def _recap_text(self, ctx: DistributorContext, call: _CallOffer) -> str:
        names = {i.id: i.name for i in ctx.items}
        parts = [f"{names[k]} ${v:.2f}" for k, v in call.item_prices.items() if k in names]
        if call.bundle_total is not None:
            parts.append(f"total for the list ${call.bundle_total:.2f}")
        if call.delivery_fee is not None:
            parts.append("free delivery" if call.delivery_fee == 0 else f"delivery ${call.delivery_fee:.2f}")
        if call.lead_days is not None:
            parts.append(f"lead time {call.lead_days} day{'s' if call.lead_days != 1 else ''}")
        return (f"Thank you. This is the AI assistant for {BUSINESS_NAME}. Summary of our call: "
                + "; ".join(parts) + ". This is not an order. The owner will decide. "
                "Please text an itemized quote to this number.")

    # ------------------------------------------------------------ run

    async def run(self, ctx: DistributorContext) -> SupplierOffers | None:
        to = self.cfg.demo_distributor_phone
        if not to:  # guardrail: never text or call a number that is not the team's demo phone
            await ctx.emit("dependency_degraded", {"dep": "phone", "reason": "DEMO_DISTRIBUTOR_PHONE not set"})
            return None
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()

        sms_offers: SupplierOffers | None = None
        if ctx.settings.distributor_flow == "sms_first":
            sms_offers = await self._sms_first(ctx, to, loop, t0)
            if sms_offers is not None and (sms_offers.status == "ok" or sms_offers.error == "declined"):
                return sms_offers

        # Call when the first plan exists (at the latest T0 + distributor_call_latest_s).
        # call_first (default): this is the first contact; the text recap follows the call.
        await wait_for_call_start(ctx, loop.time)
        plan = ctx.best_plan()
        if plan is None:
            await ctx.emit("call_skipped", {"reason": "no plan to anchor the call"})
            return sms_offers
        line_costs = {a.item_id: a.line_cost for a in plan.assignments}
        scope = call_scope(ctx.items, plan, ctx.settings.distributor_call_max_items)
        if len(scope) < len(ctx.items):
            # A short call: only these items. The anchor is the web cost of exactly these items.
            ctx = dataclasses.replace(ctx, items=scope)
            anchor = round(sum(line_costs.get(i.id, 0.0) for i in scope), 2)
            await ctx.emit("call_scope", {"itemIds": [i.id for i in scope], "anchor": anchor})
        else:
            anchor = plan.total
        return await self._run_call(ctx, to, anchor, line_costs, sms_offers)

    async def _sms_first(self, ctx: DistributorContext, to: str, loop: asyncio.AbstractEventLoop,
                         t0: float) -> SupplierOffers | None:
        # SMS request, then wait for a reply (J8 on each reply).
        await self._send_sms(ctx, to, await self._draft_request(ctx))
        wait_s = ctx.settings.distributor_reply_wait_s * max(ctx.settings.fixture_time_scale, 0)
        deadline = loop.time() + max(0.0, wait_s - (time.monotonic() - t0))
        sms_offers: SupplierOffers | None = None
        replied = False
        while True:
            event = await self._next_event(ctx, deadline)
            if event is None:
                break
            if event.get("type") != "sms_in":
                continue
            replied = True
            intent, offers = await self._handle_sms_reply(ctx, event)
            if offers is not None:
                sms_offers = offers
                if offers.status == "ok":
                    return offers  # a full SMS quote: no call is needed
            if intent == "decline":
                await ctx.emit("call_skipped", {"reason": "the distributor declined by SMS"})
                if sms_offers is None:
                    return SupplierOffers(supplier_id=ctx.distributor.id, source="sms", status="failed",
                                          error="declined")
                sms_offers.error = "declined"
                return sms_offers
        if not replied:
            await ctx.emit("reply_received", {"channel": "sms", "intent": "no_reply"})
        return sms_offers
