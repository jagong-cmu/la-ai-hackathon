from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.data.loader import load_books
from app.main import create_app
from app.planner.adjust import date_phrases, quantity_phrases, resolve_date
from app.planner.loop import LaunchError, MissContext, apply_cause, launch_errors, replay
from app.planner.model import LAUNCH_WEEKS, conformal_quantile, fit, launch_range
from app.planner.policy import advise
from app.planner.sales import load_fixture
from app.planner.service import Planner


def test_model_flat_series_and_range():
    f = fit([100.0] * 10)
    assert abs(f.mean(1) - 100) < 1 and f.trend == pytest.approx(0, abs=1)
    low, high = f.range(1)
    assert low <= 100 <= high


def test_model_damped_trend_does_not_run_away():
    f = fit([10.0 * i for i in range(1, 13)])
    assert f.mean(1) > 110
    assert f.mean(52) < 120 + 52 * 10  # damping keeps far horizons below a straight line


def test_date_and_quantity_phrases():
    text = "Catering a wedding next Saturday, about 300 extra buns, and 2 cakes on Oct 24"
    dates = date_phrases(text)
    assert "next Saturday" in dates and "Oct 24" in dates
    assert quantity_phrases(text, dates)[:2] == ["300 extra", "2 cakes"]
    monday = date(2026, 10, 5)
    assert resolve_date("next Saturday", monday) == date(2026, 10, 10)
    assert resolve_date("Oct 24", monday) == date(2026, 10, 24)
    assert resolve_date("tomorrow", monday) == date(2026, 10, 6)
    assert resolve_date("next month", monday) == date(2026, 11, 1)


def test_policy_orders_up_to_level_in_packs():
    a = advise(on_hand=100, weekly_mean=[200, 200, 200], weekly_sigma=50, lead_weeks=1, pack=250,
               as_of=date(2026, 10, 5))
    assert a.recommended_qty % 250 == 0 and a.recommended_qty >= a.order_up_to - 100
    assert a.status == "order_now" and a.run_out_date == date(2026, 10, 8)


def test_conformal_quantile_and_launch_range():
    scores = [0.1 * i for i in range(1, 11)]  # 0.1 .. 1.0
    assert conformal_quantile(scores) == 1.0  # ceil(11 * 0.9) = 10: the largest of 10
    assert conformal_quantile(scores + [0.05] * 9) == 0.9  # ceil(20 * 0.9) = 18
    assert launch_range(100.0, 80.0, 120.0, [0.5]) == (80.0, 120.0)  # too few scores: unchanged
    assert launch_range(100.0, 80.0, 120.0, [0.5] * 5) == (50.0, 150.0)
    assert launch_range(100.0, 10.0, 300.0, [0.5] * 5) == (10.0, 300.0)  # never narrower


async def test_launch_ranges_use_only_earlier_weeks_of_other_products():
    sales = load_fixture()
    item = "item-00003"
    plain = await replay(sales, item, "plain")
    # Errors from weeks after every forecast, or from the same product, must change nothing.
    unseen = [LaunchError(sales.weeks + 1, "item-00001", 9.0)] * 5 + [LaunchError(0, item, 9.0)] * 5
    assert (await replay(sales, item, "plain", calibration=unseen)).forecasts == plain.forecasts
    calibrated = await replay(sales, item, "plain", calibration=await launch_errors(sales))
    widths = [(hi - lo, h2 - l2) for (_, _, _, lo, hi), (_, _, _, l2, h2) in zip(plain.forecasts, calibrated.forecasts, strict=True)]
    assert all(new >= old for old, new in widths[:LAUNCH_WEEKS])
    assert any(new > old for old, new in widths[:LAUNCH_WEEKS])
    assert widths[LAUNCH_WEEKS:] == [(w, w) for w, _ in widths[LAUNCH_WEEKS:]]


def _ctx(item_id: str, week: int, mean: float = 0.0, lost=None) -> MissContext:
    return MissContext(item_id, "x", week, "", 0, mean, 0, 0, "far above the range", "", [], lost or [])


async def test_cause_rules_change_the_clean_history():
    sales = load_fixture()
    item = "item-00001"
    state = await replay(sales, item, "plain")
    week = 20
    apply_cause(state, _ctx(item, week, mean=123.0), "one_time_event", sales)
    assert state.clean[week] == 123.0
    before = sum(state.clean[: week + 1])
    cid = next(iter(sales.by_customer[(week - 1, item)]))
    apply_cause(state, _ctx(item, week, lost=[cid]), "customer_lost", sales)
    assert sum(state.clean[: week + 1]) < before
    apply_cause(state, _ctx(item, 40), "lasting_shift_down", sales)
    level = (state.raw[39] + state.raw[40]) / 2
    assert state.clean[state.fit_start] == level and state.clean[38] == level


