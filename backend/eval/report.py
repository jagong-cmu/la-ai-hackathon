"""Writes eval/report.md and eval/report.json from the runner's result dict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

METHOD_LABELS = {"jev": "Jev (TypeSafe)", "step5": "Step 5 Preview", "rules": "Keyword rules"}

DISCLOSURES = [
    "**Synthetic data.** The product sales come from the Intuit hackathon dataset company-001, which "
    "the qb-world-sim simulator made. They are not a real bakery's books. The results apply to this simulator.",
    "**Derived fixture.** `backend/fixtures/company-001-sales.json` is a compact file that code derived "
    "from the dataset (weekly sales per customer, bill memos, purchases). Tests and this kit use it instead "
    "of the 400 MB dataset.",
    "**Assumed usage ratios.** Supply use = real product sales x usage ratios that we assumed "
    "(`backend/fixtures/usage_ratios.yaml`). The supply bills are synthetic and were regenerated from the "
    "sales through those ratios. The J11/J12 results do not use them; the planner's supply plans do.",
    "**Weak J11 labels.** Rules over the code-detected facts plus the next 3 weeks of sales (hindsight) "
    "made the J11 labels. No person checked them. Two label rules (backorder memo, lost customer) use the "
    "same facts as the keyword baseline, so the baseline is favoured on those causes by construction.",
    "**Hand-written J12 messages.** One author wrote the owner messages and their labels for this "
    "evaluation. They are not real owner texts. The author knew the weak points of the regex candidates "
    "and wrote hard cases on purpose (see the tags).",
    "**One year of data.** The forecaster learns no seasonality.",
    "**Company-reported price.** Jev's $0.042 per 1M input tokens is the vendor's claim (PROJECT_BRIEF §6.10).",
]


def _f(x: Any, pct: bool = False, nd: int = 3) -> str:
    if x is None:
        return "N/A"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if pct:
        return f"{100 * x:.1f}%"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _table(head: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _ran(report: dict) -> list[tuple[str, dict]]:
    return [(n, m) for n, m in report["methods"].items() if m["status"] == "ran"]


def _status_table(report: dict) -> str:
    return _table(["Method", "Status"], [[METHOD_LABELS.get(n, n), m["status"]] for n, m in report["methods"].items()])


def _reliability(cal: dict) -> str:
    rows = [[f"{b['lo']:.1f}-{b['hi']:.1f}", b["n"], _f(b["meanConfidence"]), _f(b["accuracy"])]
            for b in cal.get("bins", []) if b["n"]]
    return _table(["Confidence bin", "n", "Mean confidence", "Accuracy"], rows) if rows else "_No items._"


def _stability_rows(name: str, stab: dict, fields: list[str]) -> list[list[Any]]:
    rows = []
    for f in fields:
        normal = stab.get("normal", {}).get(f, {})
        rev, shu = stab.get("reversed", {}).get(f, {}), stab.get("shuffled", {}).get(f, {})
        rows.append([METHOD_LABELS.get(name, name), f, rev.get("n", 0), _f(rev.get("agreement"), True),
                     _f(shu.get("agreement"), True), _f(normal.get("firstOptionRate"), True),
                     _f(rev.get("firstOptionRate"), True), _f(shu.get("firstOptionRate"), True)])
    return rows


def render(report: dict) -> str:
    out: list[str] = []
    ran = _ran(report)
    out.append("# Intuit / QuickBooks track: evaluation report\n")
    out.append(f"Generated {report['generatedAt']} by `uv run python -m eval.run_eval`. "
               f"Split: **{report['split']}**" + (f", limit {report['limit']}" if report.get("limit") else "")
               + f". Seed {report['seed']}. Jev model `{report['models']['jev']}`, Step 5 model "
                 f"`{report['models']['step5']}`.\n")
    out.append("All numbers below come from this run. A method without a key is reported as "
               "\"not run (no key)\". The kit never puts stub answers in a Jev or Step 5 column.\n")
    out.append("## Methods run\n")
    out.append(_status_table(report) + "\n")

    out.append("## Method\n")
    out.append(
        "- **Same input for every method.** The runner calls `app.judgments.j12_owner_message` and "
        "`j11_miss_cause` with a capture object in place of `Decider`. So every method gets the exact state "
        "and questions that the app sends in production.\n"
        "- **Jev** runs through `JevClient.ask` on the live API. **Step 5 Preview** runs through "
        "`Step5Client.answer_questions` (the escalation path of `decide()`). **Keyword rules** are "
        "`app.stub_rules` (the fixture-mode stand-in).\n"
        "- **One decision** = one call with all the questions for one message or one miss.\n"
        "- **J12 scoring.** item and kind: exact option match. quantity and date are scored only on "
        "actionable rows (an item from the catalog and a kind that is not `not_a_forecast_change`). "
        "quantity is correct when code turns the chosen phrase into the labelled number. date is correct when "
        "`resolve_date(chosen phrase, receivedAt)` gives the labelled date. When the label has no quantity or "
        "date, the correct answer is \"not stated\" (or no question). \"all\" = every scored field is correct.\n"
        "- **J11 scoring.** cause: exact match with the weak label.\n"
        "- **Calibration** uses the Choice `probabilities`. Brier (multi-class) = mean of sum over options of "
        "(p - y)^2, range 0-2. Brier (top) = mean of (p of the chosen option - 1 if correct else 0)^2, range "
        "0-1. Reliability: 10 equal bins on the probability of the chosen option. ECE = size-weighted mean "
        "of |accuracy - mean confidence| over the bins.\n"
        "- **Latency** is per decision, measured one call at a time (no parallel calls). "
        "**Cost per 1,000 decisions**: Jev = mean `usage.input_tokens` x $0.042 / 1M x 1,000 "
        "(output tokens are free). Step 5 = N/A, because `Step5Client` does not keep the OpenRouter usage.\n"
        "- **Option-order stability.** The runner asks each question again with the Choice options in "
        "reversed order and in one seeded random order, each pass with an empty cache. Agreement = the "
        "share of answers that stay the same. First-option rate = the share of answers that pick the option "
        "shown first. A large rise in the first-option rate after reordering shows first-option bias.\n"
        "- **Candidate recall** (J12): the share of labelled quantities and dates that the regex candidates "
        "contain at all. No method can score higher than this on those fields.\n")

    out.append("## Disclosures\n")
    out += [f"{i}. {d}" for i, d in enumerate(DISCLOSURES, 1)]
    out.append("")

    # ---------------- J12
    if "j12" in report["tasks"]:
        ds = report["datasets"]["j12"]
        out.append(f"## J12: owner message -> forecast adjustment (n = {ds['n']})\n")
        cr = report.get("j12CandidateRecall", {})
        out.append("Candidate recall (regex limit for every method): quantity "
                   f"{_f(cr.get('quantity', {}).get('rate'), True)} (n = {cr.get('quantity', {}).get('n')}), "
                   f"date {_f(cr.get('date', {}).get('rate'), True)} (n = {cr.get('date', {}).get('n')}).\n")
        if ran:
            rows = []
            for name, m in ran:
                j = m.get("j12")
                if not j:
                    continue
                a, c, cal = j["accuracy"], j["calls"], j["calibration"]
                rows.append([METHOD_LABELS.get(name, name), _f(a["item"]["accuracy"], True),
                             _f(a["kind"]["accuracy"], True), _f(j["macroF1Kind"]),
                             f"{_f(a['quantity']['accuracy'], True)} (n={a['quantity']['n']})",
                             f"{_f(a['date']['accuracy'], True)} (n={a['date']['n']})",
                             _f(a["all"]["accuracy"], True),
                             _f(cal.get("item", {}).get("brierMulticlass")),
                             _f(cal.get("kind", {}).get("brierMulticlass")),
                             _f(cal.get("pooled", {}).get("ece")),
                             _f(c["latencyP50Ms"], nd=2), _f(c["latencyP95Ms"], nd=2),
                             _f(c["costPer1000Usd"], nd=4), c["errors"]])
            out.append(_table(["Method", "item acc", "kind acc", "kind macro-F1", "quantity acc", "date acc",
                               "all fields", "Brier item", "Brier kind", "ECE (item+kind)", "p50 ms", "p95 ms",
                               "$ / 1,000", "errors"], rows) + "\n")
            for name, m in ran:
                if m.get("probabilityNote"):
                    out.append(f"- {METHOD_LABELS.get(name, name)}: {m['probabilityNote']}")
                if m.get("j12"):
                    out.append(f"- {METHOD_LABELS.get(name, name)} cost: {m['j12']['calls']['costNote']}.")
            out.append("")
            for name, m in ran:
                cal = (m.get("j12") or {}).get("calibration", {}).get("pooled")
                if cal:
                    out.append(f"### J12 reliability, {METHOD_LABELS.get(name, name)} (item + kind, n = {cal['n']})\n")
                    out.append(_reliability(cal) + "\n")
            stab_rows = []
            for name, m in ran:
                if (m.get("j12") or {}).get("stability"):
                    stab_rows += _stability_rows(name, m["j12"]["stability"], ["item", "kind", "quantity", "date"])
            if stab_rows:
                out.append("### J12 option-order stability\n")
                out.append(_table(["Method", "field", "n", "agree (reversed)", "agree (shuffled)",
                                   "first-option rate (normal)", "(reversed)", "(shuffled)"], stab_rows) + "\n")
            tags = sorted({t for _, m in ran for t in (m.get("j12") or {}).get("byTag", {})})
            if tags:
                out.append("### J12 all-fields accuracy by hard-case tag\n")
                head = ["Tag", "n"] + [METHOD_LABELS.get(n, n) for n, m in ran if m.get("j12")]
                rows = []
                for t in tags:
                    cells = [m["j12"]["byTag"].get(t) for _, m in ran if m.get("j12")]
                    n = next((c["n"] for c in cells if c), 0)
                    rows.append([t, n] + [_f(c["allCorrect"], True) if c else "N/A" for c in cells])
                out.append(_table(head, rows) + "\n")
        else:
            out.append("_No method ran._\n")

    # ---------------- J11
    if "j11" in report["tasks"]:
        ds = report["datasets"]["j11"]
        out.append(f"## J11: forecast-miss cause (n = {ds['n']}, WEAK labels)\n")
        out.append("Label counts: " + ", ".join(f"{k} {v}" for k, v in ds["labels"].items()) + ".\n")
        rows = []
        for name, m in ran:
            j = m.get("j11")
            if not j:
                continue
            c, cal = j["calls"], j["calibration"].get("cause", {})
            rows.append([METHOD_LABELS.get(name, name), _f(j["accuracy"], True), _f(j["macroF1Cause"]),
                         _f(cal.get("brierMulticlass")), _f(cal.get("brierTop")), _f(cal.get("ece")),
                         _f(c["latencyP50Ms"], nd=2), _f(c["latencyP95Ms"], nd=2), _f(c["costPer1000Usd"], nd=4),
                         c["errors"]])
        if rows:
            out.append(_table(["Method", "cause acc", "cause macro-F1", "Brier (multi)", "Brier (top)", "ECE",
                               "p50 ms", "p95 ms", "$ / 1,000", "errors"], rows) + "\n")
            rules = sorted({r for _, m in ran for r in (m.get("j11") or {}).get("byRule", {})})
            head = ["Label rule", "n"] + [METHOD_LABELS.get(n, n) for n, m in ran if m.get("j11")]
            trows = []
            for r in rules:
                cells = [m["j11"]["byRule"].get(r) for _, m in ran if m.get("j11")]
                n = next((c["n"] for c in cells if c), 0)
                trows.append([r, n] + [_f(c["accuracy"], True) if c else "N/A" for c in cells])
            out.append("### J11 accuracy by label rule\n")
            out.append("R1 and R2 share their facts with the keyword baseline, so a high baseline score there "
                       "is expected by construction.\n")
            out.append(_table(head, trows) + "\n")
            for name, m in ran:
                cal = (m.get("j11") or {}).get("calibration", {}).get("cause")
                if cal:
                    out.append(f"### J11 reliability, {METHOD_LABELS.get(name, name)} (n = {cal['n']})\n")
                    out.append(_reliability(cal) + "\n")
            stab_rows = []
            for name, m in ran:
                if (m.get("j11") or {}).get("stability"):
                    stab_rows += _stability_rows(name, m["j11"]["stability"], ["cause"])
            if stab_rows:
                out.append("### J11 option-order stability\n")
                out.append(_table(["Method", "field", "n", "agree (reversed)", "agree (shuffled)",
                                   "first-option rate (normal)", "(reversed)", "(shuffled)"], stab_rows) + "\n")
        else:
            out.append("_No method ran._\n")

    # ---------------- backtest
    bt = report.get("backtest")
    if bt:
        out.append(f"## Backtest: replay of the year ({bt['weeks']} weeks, {bt['products']} products)\n")
        out.append(f"**jevSource = `{bt['jevSource']}`.** "
                   + ("Variant C used live Jev answers." if bt["jevSource"] == "jev" else
                      "Variant C did NOT use Jev: the fixture keyword rules answered J11. "
                      "Do not read row C as a Jev result.") + "\n")
        names = {"plain": "A plain", "fixed_rule": "B fixed_rule", "jev": "C jev (J11 causes)"}
        rows = [[names.get(v["variant"], v["variant"]), _f(v["wape"]), _f(v["bias"]), _f(v["coverage"], True),
                 v["misses"], v["ownerQuestions"], v["stockoutWeeks"], v["avgStockUnits"], _f(v["fillRate"], True)]
                for v in bt["variants"]]
        out.append(_table(["Variant", "WAPE", "bias", "range coverage", "misses", "owner questions",
                           "stockout weeks", "avg stock units", "fill rate"], rows) + "\n")
        ap = bt.get("actualPurchasing", {})
        out.append(f"What the company actually did (implied from real purchases): average implied stock "
                   f"{ap.get('avgImpliedStockUnits')} units, short weeks {ap.get('shortWeeks')}.\n")
        out += [f"- {n}" for n in bt.get("notes", [])]
        out.append("")

    out.append("## What needs keys\n")
    out.append("- Jev columns: set `JEV_API_KEY` in `.env`, then run `uv run python -m eval.run_eval`.\n"
               "- Step 5 columns: set `OPENROUTER_API_KEY`. Add `--step5-shuffle` for its order passes.\n"
               "- Backtest row C with live Jev: needs `JEV_API_KEY` and `--methods` that include `jev`.\n")
    return "\n".join(out)


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / "report.md", out_dir / "report.json"
    md.write_text(render(report))
    js.write_text(json.dumps(report, indent=1, default=str))
    return md, js
