"""End to end in fixture mode: no keys, the real orchestrator, real stages, fixture lanes."""

from __future__ import annotations

import asyncio
import json
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


def test_bills_give_the_price_history(client):
    insights = {i["itemId"]: i for i in client.get("/insights").json()["items"]}
    lines = client.get("/bills", params={"itemId": "syn-gloves"}).json()
    assert lines and all(x["itemId"] == "syn-gloves" for x in lines)
    assert [x["txnDate"] for x in lines] == sorted(x["txnDate"] for x in lines)
    # The past unit price on the insights is the quantity-weighted mean of these lines.
    qty = sum(x["quantity"] for x in lines)
    assert abs(sum(x["amount"] for x in lines) / qty - insights["syn-gloves"]["pastUnitPrice"]) < 1e-3
    assert {x["itemId"] for x in client.get("/bills").json()} == set(insights)


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


def test_webhook_bad_bodies_are_400_not_500(client):
    """Soak S-004: a body that is not a JSON object, or a jobId that is not a string, raised (500)."""
    h = {"X-Webhook-Secret": "dev-webhook", "Content-Type": "application/json"}
    for raw in (b"{not json", b"[1, 2]", b'"text"', b"null", b"\xff\xfe"):
        assert client.post("/webhooks/photon", content=raw, headers=h).status_code == 400, raw
    r = client.post("/webhooks/photon", json={"jobId": ["a"], "eventId": {"x": 1}}, headers=h)
    assert r.status_code == 200 and r.json() == {"routed": False}


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


def test_reused_idempotency_key_with_another_body_is_422(client):
    """Soak S-005: the same key with a changed body silently returned the first job."""
    draft = client.get("/shopping-list/draft").json()
    first = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-reuse-01"}).json()["jobId"]
    assert client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-reuse-01"}).json()["jobId"] == first
    draft["items"][0]["quantity"] += 1
    r = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-reuse-01"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "idempotency_key_reused"
    wait_for(client, first, {"results_ready"})


def test_jobs_left_unfinished_by_a_restart_fail_at_start(settings):
    """Soak S-006: after a restart, a gathering or results_ready job had no process. Its event stream
    never ended and approve gave 404, so a watcher waited forever. Start fails them with a reason."""
    from app.contracts import JobState, JobView, ShoppingRequest
    from app.db import Database

    with TestClient(create_app(settings)) as c:
        request = ShoppingRequest.model_validate(c.get("/shopping-list/draft").json())
    db = Database(settings.db_path)
    for jid, state in (("job_orphan0001", JobState.gathering), ("job_orphan0002", JobState.results_ready)):
        db.save_job(JobView(job_id=jid, state=state, request=request), idempotency_key=f"key-{jid}")
    db.close()

    with TestClient(create_app(settings)) as c:
        for jid in ("job_orphan0001", "job_orphan0002"):
            assert c.get(f"/jobs/{jid}").json()["state"] == "failed"
            events = _all_events(c, jid)  # the live stream ends
            assert [e["type"] for e in events[-2:]] == ["job_failed", "job_state"]
            assert "restart" in events[-2]["data"]["error"]
        body = request.model_dump(mode="json", by_alias=True)
        again = c.post("/jobs", json=body, headers={"Idempotency-Key": "key-job_orphan0001"}).json()["jobId"]
        assert again != "job_orphan0001"  # the key starts a new job
        wait_for(c, again, {"results_ready"})


def test_late_update_does_not_replace_results_after_approval(settings, monkeypatch):
    """Soak S-007: _late() checks for an approval, then awaits the summary (Step 5 takes ~12 s live),
    then sets view.results. An approval during that wait got results_updated after it."""
    import threading

    fast = settings.model_copy(update={"job_deadline_s": 0.9})  # alpha/bravo verified, delta still searching
    with TestClient(create_app(fast)) as client:
        orch = client.app.state.s.orchestrator
        real = orch.llm.summarize
        late_started, release = threading.Event(), asyncio.Event()
        calls = []

        async def summarize(facts):
            calls.append(1)
            if len(calls) == 2:  # the late update's summary
                late_started.set()
                await release.wait()
            return await real(facts)

        monkeypatch.setattr(orch.llm, "summarize", summarize)
        draft = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-late-01"}).json()["jobId"]
        first = wait_for(client, job_id, {"results_ready"})
        plan_id = first["results"]["recommendedPlanId"]
        assert plan_id and first["results"]["deadlineHit"]
        assert late_started.wait(10)
        assert client.post(f"/jobs/{job_id}/approve", json={"action": "approve", "planId": plan_id}).status_code == 200
        client.portal.call(release.set)
        view = wait_for(client, job_id, {"at_payment", "partial_handoff"})
        time.sleep(0.2)
        view = client.get(f"/jobs/{job_id}").json()
        assert plan_id in [p["planId"] for p in view["results"]["plans"]]
        types = [line[7:] for line in client.get(f"/jobs/{job_id}/events?follow=false").text.splitlines()
                 if line.startswith("event: ")]
        assert "results_updated" not in types[types.index("approved"):]


