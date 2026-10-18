"""Sentry gets the job's spans (they run after the request ends) and the gen_ai attributes."""

import time

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

import app.observability as obs
from app.main import create_app


class KeepTransactions(Transport):
    """Keeps the events in memory: nothing leaves the machine."""

    def __init__(self, options=None):
        super().__init__(options)
        self.transactions: list[dict] = []
        self.ai_spans: list[dict] = []  # the SDK sends gen_ai spans as their own "span" items

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "transaction":
                self.transactions.append(item.payload.json)
            elif item.type == "span":
                self.ai_spans += item.payload.json["items"]


@pytest.fixture
def sentry(monkeypatch):
    transport = KeepTransactions()
    real_init = sentry_sdk.init
    monkeypatch.setattr(obs.sentry_sdk, "init", lambda **kw: real_init(**kw, transport=transport))
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.init()  # no DSN: Sentry off again
    obs._enabled = False


def test_job_spans_reach_sentry_in_the_request_trace(settings, sentry):
    on = settings.model_copy(update={"sentry_dsn": "https://public@example.invalid/1"})
    with TestClient(create_app(on)) as client:
        body = client.get("/shopping-list/draft").json()
        job_id = client.post("/jobs", json=body, headers={"Idempotency-Key": "sentry-test-01"}).json()["jobId"]
        for _ in range(300):
            if client.get(f"/jobs/{job_id}").json()["state"] in ("results_ready", "failed"):
                break
            time.sleep(0.05)
    sentry_sdk.flush()
    by_name = {t["transaction"]: t for t in sentry.transactions}
    job, request = by_name["restock job"], by_name["/jobs"]
    ops = {s["op"] for s in job["spans"]}
    assert {"lane.open", "lane.search", "distributor.run"} <= ops
    assert job["contexts"]["trace"]["trace_id"] == request["contexts"]["trace"]["trace_id"]
    assert job["tags"]["job_id"] == job_id
    # Parallel lanes are siblings: each search is a child of its own lane span, and each lane span of the job.
    job_span = job["contexts"]["trace"]["span_id"]
    lanes = {s["span_id"]: s for s in job["spans"] if s["op"] == "lane.run"}
    agents = {s["span_id"] for s in sentry.ai_spans if s["attributes"]["sentry.op"]["value"] == "gen_ai.invoke_agent"}
    searches = [s for s in job["spans"] if s["op"] == "lane.search"]
    assert searches and all(s["parent_span_id"] in lanes.keys() | agents for s in searches)
    assert lanes and all(s["parent_span_id"] == job_span for s in lanes.values())
    # Each step records how it ended, so Sentry can group runs by result.
    assert all("offers.status" in s["data"] or s["data"]["error.type"] for s in searches)
    assert any(s["data"].get("error.type") == "LaneBlocked" and s["status"] != "ok" for s in searches)
    assert all(s["data"]["lane.state"] and s["status"] for s in lanes.values())
    carts = [s for s in job["spans"] if s["op"] == "lane.set_cart"]
    assert carts and all(s["data"]["cart.verification"] for s in carts)
    trace = job["contexts"]["trace"]
    assert trace["data"]["job.state"] == "results_ready" and trace["data"]["job.lanes"] >= 1
    decisions = [s for s in job["spans"] if s["op"] == "decide"]
    assert decisions and all(s["data"]["decision.band"] for s in decisions)


def test_ai_span_has_gen_ai_attributes(sentry):
    obs.init_sentry(type("S", (), {"sentry_dsn": "https://public@example.invalid/1", "run_mode": "fixture",
                                    "sentry_traces_sample_rate": 1.0})())
    with sentry_sdk.start_transaction(op="test", name="ai"):
        with obs.ai_span("stepfun", "step-5-preview", "spec") as s:
            obs.set_data(s, **{"gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": None})
    sentry_sdk.flush()
    (span,) = sentry.ai_spans
    attrs = {k: v["value"] for k, v in span["attributes"].items()}
    assert span["name"] == "chat step-5-preview" and attrs["sentry.op"] == "gen_ai.chat"
    assert attrs["gen_ai.request.model"] == "step-5-preview" and attrs["gen_ai.function_id"] == "spec"
    assert attrs["gen_ai.usage.input_tokens"] == 12
    assert "gen_ai.usage.output_tokens" not in attrs  # None values are skipped
    assert span["trace_id"] == sentry.transactions[0]["contexts"]["trace"]["trace_id"]
