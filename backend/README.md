# Backend

FastAPI + Python 3.12 + SQLite. The backend holds all the state, all the arithmetic, every Jev decision and the payment rules. The workers (supplier lanes, the Photon channel) plug into it through two interfaces.

## Commands (run from `backend/`)

| Task | Command |
|---|---|
| Install | `uv sync` |
| Tests | `uv run pytest -q` |
| Lint | `uv run ruff check app tests scripts` |
| Server | `uv run uvicorn app.main:app --reload --port 8000` |
| API types for the frontend | `uv run python scripts/export_openapi.py` (writes `web/openapi.json`) |
| Dataset | `scripts/fetch_dataset.sh`, then set `QB_DATA_DIR` |
| Regenerate synthetic supply bills | `uv run python scripts/generate_supply_bills.py` (after you change `fixtures/usage_ratios.yaml`) |

## Run modes

- `RUN_MODE=fixture` (default): no keys. Each dependency is `cached`. Supplier lanes are recorded fixtures, Jev answers come from deterministic stub rules, and Step 5 Preview uses template fallbacks.
- `RUN_MODE=live`: each dependency is `live` when its key is set, and `cached` when it is not.
- The admin drawer changes one dependency at a time while the server runs. Send `POST /admin/mode` with `{"dependency": "jev", "mode": "cached"}` and the `X-Admin-Token` header.
- `FIXTURE_TIME_SCALE` speeds up or slows down the fixture lanes. Use 1.0 for a realistic run, 0.05 for a fast one.

## Module map

| Module | Job | Spec |
|---|---|---|
| `app/contracts.py` | Every data shape (Pydantic, camelCase JSON) | DATA_FLOW §1–8 |
| `app/orchestrator.py` | Job and lane state machines, parallel lanes, live re-plan, deadline, approval, handoff | §4, §10 |
| `app/stages/extract.py` | Regex candidates: pack counts, money amounts | §6 |
| `app/stages/normalize.py` | Packs needed, cost per unit, eligibility, flags | DATA_FLOW §3.1 |
| `app/stages/optimize.py` | Every assignment, cart pricing, subset requests | DATA_FLOW §3.2 |
| `app/stages/solver.py` | MILP fallback (PuLP + HiGHS) that picks candidate assignments past 200,000 | DATA_FLOW §3.2 |
| `app/stages/totals.py` | J7 picks → costs → sum check (verified or not) | DATA_FLOW §4 |
| `app/stages/rules.py`, `cards.py`, `rank.py` | Spending rules, simulated cards, results + warnings | §7 |
| `app/jev.py` | Jev HTTP client: cache, semaphore, retries, stub fallback | §6 |
| `app/judgments.py` | J1–J10 question design and interpretation | §6 |
| `app/decide.py` | The cascade: Jev → Step 5 Preview; safety never escalates; logs every decision | §6 |
| `app/guard.py` | Payment guard: DOM card-field check + J6; fails closed | §5 |
| `app/llm.py` | Step 5 Preview: specs, escalation, summary; template fallbacks | §2 |
| `app/discovery.py` | CREAO run + poll + cache → J3 filter → lane selection | §4.1 |
| `app/data/` | Dataset + injected bills → insights, draft list, rules | §3 |
| `app/events.py` | Event log, SSE wake-up, replay | §10.4 |
| `app/planner/` | Restock planner: sales from the books, damped-trend forecast, J11 miss causes and update rules (`loop.py`), J12 owner messages (`adjust.py`), ordering rule (`policy.py`), backtest (`service.py`) | §13 |
| `app/preflight.py` | The preflight checks; a red check switches that dependency to cached mode | §10.7 |

## What each request does

1. `GET /insights` loads the books and runs J1 on each distinct item, so only supply items remain.
2. `GET /suppliers` runs CREAO discovery or reads the cache, then J3 filters the candidates.
3. `GET /shopping-list/draft` gives the prefilled `ShoppingRequest` for screen 1.
4. `POST /jobs` starts the job and returns at once. Every step after that arrives as an event:
   1. Step 5 Preview writes the specs.
   2. Each supplier lane searches (J5), builds the cart and reads the totals (J7 + the sum check).
   3. The optimizer re-plans after every event.
   4. Subset carts are priced for the top 2 split plans.
   5. The distributor gets an SMS, then a call, and each offer during the call causes a re-plan (J9).
5. At the deadline, or when all the work ends, the results are built. They include rule checks, warnings and a summary.
6. `POST /jobs/{id}/approve` checks the rules again and mints simulated cards. Then each lane sets its cart to the approved plan, goes to checkout and stops at the payment step (the guard).

## Rules to keep

- **No arithmetic in a model.** Code finds the candidates, Jev selects one, and code computes the result.
- **Fail closed on safety.** If J6 is unavailable, the guard stops the lane.
- **Every external call has a timeout and a fallback.** See spec §10.2. When you add a call, add its row there.
- **Every Jev call goes through `decide()`.** This keeps the cache, the logging and the Intuit evaluation data in one place.
- **Thresholds live in `config.Thresholds`.** Tune them on cached runs at H10, and record the values in spec §6.

## Before the live demo

- [ ] Set `JEV_API_KEY`, `OPENROUTER_API_KEY`, `CREAO_API_KEY` and `CREAO_APP_ID`.
- [ ] Set `CHECKOUT_CONTACT__*` to a team-controlled address.
- [ ] Change `ADMIN_TOKEN` and `WEBHOOK_SECRET` from their dev values.
- [ ] `GET /health/preflight` is green or amber everywhere.
- [ ] Do one full warm-up run, and keep its job id for `POST /admin/replay/{jobId}`.

## Known gaps (to do)

- The live Photon channel and the live browser lanes belong to the agents team (see `app/lanes/README.md`).
- The `decisions` table holds the evaluation data. The labelling script and the report (Intuit) are not written yet.
- Sentry is initialized and wraps the Jev and LLM calls in spans. Lane-level spans are not added yet.
- Job state lives in memory while the server runs, and as a snapshot in SQLite. A restart does not resume running jobs. A resumed job would need the browser sessions, which do not survive a restart anyway.