def test_negative_past_price_or_cadence_is_refused(client):
    """Soak S-011: a negative pastUnitPrice was accepted. call_anchor() then anchored the distributor
    call on a negative price ("we paid about $-..."), and a negative cadence gave negative savings."""
    draft = client.get("/shopping-list/draft").json()
    for field, value in (("pastUnitPrice", -3), ("orderCadenceDays", -7)):
        body = json.loads(json.dumps(draft))
        body["items"][0][field] = value
        r = client.post("/jobs", json=body, headers={"Idempotency-Key": f"test-neg-{field}"})
        assert r.status_code == 422, (field, r.status_code)


def test_evidence_stays_in_the_evidence_folder(settings):
    """Soak S-012: the request's distributor and supplier ids are path parts of the evidence files.
    distributor.id='../../../escaped' wrote escaped/call.txt outside var/evidence (POST /jobs is public)."""
    from app.contracts import path_segment

    assert path_segment("../../x") not in ("..", "") and "/" not in path_segment("../../x")
    assert path_segment("vend-syn-larkmoor") == "vend-syn-larkmoor"  # normal ids keep their name
    root = settings.var_dir.parent
    with TestClient(create_app(settings)) as c:
        d = c.get("/shopping-list/draft").json()
        d["distributor"]["id"] = "../../../escaped"
        d["suppliers"][0]["id"] = "../../../escaped-lane"
        d["items"][0]["id"] = "../../../../escaped-item"
        jid = c.post("/jobs", json=d, headers={"Idempotency-Key": "test-trav-01"}).json()["jobId"]
        view = wait_for(c, jid, {"results_ready"})
        evidence = settings.evidence_dir.resolve()
        outside = [p for p in root.rglob("*escaped*") if evidence not in p.resolve().parents]
        assert not outside, outside
        lanes = {lane["supplierId"]: lane for lane in view["lanes"]}
        assert lanes["bravo"]["state"] == "cart_ready", lanes["bravo"]  # an odd item id does not break a lane
        urls = [o["evidence"]["transcriptUrl"] for o in view["offers"] if o["evidence"].get("transcriptUrl")]
        assert urls and all(c.get(u).status_code == 200 for u in urls)  # the served path still works


def test_lane_evidence_names_stay_in_the_lane_folder(settings, decider, tmp_path):
    """S-012: lanes name files after request item ids (listing_{item.id}.json)."""
    from app.contracts import SupplierRef
    from tests.test_lanes_live import make_ctx

    ctx, _ = make_ctx(settings, decider, tmp_path, SupplierRef(id="../up", name="X", url="https://x.example"))
    path, url = ctx.evidence("listing_../../../../escaped.json")
    assert ctx.evidence_dir.resolve() in path.resolve().parents
    assert ".." not in url.split("/")


def test_supplier_url_must_be_web(client):
    """Soak S-013 (security): POST /jobs is public, and a live lane opens supplier.url. file:// or
    javascript: URLs were accepted. A URL with no scheme (CREAO gives www.example.com) gets https://."""
    from app.contracts import SupplierRef

    assert SupplierRef(id="k", name="K", url="www.katom.com").url == "https://www.katom.com"
    draft = client.get("/shopping-list/draft").json()
    for url in ("file:///etc/passwd", "javascript:alert(1)", "chrome://settings", "ftp://x.example/"):
        body = json.loads(json.dumps(draft))
        body["suppliers"][0]["url"] = url
        r = client.post("/jobs", json=body, headers={"Idempotency-Key": f"test-url-{abs(hash(url))}"})
        assert r.status_code == 422, url


def test_fixture_lane_path_ignores_traversal():
    from app.lanes.fixture import FIXTURES_DIR, fixture_path

    assert (FIXTURES_DIR / "suppliers") in fixture_path("../discovery").parents
    assert not fixture_path("../discovery").exists()


def test_discovery_skips_a_candidate_with_a_bad_url():
    from app.contracts import DiscoveryResult, SupplierCandidate
    from app.discovery import selected_suppliers

    found = DiscoveryResult(category="c", zip_code="1", distributor=None, source="creao", candidates=[
        SupplierCandidate(id="ok", name="OK", url="www.ok.example", selected=True),
        SupplierCandidate(id="bad", name="Bad", url="javascript:alert(1)", selected=True),
        SupplierCandidate(id="none", name="None", url="", selected=True),
    ])
    assert [(s.id, s.url) for s in selected_suppliers(found)] == [("ok", "https://www.ok.example")]


