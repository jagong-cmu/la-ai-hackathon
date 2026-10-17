# Intuit / QuickBooks track: evaluation report

All numbers below come from this run. A method without a key is reported as "not run (no key)". The kit never puts stub answers in a Jev or Step 5 column.

## Methods run

| Method | Status |
|---|---|
| Keyword rules | ran |

## Method

- **Same input for every method.** The runner calls `app.judgments.j12_owner_message` and `j11_miss_cause` with a capture object in place of `Decider`. So every method gets the exact state and questions that the app sends in production.
- **Jev** runs through `JevClient.ask` on the live API. **Step 5 Preview** runs through `Step5Client.answer_questions` (the escalation path of `decide()`). **Keyword rules** are `app.stub_rules` (the fixture-mode stand-in).
- **One decision** = one call with all the questions for one message or one miss.
- **J12 scoring.** item and kind: exact option match. quantity and date are scored only on actionable rows (an item from the catalog and a kind that is not `not_a_forecast_change`). quantity is correct when code turns the chosen phrase into the labelled number. date is correct when `resolve_date(chosen phrase, receivedAt)` gives the labelled date. When the label has no quantity or date, the correct answer is "not stated" (or no question). "all" = every scored field is correct.
- **J11 scoring.** cause: exact match with the weak label.
- **Calibration** uses the Choice `probabilities`. Brier (multi-class) = mean of sum over options of (p - y)^2, range 0-2. Brier (top) = mean of (p of the chosen option - 1 if correct else 0)^2, range 0-1. Reliability: 10 equal bins on the probability of the chosen option. ECE = size-weighted mean of |accuracy - mean confidence| over the bins.
- **Latency** is per decision, measured one call at a time (no parallel calls). **Cost per 1,000 decisions**: Jev = mean `usage.input_tokens` x $0.042 / 1M x 1,000 (output tokens are free). Step 5 = N/A, because `Step5Client` does not keep the OpenRouter usage.
- **Option-order stability.** The runner asks each question again with the Choice options in reversed order and in one seeded random order, each pass with an empty cache. Agreement = the share of answers that stay the same. First-option rate = the share of answers that pick the option shown first. A large rise in the first-option rate after reordering shows first-option bias.
- **Candidate recall** (J12): the share of labelled quantities and dates that the regex candidates contain at all. No method can score higher than this on those fields.

## Disclosures

1. **Synthetic data.** The product sales come from the Intuit hackathon dataset company-001, which the qb-world-sim simulator made. They are not a real bakery's books. The results apply to this simulator.
2. **Derived fixture.** `backend/fixtures/company-001-sales.json` is a compact file that code derived from the dataset (weekly sales per customer, bill memos, purchases). Tests and this kit use it instead of the 400 MB dataset.
3. **Assumed usage ratios.** Supply use = real product sales x usage ratios that we assumed (`backend/fixtures/usage_ratios.yaml`). The supply bills are synthetic and were regenerated from the sales through those ratios. The J11/J12 results do not use them; the planner's supply plans do.
4. **Weak J11 labels.** Rules over the code-detected facts plus the next 3 weeks of sales (hindsight) made the J11 labels. No person checked them. Two label rules (backorder memo, lost customer) use the same facts as the keyword baseline, so the baseline is favoured on those causes by construction.
5. **Hand-written J12 messages.** One author wrote the owner messages and their labels for this evaluation. They are not real owner texts. The author knew the weak points of the regex candidates and wrote hard cases on purpose (see the tags).
6. **One year of data.** The forecaster learns no seasonality.
7. **Company-reported price.** Jev's $0.042 per 1M input tokens is the vendor's claim (PROJECT_BRIEF §6.10).

## J12: owner message -> forecast adjustment (n = 83)

Candidate recall (regex limit for every method): quantity 90.2% (n = 41), date 98.0% (n = 51).

| Method | item acc | kind acc | kind macro-F1 | quantity acc | date acc | all fields | Brier item | Brier kind | ECE (item+kind) | p50 ms | p95 ms | $ / 1,000 | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Keyword rules | 75.9% | 60.2% | 0.619 | 88.5% (n=61) | 91.8% (n=61) | 42.2% | 0.437 | 0.701 | 0.209 | 0.01 | 0.04 | 0.0000 | 0 |

- Keyword rules: The rules return fixed constants (for example 0.9) as probabilities. They are not estimates, so read their calibration as a reference only.
- Keyword rules cost: local code, no API cost.

### J12 reliability, Keyword rules (item + kind, n = 166)

| Confidence bin | n | Mean confidence | Accuracy |
|---|---|---|---|
| 0.8-0.9 | 83 | 0.880 | 0.602 |
| 0.9-1.0 | 83 | 0.900 | 0.759 |

### J12 option-order stability

