"""Orchestrator: job and lane state machines, live re-planning, deadline, approval, handoff.

Reliability rules (spec §10):
- Lanes are isolated: a lane failure sets that lane to failed/blocked; the job continues.
- Every lane step has a budget; one retry for a fast failure; timeouts are not retried.
- At the job deadline the results use whatever finished; late work emits results_updated.
- Approval is idempotent and re-checks the rules; cards stay in the backend.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.config import CheckoutContact, RuntimeModes, Settings
from app.contracts import (
    ApprovalDecision,
    CartLine,
    CheckoutHandoff,
    Event,
    JobCreated,
    JobState,
    JobView,
    LaneState,
    LaneStatus,
    Plan,
    PlanCart,
    SearchSpec,
    ShoppingItem,
    ShoppingRequest,
    SupplierOffers,
    VerifiedCart,
    utcnow,
)
from app.data.insights import rules_from_books
from app.data.loader import Books
from app.db import Database
from app.decide import Decider
from app.distributor.base import DistributorContext
from app.distributor.registry import channel_for
from app.events import EventBus
from app.lanes.base import LaneBlocked, LaneContext, LaneGuardStopped, SupplierLane
from app.lanes.registry import NoLaneAvailable, lane_for
from app.llm import Step5Client
from app.observability import capture, current, forked, in_transaction, outcome, span
from app.stages.cards import mint_cards
from app.stages.normalize import NormalizeResult, normalize
from app.stages.optimize import optimize
from app.stages.rank import build_results, summary_facts
from app.stages.rules import SpendingRules, check_plan

T = TypeVar("T")

# Sentry span status for each result, so a failed step shows as an error in Sentry and not only as a slow span.
OFFERS_STATUS = {"ok": "ok", "partial": "ok", "not_found": "not_found", "blocked": "permission_denied",
                 "failed": "internal_error"}
CART_STATUS = {"verified": "ok", "estimated": "ok", "quoted": "ok", "price_changed": "ok", "out_of_stock": "not_found",
               "blocked": "permission_denied", "failed": "internal_error"}
LANE_STATUS = {LaneState.cart_ready: "ok", LaneState.at_payment: "ok", LaneState.idle: "not_found",
               LaneState.blocked: "permission_denied", LaneState.failed: "internal_error"}


def cart_data(cart: VerifiedCart) -> dict[str, Any]:
    return {"cart.total": cart.costs.total if cart.costs else None, "cart.verification": cart.verification_status,
            "cart.totals_check": cart.totals_check, "cart.lines": len(cart.cart_items), "cart.reason": cart.reason}
SUMMARY_TIMEOUT_S = 12
# A job in one of these states is done. Any other saved state means that a process was running it.
FINAL_JOB_STATES = {JobState.at_payment, JobState.partial_handoff, JobState.rejected, JobState.failed}


class ApprovalError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass
class LaneRuntime:
    status: LaneStatus
    lane: SupplierLane | None = None
    ctx: LaneContext | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set by the verify phase: the lane went through checkout to payment for this cart and read
    # the real total there; approval reuses that payment page instead of redoing checkout.
    quote_key: str | None = None
    quote_handoff: CheckoutHandoff | None = None
    cart_key: str | None = None  # the cart the lane's browser holds now

    @property
    def is_agent(self) -> bool:
        return self.status.mode == "llm_agent"


@dataclass
class JobRuntime:
    view: JobView
    rules: SpendingRules
    names: dict[str, str]
    urls: dict[str, str]
    lanes: dict[str, LaneRuntime] = field(default_factory=dict)
    offers: dict[str, SupplierOffers] = field(default_factory=dict)
    carts: dict[str, VerifiedCart] = field(default_factory=dict)
    normalized: NormalizeResult = field(default_factory=NormalizeResult)
    plans: list[Plan] = field(default_factory=list)
    subset_requests: list[tuple[str, list[CartLine]]] = field(default_factory=list)
    specs: dict[str, SearchSpec] = field(default_factory=dict)
    first_plan: asyncio.Event = field(default_factory=asyncio.Event)
    plan_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    approve_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dist_ctx: DistributorContext | None = None
    started_at: float = 0.0

    @property
    def job_id(self) -> str:
        return self.view.job_id

    @property
    def request(self) -> ShoppingRequest:
        return self.view.request


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        bus: EventBus,
        decider: Decider,
        llm: Step5Client,
        modes: RuntimeModes,
        books: Books,
        insights_provider: Callable[[], Awaitable[Any]],
    ):
        self.settings = settings
        self.db = db
        self.bus = bus
        self.decider = decider
        self.llm = llm
        self.modes = modes
        self.books = books
        self.insights_provider = insights_provider
        self.jobs: dict[str, JobRuntime] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ public API

    async def create_job(self, request: ShoppingRequest, idempotency_key: str) -> JobCreated:
        existing = self.db.job_for_idempotency_key(idempotency_key)
        if existing:
            view = self.get_view(existing)
            if existing in self.jobs or (view is not None and view.state in FINAL_JOB_STATES):
                return JobCreated(job_id=existing, state=view.state if view else JobState.created)
            # An earlier server process saved this job and stopped before it ended. Nothing runs
            # it now, so mark it failed and start a new job with the same key.
            if view is not None:
                view.state = JobState.failed
                self.db.save_job(view)
            self.db.release_idempotency_key(existing)
        job_id = "job_" + secrets.token_hex(5)
        insights = await self.insights_provider()
        approved = [s.id for s in request.suppliers]
        names = {s.id: s.name for s in request.suppliers}
        urls = {s.id: s.url for s in request.suppliers}
        if request.distributor:
            approved.append(request.distributor.id)
            names[request.distributor.id] = request.distributor.name
        rules = rules_from_books(self.books, insights, approved)
        view = JobView(job_id=job_id, state=JobState.created, request=request)
        rt = JobRuntime(view=view, rules=rules, names=names, urls=urls)
        self.jobs[job_id] = rt
        self.db.save_job(view, idempotency_key=idempotency_key)
        self._spawn(in_transaction("job", "restock job", self._run(rt), job_id=job_id))
        return JobCreated(job_id=job_id, state=view.state)

    def get_view(self, job_id: str) -> JobView | None:
        rt = self.jobs.get(job_id)
        return rt.view if rt else self.db.load_job(job_id)

    def list_views(self, limit: int = 50) -> list[JobView]:
        """Stored jobs, newest first, with the live view for each job that still runs."""
        return [self.jobs[v.job_id].view if v.job_id in self.jobs else v for v in self.db.list_jobs(limit)]

    def rules_for(self, job_id: str) -> SpendingRules | None:
        rt = self.jobs.get(job_id)
        return rt.rules if rt else None

    async def approve(self, job_id: str, decision: ApprovalDecision) -> JobView:
        rt = self.jobs.get(job_id)
        if rt is None:
            raise ApprovalError(404, "job not found or not active on this server")
        async with rt.approve_lock:
            prior = rt.view.approval
            if prior is not None:
                if prior.action == decision.action and prior.plan_id == decision.plan_id:
                    return rt.view  # idempotent repeat
                raise ApprovalError(409, {"code": "already_decided", "text": "This job already has a different decision."})
            if rt.view.state is not JobState.results_ready or rt.view.results is None:
                raise ApprovalError(409, {"code": "not_ready",
                                          "text": f"The job is {rt.view.state.value}, not ready for approval."})
            if decision.action == "reject":
                rt.view.approval = decision
                await self._set_job_state(rt, JobState.rejected)
                await self.bus.emit(job_id, "rejected", {"reason": decision.reason})
                self._spawn(self._close_lanes(rt))
                return rt.view

            plan = next((p for p in rt.view.results.plans if p.plan_id == decision.plan_id), None)
            if plan is None:
                raise ApprovalError(404, "plan not found")
            if plan.uncovered_items:
                raise ApprovalError(409, {"code": "plan_incomplete", "items": plan.uncovered_items,
                                          "text": "This plan does not cover every item: " + ", ".join(plan.uncovered_items)})
            if plan.status == "estimated":
                raise ApprovalError(409, {"code": "not_verified", "text": "This plan has carts that are not verified."})
            if plan.status == "quoted" and not decision.accept_quoted:
                raise ApprovalError(409, {"code": "quoted_not_accepted",
                                          "text": "Turn on 'Accept quoted offer' to approve a quoted plan."})
            check = check_plan(plan, rt.rules)
            await self.bus.emit(job_id, "rules_checked", {"planId": plan.plan_id, "pass": check.passed,
                                                         "failed": [f.model_dump() for f in check.failed]})
            if not check.passed:
                raise ApprovalError(409, {"code": "rules_failed", "failed": [f.model_dump() for f in check.failed],
                                          "text": " ".join(f.text for f in check.failed)})

            cards = mint_cards(job_id, plan, rt.urls)
            rt.view.cards = cards
            rt.view.approval = decision
            for card in cards:
                await self.bus.emit(job_id, "card_minted", {"supplierId": card.supplier_id, "last4": card.last4,
                                                           "limitUsd": card.limit_usd, "simulated": True})
            await self._set_job_state(rt, JobState.approved)
            await self.bus.emit(job_id, "approved", {"planId": plan.plan_id, "total": plan.total})
            await self._set_job_state(rt, JobState.handing_off)
            self._spawn(in_transaction("job.handoff", "checkout handoff", self._handoff(rt, plan), job_id=job_id))
            return rt.view

    async def replay(self, source_job_id: str, speed: float = 1.0) -> JobCreated:
        source = self.get_view(source_job_id)
        if source is None:
            raise KeyError(source_job_id)
        job_id = "job_" + secrets.token_hex(5)
        final = source.model_copy(update={"job_id": job_id, "replay_of": source_job_id})
        # The replay starts like a new job. Its state follows the replayed job_state events, so the
        # event stream stays open until the replay reaches the source's final state.
        view = JobView(job_id=job_id, state=JobState.created, replay_of=source_job_id, request=source.request)
        self.db.save_job(view)

        def follow(event: Event) -> None:
            if event.type != "job_state":
                return
            try:
                state = JobState(event.data.get("state"))
            except ValueError:
                return
            if state in FINAL_JOB_STATES or state is final.state:
                self.db.save_job(final.model_copy(update={"state": state}))
                return
            view.state = state
            if state is not JobState.gathering:
                view.results = final.results
            view.updated_at = utcnow()
            self.db.save_job(view)

        async def run() -> None:
            await self.bus.replay(source_job_id, job_id, speed, before_emit=follow)
            self.db.save_job(final)

        self._spawn(run())
        return JobCreated(job_id=job_id, state=view.state)

    async def route_webhook(self, provider: str, payload: dict[str, Any]) -> bool:
        job_id = payload.get("jobId")
        candidates = [self.jobs[job_id]] if job_id in self.jobs else [
            rt for rt in self.jobs.values() if rt.view.state is JobState.gathering
        ]
        for rt in candidates:
            if rt.dist_ctx is not None:
                await rt.dist_ctx.inbox.put({"provider": provider, **payload})
                return True
        return False

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task

    # ------------------------------------------------------------ job run

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.ensure_future(forked(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run(self, rt: JobRuntime) -> None:
        loop = asyncio.get_running_loop()
        rt.started_at = loop.time()
        job_id = rt.job_id
        try:
            await self._set_job_state(rt, JobState.gathering)
            await self.bus.emit(job_id, "job_started", {
                "items": [i.id for i in rt.request.items],
                "suppliers": [s.id for s in rt.request.suppliers],
                "deadlineS": self.settings.job_deadline_s,
                "modes": self.modes.as_dict(),
            })
            await self._make_specs(rt)
            lane_tasks = self._start_lanes(rt)
            work = [self._spawn(self._lanes_then_subsets(rt, lane_tasks))]
            if rt.request.distributor is not None:
                work.append(self._spawn(self._run_distributor(rt)))
            remaining = self.settings.job_deadline_s - (loop.time() - rt.started_at)
            _, pending = await asyncio.wait(work, timeout=max(0.0, remaining))
            deadline_hit = bool(pending)
            if deadline_hit:
                await self.bus.emit(job_id, "deadline_hit", {"pending": len(pending)})
            await self._finalize(rt, deadline_hit=deadline_hit, event="results_ready")
            self._record_job(rt)
            if pending:
                self._spawn(self._late(rt, pending))
        except Exception as exc:  # the job must always end in a known state
            capture(exc)
            outcome(current(), "internal_error", **{"job.state": "failed", "job.error": type(exc).__name__})
            await self._set_job_state(rt, JobState.failed)
            await self.bus.emit(job_id, "job_failed", {"error": f"{type(exc).__name__}: {exc}"})

    def _record_job(self, rt: JobRuntime) -> None:
        """Put the job result on its transaction: one row per job in Sentry, to compare runs and releases."""
        res = rt.view.results
        if res is None:
            return
        best = next((p for p in res.plans if p.plan_id == res.recommended_plan_id), None)
        states = [lr.status.state for lr in rt.lanes.values()]
        outcome(current(), "ok" if best else "not_found", **{
            "job.state": rt.view.state.value, "job.results": res.status, "job.deadline_hit": res.deadline_hit,
            "job.plans": len(res.plans), "job.recommended_total": best.total if best else None,
            "job.recommended_status": best.status if best else None,
            "job.no_recommendation_reason": res.no_recommendation_reason,
            "job.savings_vs_paid_before": res.savings_vs_paid_before,
            "job.items": len(rt.request.items), "job.lanes": len(states),
            "job.lanes_ok": sum(s in (LaneState.cart_ready, LaneState.at_payment) for s in states),
            "job.lanes_failed": sum(s in (LaneState.failed, LaneState.blocked) for s in states),
            "job.quote": any(o.source != "web" for o in rt.offers.values()),
        })

    async def _make_specs(self, rt: JobRuntime) -> None:
        items = rt.request.items
        specs = await asyncio.gather(*(forked(self.llm.make_spec(item)) for item in items))
        for item, spec in zip(items, specs, strict=True):
            item.spec = spec
            rt.specs[item.id] = spec
            await self.bus.emit(rt.job_id, "spec_ready", {"itemId": item.id, "query": spec.query,
                                                         "mustHave": spec.must_have, "fallback": spec.spec_fallback})
        self._save(rt)

    def _start_lanes(self, rt: JobRuntime) -> list[tuple[LaneRuntime, asyncio.Task]]:
        tasks = []
        for supplier in rt.request.suppliers:
            lane_id = f"lane_{supplier.id}"
            try:
                lane: SupplierLane | None = lane_for(supplier, self.modes)
                mode = lane.mode
                reason = None
            except NoLaneAvailable as exc:
                lane, mode, reason = None, ("adapter" if supplier.adapter else "llm_agent"), str(exc)
            status = LaneStatus(lane_id=lane_id, supplier_id=supplier.id,
                                mode=mode if mode in ("adapter", "llm_agent", "fixture") else "adapter")
            lr = LaneRuntime(status=status, lane=lane)
            rt.lanes[supplier.id] = lr
            if lane is None:
                status.state, status.reason = LaneState.failed, "no_lane_available"
                self._spawn(self.bus.emit(rt.job_id, "lane_state", {"state": "failed", "reason": reason}, lane_id))
                continue
            lr.ctx = LaneContext(
                job_id=rt.job_id, lane_id=lane_id, supplier=supplier, settings=self.settings,
                decider=self.decider, emit=self._lane_emitter(rt, lr), evidence_dir=self.settings.evidence_dir,
                specs=rt.specs,
            )
            # Its own transaction in the job's trace: a lane can end after the job transaction (the
            # deadline), and Sentry drops spans whose transaction has already been sent.
            tasks.append((lr, self._spawn(in_transaction("lane", f"lane {supplier.id}", self._run_lane(rt, lr),
                                                         job_id=rt.job_id, supplier=supplier.id))))
        self._save(rt)
        return tasks

    def _lane_emitter(self, rt: JobRuntime, lr: LaneRuntime) -> Callable[[str, dict], Awaitable[None]]:
        async def emit(type_: str, data: dict) -> None:
            if type_ == "screenshot":
                lr.status.last_screenshot_url = data.get("url")
            await self.bus.emit(rt.job_id, type_, data, lr.status.lane_id)

        return emit

    def _lane_items(self, rt: JobRuntime, lr: LaneRuntime) -> list[ShoppingItem]:
        items = rt.request.items
        if not lr.is_agent or len(items) <= self.settings.agent_lane_max_items:
            return items
        # Largest past spend first: the most savings for the agent lane's few searches.
        ranked = sorted(items, key=lambda i: -(i.past_unit_price or 0.0) * i.quantity)
        return ranked[: self.settings.agent_lane_max_items]

    def _budget(self, lr: LaneRuntime, base: float) -> float:
        # A lane can set its own factor: a hosted agent run takes minutes, not seconds.
        own = getattr(lr.lane, "budget_factor", None)
        if isinstance(own, (int, float)) and own > 0:
            return base * own
        return base * (self.settings.lane_agent_budget_factor if lr.is_agent else 1.0)

    async def _timed(self, fn: Callable[[], Awaitable[T]], budget: float, retries: int = 1) -> T:
        for attempt in range(retries + 1):
            try:
                return await asyncio.wait_for(fn(), timeout=budget)
            except (LaneBlocked, LaneGuardStopped, TimeoutError, asyncio.CancelledError):
                raise
            except Exception:
                if attempt >= retries:
                    raise
        raise RuntimeError("unreachable")

    async def _run_lane(self, rt: JobRuntime, lr: LaneRuntime) -> None:
        sid = lr.status.supplier_id
        if lr.is_agent:
            # The browser agent lane is an AI agent run: Sentry's AI Agents view groups it with its model calls.
            lane_span = span("gen_ai.invoke_agent", f"invoke_agent browser lane {sid}", **{
                "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "browser-use lane"}, supplier=sid)
        else:
            lane_span = span("lane.run", f"lane {sid}", supplier=sid)
        with lane_span as s:
            await self._run_lane_steps(rt, lr)
            st = lr.status
            outcome(s, LANE_STATUS.get(st.state, "unknown"), **{
                "lane.mode": st.mode, "lane.state": st.state.value, "lane.reason": st.reason,
                "lane.items": len(self._lane_items(rt, lr))})

    async def _run_lane_steps(self, rt: JobRuntime, lr: LaneRuntime) -> None:
        assert lr.lane is not None and lr.ctx is not None
        lane, ctx, sid = lr.lane, lr.ctx, lr.status.supplier_id
        try:
            async with lr.lock:
                await self._lane_state(rt, lr, LaneState.searching)
                with span("lane.open", f"open {sid}", supplier=sid):
                    await self._timed(lambda: lane.open(ctx), self._budget(lr, self.settings.lane_search_budget_s))
                lr.status.session_id, lr.status.live_url = ctx.session_id, ctx.live_url
                if ctx.live_url:  # the "searching" event went out before open(): send the live view now
                    await self._lane_state(rt, lr, LaneState.searching)
                with span("lane.search", f"search {sid}", supplier=sid) as s:
                    offers = await self._timed(lambda: lane.search(ctx, self._lane_items(rt, lr)),
                                               self._budget(lr, self.settings.lane_search_budget_s))
                    outcome(s, OFFERS_STATUS[offers.status], **{
                        "offers.status": offers.status, "offers.products": len(offers.products),
                        "offers.items_found": len({p.item_id for p in offers.products}), "offers.error": offers.error})
                rt.offers[sid] = offers
                await self.bus.emit(rt.job_id, "offers_ready", {"status": offers.status, "products": len(offers.products)},
                                    lr.status.lane_id)
                if offers.status in ("blocked", "failed"):
                    await self._lane_state(rt, lr, LaneState.failed, offers.error or offers.status)
                    return
                rt.normalized = normalize(rt.request.items, list(rt.offers.values()), self.settings.thresholds)
                options = rt.normalized.lines_for_supplier(sid)
                if not options:
                    await self._lane_state(rt, lr, LaneState.idle, "no_eligible_items")
                    await self._replan(rt, f"offers:{sid}")
                    return
                lines = [CartLine(item_id=o.item_id, sku=o.sku, pack_quantity=o.packs_needed, unit_price=o.pack_price, url=o.url)
                         for o in sorted(options, key=lambda o: o.item_id)]
                await self._lane_state(rt, lr, LaneState.carting)
                with span("lane.set_cart", f"set_cart {sid}", supplier=sid, **{"cart.requested_lines": len(lines)}) as s:
                    cart = await self._timed(lambda: lane.set_cart(ctx, lines),
                                             self._budget(lr, self.settings.lane_cart_budget_s))
                    outcome(s, CART_STATUS[cart.verification_status], **cart_data(cart))
                rt.carts[cart.cart_key] = cart
                lr.cart_key = cart.cart_key
                await self.bus.emit(rt.job_id, "supplier_cart_ready", {
                    "cartKey": cart.cart_key, "total": cart.costs.total if cart.costs else None,
                    "totalsCheck": cart.totals_check, "status": cart.verification_status,
                    "screenshotUrl": cart.screenshot_url}, lr.status.lane_id)
                await self._lane_state(rt, lr, LaneState.cart_ready)
            await self._replan(rt, f"cart:{sid}")
        except LaneBlocked as exc:
            await self._lane_state(rt, lr, LaneState.blocked, exc.reason)
        except LaneGuardStopped as exc:
            # The payment guard stopped the lane before checkout: a page looked like payment.
            await self._lane_state(rt, lr, LaneState.failed, f"guard_stop: {exc.verdict.reason}")
        except TimeoutError:
            await self._lane_state(rt, lr, LaneState.failed, "timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            capture(exc)
            await self._lane_state(rt, lr, LaneState.failed, f"adapter_error: {type(exc).__name__}")

    async def _lanes_then_subsets(self, rt: JobRuntime, lane_tasks: list[tuple[LaneRuntime, asyncio.Task]]) -> None:
        fast = [t for lr, t in lane_tasks if not lr.is_agent]
        slow = [t for lr, t in lane_tasks if lr.is_agent]
        await asyncio.gather(*fast, return_exceptions=True)
        await self._subset_phase(rt)
        await self._verify_phase(rt)
        if slow:
            await asyncio.gather(*slow, return_exceptions=True)
            await self._subset_phase(rt)
            await self._verify_phase(rt)

    async def _verify_phase(self, rt: JobRuntime, rounds: int = 2) -> None:
        """Check the best plan's web carts against the real checkout total: each lane sets the
        cart, goes through guest checkout to the payment page, reads the order summary there
        (subtotal, shipping, tax, total) and stops. A verified total can change the ranking, so a
        new best plan gets one more round. Lanes that cannot quote keep their cart estimates."""
        contact = self.settings.checkout_contact
        if "NOT SET" in contact.address1:
            return  # never send placeholder data to a real store
        tried: set[str] = set()
        for _ in range(rounds):
            best = self._best_web_plan(rt)
            if best is None:
                return
            todo = [c for c in best.carts if c.status != "verified" and c.cart_key not in tried
                    and (lr := rt.lanes.get(c.supplier_id)) is not None and hasattr(lr.lane, "quote")
                    and getattr(lr.lane, "quote_at_checkout", True)
                    and lr.ctx is not None and lr.status.state is LaneState.cart_ready]
            if not todo:
                return
            tried.update(c.cart_key for c in todo)
            await asyncio.gather(*(forked(self._quote_cart(rt, c, contact)) for c in todo))
            await self._replan(rt, "verify")

    async def _quote_cart(self, rt: JobRuntime, plan_cart: PlanCart, contact: CheckoutContact) -> None:
        sid = plan_cart.supplier_id
        lr = rt.lanes[sid]
        lane, ctx = lr.lane, lr.ctx
        assert lane is not None and ctx is not None
        try:
            async with lr.lock:
                await self._lane_state(rt, lr, LaneState.checkout, "verifying_total")
                with span("lane.quote", f"checkout quote {sid}", supplier=sid) as s:
                    if lr.cart_key != plan_cart.cart_key:  # the browser holds another cart: set this one
                        await self._timed(lambda: lane.set_cart(ctx, plan_cart.lines),
                                          self._budget(lr, self.settings.lane_subset_budget_s), retries=0)
                        lr.cart_key = plan_cart.cart_key
                    cart, handoff = await self._timed(lambda: lane.quote(ctx, contact, plan_cart.lines),
                                                      self._budget(lr, self.settings.lane_cart_budget_s), retries=0)
                    outcome(s, CART_STATUS[cart.verification_status] if cart else "not_found",
                            **(cart_data(cart) if cart else {}))
                if cart is not None:
                    rt.carts[cart.cart_key] = cart
                    await self.bus.emit(rt.job_id, "cart_verified", {
                        "cartKey": cart.cart_key, "total": cart.costs.total if cart.costs else None,
                        "shipping": cart.costs.shipping if cart.costs else None,
                        "tax": cart.costs.tax if cart.costs else None, "status": cart.verification_status,
                        "screenshotUrl": cart.screenshot_url}, lr.status.lane_id)
                if handoff.status == "at_payment" and cart is not None:
                    lr.quote_key, lr.quote_handoff = cart.cart_key, handoff
                await self._lane_state(rt, lr, LaneState.cart_ready)
        except TimeoutError:
            await self._lane_state(rt, lr, LaneState.cart_ready, "verify_timeout")
        except Exception as exc:  # a failed check keeps the estimate; the job goes on
            capture(exc)
            await self._lane_state(rt, lr, LaneState.cart_ready, "verify_failed")

    async def _subset_phase(self, rt: JobRuntime) -> None:
        by_supplier: dict[str, list[list[CartLine]]] = {}
        for sid, lines in rt.subset_requests:
            by_supplier.setdefault(sid, []).append(lines)

        async def price(sid: str, requests: list[list[CartLine]]) -> None:
            lr = rt.lanes.get(sid)
            if lr is None or lr.lane is None or lr.ctx is None or lr.status.state is not LaneState.cart_ready:
                return
            lane, ctx = lr.lane, lr.ctx
            for lines in requests:
                try:
                    async with lr.lock:
                        await self._lane_state(rt, lr, LaneState.subset_pricing)
                        with span("lane.subset_set_cart", f"subset set_cart {sid}", supplier=sid,
                                  **{"cart.requested_lines": len(lines)}) as s:
                            cart = await self._timed(lambda lines=lines: lane.set_cart(ctx, lines),
                                                     self._budget(lr, self.settings.lane_subset_budget_s), retries=0)
                            outcome(s, CART_STATUS[cart.verification_status], **cart_data(cart))
                        rt.carts[cart.cart_key] = cart
                        lr.cart_key = cart.cart_key
                        await self.bus.emit(rt.job_id, "subset_total", {
                            "cartKey": cart.cart_key, "total": cart.costs.total if cart.costs else None,
                            "status": cart.verification_status}, lr.status.lane_id)
                        await self._lane_state(rt, lr, LaneState.cart_ready)
                except TimeoutError:
                    await self._lane_state(rt, lr, LaneState.cart_ready, "subset_timeout")
                except Exception as exc:
                    capture(exc)
                    await self._lane_state(rt, lr, LaneState.cart_ready, "subset_failed")
                await self._replan(rt, f"subset:{sid}")

        await asyncio.gather(*(forked(price(sid, reqs)) for sid, reqs in by_supplier.items()))

    async def _run_distributor(self, rt: JobRuntime) -> None:
        dist = rt.request.distributor
        assert dist is not None
        channel = channel_for(self.modes)
        if channel is None:
            await self.bus.emit(rt.job_id, "dependency_degraded", {"dep": "phone", "reason": "no live channel"})
            return

        async def emit(type_: str, data: dict) -> None:
            await self.bus.emit(rt.job_id, type_, data, "distributor")

        async def on_offer(offers: SupplierOffers) -> None:
            rt.offers[offers.supplier_id] = offers
            rt.normalized = normalize(rt.request.items, list(rt.offers.values()), self.settings.thresholds)
            await self.bus.emit(rt.job_id, "quote_received", {"source": offers.source}, "distributor")
            await self._replan(rt, f"quote:{offers.supplier_id}")
            # A partial-list quote creates plans that mix it with web carts: price those carts now.
            if rt.subset_requests:
                await self._subset_phase(rt)

        rt.dist_ctx = DistributorContext(
            job_id=rt.job_id, distributor=dist, items=rt.request.items, settings=self.settings,
            decider=self.decider, emit=emit, best_plan=lambda: self._best_web_plan(rt),
            first_plan_ready=rt.first_plan, on_offer=on_offer, started_at=rt.started_at,
        )
        try:
            with span("distributor.run", f"distributor {dist.id}", supplier=dist.id) as s:
                await channel.run(rt.dist_ctx)
                quoted = dist.id in rt.offers
                outcome(s, "ok" if quoted else "not_found", **{"distributor.quoted": quoted})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            capture(exc)
            await self.bus.emit(rt.job_id, "dependency_degraded", {"dep": "phone", "reason": type(exc).__name__})

    def _best_web_plan(self, rt: JobRuntime) -> Plan | None:
        web = [p for p in rt.plans if p.status != "quoted" and not p.uncovered_items
               and all(c.status != "quoted" for c in p.carts)]
        return min(web, key=lambda p: p.total) if web else None

    async def _replan(self, rt: JobRuntime, reason: str) -> None:
        async with rt.plan_lock:
            # A worker thread, so a long MILP solve does not stop the events and the call webhooks.
            # The copies keep other tasks from changing the dicts during the solve.
            result = await asyncio.to_thread(optimize, list(rt.request.items), rt.normalized,
                                             dict(rt.offers), dict(rt.carts), dict(rt.names))
            for plan in result.plans:
                plan.rules_check = check_plan(plan, rt.rules)
            rt.plans = result.plans
            rt.subset_requests = result.subset_requests
            self._save(rt)
            best = rt.plans[0] if rt.plans else None
            await self.bus.emit(rt.job_id, "plans_updated", {
                "reason": reason, "count": len(rt.plans),
                "bestPlanId": best.plan_id if best else None,
                "bestTotal": best.total if best else None,
                "bestStatus": best.status if best else None,
            })
            if self._best_web_plan(rt) is not None:
                rt.first_plan.set()

    async def _finalize(self, rt: JobRuntime, *, deadline_hit: bool, event: str) -> None:
        async with rt.plan_lock:
            terms = {sid: o.jev_terms for sid, o in rt.offers.items() if o.jev_terms}
            results = build_results(
                rt.job_id, rt.request.items, rt.plans, rt.normalized,
                [lr.status for lr in rt.lanes.values()], rt.rules, rt.names,
                accept_quoted=rt.request.accept_quoted_default, deadline_hit=deadline_hit, terms=terms,
            )
        try:
            results.summary = await asyncio.wait_for(
                self.llm.summarize(summary_facts(results, results.plans)), timeout=SUMMARY_TIMEOUT_S)
        except TimeoutError:
            from app.llm import template_summary

            results.summary = template_summary(summary_facts(results, results.plans))
        rt.view.results = results
        if rt.view.state is JobState.gathering:
            await self._set_job_state(rt, JobState.results_ready)
        self._save(rt)
        best = next((p for p in results.plans if p.plan_id == results.recommended_plan_id), None)
        await self.bus.emit(rt.job_id, event, {
            "recommendedPlanId": results.recommended_plan_id,
            "total": best.total if best else None,
            "status": results.status,
            "deadlineHit": results.deadline_hit,
            "savingsVsPaidBefore": results.savings_vs_paid_before,
        })

    async def _late(self, rt: JobRuntime, pending: set[asyncio.Task]) -> None:
        await asyncio.wait(pending)
        if rt.view.state is JobState.results_ready and rt.view.approval is None:
            await self._finalize(rt, deadline_hit=False, event="results_updated")

    # ------------------------------------------------------------ approval handoff

    async def _handoff(self, rt: JobRuntime, plan: Plan) -> None:
        cards = {c.supplier_id: c for c in rt.view.cards}
        contact = self.settings.checkout_contact

        async def one(cart_plan) -> CheckoutHandoff:
            sid = cart_plan.supplier_id
            card = cards.get(sid)
            if cart_plan.status == "quoted":
                return CheckoutHandoff(supplier_id=sid, status="quoted_confirm_by_phone",
                                       card_id=card.card_id if card else None)
            lr = rt.lanes.get(sid)
            if lr is None or lr.lane is None or lr.ctx is None or lr.status.state in (LaneState.failed, LaneState.blocked):
                return CheckoutHandoff(supplier_id=sid, status="failed", reason="lane_unavailable")
            lane, ctx = lr.lane, lr.ctx
            if lr.quote_handoff is not None and lr.quote_key == cart_plan.cart_key:
                # The verify phase already stopped this lane at payment with exactly this cart.
                handoff = lr.quote_handoff.model_copy(update={"card_id": card.card_id if card else None,
                                                             "live_url": lr.quote_handoff.live_url or ctx.live_url})
                await self._lane_state(rt, lr, LaneState.at_payment)
                return handoff
            try:
                async with lr.lock:
                    lr.quote_key = lr.quote_handoff = None  # the browser leaves that payment page
                    lr.cart_key = cart_plan.cart_key
                    await self._lane_state(rt, lr, LaneState.checkout)
                    with span("lane.set_cart", f"checkout set_cart {sid}", supplier=sid):
                        await self._timed(lambda: lane.set_cart(ctx, cart_plan.lines),
                                          self._budget(lr, self.settings.lane_subset_budget_s), retries=0)
                    with span("lane.checkout", f"checkout {sid}", supplier=sid) as s:
                        handoff = await self._timed(lambda: lane.checkout(ctx, contact),
                                                    self._budget(lr, self.settings.lane_cart_budget_s), retries=0)
                        outcome(s, "ok" if handoff.status == "at_payment" else "internal_error", **{
                            "handoff.status": handoff.status, "handoff.reason": handoff.reason})
                handoff.card_id = card.card_id if card else None
                handoff.live_url = handoff.live_url or ctx.live_url
                await self._lane_state(rt, lr, LaneState.at_payment if handoff.status == "at_payment"
                                       else LaneState.failed, handoff.reason)
                return handoff
            except LaneGuardStopped as exc:
                # A lane returns at_payment itself when it reaches the payment step. A raised stop
                # means the guard fired somewhere unexpected, so the handoff is not trusted.
                await self._lane_state(rt, lr, LaneState.failed, f"guard_stop: {exc.verdict.reason}")
                return CheckoutHandoff(supplier_id=sid, status="failed", reason=f"guard_stop: {exc.verdict.reason}")
            except TimeoutError:
                await self._lane_state(rt, lr, LaneState.failed, "checkout_timeout")
                return CheckoutHandoff(supplier_id=sid, status="failed", reason="timeout")
            except Exception as exc:
                capture(exc)
                await self._lane_state(rt, lr, LaneState.failed, "checkout_error")
                return CheckoutHandoff(supplier_id=sid, status="failed", reason=type(exc).__name__)

        handoffs = await asyncio.gather(*(forked(one(c)) for c in plan.carts))
        rt.view.handoffs = list(handoffs)
        for h in handoffs:
            await self.bus.emit(rt.job_id, "handoff_ready", h.model_dump(by_alias=True, mode="json"))
        ok = all(h.status in ("at_payment", "quoted_confirm_by_phone") for h in handoffs)
        await self._set_job_state(rt, JobState.at_payment if ok else JobState.partial_handoff)
        await self._close_lanes(rt, keep=[h.supplier_id for h in handoffs if h.status == "at_payment"])

    async def _close_lanes(self, rt: JobRuntime, keep: list[str] | None = None) -> None:
        for sid, lr in rt.lanes.items():
            if keep and sid in keep:
                continue  # keep the session open for the live view on screen 4
            if lr.lane is not None and lr.ctx is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(lr.lane.close(lr.ctx), timeout=10)

    # ------------------------------------------------------------ state helpers

    async def _lane_state(self, rt: JobRuntime, lr: LaneRuntime, state: LaneState, reason: str | None = None) -> None:
        lr.status.state = state
        lr.status.reason = reason
        lr.status.updated_at = utcnow()
        self._save(rt)
        await self.bus.emit(rt.job_id, "lane_state", {"state": state.value, "reason": reason,
                                                     "liveUrl": lr.status.live_url}, lr.status.lane_id)

    async def _set_job_state(self, rt: JobRuntime, state: JobState) -> None:
        rt.view.state = state
        self._save(rt)
        await self.bus.emit(rt.job_id, "job_state", {"state": state.value})

    def _save(self, rt: JobRuntime) -> None:
        rt.view.lanes = [lr.status for lr in rt.lanes.values()]
        rt.view.offers = list(rt.offers.values())
        rt.view.carts = list(rt.carts.values())
        rt.view.plans = rt.plans
        rt.view.updated_at = utcnow()
        self.db.save_job(rt.view)
