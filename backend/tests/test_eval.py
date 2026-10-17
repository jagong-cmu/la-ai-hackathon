"""Tests for the evaluation kit (backend/eval)."""

from __future__ import annotations

import json
import random

import pytest

from app.judgments import ADJUSTMENT_KINDS
from eval import metrics as M
from eval.j11_labels import MISS_CASES, build_cases, load_cases
from eval.methods import NOT_RUN_NO_KEY
from eval.report import write_reports
from eval.run_eval import (
    OWNER_MESSAGES,
    catalog,
    j12_inputs,
    load_jsonl,
    reorder,
    run,
    score_j12,
    validate_miss_cases,
    validate_owner_messages,
)

# ---------------------------------------------------------------- datasets


def test_owner_messages_schema_and_coverage():
    rows = load_jsonl(OWNER_MESSAGES)
    names = list(catalog().values())
    assert validate_owner_messages(rows, names) == []
    assert len(rows) >= 100
    splits = {r["split"] for r in rows}
    assert splits == {"dev", "test"}
    assert {r["kind"] for r in rows} == set(ADJUSTMENT_KINDS)
    assert {r["item"] for r in rows} == set(names) | {"none of these"}
    tags = {t for r in rows for t in r["tags"]}
    for hard in ("typo", "two_numbers", "abbrev_weekday", "negation", "vague_quantity", "chatter"):
        assert hard in tags


def test_validator_catches_bad_rows():
    names = list(catalog().values())
    bad = {"id": "x", "split": "train", "receivedAt": "nope", "message": "hi", "item": "Bagel",
           "kind": "grow", "quantity": "ten", "quantityPhrase": "10", "datePhrase": None,
           "resolvedDate": "2026-10-10", "tags": []}
    problems = " ".join(validate_owner_messages([bad], names))
    for word in ("bad split", "not in the catalog", "bad kind", "bad date", "quantityPhrase", "disagree",
                 "not a number"):
        assert word in problems


def test_miss_cases_schema_weak_labels_and_regeneration():
    rows = load_cases(MISS_CASES)
    assert rows and validate_miss_cases(rows) == []
    assert all(r["labelKind"].startswith("weak") for r in rows)


async def test_miss_cases_file_matches_the_replay():
    rebuilt = await build_cases()
    assert json.loads(json.dumps(rebuilt)) == load_cases(MISS_CASES)


# ---------------------------------------------------------------- metrics


