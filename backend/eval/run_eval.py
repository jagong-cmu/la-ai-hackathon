"""Evaluation runner for the Intuit / QuickBooks track (spec §13.6).

Usage (from backend/):
    uv run python -m eval.run_eval                      # all methods; methods without keys: "not run (no key)"
    uv run python -m eval.run_eval --methods rules      # no keys needed
    uv run python -m eval.run_eval --split all --limit 20 --methods jev

It compares Jev, Step 5 Preview and keyword rules on J12 (owner message -> adjustment) and
J11 (forecast-miss cause), and runs Planner.backtest() (variants A plain / B fixed_rule / C jev).
Output: eval/report.md and eval/report.json. Only numbers that this run produced are written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.data.loader import load_books
from app.db import Database
from app.decide import Decider, Decision
from app.jev import JevClient
from app.judgments import ADJUSTMENT_KINDS, MISS_CAUSES, j11_miss_cause, j12_owner_message
from app.llm import Step5Client
from app.planner.adjust import date_phrases, phrase_quantity, quantity_phrases, resolve_date
from app.planner.sales import load_fixture
from app.planner.service import Planner
from eval import metrics as M
from eval.j11_labels import MISS_CASES, load_cases
from eval.methods import JEV_USD_PER_M_INPUT, METHODS, Call, Method, NotRun, _modes

EVAL_DIR = Path(__file__).resolve().parent
OWNER_MESSAGES = EVAL_DIR / "data" / "owner_messages.jsonl"
NONE_ITEM = "none of these"
NOT_STATED = "not stated"
ALL_METHODS = ("jev", "step5", "rules")
ALL_TASKS = ("j12", "j11", "backtest")


# ---------------------------------------------------------------- datasets

def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def catalog() -> dict[str, str]:
    return {pid: p.name for pid, p in load_fixture().products.items()}


def validate_owner_messages(rows: list[dict], names: list[str]) -> list[str]:
    """Returns a list of problems (empty = valid)."""
    problems = []
    items = set(names) | {NONE_ITEM}
    seen = set()
    for r in rows:
        rid = r.get("id")
        for key in ("id", "split", "receivedAt", "message", "item", "kind", "quantity", "quantityPhrase",
                    "datePhrase", "resolvedDate", "tags"):
            if key not in r:
                problems.append(f"{rid}: missing {key}")
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        if r.get("split") not in ("dev", "test"):
            problems.append(f"{rid}: bad split")
        if r.get("item") not in items:
            problems.append(f"{rid}: item {r.get('item')!r} not in the catalog")
        if r.get("kind") not in ADJUSTMENT_KINDS:
            problems.append(f"{rid}: bad kind")
        try:
            datetime.fromisoformat(r["receivedAt"])
            if r.get("resolvedDate") is not None:
                date.fromisoformat(r["resolvedDate"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{rid}: bad date")
        msg = str(r.get("message", "")).lower()
        for k in ("quantityPhrase", "datePhrase"):
            if r.get(k) is not None and str(r[k]).lower() not in msg:
                problems.append(f"{rid}: {k} not in the message")
        if (r.get("datePhrase") is None) != (r.get("resolvedDate") is None):
            problems.append(f"{rid}: datePhrase and resolvedDate disagree")
        if r.get("quantity") is not None and not isinstance(r["quantity"], int | float):
            problems.append(f"{rid}: quantity is not a number")
    return problems


def validate_miss_cases(rows: list[dict]) -> list[str]:
    problems = []
    for r in rows:
        rid = r.get("id")
        if r.get("label") not in MISS_CAUSES:
            problems.append(f"{rid}: bad label")
        if not str(r.get("labelKind", "")).startswith("weak"):
            problems.append(f"{rid}: labelKind must say weak")
        if not r.get("provenance", {}).get("rule"):
            problems.append(f"{rid}: no provenance rule")
        if set(r.get("state", {})) != {"item", "week", "miss", "previousWeek", "facts"}:
            problems.append(f"{rid}: bad state keys")
        if r.get("split") not in ("dev", "test"):
            problems.append(f"{rid}: bad split")
    return problems


# ---------------------------------------------------------------- question capture

class _Capture:
    """Stands in for Decider: records the exact state and questions that app.judgments builds."""

    def __init__(self) -> None:
        self.state: Any = None
        self.questions: dict[str, Any] = {}

    async def decide(self, j_id: str, state: Any, questions: dict[str, Any], **_: Any) -> Decision:
        self.state, self.questions = state, questions
        return Decision({}, "act")


async def j12_inputs(message: str, names: dict[str, str]) -> tuple[Any, dict[str, Any], list[str], list[str]]:
    dates = date_phrases(message)
    quantities = quantity_phrases(message, dates)
    cap = _Capture()
    await j12_owner_message(cap, message, names, quantities, dates)  # type: ignore[arg-type]
    return cap.state, cap.questions, quantities, dates


async def j11_inputs(state: dict) -> tuple[Any, dict[str, Any]]:
    cap = _Capture()
    await j11_miss_cause(cap, state)  # type: ignore[arg-type]
    return cap.state, cap.questions


def reorder(questions: dict[str, Any], how: str, rng: random.Random) -> dict[str, Any]:
    """Same questions, Choice options in another order ("reversed" or "shuffled")."""
    out = {}
    for key, q in questions.items():
        if q.get("type") != "choice":
            out[key] = q
            continue
        opts = list(q["criteria"].items())
        if how == "reversed":
            opts.reverse()
        else:
            rng.shuffle(opts)
        out[key] = {**q, "criteria": dict(opts)}
    return out


# ---------------------------------------------------------------- scoring

def _choice(answers: dict, key: str) -> str | None:
    a = answers.get(key)
    return a.get("choice") if isinstance(a, dict) else None


def _probs(answers: dict, key: str) -> dict[str, float] | None:
    a = answers.get(key)
    p = a.get("probabilities") if isinstance(a, dict) else None
    return {k: float(v) for k, v in p.items()} if p else None


def score_j12(row: dict, questions: dict, answers: dict) -> dict:
    today = datetime.fromisoformat(row["receivedAt"]).date()
    pred = {k: _choice(answers, k) for k in ("item", "kind", "quantity", "date")}
    out: dict[str, Any] = {"pred": pred}
    out["item"] = pred["item"] == row["item"]
    out["kind"] = pred["kind"] == row["kind"]
    actionable = row["item"] != NONE_ITEM and row["kind"] != "not_a_forecast_change"
    out["actionable"] = actionable
    if actionable:
        gq = row["quantity"]
        if gq is None:
            out["quantity"] = "quantity" not in questions or pred["quantity"] == NOT_STATED
        else:
            q = pred["quantity"]
            out["quantity"] = bool(q and q != NOT_STATED and phrase_quantity(q) == float(gq))
        gd = row["resolvedDate"]
        if gd is None:
            out["date"] = "date" not in questions or pred["date"] == NOT_STATED
        else:
            d = pred["date"]
            out["date"] = bool(d and d != NOT_STATED and resolve_date(d, today) == date.fromisoformat(gd))
        out["all"] = out["item"] and out["kind"] and out["quantity"] and out["date"]
    else:
        out["quantity"] = out["date"] = None
        out["all"] = out["item"] and out["kind"]
    return out


def candidate_recall(row: dict, quantities: list[str], dates: list[str]) -> dict:
    """Can ANY method get the field right? (The regex candidates limit every method.)"""
    today = datetime.fromisoformat(row["receivedAt"]).date()
    out: dict[str, bool | None] = {"quantity": None, "date": None}
    if row["quantity"] is not None:
        out["quantity"] = any(phrase_quantity(q) == float(row["quantity"]) for q in quantities)
    if row["resolvedDate"] is not None:
        out["date"] = any(resolve_date(d, today) == date.fromisoformat(row["resolvedDate"]) for d in dates)
    return out


def calibration(pairs: list[tuple[dict[str, float], str, str]]) -> dict:
    """pairs: (probabilities, chosen, gold). Brier (multi-class and top-label), 10 bins, ECE."""
    if not pairs:
        return {"n": 0, "brierMulticlass": None, "brierTop": None, "ece": None, "bins": []}
    conf = [p.get(c, 0.0) for p, c, _ in pairs]
    correct = [c == g for _, c, g in pairs]
    bins = M.reliability_bins(conf, correct, 10)
    return {
        "n": len(pairs),
        "brierMulticlass": M.brier_multiclass([p for p, _, _ in pairs], [g for _, _, g in pairs]),
        "brierTop": M.brier_top(conf, correct),
        "ece": M.ece(bins),
        "bins": bins,
    }


def summarize_calls(calls: list[Call], method: Method) -> dict:
    lat = [c.latency_ms for c in calls if c.error is None]
    tokens = [c.input_tokens for c in calls if c.error is None and c.input_tokens is not None]
    if method.name == "rules":
        cost: float | None = 0.0
        cost_note = "local code, no API cost"
    elif method.name == "jev" and tokens and sum(tokens) > 0:
        cost = sum(tokens) / len(tokens) * JEV_USD_PER_M_INPUT / 1e6 * 1000
        cost_note = f"mean {sum(tokens) / len(tokens):.0f} input tokens/decision x ${JEV_USD_PER_M_INPUT}/1M"
    elif method.name == "jev":
        cost, cost_note = None, "N/A: the API returned no usage.input_tokens"
    else:
        cost, cost_note = None, "N/A: Step5Client does not keep the OpenRouter usage field"
    return {
        "decisions": len(calls), "errors": sum(c.error is not None for c in calls),
        "latencyP50Ms": M.percentile(lat, 50), "latencyP95Ms": M.percentile(lat, 95),
        "costPer1000Usd": cost, "costNote": cost_note,
    }


def stability(base: list[dict], other: list[dict], first: list[dict], fields: list[str]) -> dict:
    """base/other: per-row answers of the normal and reordered pass; first: per-row first option shown."""
    out = {}
    for f in fields:
        pairs = [(b.get(f), o.get(f)) for b, o in zip(base, other, strict=True)
                 if b.get(f) is not None and o.get(f) is not None]
        firsts = [(o.get(f), fo.get(f)) for o, fo in zip(other, first, strict=True) if o.get(f) is not None]
        out[f] = {
            "n": len(pairs),
            "agreement": sum(a == b for a, b in pairs) / len(pairs) if pairs else None,
            "firstOptionRate": sum(a == b for a, b in firsts) / len(firsts) if firsts else None,
        }
    return out


# ---------------------------------------------------------------- tasks

async def _ask_all(method: Method, j_id: str, items: list[tuple[Any, dict]]) -> list[Call]:
    return [await method.ask(j_id, state, qs) for state, qs in items]


async def run_j12(method: Method, rows: list[dict], names: dict[str, str], shuffle: bool, seed: int) -> dict:
    inputs = [await j12_inputs(r["message"], names) for r in rows]
    method.new_pass("j12-normal")
    calls = await _ask_all(method, "J12", [(s, q) for s, q, _, _ in inputs])
    scored = [score_j12(r, q, c.answers) for r, (_, q, _, _), c in zip(rows, inputs, calls, strict=True)]
    fields = ["item", "kind", "quantity", "date", "all"]
    acc = {}
    for f in fields:
        vals = [s[f] for s in scored if s[f] is not None]
        acc[f] = {"n": len(vals), "accuracy": sum(vals) / len(vals) if vals else None}
    kinds = list(ADJUSTMENT_KINDS)
    macro = M.macro_f1([r["kind"] for r in rows], [s["pred"]["kind"] for s in scored], kinds)
    cal: dict[str, Any] = {}
    if method.has_probabilities:
        pooled = []
        for f in ("item", "kind"):
            pairs = [(p, c, r[f]) for r, call in zip(rows, calls, strict=True)
                     if (p := _probs(call.answers, f)) and (c := _choice(call.answers, f)) is not None]
            cal[f] = calibration(pairs)
            pooled += pairs
        cal["pooled"] = calibration(pooled)
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for r, s in zip(rows, scored, strict=True):
        for t in r["tags"] or ["plain"]:
            by_tag[t].append(bool(s["all"]))
    result: dict[str, Any] = {
        "n": len(rows), "accuracy": acc, "macroF1Kind": macro, "calibration": cal,
        "calls": summarize_calls(calls, method),
        "byTag": {t: {"n": len(v), "allCorrect": sum(v) / len(v)} for t, v in sorted(by_tag.items())},
        "rows": [{"id": r["id"], "pred": s["pred"], "correct": {f: s[f] for f in fields},
                  "error": c.error} for r, s, c in zip(rows, scored, calls, strict=True)],
    }
    if shuffle:
        result["stability"] = await _stability(method, "J12", [(s, q) for s, q, _, _ in inputs], calls,
                                               ["item", "kind", "quantity", "date"], seed)
    return result


async def run_j11(method: Method, cases: list[dict], shuffle: bool, seed: int) -> dict:
    inputs = [await j11_inputs(c["state"]) for c in cases]
    method.new_pass("j11-normal")
    calls = await _ask_all(method, "J11", inputs)
    gold = [c["label"] for c in cases]
    pred = [_choice(call.answers, "cause") for call in calls]
    cal = {}
    if method.has_probabilities:
        pairs = [(p, c, g) for call, g in zip(calls, gold, strict=True)
                 if (p := _probs(call.answers, "cause")) and (c := _choice(call.answers, "cause")) is not None]
        cal["cause"] = calibration(pairs)
    by_rule: dict[str, list[bool]] = defaultdict(list)
    for c, p in zip(cases, pred, strict=True):
        by_rule[c["provenance"]["rule"]].append(p == c["label"])
    result: dict[str, Any] = {
        "n": len(cases), "accuracy": M.accuracy(gold, pred),
        "macroF1Cause": M.macro_f1(gold, pred), "calibration": cal,
        "calls": summarize_calls(calls, method),
        "byRule": {k: {"n": len(v), "accuracy": sum(v) / len(v)} for k, v in sorted(by_rule.items())},
        "confusion": _confusion(gold, pred),
        "rows": [{"id": c["id"], "label": c["label"], "pred": p, "error": call.error}
                 for c, p, call in zip(cases, pred, calls, strict=True)],
    }
    if shuffle:
        result["stability"] = await _stability(method, "J11", inputs, calls, ["cause"], seed)
    return result


def _confusion(gold: list[str], pred: list[str | None]) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g, p in zip(gold, pred, strict=True):
        table[g][p or "(error)"] += 1
    return {g: dict(v) for g, v in table.items()}


async def _stability(method: Method, j_id: str, items: list[tuple[Any, dict]], base_calls: list[Call],
                     fields: list[str], seed: int) -> dict:
    base = [{f: _choice(c.answers, f) for f in fields} for c in base_calls]
    out = {}
    for how in ("reversed", "shuffled"):
        rng = random.Random(seed)
        reordered = [(s, reorder(q, how, rng)) for s, q in items]
        method.new_pass(f"{j_id.lower()}-{how}")
        calls = await _ask_all(method, j_id, reordered)
        other = [{f: _choice(c.answers, f) for f in fields} for c in calls]
        first = [{f: next(iter(q[f]["criteria"])) if f in q else None for f in fields} for _, q in reordered]
        out[how] = stability(base, other, first, fields)
    base_first = [{f: next(iter(q[f]["criteria"])) if f in q else None for f in fields} for _, q in items]
    out["normal"] = {f: v for f, v in stability(base, base, base_first, fields).items()}
    return out


async def run_backtest(settings: Settings, use_jev: bool, use_llm: bool, work_dir: Path) -> dict:
    db = Database(work_dir / "backtest.db")
    modes = _modes(jev=use_jev, llm=use_llm)
    jev, llm = JevClient(settings, db, modes), Step5Client(settings, db, modes)
    try:
        planner = Planner(settings, db, Decider(settings, db, jev, llm), load_fixture(),
                          load_books(None, settings.injected_bills_path, settings.business_id))
        report = await planner.backtest()
    finally:
        await jev.aclose()
        await llm.aclose()
    return report.model_dump(mode="json", by_alias=True)


# ---------------------------------------------------------------- orchestration

async def run(settings: Settings, methods: list[str], tasks: list[str], split: str = "test",
              shuffle: bool = True, step5_shuffle: bool = False, limit: int | None = None,
              seed: int = 7, work_dir: Path | None = None) -> dict:
    work_dir = work_dir or Path(tempfile.mkdtemp(prefix="eval-run-"))
    names = catalog()
    msgs = load_jsonl(OWNER_MESSAGES)
    cases = load_cases(MISS_CASES)
    problems = validate_owner_messages(msgs, list(names.values())) + validate_miss_cases(cases)
    if problems:
        raise ValueError("dataset problems: " + "; ".join(problems[:10]))
    if split != "all":
        msgs = [m for m in msgs if m["split"] == split]
        cases = [c for c in cases if c["split"] == split]
    if limit:
        msgs, cases = msgs[:limit], cases[:limit]

    report: dict[str, Any] = {
        "split": split, "limit": limit, "seed": seed, "methods": {}, "tasks": tasks,
        "datasets": {"j12": {"path": "eval/data/owner_messages.jsonl", "n": len(msgs)},
                     "j11": {"path": "eval/data/miss_cases.jsonl", "n": len(cases),
                             "labels": dict(sorted(_count(c["label"] for c in cases).items()))}},
        "models": {"jev": settings.jev_model, "step5": settings.step5_model},
    }
    if "j12" in tasks:
        recall = [candidate_recall(m, *(await j12_inputs(m["message"], names))[2:]) for m in msgs]
        report["j12CandidateRecall"] = {
            f: _rate([r[f] for r in recall if r[f] is not None]) for f in ("quantity", "date")}
    live: dict[str, bool] = {}
    for name in methods:
        entry: dict[str, Any] = {"status": "ran"}
        try:
            method = METHODS[name](settings, work_dir)
        except NotRun as exc:
            report["methods"][name] = {"status": str(exc)}
            live[name] = False
            continue
        live[name] = name != "rules"
        do_shuffle = shuffle and (name != "step5" or step5_shuffle)
        entry["probabilityNote"] = method.probability_note
        try:
            if "j12" in tasks:
                entry["j12"] = await run_j12(method, msgs, names, do_shuffle, seed)
            if "j11" in tasks:
                entry["j11"] = await run_j11(method, cases, do_shuffle, seed)
        finally:
            await method.aclose()
        report["methods"][name] = entry
    if "backtest" in tasks:
        report["backtest"] = await run_backtest(settings, live.get("jev", False), live.get("step5", False),
                                                work_dir)
    return report


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(out)


def _rate(values: list[bool]) -> dict:
    return {"n": len(values), "rate": sum(values) / len(values) if values else None}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--methods", default=",".join(ALL_METHODS), help="comma list of jev,step5,rules")
    ap.add_argument("--tasks", default=",".join(ALL_TASKS), help="comma list of j12,j11,backtest")
    ap.add_argument("--split", default="test", choices=["test", "dev", "all"])
    ap.add_argument("--no-shuffle", action="store_true", help="skip the option-order stability passes")
    ap.add_argument("--step5-shuffle", action="store_true", help="also run the order passes for Step 5 (cost)")
    ap.add_argument("--limit", type=int, default=None, help="use only the first N rows of each dataset")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", type=Path, default=EVAL_DIR)
    args = ap.parse_args(argv)
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    bad = [m for m in methods if m not in ALL_METHODS] + [t for t in tasks if t not in ALL_TASKS]
    if bad:
        ap.error(f"unknown: {bad}")
    work = Path(tempfile.mkdtemp(prefix="eval-run-"))
    settings = Settings().model_copy(update={"var_dir": work / "var"})  # keys come from .env / env vars
    report = asyncio.run(run(settings, methods, tasks, args.split, not args.no_shuffle, args.step5_shuffle,
                             args.limit, args.seed, work))
    from eval.report import write_reports

    md, js = write_reports(report, args.out_dir)
    print(f"wrote {md} and {js}")
    for name, m in report["methods"].items():
        print(f"  {name}: {m['status']}")


if __name__ == "__main__":
    main()