def test_huge_numbers_are_refused_not_overflowed(client):
    """Soak S-014: after=10**25 (or Last-Event-ID) overflowed SQLite's 64-bit integer and broke the
    stream (200 with an empty body). quantity=10**25 gave totals like 8.799999999999999e+24."""
    draft = client.get("/shopping-list/draft").json()
    job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-big-0001"}).json()["jobId"]
    wait_for(client, job_id, {"results_ready"})
    big = "9" * 25
    assert client.get(f"/jobs/{job_id}/events", params={"follow": "false", "after": big}).status_code == 422
    r = client.get(f"/jobs/{job_id}/events", params={"follow": "false"}, headers={"Last-Event-ID": big})
    assert r.status_code == 200 and r.text.startswith("retry:")  # an id past the end: no events, a clean stream
    body = json.loads(json.dumps(draft))
    body["items"][0]["quantity"] = 10**25
    assert client.post("/jobs", json=body, headers={"Idempotency-Key": "test-big-0002"}).status_code == 422


def test_a_stored_job_that_breaks_a_newer_rule_does_not_stop_the_server(settings):
    """Soak S-018: S-011 made pastUnitPrice ge=0. A job stored before that (-3.0) made the start fail
    ('Application startup failed') in fail_orphans(), and GET /jobs failed too."""
    from app.contracts import JobState, JobView, ShoppingRequest
    from app.db import Database

    with TestClient(create_app(settings)) as c:
        request = ShoppingRequest.model_validate(c.get("/shopping-list/draft").json())
    db = Database(settings.db_path)
    for jid, state in (("job_legacy0001", JobState.gathering), ("job_legacy0002", JobState.rejected)):
        snap = json.loads(JobView(job_id=jid, state=state, request=request).model_dump_json(by_alias=True))
        snap["request"]["items"][0]["pastUnitPrice"] = -3.0  # valid when it was stored
        db.save_job(JobView(job_id=jid, state=state, request=request))
        db._conn.execute("UPDATE jobs SET snapshot = ? WHERE job_id = ?", (json.dumps(snap), jid))
    db.close()
    with TestClient(create_app(settings)) as c:  # starts
        assert c.get("/health").status_code == 200
        assert c.get("/jobs").status_code == 200
        assert c.get("/jobs/job_legacy0002").status_code == 404  # unreadable now, not a 500
    db = Database(settings.db_path)
    assert db._conn.execute("SELECT state FROM jobs WHERE job_id = 'job_legacy0001'").fetchone()[0] == "failed"


def test_a_very_long_supplier_id_does_not_crash_the_job(client):
    """Soak S-019 (found by the fuzzer): a 5,000-character supplier id made fixture_path().exists()
    raise OSError 'File name too long', and the whole job failed."""
    from app.contracts import path_segment

    assert len(path_segment("x" * 5000)) <= 80 and path_segment("x" * 5000) != path_segment("x" * 4999)
    draft = client.get("/shopping-list/draft").json()
    draft["suppliers"][0]["id"] = "x" * 5000
    jid = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-long-0001"}).json()["jobId"]
    view = wait_for(client, jid, {"results_ready", "failed"})
    assert view["state"] == "results_ready"


def test_replay_streams_every_recorded_event(client):
    draft = client.get("/shopping-list/draft").json()
    source = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-job-replay"}).json()["jobId"]
    view = wait_for(client, source, {"results_ready"})
    plan_id = view["results"]["recommendedPlanId"]
    assert client.post(f"/jobs/{source}/approve", json={"action": "approve", "planId": plan_id}).status_code == 200
    wait_for(client, source, {"at_payment", "partial_handoff"})
    recorded = _all_events(client, source)

    # Record what the replay's job view shows when each replayed results_ready goes out.
    s = client.app.state.s
    at_results: list[dict] = []
    emit = s.orchestrator.bus.emit

    async def recording_emit(job_id, type_, data=None, lane_id=None):
        if type_ == "results_ready" and job_id != source:
            at_results.append(s.db.load_job(job_id).model_dump(by_alias=True, mode="json"))
        return await emit(job_id, type_, data, lane_id)

    s.orchestrator.bus.emit = recording_emit
    r = client.post(f"/admin/replay/{source}?speed=50", headers={"X-Admin-Token": "dev-admin"})
    assert r.status_code == 200
    replay_id = r.json()["jobId"]
    # The replay starts like a new job instead of copying the finished one.
    started = client.get(f"/jobs/{replay_id}").json()
    assert started["replayOf"] == source
    assert started["state"] not in {"at_payment", "partial_handoff"} and started["results"] is None

    # The live stream stays open until the replay reaches the source's final state.
    replayed = _all_events(client, replay_id)
    assert replayed[0]["type"] == "replay_started"
    assert [e["type"] for e in replayed[1:]] == [e["type"] for e in recorded]
    done = client.get(f"/jobs/{replay_id}").json()
    assert done["state"] == client.get(f"/jobs/{source}").json()["state"]
    assert done["results"]["recommendedPlanId"] == plan_id

    # At results the replay shows the source's plans, but its lane tiles still follow the
    # replayed events: the source's final lanes are already stopped at payment.
    mid = at_results[0]
    assert mid["state"] == "results_ready" and mid["results"]["recommendedPlanId"] == plan_id
    assert mid["lanes"] == [] and mid["handoffs"] == [] and mid["approval"] is None