def test_accuracy_and_macro_f1():
    assert M.accuracy(["a", "b"], ["a", "a"]) == 0.5
    # a: tp=1 fp=1 fn=0 -> 2/3 ; b: tp=0 fp=0 fn=1 -> 0 ; mean 1/3
    assert M.macro_f1(["a", "b"], ["a", "a"]) == pytest.approx(1 / 3)
    assert M.macro_f1(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    # an explicit label with no gold and no prediction is skipped
    assert M.macro_f1(["a"], ["a"], labels=["a", "z"]) == 1.0
    assert M.accuracy([], []) is None


def test_brier_scores():
    probs = [{"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}]
    # item 1: 0 ; item 2: (0.5-1)^2 + 0.5^2 = 0.5 ; mean 0.25
    assert M.brier_multiclass(probs, ["a", "a"]) == pytest.approx(0.25)
    assert M.brier_multiclass([{"a": 1.0}], ["b"]) == pytest.approx(2.0)  # missing key counts as 0
    assert M.brier_top([0.9, 0.6], [True, False]) == pytest.approx((0.01 + 0.36) / 2)


def test_reliability_bins_and_ece():
    bins = M.reliability_bins([0.05, 0.95, 1.0, 0.55], [False, True, True, False], 10)
    assert len(bins) == 10
    assert bins[0]["n"] == 1 and bins[0]["accuracy"] == 0.0
    assert bins[9]["n"] == 2 and bins[9]["accuracy"] == 1.0  # 1.0 goes in the last bin
    assert bins[9]["meanConfidence"] == pytest.approx(0.975)
    assert bins[5]["n"] == 1 and bins[3]["n"] == 0 and bins[3]["accuracy"] is None
    # ECE = (1*0.05 + 2*0.025 + 1*0.55) / 4
    assert M.ece(bins) == pytest.approx((0.05 + 0.05 + 0.55) / 4)


def test_percentile():
    assert M.percentile([1, 2, 3, 4], 50) == 2.5
    assert M.percentile([10.0], 95) == 10.0
    assert M.percentile([], 50) is None


# ---------------------------------------------------------------- scoring helpers


async def test_score_j12_resolves_quantity_and_date_in_code():
    row = {"id": "t", "receivedAt": "2026-10-05T09:00:00-04:00", "item": "Hamburger Bun Flat, 8 ct",
           "kind": "one_time_extra", "quantity": 300, "resolvedDate": "2026-10-10"}
    msg = "Catering a wedding Saturday, about 300 extra hamburger buns"
    _, questions, quantities, dates = await j12_inputs(msg, catalog())
    assert set(questions) == {"item", "kind", "quantity", "date"}
    answers = {"item": {"choice": row["item"]}, "kind": {"choice": "one_time_extra"},
               "quantity": {"choice": quantities[0]}, "date": {"choice": dates[0]}}
    assert score_j12(row, questions, answers)["all"] is True
    answers["date"] = {"choice": "not stated"}
    assert score_j12(row, questions, answers)["date"] is False


def test_reorder_keeps_options_and_changes_order():
    q = {"c": {"type": "choice", "instructions": "x", "criteria": {"a": 1, "b": 2, "c": 3}},
         "n": {"type": "noul", "instructions": "y"}}
    rev = reorder(q, "reversed", random.Random(1))
    assert list(rev["c"]["criteria"]) == ["c", "b", "a"] and rev["n"] == q["n"]
    shu = reorder(q, "shuffled", random.Random(1))
    assert shu["c"]["criteria"] == q["c"]["criteria"]


# ---------------------------------------------------------------- end to end


async def test_runner_rules_only_without_keys(settings, tmp_path):
    report = await run(settings, ["jev", "step5", "rules"], ["j12", "j11", "backtest"], split="all",
                       work_dir=tmp_path / "work")
    assert report["methods"]["jev"]["status"].startswith(NOT_RUN_NO_KEY)
    assert report["methods"]["step5"]["status"].startswith(NOT_RUN_NO_KEY)
    assert "j12" not in report["methods"]["jev"]
    rules = report["methods"]["rules"]
    assert rules["status"] == "ran"
    assert rules["j12"]["n"] == len(load_jsonl(OWNER_MESSAGES))
    assert 0 <= rules["j12"]["accuracy"]["kind"]["accuracy"] <= 1
    assert rules["j12"]["calibration"]["pooled"]["n"] == 2 * rules["j12"]["n"]
    assert "reversed" in rules["j12"]["stability"]
    assert rules["j11"]["n"] == len(load_cases(MISS_CASES))
    assert report["backtest"]["jevSource"] == "stub"
    assert [v["variant"] for v in report["backtest"]["variants"]] == ["plain", "fixed_rule", "jev"]

    md, js = write_reports(report, tmp_path / "out")
    text = md.read_text()
    assert "not run (no key)" in text and "jevSource = `stub`" in text and "Disclosures" in text
    assert "Weak J11 labels" in text
    assert json.loads(js.read_text())["methods"]["rules"]["status"] == "ran"


async def test_jev_path_with_a_mock_api_detects_first_option_bias(settings, tmp_path, monkeypatch):
    """A fake API that always picks the first option: the cache must not hide the reordering,
    the stability table must show the bias, and the cost must come from usage.input_tokens."""
    import httpx
    from pydantic import SecretStr

    from app.jev import JevClient, choice_answer

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        answers = {}
        for key, q in body["questions"].items():
            opts = list(q["criteria"])
            answers[key] = choice_answer(opts, opts[0], 0.6)
        return httpx.Response(200, json={"answers": answers, "usage": {"input_tokens": 500}})

    monkeypatch.setattr(JevClient, "_client", lambda self: httpx.AsyncClient(
        base_url="https://mock.invalid", transport=httpx.MockTransport(handler)))
    keyed = settings.model_copy(update={"jev_api_key": SecretStr("test-key")})
    report = await run(keyed, ["jev"], ["j11"], split="test", limit=8, work_dir=tmp_path / "w")
    j11 = report["methods"]["jev"]["j11"]
    stab = j11["stability"]
    assert stab["reversed"]["cause"]["firstOptionRate"] == 1.0
    assert stab["reversed"]["cause"]["agreement"] == 0.0  # 8 options: reversed first != normal first
    assert j11["calls"]["costPer1000Usd"] == pytest.approx(500 * 0.042 / 1e6 * 1000)
    assert j11["calibration"]["cause"]["n"] == 8
