"""FastAPI app. Run: `uv run uvicorn app.main:app --reload` from backend/."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app.distributor.live_setup  # noqa: F401  (live Photon/ElevenLabs channel registers there)
import app.lanes.live_setup  # noqa: F401  (agents team registers live lanes there)
from app.config import DEPENDENCIES, Mode, RuntimeModes, Settings, get_settings
from app.contracts import (
    ApprovalDecision,
    DiscoveryResult,
    DistributorRef,
    JobCreated,
    JobView,
    ShoppingRequest,
    SpendInsights,
)
from app.data.insights import build_insights, draft_request, rules_from_books
from app.data.loader import Books, load_books
from app.db import Database, stable_hash
from app.decide import Decider
from app.discovery import Discovery, selected_suppliers
from app.events import EventBus
from app.jev import JevClient
from app.llm import Step5Client
from app.observability import capture, init_sentry
from app.orchestrator import ApprovalError, Orchestrator
from app.planner.sales import load_sales
from app.planner.schemas import (
    BacktestReport,
    ForecastAdjustment,
    MissAnswer,
    OwnerMessage,
    PlannerCheck,
    PlannerView,
    SupplyPlan,
)
from app.planner.service import Planner
from app.preflight import PreflightReport, run_preflight
from app.stages.rules import SpendingRules

DEFAULT_CATEGORY = "packaging_disposables"
TERMINAL_JOB_STATES = {"at_payment", "partial_handoff", "rejected", "failed"}


def _plural(unit: str) -> str:
    return unit + ("es" if unit.endswith(("s", "x", "ch", "sh")) else "s")  # box -> boxes


def due_supplies(view: PlannerView) -> list[SupplyPlan]:
    """The supplies to order now or soon. /planner/check and the draft list use the same rule."""
    return [p for p in view.supplies if p.status != "ok" and p.recommended_qty > 0]


MAX_SEQ = 2**62  # event seqs are SQLite integers (64-bit); a larger "after" would overflow


class ModeChange(BaseModel):
    dependency: str
    mode: Mode


class AppState:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.var_dir.mkdir(parents=True, exist_ok=True)
        settings.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.modes = RuntimeModes(settings.initial_modes())
        self.bus = EventBus(self.db)
        self.jev = JevClient(settings, self.db, self.modes)
        self.llm = Step5Client(settings, self.db, self.modes)
        self.decider = Decider(settings, self.db, self.jev, self.llm)
        self.books: Books = load_books(settings.qb_data_dir, settings.injected_bills_path, settings.business_id)
        self.discovery = Discovery(settings, self.db, self.modes, self.decider)
        self.sales = load_sales(settings.qb_data_dir, settings.business_id)
        self.planner = Planner(settings, self.db, self.decider, self.sales, self.books)
        self._insights: SpendInsights | None = None
        self._insights_lock = asyncio.Lock()
        self._warm: asyncio.Task | None = None
        self.orchestrator = Orchestrator(settings, self.db, self.bus, self.decider, self.llm, self.modes,
                                         self.books, self.insights)

    async def insights(self) -> SpendInsights:
        async with self._insights_lock:
            if self._insights is None:
                self._insights = await build_insights(self.decider, self.books)
            return self._insights

    def distributor(self) -> DistributorRef | None:
        d = self.books.distributor
        if not d:
            return None
        return DistributorRef(id=d["id"], name=d["name"], phone=d.get("phone"), email=d.get("email"))

    async def draft(self, category: str = DEFAULT_CATEGORY) -> ShoppingRequest:
        """Screen 1: the planner's quantities for supplies that are due; else the usual order size."""
        insights = await self.insights()
        found = await self.discovery.discover(category, self.settings.demo_zip, self.distributor(),
                                              items=[i.bill_item_name for i in insights.items])
        suppliers = selected_suppliers(found)
        if not suppliers:
            # A request needs a supplier. Say why none was picked (no lane can run them in this
            # browser mode, discovery found none...) instead of a ShoppingRequest ValidationError.
            raise HTTPException(503, {
                "code": "no_suppliers",
                "text": f"No supplier can be searched now ({len(found.candidates)} found by {found.source}).",
                "candidates": [{"id": c.id, "name": c.name, "reason": c.reason} for c in found.candidates],
            })
        request = draft_request(insights, suppliers, self.distributor(), self.settings.demo_zip)
        plan = {p.item_id: p for p in due_supplies(await self.planner.view())}
        due = [i for i in request.items if i.id in plan]
        for item in due:
            item.quantity = plan[item.id].recommended_qty
        if due:
            request.items = due
        self._prewarm(request)
        return request

    def warm_up(self) -> None:
        """Build the planner in the background at start, so the first request does not wait for it.

        The first build sends every forecast miss of the year to J11 (about 80 s with live Jev and
        Step 5, more when the APIs are slow). Later builds use the cached answers."""
        self._warm = asyncio.create_task(self._warm_planner())

    async def _warm_planner(self) -> None:
        try:
            await self.planner.view()
        except Exception as exc:  # a failed warm-up only means the first request builds it
            capture(exc)

    def _prewarm(self, request: ShoppingRequest) -> None:
        """Start the adapter lanes' browsers while the owner reviews the list (live browsers only)."""
        if self.modes.get("browser") != "live":
            return
        from app.lanes.registry import registered

        adapters = set(registered()["adapters"])  # type: ignore[arg-type]
        suppliers = [s for s in request.suppliers if s.adapter in adapters]
        if not suppliers:
            return
        with contextlib.suppress(ImportError):  # the browser extra is optional
            from app.lanes.live.warm import prewarm

            asyncio.get_running_loop().create_task(prewarm(suppliers))

    async def aclose(self) -> None:
        if self._warm is not None and not self._warm.done():
            self._warm.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._warm
        with contextlib.suppress(ImportError):
            from app.lanes.live.warm import close_all

            await close_all()
        await self.orchestrator.shutdown()
        await self.jev.aclose()
        await self.llm.aclose()
        self.db.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    init_sentry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.s = AppState(settings)
        await app.state.s.orchestrator.fail_orphans()
        app.state.s.warm_up()
        yield
        await app.state.s.aclose()

    app = FastAPI(title="Supplier Agent Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["*"])
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/evidence", StaticFiles(directory=settings.evidence_dir), name="evidence")

    def state(request: Request) -> AppState:
        return request.app.state.s

    def require_admin(x_admin_token: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_admin_token, settings.admin_token.get_secret_value()):
            raise HTTPException(401, "admin token required")

    # ------------------------------------------------------------ read side

    @app.get("/health")
    async def health(s: AppState = Depends(state)) -> dict[str, Any]:
        return {"ok": True, "runMode": settings.run_mode, "modes": s.modes.as_dict()}

    @app.get("/insights", response_model=SpendInsights)
    async def insights(s: AppState = Depends(state)) -> SpendInsights:
        return await s.insights()

    @app.get("/suppliers", response_model=DiscoveryResult)
    async def suppliers(category: str = DEFAULT_CATEGORY, zip: str | None = None,
                        s: AppState = Depends(state)) -> DiscoveryResult:
        items = [i.bill_item_name for i in (await s.insights()).items]
        return await s.discovery.discover(category, zip or settings.demo_zip, s.distributor(), items=items)

    @app.get("/shopping-list/draft", response_model=ShoppingRequest)
    async def draft(category: str = DEFAULT_CATEGORY, s: AppState = Depends(state)) -> ShoppingRequest:
        return await s.draft(category)

    # ------------------------------------------------------------ restock planner (spec §13)

    @app.get("/planner", response_model=PlannerView)
    async def planner_view(s: AppState = Depends(state)) -> PlannerView:
        return await s.planner.view()

    @app.post("/planner/check", response_model=PlannerCheck)
    async def planner_check(autostart: bool = False, authorization: str = Header(default=""),
                            s: AppState = Depends(state)) -> PlannerCheck:
        """Daily check (a CREAO scheduled run calls this). autostart=true starts a sourcing job.

        The tunnel makes this route public, so it needs `Authorization: Bearer <RESTOCK_TOKEN>`.
        Without a token configured, it is open only in fixture mode."""
        token = settings.restock_token.get_secret_value() if settings.restock_token else None
        if token is None and settings.run_mode == "live":
            raise HTTPException(503, "set RESTOCK_TOKEN before exposing /planner/check")
        if token is not None and not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "bearer token required")
        view = await s.planner.view()
        due = due_supplies(view)
        if not due:
            return PlannerCheck(due=[], message="No supply needs an order this week.")
        job_id = None
        if autostart:
            # One job per calendar day and order: a repeat call on the same day gets the same job,
            # but a new day or changed quantities start a new one. (view.as_of is the last week of
            # the data, so it never changes and cannot be the key alone.)
            order = stable_hash(sorted((p.item_id, p.recommended_qty) for p in due))[:12]
            created = await s.orchestrator.create_job(await s.draft(), f"planner-{date.today().isoformat()}-{order}")
            job_id = created.job_id
        names = ", ".join(f"{p.name} ({p.recommended_qty} {_plural(p.unit)})" for p in due)
        return PlannerCheck(due=due, job_id=job_id, message=f"Order soon: {names}.")

    @app.post("/planner/owner-messages", response_model=ForecastAdjustment)
    async def owner_message(body: OwnerMessage, s: AppState = Depends(state)) -> ForecastAdjustment:
        return await s.planner.owner_message(body.text, body.received_at)

    @app.post("/planner/adjustments/{adjustment_id}/confirm", response_model=ForecastAdjustment)
    async def confirm_adjustment(adjustment_id: str, accept: bool = True,
                                 s: AppState = Depends(state)) -> ForecastAdjustment:
        try:
            return await s.planner.confirm_adjustment(adjustment_id, accept)
        except KeyError as exc:
            raise HTTPException(404, "adjustment not found") from exc

    @app.post("/planner/misses/{miss_id}/answer", response_model=PlannerView)
    async def answer_miss(miss_id: str, body: MissAnswer, s: AppState = Depends(state)) -> PlannerView:
        try:
            return await s.planner.answer_miss(miss_id, body.cause)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/planner/backtest", response_model=BacktestReport)
    async def planner_backtest(s: AppState = Depends(state)) -> BacktestReport:
        return await s.planner.backtest()

    # ------------------------------------------------------------ jobs

    @app.post("/jobs", response_model=JobCreated, status_code=202)
    async def create_job(body: ShoppingRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"),
                         s: AppState = Depends(state)) -> JobCreated:
        if not 8 <= len(idempotency_key) <= 128:
            raise HTTPException(422, "Idempotency-Key must be 8 to 128 characters")
        sent = stable_hash(body.model_dump(mode="json", by_alias=True))
        if s.db.claim_request_hash(idempotency_key, sent) != sent:
            raise HTTPException(422, {"code": "idempotency_key_reused",
                                      "text": "This Idempotency-Key was sent before with another request."})
        return await s.orchestrator.create_job(body, idempotency_key)

    @app.get("/jobs", response_model=list[JobView])
    async def list_jobs(limit: int = Query(50, ge=1, le=200), s: AppState = Depends(state)) -> list[JobView]:
        return s.orchestrator.list_views(limit)

    @app.get("/jobs/{job_id}", response_model=JobView)
    async def get_job(job_id: str, s: AppState = Depends(state)) -> JobView:
        view = s.orchestrator.get_view(job_id)
        if view is None:
            raise HTTPException(404, "job not found")
        return view

    @app.get("/rules", response_model=SpendingRules)
    async def default_rules(s: AppState = Depends(state)) -> SpendingRules:
        """The rules a new order gets: derived from the books, with the draft's suppliers approved."""
        request = await s.draft()
        approved = [x.id for x in request.suppliers] + ([request.distributor.id] if request.distributor else [])
        return rules_from_books(s.books, await s.insights(), approved)

    @app.get("/jobs/{job_id}/rules", response_model=SpendingRules)
    async def get_rules(job_id: str, s: AppState = Depends(state)) -> SpendingRules:
        rules = s.orchestrator.rules_for(job_id)
        if rules is None:
            raise HTTPException(404, "job not active")
        return rules

    @app.get("/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, after: int = Query(0, ge=0, le=MAX_SEQ),
                         follow: bool = Query(True, description="false: send the events so far, then close"),
                         last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
                         s: AppState = Depends(state)) -> StreamingResponse:
        if s.orchestrator.get_view(job_id) is None:
            raise HTTPException(404, "job not found")
        start = min(int(last_event_id), MAX_SEQ) if last_event_id and last_event_id.isdigit() else after

        def frame(event) -> str:
            return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json(by_alias=True)}\n\n"

        async def stream() -> AsyncIterator[str]:
            yield "retry: 2000\n\n"
            view = s.orchestrator.get_view(job_id)
            if not follow or (view is not None and view.state.value in TERMINAL_JOB_STATES):
                for event in s.db.events_after(job_id, start, limit=100_000):
                    yield frame(event)
                return
            async for event in s.bus.stream(job_id, start):
                if await request.is_disconnected():
                    break
                if event is None:
                    yield ": keepalive\n\n"
                    continue
                yield frame(event)
                if event.type == "job_state" and event.data.get("state") in TERMINAL_JOB_STATES:
                    break  # nothing more will happen; the client may reconnect with Last-Event-ID

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/jobs/{job_id}/approve", response_model=JobView)
    async def approve(job_id: str, body: ApprovalDecision, s: AppState = Depends(state)) -> JobView:
        try:
            return await s.orchestrator.approve(job_id, body)
        except ApprovalError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    # ------------------------------------------------------------ operations

    @app.get("/health/preflight", response_model=PreflightReport)
    async def preflight(apply: bool = True, s: AppState = Depends(state)) -> PreflightReport:
        return await run_preflight(settings, s.modes, s.jev, s.llm, dataset_loaded=s.books.dataset_loaded,
                                   injected_loaded=bool(s.books.distributor), apply=apply)

    @app.get("/admin/mode", dependencies=[Depends(require_admin)])
    async def get_modes(s: AppState = Depends(state)) -> dict[str, str]:
        return s.modes.as_dict()

    @app.post("/admin/mode", dependencies=[Depends(require_admin)])
    async def set_mode(change: ModeChange, s: AppState = Depends(state)) -> dict[str, str]:
        if change.dependency not in DEPENDENCIES:
            raise HTTPException(422, f"dependency must be one of {DEPENDENCIES}")
        s.modes.set(change.dependency, change.mode)
        return s.modes.as_dict()

    @app.post("/admin/replay/{source_job_id}", response_model=JobCreated, dependencies=[Depends(require_admin)])
    async def replay(source_job_id: str, speed: float = 1.0, s: AppState = Depends(state)) -> JobCreated:
        try:
            return await s.orchestrator.replay(source_job_id, speed)
        except KeyError as exc:
            raise HTTPException(404, "source job not found") from exc

    @app.get("/admin/decisions", dependencies=[Depends(require_admin)])
    async def decisions(j_id: str | None = None, s: AppState = Depends(state)) -> list[dict]:
        return s.db.decisions(j_id)

    @app.post("/webhooks/{provider}")
    async def webhook(provider: str, request: Request, x_webhook_secret: str = Header(default=""),
                      s: AppState = Depends(state)) -> JSONResponse:
        if provider not in ("photon", "elevenlabs"):
            raise HTTPException(404, "unknown provider")
        if not hmac.compare_digest(x_webhook_secret, settings.webhook_secret.get_secret_value()):
            raise HTTPException(401, "bad webhook secret")
        try:
            payload = json.loads(await request.body() or b"{}")
        except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "body must be a JSON object")
        event_id = str(payload.get("eventId") or payload.get("id") or "")
        if event_id and not s.db.first_time_webhook(provider, event_id):
            return JSONResponse({"duplicate": True})
        routed = await s.orchestrator.route_webhook(provider, payload)
        return JSONResponse({"routed": routed})

    return app


app = create_app()
