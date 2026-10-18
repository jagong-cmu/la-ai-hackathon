"""End to end in fixture mode: no keys, the real orchestrator, real stages, fixture lanes."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def wait_for(client: TestClient, job_id: str, states: set[str], timeout: float = 20) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        view = client.get(f"/jobs/{job_id}").json()
        if view["state"] in states:
            return view
        time.sleep(0.05)
    raise AssertionError(f"job stayed in {view['state']}")


def wait_until(client: TestClient, job_id: str, predicate, timeout: float = 20) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        view = client.get(f"/jobs/{job_id}").json()
        if predicate(view):
            return view
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def test_insights_from_injected_bills(client):
    body = client.get("/insights").json()
    ids = [i["itemId"] for i in body["items"]]
    assert set(ids) == {"syn-gloves", "syn-bakery-boxes", "syn-parchment", "syn-bread-bags"}
    gloves = next(i for i in body["items"] if i["itemId"] == "syn-gloves")
    # Generated from real sales: whole packs of 1,000, a weekly-or-slower cadence, labelled synthetic.
    assert gloves["pastOrderQty"] % 1000 == 0 and gloves["orderCadenceDays"] >= 7 and gloves["synthetic"]
    assert body["disclosure"]


def test_discovery_filters_the_directory(client):
    body = client.get("/suppliers").json()
    by_id = {c["id"]: c for c in body["candidates"]}
    assert not by_id["echo"]["selected"]  # a directory, not a seller (J3)
    assert {c for c, v in by_id.items() if v["selected"]} == {"alpha", "bravo", "charlie", "delta"}


def test_full_flow_to_payment_stop(client):
    draft = client.get("/shopping-list/draft").json()
    assert draft["distributor"]["id"] == "vend-syn-larkmoor"

    r = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-job-0001"})
    assert r.status_code == 202
    job_id = r.json()["jobId"]
    again = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-job-0001"}).json()
    assert again["jobId"] == job_id  # idempotent create

    view = wait_for(client, job_id, {"results_ready"})
    lanes = {lane["supplierId"]: lane for lane in view["lanes"]}
    assert lanes["charlie"]["state"] == "blocked" and lanes["charlie"]["reason"] == "bot_check"
    assert lanes["alpha"]["state"] == "cart_ready" and lanes["bravo"]["state"] == "cart_ready"

    results = view["results"]
    best = next(p for p in results["plans"] if p["planId"] == results["recommendedPlanId"])
    assert best["status"] == "verified" and best["kind"] == "split"
    assert best["rulesCheck"]["pass"] is True
    assert results["savingsVsPaidBefore"] > 0
    assert results["quotedPlanId"] is not None  # the distributor's phone quote
    quoted = next(p for p in results["plans"] if p["planId"] == results["quotedPlanId"])
    assert quoted["total"] < best["total"]  # the call beat the web price
    # Every price carries evidence: the phone quote links to the saved call transcript.
    quoted_ids = {c["supplierId"] for c in quoted["carts"] if c["status"] == "quoted"}
    offer = next(o for o in view["offers"] if o["supplierId"] in quoted_ids and o["source"] == "call")
    transcript_url = offer["evidence"]["transcriptUrl"]
    assert transcript_url == f"/evidence/{job_id}/{offer['supplierId']}/call.txt"
    transcript = client.get(transcript_url)
    assert transcript.status_code == 200 and "supplier:" in transcript.text
    assert any(w["code"] == "lane_blocked" for w in results["warnings"])

    # An estimated or quoted plan cannot be approved without the right switch.
    r = client.post(f"/jobs/{job_id}/approve", json={"action": "approve", "planId": quoted["planId"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "quoted_not_accepted"

    decision = {"action": "approve", "planId": best["planId"]}
    r = client.post(f"/jobs/{job_id}/approve", json=decision)
    assert r.status_code == 200
    assert client.post(f"/jobs/{job_id}/approve", json=decision).status_code == 200  # idempotent
    assert client.post(f"/jobs/{job_id}/approve", json={"action": "reject"}).status_code == 409

    done = wait_for(client, job_id, {"at_payment", "partial_handoff"})
    assert done["state"] == "at_payment"
    assert {h["status"] for h in done["handoffs"]} == {"at_payment"}
    assert all(c["simulated"] for c in done["cards"])

    all_events = _all_events(client, job_id)
    types = [e["type"] for e in all_events]
    assert types.index("job_started") < types.index("results_ready") < types.index("approved")
    assert "guard_stop" in types and "call_offer" in types and "card_minted" in types
    seqs = [e["seq"] for e in all_events]
    assert seqs == list(range(1, len(seqs) + 1))  # no gaps


def _all_events(client: TestClient, job_id: str) -> list[dict]:
    import json

    # The job is terminal, so the live stream ends on its own after the final job_state.
    with client.stream("GET", f"/jobs/{job_id}/events") as resp:
        return [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]


def test_sse_resumes_from_last_event_id(client):
    draft = client.get("/shopping-list/draft").json()
    job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-job-0002"}).json()["jobId"]
    wait_for(client, job_id, {"results_ready"})
    resp = client.get(f"/jobs/{job_id}/events?follow=false", headers={"Last-Event-ID": "5"})
    ids = [int(line[4:]) for line in resp.text.splitlines() if line.startswith("id: ")]
    assert ids[0] == 6 and ids == list(range(6, 6 + len(ids)))


def test_admin_requires_token(client):
    assert client.get("/admin/mode").status_code == 401
    r = client.post("/admin/mode", json={"dependency": "jev", "mode": "cached"},
                    headers={"X-Admin-Token": "dev-admin"})
    assert r.status_code == 200 and r.json()["jev"] == "cached"


def test_webhook_secret_and_dedupe(client):
    assert client.post("/webhooks/photon", json={"eventId": "e1"}).status_code == 401
    h = {"X-Webhook-Secret": "dev-webhook"}
    assert client.post("/webhooks/photon", json={"eventId": "e1"}, headers=h).json() == {"routed": False}
    assert client.post("/webhooks/photon", json={"eventId": "e1"}, headers=h).json() == {"duplicate": True}


def test_preflight_runs_in_fixture_mode(client):
    report = client.get("/health/preflight").json()
    names = {c["name"] for c in report["checks"]}
    assert {"jev", "step5", "browser_lanes", "injected_bills", "checkout_contact"} <= names


def test_deadline_gives_partial_results_then_an_update(settings):
    # The unknown-site lane (delta) needs ~1.7 s at this time scale; the deadline is 0.6 s.
    fast = settings.model_copy(update={"job_deadline_s": 0.6})
    with TestClient(create_app(fast)) as client:
        draft = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-job-0003"}).json()["jobId"]
        first = wait_for(client, job_id, {"results_ready"})
        assert first["results"]["deadlineHit"] is True and first["results"]["status"] == "partial"
        later = wait_until(client, job_id, lambda v: v["results"] and v["results"]["status"] == "complete")
        assert later["results"]["deadlineHit"] is False
        types = [line[7:] for line in client.get(f"/jobs/{job_id}/events?follow=false").text.splitlines()
                 if line.startswith("event: ")]
        assert "deadline_hit" in types and "results_updated" in types


def test_full_flow_with_the_milp_path(client, monkeypatch):
    """The same job twice: full enumeration, then the MILP for every re-plan. Same recommendation."""
    import app.stages.optimize as optimize_mod
    import app.stages.solver as solver

    draft = client.get("/shopping-list/draft").json()
    job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-job-enum"}).json()["jobId"]
    enum_results = wait_for(client, job_id, {"results_ready"})["results"]

    calls = []
    real = solver.candidate_assignments
    monkeypatch.setattr(solver, "candidate_assignments", lambda *a, **kw: calls.append(1) or real(*a, **kw))
    monkeypatch.setattr(optimize_mod, "MAX_ASSIGNMENTS", 0)
    job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-job-milp"}).json()["jobId"]
    view = wait_for(client, job_id, {"results_ready"})
    results = view["results"]
    assert calls  # every re-plan went through the MILP

    def best(r: dict) -> dict:
        return next(p for p in r["plans"] if p["planId"] == r["recommendedPlanId"])

    assert (best(results)["label"], best(results)["total"]) == (best(enum_results)["label"],
                                                                 best(enum_results)["total"])
    assert all("riskPenalty" in p for p in results["plans"])

    r = client.post(f"/jobs/{job_id}/approve", json={"action": "approve", "planId": results["recommendedPlanId"]})
    assert r.status_code == 200
    assert wait_for(client, job_id, {"at_payment", "partial_handoff"})["state"] == "at_payment"


def test_verify_phase_checks_the_best_plan_at_checkout(settings, monkeypatch):
    """Carts that only showed estimates are checked at checkout before results: the lane reads the
    real total on the payment page, the plan becomes verified, and approval reuses that page."""
    from app.config import CheckoutContact
    from app.contracts import CheckoutHandoff
    from app.lanes.fixture import FixtureLane

    real_set_cart = FixtureLane.set_cart
    quotes: list[str] = []
    checkouts: list[str] = []

    async def estimated_cart(self, ctx, lines):
        cart = await real_set_cart(self, ctx, lines)
        self._true_cart = cart  # what checkout will show
        return cart.model_copy(update={"verification_status": "estimated", "reason": "shipping_not_shown"})

    async def quote(self, ctx, contact, lines):
        quotes.append(self.supplier.id)
        handoff = CheckoutHandoff(supplier_id=self.supplier.id, status="at_payment", live_url="https://live.example")
        return self._true_cart, handoff

    real_checkout = FixtureLane.checkout

    async def counted_checkout(self, ctx, contact):
        checkouts.append(self.supplier.id)
        return await real_checkout(self, ctx, contact)

    monkeypatch.setattr(FixtureLane, "set_cart", estimated_cart)
    monkeypatch.setattr(FixtureLane, "quote", quote, raising=False)
    monkeypatch.setattr(FixtureLane, "checkout", counted_checkout)
    live = settings.model_copy(update={"checkout_contact": CheckoutContact(address1="325 Westwood Plaza")})
    with TestClient(create_app(live)) as client:
        draft = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-verify"}).json()["jobId"]
        view = wait_for(client, job_id, {"results_ready"})
        results = view["results"]
        best = next(p for p in results["plans"] if p["planId"] == results["recommendedPlanId"])
        assert best["status"] == "verified"  # every cart of the best plan was checked at checkout
        assert set(quotes) == {c["supplierId"] for c in best["carts"]}

        r = client.post(f"/jobs/{job_id}/approve", json={"action": "approve", "planId": best["planId"]})
        assert r.status_code == 200
        done = wait_for(client, job_id, {"at_payment", "partial_handoff"})
        assert done["state"] == "at_payment"
        assert checkouts == []  # the lanes were already at payment with these carts
        assert {h["liveUrl"] for h in done["handoffs"]} == {"https://live.example"}
        events = _all_events(client, job_id)
        types = [e["type"] for e in events]
        verified = {e["data"]["cartKey"] for e in events if e["type"] == "cart_verified"}
        assert {c["cartKey"] for c in best["carts"]} <= verified  # a later round may check runner-ups too
        assert types.index("cart_verified") < types.index("results_ready")


def test_verify_phase_skips_sites_without_checkout_quotes(settings, monkeypatch):
    """A site marked quote_at_checkout=False (WebstaurantStore) keeps its cart estimate."""
    from app.config import CheckoutContact
    from app.lanes.fixture import FixtureLane

    quotes: list[str] = []

    async def quote(self, ctx, contact, lines):
        quotes.append(self.supplier.id)
        raise AssertionError("must not be called")

    real_set_cart = FixtureLane.set_cart

    async def estimated_cart(self, ctx, lines):
        cart = await real_set_cart(self, ctx, lines)
        return cart.model_copy(update={"verification_status": "estimated", "reason": "tax_not_shown; total_not_shown"})

    monkeypatch.setattr(FixtureLane, "set_cart", estimated_cart)
    monkeypatch.setattr(FixtureLane, "quote", quote, raising=False)
    monkeypatch.setattr(FixtureLane, "quote_at_checkout", False, raising=False)
    live = settings.model_copy(update={"checkout_contact": CheckoutContact(address1="325 Westwood Plaza")})
    with TestClient(create_app(live)) as client:
        draft = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-no-quote"}).json()["jobId"]
        results = wait_for(client, job_id, {"results_ready"})["results"]
        web = [p for p in results["plans"] if p["kind"] != "quoted_bundle"]
        assert quotes == []
        assert web and all(p["status"] != "verified" for p in web if not any(c["status"] == "quoted" for c in p["carts"]))


def test_jobs_list_and_default_rules(client):
    assert client.get("/jobs").json() == []
    draft = client.get("/shopping-list/draft").json()
    job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "list-test-0001"}).json()["jobId"]
    jobs = client.get("/jobs").json()
    assert [j["jobId"] for j in jobs] == [job_id]
    assert client.get("/jobs", params={"limit": 0}).status_code == 422

    rules = client.get("/rules").json()
    assert rules["perOrderCap"] > 0
    assert set(s["id"] for s in draft["suppliers"]) <= set(rules["approvedSuppliers"])


def test_lane_state_carries_the_live_view_as_soon_as_open_sets_it(settings, monkeypatch):
    """The first "searching" event goes out before open(), so the UI needs a second one with the URL."""
    import json

    from app.lanes.fixture import FixtureLane

    real_open = FixtureLane.open

    async def open_with_live_view(self, ctx):
        await real_open(self, ctx)
        ctx.live_url = f"https://live.example/{self.supplier.id}"

    monkeypatch.setattr(FixtureLane, "open", open_with_live_view)
    with TestClient(create_app(settings)) as client:
        draft = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-live-url"}).json()["jobId"]
        wait_for(client, job_id, {"results_ready"})
        resp = client.get(f"/jobs/{job_id}/events", params={"follow": "false"})  # results_ready is not final
        events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    for lane_id in {e["laneId"] for e in events if e["type"] == "offers_ready"}:
        lane = [e for e in events if e.get("laneId") == lane_id]
        types = [e["type"] for e in lane]
        live = [i for i, e in enumerate(lane) if e["type"] == "lane_state" and e["data"].get("liveUrl")]
        assert live and live[0] < types.index("offers_ready"), lane_id


def test_draft_with_no_selected_supplier_is_a_503_not_a_crash(client, monkeypatch):
    """Live 2026-10-18 (SUPPLIER-AGENT-API-8): discovery selected no supplier, and the draft raised
    a ShoppingRequest ValidationError (500). The owner and the daily check need the reason instead."""
    discovery = client.app.state.s.discovery
    real = discovery.discover

    async def none_selected(*args, **kwargs):
        found = await real(*args, **kwargs)
        for c in found.candidates:
            c.selected = False
            c.reason = c.reason or "cannot run in this browser mode"
        return found

    monkeypatch.setattr(discovery, "discover", none_selected)
    r = client.get("/shopping-list/draft")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "no_suppliers" and detail["text"]
    assert detail["candidates"] and all(c["reason"] for c in detail["candidates"])
    check = client.post("/planner/check", params={"autostart": "true"})
    assert check.status_code in (200, 503)  # never a 500


def test_following_a_replay_streams_every_event(client, settings, monkeypatch):
    """A replay copies its source's final view at once, so the events route closed the live stream
    after the events emitted so far (soak S-003). A follower must get the whole playback."""
    draft = client.get("/shopping-list/draft").json()
    src = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-replay-01"}).json()["jobId"]
    wait_for(client, src, {"results_ready"})
    client.post(f"/jobs/{src}/approve", json={"action": "reject"})
    wait_for(client, src, {"rejected"})
    source_events = _all_events(client, src)
    # The reason comes before the terminal job_state, which a follower's stream ends on.
    assert [e["type"] for e in source_events[-2:]] == ["rejected", "job_state"]

    bus = client.app.state.s.bus
    real_emit = bus.emit

    async def slow_emit(job_id, *args, **kwargs):
        if job_id != src:
            await asyncio.sleep(0.01)  # stretch the playback so the follower joins while it runs
        return await real_emit(job_id, *args, **kwargs)

    monkeypatch.setattr(bus, "emit", slow_emit)
    token = {"X-Admin-Token": settings.admin_token.get_secret_value()}
    rid = client.post(f"/admin/replay/{src}", params={"speed": 1000}, headers=token).json()["jobId"]
    replayed = _all_events(client, rid)
    assert [e["type"] for e in replayed] == ["replay_started"] + [e["type"] for e in source_events]