| Method | field | n | agree (reversed) | agree (shuffled) | first-option rate (normal) | (reversed) | (shuffled) |
|---|---|---|---|---|---|---|---|
| Keyword rules | item | 83 | 71.1% | 83.1% | 24.1% | 27.7% | 19.3% |
| Keyword rules | kind | 83 | 100.0% | 100.0% | 55.4% | 3.6% | 14.5% |
| Keyword rules | quantity | 46 | 0.0% | 41.3% | 100.0% | 100.0% | 100.0% |
| Keyword rules | date | 63 | 0.0% | 58.7% | 100.0% | 100.0% | 100.0% |

### J12 all-fields accuracy by hard-case tag

| Tag | n | Keyword rules |
|---|---|---|
| abbrev_date | 1 | 0.0% |
| ambiguous_next | 1 | 100.0% |
| cancel_previous | 2 | 0.0% |
| chatter | 8 | 100.0% |
| delta_needs_math | 2 | 0.0% |
| distractor_word | 2 | 0.0% |
| holiday_date | 1 | 0.0% |
| negation | 8 | 12.5% |
| no_keyword | 5 | 0.0% |
| number_is_duration | 1 | 0.0% |
| number_not_quantity | 4 | 75.0% |
| ordinal_day_only | 1 | 100.0% |
| out_of_catalog | 1 | 100.0% |
| plain | 33 | 45.5% |
| resume_date_not_start | 1 | 0.0% |
| spelled_number | 1 | 0.0% |
| synonym_item | 10 | 20.0% |
| two_dates | 1 | 0.0% |
| two_numbers | 3 | 0.0% |
| typo | 3 | 33.3% |
| vague_quantity | 3 | 66.7% |

## J11: forecast-miss cause (n = 32, WEAK labels)

Label counts: customer_gained 3, customer_lost 7, lasting_shift_up 1, noise 4, one_time_event 7, supply_shortage 10.

| Method | cause acc | cause macro-F1 | Brier (multi) | Brier (top) | ECE | p50 ms | p95 ms | $ / 1,000 | errors |
|---|---|---|---|---|---|---|---|---|---|
| Keyword rules | 78.1% | 0.776 | 0.385 | 0.173 | 0.101 | 0.00 | 0.00 | 0.0000 | 0 |

### J11 accuracy by label rule

R1 and R2 share their facts with the keyword baseline, so a high baseline score there is expected by construction.

| Label rule | n | Keyword rules |
|---|---|---|
| R1:backorder_memo+below | 10 | 100.0% |
| R2:lost_customer+below | 7 | 100.0% |
| R4:first_order+reorders | 3 | 100.0% |
| R5:first_order+no_reorder | 5 | 0.0% |
| R6:persists | 1 | 100.0% |
| R7:far_above+reverts | 2 | 100.0% |
| R8:default | 4 | 50.0% |

### J11 reliability, Keyword rules (n = 32)

| Confidence bin | n | Mean confidence | Accuracy |
|---|---|---|---|
| 0.7-0.8 | 2 | 0.700 | 1.000 |
| 0.8-0.9 | 30 | 0.855 | 0.767 |

### J11 option-order stability

| Method | field | n | agree (reversed) | agree (shuffled) | first-option rate (normal) | (reversed) | (shuffled) |
|---|---|---|---|---|---|---|---|
| Keyword rules | cause | 32 | 100.0% | 100.0% | 31.2% | 6.2% | 15.6% |

## Backtest: replay of the year (52 weeks, 6 products)

**jevSource = `stub`.** Variant C did NOT use Jev: the fixture keyword rules answered J11. Do not read row C as a Jev result.

| Variant | WAPE | bias | range coverage | misses | owner questions | stockout weeks | avg stock units | fill rate |
|---|---|---|---|---|---|---|---|---|
| A plain | 0.329 | 0.061 | 84.2% | 23 | 0 | 0 | 652.3 | 100.0% |
| B fixed_rule | 0.315 | 0.052 | 56.9% | 63 | 0 | 1 | 518.3 | 99.8% |
| C jev (J11 causes) | 0.325 | 0.016 | 80.1% | 29 | 6 | 1 | 591.5 | 100.0% |

What the company actually did (implied from real purchases): average implied stock 369.2 units, short weeks 15.0.

- One-step-ahead forecasts, refit every week (rolling origin).
- Stock simulation: weekly order-up-to rule, 1-week delivery, same rule for every variant.
- jevSource 'stub' means fixture rules answered J11; it is NOT a Jev result.
- Data is simulated (qb-world-sim); results apply to this simulator.

## What needs keys

- Jev columns: set `JEV_API_KEY` in `.env`, then run `uv run python -m eval.run_eval`.
- Step 5 columns: set `OPENROUTER_API_KEY`. Add `--step5-shuffle` for its order passes.
- Backtest row C with live Jev: needs `JEV_API_KEY` and `--methods` that include `jev`.