@pytest.fixture
async def planner(settings, db, decider):
    return Planner(settings, db, decider, load_fixture(), load_books(None, settings.injected_bills_path, "company-001"))


async def test_planner_view_supplies_and_misses(planner):
    view = await planner.view()
    assert view.as_of == date(2026, 10, 5)
    assert {s.item_id for s in view.supplies} == {"syn-gloves", "syn-bakery-boxes", "syn-bread-bags", "syn-parchment"}
    bags = next(s for s in view.supplies if s.item_id == "syn-bread-bags")
    assert bags.status == "order_now" and bags.recommended_qty > 0 and bags.recommended_qty % 1000 == 0
    assert view.misses and all(m.facts is not None for m in view.misses)
    assert any(m.status == "applied" for m in view.misses)


async def test_owner_message_changes_the_forecast(planner):
    before = next(p for p in (await planner.view()).products if p.item_id == "item-00001").forecast[0].mean
    adj = await planner.owner_message("Catering a wedding Saturday, about 300 extra hamburger buns")
    assert (adj.item_id, adj.kind, adj.quantity, adj.status) == ("item-00001", "one_time_extra", 300.0, "applied")
    after = next(p for p in (await planner.view()).products if p.item_id == "item-00001").forecast[0].mean
    assert after == pytest.approx(before + 300, abs=0.2)
    ignored = await planner.owner_message("Thanks, see you tomorrow")
    assert ignored.status == "ignored"


async def test_owner_answer_resolves_a_miss_and_is_logged(planner, db):
    view = await planner.view()
    miss = view.misses[0]
    view = await planner.answer_miss(miss.miss_id, "one_time_event")
    answered = next(m for m in view.misses if m.miss_id == miss.miss_id)
    assert answered.status == "answered" and answered.cause == "one_time_event"
    assert any(r["label"] == "one_time_event" for r in db.decisions("J11"))


async def test_backtest_reports_three_variants_and_is_honest_about_stubs(planner):
    report = await planner.backtest()
    assert [v.variant for v in report.variants] == ["plain", "fixed_rule", "jev"]
    assert report.jev_source == "stub"  # fixture rules, not Jev
    plain = report.variants[0]
    assert plain.coverage is not None and plain.coverage >= 0.88  # 0.84 before the launch ranges
    assert all(v.wape is not None and 0 < v.wape < 1 for v in report.variants)


def test_planner_api_and_autostart(settings):
    with TestClient(create_app(settings)) as client:
        view = client.get("/planner").json()
        assert view["supplies"] and view["disclosure"]
        check = client.post("/planner/check?autostart=true").json()
        assert check["due"] and check["jobId"]
        job = client.get(f"/jobs/{check['jobId']}").json()
        due = {d["itemId"]: d["recommendedQty"] for d in check["due"]}
        assert {i["id"]: i["quantity"] for i in job["request"]["items"]} == due
        adj = client.post("/planner/owner-messages", json={"text": "We will stop selling croissants next month"}).json()
        assert adj["itemId"] == "item-00002" and adj["kind"] == "discontinue"
        assert client.post("/planner/misses/nope/answer", json={"cause": "bad"}).status_code == 422


