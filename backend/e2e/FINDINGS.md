# Soak findings

The overnight soak loop (`backend/e2e/`) writes one row for each defect that it finds. Each fix has a
regression test and its own commit on the soak branch. The loop runs in fixture mode only: every
dependency is cached, so no run reaches a paid service, a real supplier site, a phone or an SMS.

How to run one cycle:

```bash
cd backend && uv run python -m e2e.run_cycle --out /tmp/soak/cycle-001
```

Status values: `fixed` (commit given), `open` (not fixed yet, with the reason), `not-a-bug` (the
expectation in the harness was wrong).

| ID | Area | Symptom | Root cause | Status |
|---|---|---|---|---|
| S-001 | tests | `uv run pytest -q` failed 9 tests in a venv without the `browser` extra (`ModuleNotFoundError: playwright`). | `test_warm_pool_hands_one_browser_to_one_lane` and `tests/test_site_adapters.py` import modules that load Playwright at the top, with no skip marker. | fixed (1f9fb3b) |
| S-002 | draft | `GET /shopping-list/draft` gave 500 on the live server (Sentry SUPPLIER-AGENT-API-8). `/planner/check?autostart=true` takes the same path. | Discovery selected no supplier, and `ShoppingRequest` needs at least one. | fixed (8d55f72): 503 `no_suppliers` with each candidate's reason |
| S-003 | events | A follower of a replay got 0 of 78 events. A live follower of a rejected or failed job missed the `rejected` / `job_failed` event. | A replay copies the source's final view at once, so the events route closed the stream. The stream ends on the terminal `job_state`, but reject and failure emitted their reason after it. | fixed (ee39ab9) |
| S-004 | webhook | `POST /webhooks/photon` gave 500 for a body that is not a JSON object, and for a `jobId` list. | `json.loads` without a guard; `payload.get` on a list; a list as a dict key. | fixed (0bf77b4): 400 |
| S-005 | jobs | The same `Idempotency-Key` with a changed body returned the first job, with no error. | The key lookup did not compare the request. | fixed (3f355f7): 422 `idempotency_key_reused` |
| S-006 | persistence | After a restart, a job that was `gathering` or `results_ready` showed that state forever. Its event stream never ended, and approve gave 404. | No process runs a stored job after a restart, and nothing marked it. | fixed (756632e): failed at start with a `job_failed` reason; its key is freed |

Open questions for the team (not changed by the loop):
- `POST /admin/replay?speed=0` or a negative speed replays at once. The route could refuse them with 422.
- `create_job` checks the key, then awaits, then inserts. Two concurrent creates with one new key can race. The soak sends 20 concurrent creates with one new key each cycle (`create_race_new_key`); it has not failed yet.
- A replay that runs during a restart keeps its copied state, and its stream can stay open. S-006 does not change replays, because a replay is a recording.
- Live lead, not reproducible in fixture mode: Sentry SUPPLIER-AGENT-API-3, "All 3 reconnection attempts failed" from the `browser_use` `BrowserSession` logger, during a live Wees Express lane (4 events, last at 10:25 UTC).

## Cycle log

| Cycle | Time-scale | Scenarios | Failed | New findings |
|---|---|---|---|---|
| 001 | 1.0 | (killed by a harness timeout) | - | S-001 (from pytest) |
| 002 | 0.1 | 14 | 3 | S-003, S-004, S-005; S-002 from Sentry |
| 003 | 0.1 | 14 | 0 | none (S-003 to S-005 fixes hold) |
| 004 | 0.1 | 16 | 1 | S-006 (new scenario `restart_mid_job`); `create_race_new_key` passed |