def test_finished_and_undecided_lanes_close_their_browsers(settings, monkeypatch):
    """Live 2026-10-18 (SUPPLIER-AGENT-API-G): lane browsers stayed open until a decision or the
    cloud's 15-minute timeout, even for failed lanes. After a few test jobs the account had 12
    cloud browsers open, and Browser Use answered 429 to every lane of the next job."""
    from app.lanes.fixture import FixtureLane

    closed: list[str] = []

    async def record_close(self, ctx):
        closed.append(self.supplier.id)

    monkeypatch.setattr(FixtureLane, "close", record_close)
    fast = settings.model_copy(update={"lane_idle_close_s": 0.5})
    with TestClient(create_app(fast)) as client:
        draft = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-close-01"}).json()["jobId"]
        view = wait_for(client, job_id, {"results_ready"})
        lanes = {lane["supplierId"]: lane["state"] for lane in view["lanes"]}
        ended = {sid for sid, state in lanes.items() if state in ("failed", "blocked", "idle")}
        assert ended and ended <= set(closed)  # a finished lane frees its browser at once
        assert not {sid for sid, state in lanes.items() if state == "cart_ready"} & set(closed)
        time.sleep(1.0)  # no decision within lane_idle_close_s: every lane browser closes
        assert set(closed) == set(lanes)
        assert len(closed) == len(set(closed))  # each browser closes once

        plan_id = view["results"]["recommendedPlanId"]
        assert client.post(f"/jobs/{job_id}/approve", json={"action": "approve", "planId": plan_id}).status_code == 200
        done = wait_for(client, job_id, {"at_payment", "partial_handoff"})
        assert done["state"] == "partial_handoff"
        assert {h["reason"] for h in done["handoffs"] if h["status"] == "failed"} == {"lane_closed_no_decision"}


def test_a_new_job_closes_the_browsers_of_an_undecided_job(settings, monkeypatch):
    """The account allows 10 cloud browsers and a job uses up to 8: the next job must not wait
    for the last job's idle close (live 2026-10-18: 429 on most lanes of the second job)."""
    from app.lanes.fixture import FixtureLane

    closed: list[str] = []

    async def record_close(self, ctx):
        closed.append(self.supplier.id)

    monkeypatch.setattr(FixtureLane, "close", record_close)
    with TestClient(create_app(settings)) as client:  # idle close is 300 s: it never runs here
        draft = client.get("/shopping-list/draft").json()
        first = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-free-01"}).json()["jobId"]
        lanes = {lane["supplierId"] for lane in wait_for(client, first, {"results_ready"})["lanes"]}
        assert lanes - set(closed)  # the carts stay open for a decision
        second = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-free-02"}).json()["jobId"]
        wait_for(client, second, {"results_ready"})
        assert lanes <= set(closed)


def test_a_decision_in_time_keeps_the_lane_browsers(settings, monkeypatch):
    from app.lanes.fixture import FixtureLane

    closed: list[str] = []

    async def record_close(self, ctx):
        closed.append(self.supplier.id)

    monkeypatch.setattr(FixtureLane, "close", record_close)
    slow = settings.model_copy(update={"lane_idle_close_s": 1.0})
    with TestClient(create_app(slow)) as client:
        draft = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=draft, headers={"Idempotency-Key": "test-close-02"}).json()["jobId"]
        view = wait_for(client, job_id, {"results_ready"})
        plan_id = view["results"]["recommendedPlanId"]
        assert client.post(f"/jobs/{job_id}/approve", json={"action": "approve", "planId": plan_id}).status_code == 200
        done = wait_for(client, job_id, {"at_payment", "partial_handoff"})
        time.sleep(1.5)  # the timer finds a decision and leaves the lanes alone
        assert done["state"] == "at_payment"
        kept = {h["supplierId"] for h in done["handoffs"] if h["status"] == "at_payment"}
        assert kept and not kept & set(closed)  # the payment pages stay open for the live view