def test_planner_check_requires_the_token_when_set(settings):
    secured = settings.model_copy(update={"restock_token": SecretStr("s3cret-token")})
    with TestClient(create_app(secured)) as client:
        assert client.post("/planner/check").status_code == 401
        ok = client.post("/planner/check", headers={"Authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200
    live = settings.model_copy(update={"run_mode": "live"})
    with TestClient(create_app(live)) as client:
        assert client.post("/planner/check").status_code == 503


def test_more_date_forms():
    monday = date(2026, 10, 5)
    text = "need extra next Fri, also tmrw and the 24th, maybe this weekend"
    found = date_phrases(text)
    assert {"next Fri", "tmrw", "the 24th", "this weekend"} <= set(found)
    assert resolve_date("next Fri", monday) == date(2026, 10, 9)
    assert resolve_date("tmrw", monday) == date(2026, 10, 6)
    assert resolve_date("the 24th", monday) == date(2026, 10, 24)
    assert resolve_date("this weekend", monday) == date(2026, 10, 10)


def test_full_dates_and_short_quantities():
    """Soak S-009: "on 2026-10-30" fell back to the message week (and gave "2026" as a quantity),
    and "1.5k" gave the quantity 1."""
    from app.planner.adjust import phrase_quantity

    monday = date(2026, 10, 5)
    text = "We need 300 extra buns on 2026-10-30, and 1.5k bags by 11/02/2026"
    found = date_phrases(text)
    assert {"2026-10-30", "11/02/2026"} <= set(found)
    assert resolve_date("2026-10-30", monday) == date(2026, 10, 30)
    assert resolve_date("11/02/2026", monday) == date(2026, 11, 2)
    assert resolve_date("2026-02-30", monday) is None
    qty = quantity_phrases(text, found)
    assert qty == ["300 extra", "1.5k bags"]  # no number from inside a date
    assert [phrase_quantity(q) for q in qty] == [300.0, 1500.0]
    assert phrase_quantity("2.5 cases") == 2.5


async def test_an_absurd_quantity_needs_confirmation(planner):
    """Soak S-020: "about 99999999999999999999 extra hamburger buns" was applied, and the daily
    check then asked to order 1e20 gloves. A quantity far above the product's history needs a yes."""
    adj = await planner.owner_message("Catering a wedding Saturday, about 99999999999999999999 extra hamburger buns")
    assert adj.status == "needs_confirmation" and any("history" in n for n in adj.notes)
    normal = await planner.owner_message("Catering a wedding Saturday, about 300 extra hamburger buns")
    assert normal.status == "applied"


async def test_an_extra_with_no_quantity_needs_confirmation(planner):
    adj = await planner.owner_message("We need three hundred extra hamburger buns next Friday")
    assert adj.kind == "one_time_extra" and adj.quantity is None
    assert adj.status == "needs_confirmation"  # "applied" would add 0 and look done


async def test_cancel_takes_back_the_previous_adjustment(planner):
    first = await planner.owner_message("Catering a wedding Saturday, about 300 extra hamburger buns")
    cancel = await planner.owner_message("Forget the wedding, the hamburger bun order is cancelled")
    assert cancel.kind == "cancel_previous" and cancel.status == "applied"
    assert next(a for a in planner.adjustments if a.adjustment_id == first.adjustment_id).status == "rejected"


async def test_jev_cache_keeps_option_order(settings, db, modes):
    from app.jev import JevClient, choice
    client = JevClient(settings, db, modes)
    calls = []

    class FakeHTTP:
        async def post(self, url, json, headers):
            import httpx
            calls.append(list(json["questions"]["q"]["criteria"]))
            first = calls[-1][0]
            return httpx.Response(200, json={"answers": {"q": {"type": "choice", "choice": first,
                                  "probabilities": {first: 1.0}, "confidence": 1.0}}, "usage": {"input_tokens": 5}})

    client._http = FakeHTTP()
    client.settings = settings.model_copy(update={"jev_api_key": SecretStr("k")})
    client.modes.set("jev", "live")
    a = await client.ask("t", {"s": 1}, {"q": choice("?", {"a": None, "b": None})}, timeout=2)
    b = await client.ask("t", {"s": 1}, {"q": choice("?", {"b": None, "a": None})}, timeout=2)
    assert len(calls) == 2 and a.answers["q"]["choice"] == "a" and b.answers["q"]["choice"] == "b"


def test_planner_check_reuses_the_job_on_the_same_day(settings):
    with TestClient(create_app(settings)) as client:
        first = client.post("/planner/check?autostart=true").json()
        second = client.post("/planner/check?autostart=true").json()
        assert first["jobId"] and second["jobId"] == first["jobId"]


def test_a_job_that_no_process_runs_does_not_block_its_key(settings):
    from app.contracts import JobState, JobView, ShoppingRequest

    with TestClient(create_app(settings)) as client:
        s = client.app.state.s
        request = ShoppingRequest.model_validate(client.get("/shopping-list/draft").json())
        # A job that an earlier server process saved and stopped in the middle of.
        s.db.save_job(JobView(job_id="job_dead00000", state=JobState.gathering, request=request),
                      idempotency_key="key-after-restart")
        body = request.model_dump(mode="json", by_alias=True)
        created = client.post("/jobs", json=body, headers={"Idempotency-Key": "key-after-restart"}).json()
        assert created["jobId"] != "job_dead00000"
        assert client.get("/jobs/job_dead00000").json()["state"] == "failed"
        again = client.post("/jobs", json=body, headers={"Idempotency-Key": "key-after-restart"}).json()
        assert again["jobId"] == created["jobId"]


def test_server_start_builds_the_planner_in_the_background(settings):
    # The first build can take minutes with live Jev + Step 5; the daily check must not pay for it.
    import time

    with TestClient(create_app(settings)) as client:
        s = client.app.state.s
        for _ in range(200):
            if s._warm.done():
                break
            time.sleep(0.05)
        assert s._warm.done() and s._warm.exception() is None
        assert s.planner._view is not None  # /planner and /planner/check reuse this build
