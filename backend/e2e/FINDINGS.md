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
| S-007 | jobs | After a deadline, an approval during the late update got a `results_updated` event after `approved`, and the approved results were replaced. | `_late()` checked for an approval, then awaited the summary (about 12 s with live Step 5), then set `view.results`. | fixed (1580eb2): results set under the approve lock; no late update after a decision |
| S-008 | payment guard | The DOM layer (no model) did not detect `cc_number`, `cardnum`, `cvv2`, `cvc2`, `csc`, `exp_month`, `expYear`, or `autocomplete="billing cc-number"`. | `cc-?num` allowed only a hyphen; `\bcvv\b` needs a boundary after `cvv`; the whole `autocomplete` value was compared with the token set. | fixed (4af4f34): one `is_card_field()` for the guard and the adapter; no verdict changes on the saved real pages |
| S-009 | planner | Owner message "300 extra buns on 2026-10-30" was applied to the message week. "1.5k" gave the quantity 1. "three hundred extra" was `applied` with no quantity (adds 0). | The J12 phrase code had no ISO or m/d/yyyy date, no decimal or `k` quantity, and no check for a missing quantity. | fixed (a5be44e); the rules baseline on eval j12 is unchanged |
| S-010 | distributor | `allocate_total(100, {a: 0, b: 0.5})` gave `{a: 200, b: -100}`: a negative price in a quoted plan. The live distributor sends weight 0 for an item with no web cost. | The 0-weight item took weight 1.0 in its share but 0 in the base. | fixed (b7fc3f4) |
| S-011 | validation | `pastUnitPrice=-3` was accepted. With no web plan, the distributor call anchors on past prices, so it would say "we paid about $-...". A negative cadence gave negative yearly savings. | No lower limit on `past_unit_price` or `order_cadence_days`; `call_anchor()` keeps any truthy price. | fixed (3ab21c2): `ge=0`, 422 |
| S-012 | security | A distributor id `../../../escaped` wrote `escaped/call.txt` outside `var/evidence`. Supplier and item ids are path parts too. `POST /jobs` is public through the tunnel. | Evidence paths joined request ids with no check. | fixed (e7e319a): each part goes through `path_segment()` |
| S-013 | security | `file://`, `javascript:` and `chrome://` supplier URLs were accepted; a live lane opens `supplier.url` and serves its screenshots. `fixture_path('../discovery')` loaded another JSON file. | No URL scheme check; raw id in the fixture path. | fixed (50db9bf): http(s) only (https:// added when no scheme); discovery skips bad URLs |
| S-014 | events, validation | `after=<25 digits>` or that `Last-Event-ID` gave 200 with an empty body (SQLite integer overflow in the stream). `quantity=10**25` gave totals such as `8.8e+24`. | No upper limit on either value. | fixed (859ffa0): `after` at most 2**62, Last-Event-ID clamped, quantity at most 1,000,000,000 |
| S-015 | discovery | A CREAO row with an int `id` or `phone`, or a list `name`, `email` or `adapter`, raised in discovery. The rows were cached first, so every later draft failed. | Model-written fields went to `SupplierCandidate` and a set lookup with no type check. | fixed (3b159c6): numbers become text, lists and objects are dropped |
| S-016 | optimizer | A listing with `pack_price` 0 ("$0.00, see the price in the cart") was eligible with a line cost of 0, so it ranked as the cheapest offer. | `normalize_option()` had no price check. | fixed (5ac15db): ineligible, flag `price_missing` |

Open questions for the team (not changed by the loop):
- `POST /admin/replay?speed=0` or a negative speed replays at once. The route could refuse them with 422.
- `create_job` checks the key, then awaits, then inserts. Two concurrent creates with one new key can race. The soak sends 20 concurrent creates with one new key each cycle (`create_race_new_key`); it has not failed yet.
- A replay that runs during a restart keeps its copied state, and its stream can stay open. S-006 does not change replays, because a replay is a recording.
- After a deadline, `results_updated` comes only when all the late work ends (`_late()` waits for every pending task). In a deadline run, verified plans existed at 47 s, but the owner's results showed "no recommendation" until the slow `delta` lane ended at 125 s. With live hosted-agent lanes, this wait can be minutes. The team must decide if each late plan change must update the results.
- Sentry drops job traces under load. Cycle 004 sent 20 `job` spans for 39 jobs. Sentry client reports show more than 40,000 spans dropped with `queue_overflow` and `backpressure` in 3 hours. The soak's 10/s polling caused most of it (now 2/s). But each `GET /jobs/{id}`, `/health` and SSE reconnect is a transaction at `traces_sample_rate=1.0`, so a busy web client can push the job and AI traces out too. A `traces_sampler` that samples these reads low would protect them. This needs a team decision.
- S-013 checks only the URL scheme. A live lane can still open a private address (for example `http://169.254.169.254/` or `http://localhost:8000/admin/...`) that a public request names. Blocking private hosts would also block the local test site, so the team must choose (for example, block them only when `RUN_MODE=live`).
- Access control: every job and planner route is public through the tunnel, with no authentication. `GET /jobs` lists every job id, and anyone can then call `POST /jobs/{id}/approve` (mints the simulated cards, and live lanes type the team's checkout contact; the guard still stops before payment) or `POST /planner/owner-messages` (changes the forecast that the daily check orders from). This is a design decision for the team.
- `route_webhook()` sends a webhook with no `jobId` to the first gathering job that has a distributor. With two jobs at once, a distributor's SMS reply can go to the wrong job.
- Live lead, not reproducible in fixture mode: Sentry SUPPLIER-AGENT-API-3, "All 3 reconnection attempts failed" from the `browser_use` `BrowserSession` logger, during a live Wees Express lane (4 events, last at 10:25 UTC).

## Cycle log

| Cycle | Time-scale | Scenarios | Failed | New findings |
|---|---|---|---|---|
| 001 | 1.0 | (killed by a harness timeout) | - | S-001 (from pytest) |
| 002 | 0.1 | 14 | 3 | S-003, S-004, S-005; S-002 from Sentry |
| 003 | 0.1 | 14 | 0 | none (S-003 to S-005 fixes hold) |
| 004 | 0.1 | 16 | 1 | S-006 (new scenario `restart_mid_job`); `create_race_new_key` passed |
| 005 | 1.0 | 16 | 10 | none: harness timeouts (a fixture job takes ~125 s at real time); fixed in 29a8cd2. S-007 to S-010 came from new tests this cycle |
